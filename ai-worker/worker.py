"""분석 워커 메인 루프.

Supabase의 `videos` 테이블을 폴링하며 status='uploaded'인 영상을 하나씩 집어가
(1) 원본 영상 다운로드 -> (2) YOLO11-pose+ByteTrack 포즈 추출 -> (3) 오토인코더
이상행동 탐지 -> (4) ffmpeg 클립/썸네일 추출(이상 구간 앞뒤 5초 패딩) -> (5) 결과
업로드 및 DB 반영까지 수행한다 (pipeline/report.py).

ingest-worker 가 cctv-agent-electron 으로부터 받은 5분 조각을 Storage 'videos'
버킷에 올리고 status='uploaded' 행을 만들면, 이 워커가 그걸 집어간다.

실행: cd ai-worker && python worker.py (계속 떠 있으면서 새 업로드를 감시하는 데몬)
"""

from __future__ import annotations

import os
import shutil
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

from pipeline.report import process_video

load_dotenv()

# 둘 다 선택값으로 둔다 — ingest-worker/backend 와 마찬가지로 Supabase 프로젝트가
# 아직 없어도 컨테이너가 크래시루프하지 않고 대기하게 하기 위함 (main() 참고).
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
POLL_INTERVAL_SEC = float(os.environ.get("POLL_INTERVAL_SEC", "5"))
POSE_MODEL = os.environ.get("POSE_MODEL", "yolo11n-pose.pt")
WORK_DIR = Path(os.environ.get("WORK_DIR", "./tmp"))

ANOMALY_WINDOW_FRAMES = int(os.environ.get("ANOMALY_WINDOW_FRAMES", "32"))
ANOMALY_STRIDE_FRAMES = int(os.environ.get("ANOMALY_STRIDE_FRAMES", "8"))
ANOMALY_THRESHOLD_STD = float(os.environ.get("ANOMALY_THRESHOLD_STD", "2.5"))
ANOMALY_MIN_SEGMENT_FRAMES = int(os.environ.get("ANOMALY_MIN_SEGMENT_FRAMES", "16"))


def fetch_next_pending_video(sb: Client) -> dict | None:
    res = (
        sb.table("videos")
        .select("*")
        .eq("status", "uploaded")
        .neq("storage_path", "")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def mark_processing(sb: Client, video_id: str) -> None:
    sb.table("videos").update({"status": "processing", "progress": 0}).eq("id", video_id).execute()


def update_progress(sb: Client, video_id: str, percent: int) -> None:
    sb.table("videos").update({"progress": percent}).eq("id", video_id).execute()


def mark_done(sb: Client, video_id: str, meta) -> None:
    sb.table("videos").update(
        {
            "status": "done",
            "progress": 100,
            "duration_sec": meta.frame_count / meta.fps if meta.fps else None,
            "fps": meta.fps,
            "frame_width": meta.width,
            "frame_height": meta.height,
            "processed_at": "now()",
        }
    ).eq("id", video_id).execute()


def mark_failed(sb: Client, video_id: str, error_message: str) -> None:
    sb.table("videos").update(
        {"status": "failed", "error_message": error_message[:2000]}
    ).eq("id", video_id).execute()


def video_exists(sb: Client, video_id: str) -> bool:
    res = sb.table("videos").select("id").eq("id", video_id).limit(1).execute()
    return bool(res.data)


def process_one(sb: Client, video_row: dict) -> None:
    video_id = video_row["id"]
    user_id = video_row["user_id"]
    storage_path = video_row["storage_path"]

    print(f"[ai-worker] processing video_id={video_id} storage_path={storage_path}")
    mark_processing(sb, video_id)

    video_work_dir = WORK_DIR / video_id
    video_work_dir.mkdir(parents=True, exist_ok=True)
    local_video_path = video_work_dir / "original.mp4"

    try:
        # 1) 원본 영상 다운로드
        blob = sb.storage.from_("videos").download(storage_path)
        local_video_path.write_bytes(blob)
        update_progress(sb, video_id, 3)

        # 2) 분석
        meta, results = process_video(
            video_path=str(local_video_path),
            video_id=video_id,
            work_dir=str(WORK_DIR),
            pose_model=POSE_MODEL,
            window_frames=ANOMALY_WINDOW_FRAMES,
            stride_frames=ANOMALY_STRIDE_FRAMES,
            threshold_std=ANOMALY_THRESHOLD_STD,
            min_segment_frames=ANOMALY_MIN_SEGMENT_FRAMES,
            on_progress=lambda pct: update_progress(sb, video_id, pct),
        )

        # 3) 클립/썸네일 업로드 + anomaly_events insert
        if not video_exists(sb, video_id):
            print(f"[ai-worker] skipped deleted video_id={video_id}")
            return

        for seg, clip_path, thumb_path in results:
            clip_storage_path = f"{user_id}/{video_id}/{clip_path.name}"
            thumb_storage_path = f"{user_id}/{video_id}/{thumb_path.name}"

            sb.storage.from_("clips").upload(
                clip_storage_path, str(clip_path), {"content-type": "video/mp4"}
            )
            sb.storage.from_("clips").upload(
                thumb_storage_path, str(thumb_path), {"content-type": "image/jpeg"}
            )

            sb.table("anomaly_events").insert(
                {
                    "video_id": video_id,
                    "user_id": user_id,
                    "track_id": seg.track_id,
                    "start_frame": seg.start_frame,
                    "end_frame": seg.end_frame,
                    "start_time_sec": seg.start_time_sec,
                    "end_time_sec": seg.end_time_sec,
                    "anomaly_score": seg.score,
                    "threshold": seg.threshold,
                    "clip_storage_path": clip_storage_path,
                    "thumbnail_storage_path": thumb_storage_path,
                }
            ).execute()

        # 4) 완료 처리
        mark_done(sb, video_id, meta)
        print(f"[ai-worker] done video_id={video_id} events={len(results)}")

    except Exception as exc:  # noqa: BLE001 - 워커는 절대 죽지 않고 실패를 기록해야 함
        print(f"[ai-worker] FAILED video_id={video_id}: {exc}")
        traceback.print_exc()
        mark_failed(sb, video_id, str(exc))

    finally:
        shutil.rmtree(video_work_dir, ignore_errors=True)


def main() -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("[ai-worker] SUPABASE_URL/SUPABASE_SERVICE_KEY 미설정 — 대기만 하고 폴링하지 않습니다")
        while True:
            time.sleep(POLL_INTERVAL_SEC)

    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[ai-worker] started, polling every {POLL_INTERVAL_SEC}s")
    while True:
        video_row = fetch_next_pending_video(sb)
        if video_row is None:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        process_one(sb, video_row)


if __name__ == "__main__":
    main()
