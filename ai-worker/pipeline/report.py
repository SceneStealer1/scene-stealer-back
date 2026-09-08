"""한 영상에 대해 포즈 추출 -> 이상행동 탐지 -> 클립 추출까지 end-to-end로 실행."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import anomaly_detection, clip_export, poselift_format, pose_extraction
from .anomaly_detection import AnomalySegment
from .pose_extraction import VideoMeta

# 파이프라인 단계별 진행률 배분: 다운로드는 worker.py에서 이미 처리 후 진입하므로
# 여기선 (1) 포즈추출이 가장 오래 걸려 절반 이상을 할당하고, (2) 이상행동 탐지,
# (3) 클립/썸네일 추출 순으로 남은 구간을 나눈다.
POSE_EXTRACTION_RANGE = (5, 70)
ANOMALY_DETECTION_PROGRESS = 82
CLIP_EXPORT_RANGE = (82, 98)

# 하이라이트 클립은 이상 구간 앞뒤로 5초씩 여유를 둔다 (제품 요구사항 — 대시보드에서
# 무슨 일이 있었는지 맥락을 보려면 탐지 구간만으로는 부족하다). clip_export.extract_clip
# 의 기본값(1.0초, 키프레임 seek 오차 보정용)과는 별개의 목적이라 여기서 명시적으로 덮어쓴다.
HIGHLIGHT_PAD_SEC = 5.0


def process_video(
    video_path: str,
    video_id: str,
    work_dir: str,
    pose_model: str = "yolo11n-pose.pt",
    window_frames: int = 32,
    stride_frames: int = 8,
    threshold_std: float = 2.5,
    min_segment_frames: int = 16,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[VideoMeta, list[tuple[AnomalySegment, Path, Path]]]:
    last_reported = -1

    def report(pct: int) -> None:
        nonlocal last_reported
        pct = max(0, min(100, pct))
        if pct != last_reported and on_progress is not None:
            last_reported = pct
            on_progress(pct)

    meta = pose_extraction.read_video_meta(video_path)
    report(POSE_EXTRACTION_RANGE[0])

    lo, hi = POSE_EXTRACTION_RANGE

    def on_frame(frame_idx: int, frame_count: int) -> None:
        if frame_count <= 0:
            return
        ratio = min(frame_idx / frame_count, 1.0)
        report(lo + int((hi - lo) * ratio))

    tracks = pose_extraction.extract_pose_tracks(
        video_path, model_name=pose_model, frame_count=meta.frame_count, on_frame=on_frame
    )
    report(hi)

    normalized = {
        track_id: poselift_format.normalize_track(track) for track_id, track in tracks.items()
    }

    segments = anomaly_detection.detect_anomalies_for_video(
        normalized,
        fps=meta.fps,
        window_frames=window_frames,
        stride_frames=stride_frames,
        threshold_std=threshold_std,
        min_segment_frames=min_segment_frames,
    )
    report(ANOMALY_DETECTION_PROGRESS)

    clip_dir = Path(work_dir) / video_id / "clips"
    results: list[tuple[AnomalySegment, Path, Path]] = []

    clo, chi = CLIP_EXPORT_RANGE
    for idx, seg in enumerate(segments):
        clip_path = clip_dir / f"event_{idx:03d}.mp4"
        thumb_path = clip_dir / f"event_{idx:03d}.jpg"

        clip_export.extract_clip(
            video_path, seg.start_time_sec, seg.end_time_sec, clip_path, pad_sec=HIGHLIGHT_PAD_SEC
        )
        mid_sec = (seg.start_time_sec + seg.end_time_sec) / 2
        clip_export.extract_thumbnail(video_path, mid_sec, thumb_path)

        results.append((seg, clip_path, thumb_path))
        report(clo + int((chi - clo) * (idx + 1) / len(segments)))

    report(chi)
    return meta, results
