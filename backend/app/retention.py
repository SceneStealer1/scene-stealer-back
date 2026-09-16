"""보관 정책 정리 (요구사항 3.3 · 5.5).

- 원본 조각(videos + Storage 'videos'): **7일**
- 클립/썸네일(events + Storage 'clips'): 매장 설정 `clip_retention_days` (기본 30일)

원본이 7일, 클립이 30일인 이유는 용량 차이다 — 5분 메인스트림 조각은 약
150MB 고 클립은 수 MB 다. 그래서 재분석 창(docs/ai-gate-contract.md 6절)도
클립이 남아 있는 30일이다.

별도 cron 컨테이너 대신 backend 안의 백그라운드 태스크로 돈다. 서비스가
replicas: 1 이라(docker-compose.yml) 두 번 도는 일이 없고, 운영 대상이 하나
줄어든다. 여러 replica 로 늘리면 잠금이 필요하다.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from .supabase_client import supabase

#: 원본 조각 보관 기간 (요구사항 3.3).
SEGMENT_RETENTION_DAYS = 7

#: 정리 주기. 하루 한 번이면 충분하다 — 몇 시간 늦게 지워진다고 문제가 되지 않는다.
SWEEP_INTERVAL_SEC = 24 * 60 * 60

#: 한 번에 지우는 최대 행 수. 오래 방치된 프로젝트에서 첫 정리가 수만 건이어도
#: DB 와 Storage 를 한꺼번에 때리지 않도록 나눠서 돈다.
BATCH_SIZE = 500


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sweep_segments(now: datetime) -> int:
    """7일 지난 원본 조각을 Storage 와 videos 에서 지운다."""
    if supabase is None:
        return 0
    cutoff = now - timedelta(days=SEGMENT_RETENTION_DAYS)

    rows = (
        supabase.table("videos").select("id, storage_path")
        .lt("recorded_started_at", _iso(cutoff)).limit(BATCH_SIZE).execute().data or []
    )
    if not rows:
        return 0

    paths = [r["storage_path"] for r in rows if r.get("storage_path")]
    if paths:
        try:
            supabase.storage.from_("videos").remove(paths)
        except Exception as error:  # noqa: BLE001
            # Storage 삭제가 실패해도 행은 지운다 — 남은 파일은 다음 회차에
            # 고아로 남지만, 행을 붙들고 있으면 영원히 재시도만 하게 된다.
            print(f"[backend] 원본 조각 Storage 삭제 실패: {error}")

    # events.video_id 는 on delete set null 이라 이벤트 자체는 살아남는다.
    supabase.table("videos").delete().in_("id", [r["id"] for r in rows]).execute()
    print(f"[backend] 원본 조각 {len(rows)}건 정리 (기준 {_iso(cutoff)})")
    return len(rows)


def sweep_clips(now: datetime) -> int:
    """보관 기간이 지난 클립을 Storage 에서 지운다.

    events 행은 남긴다 — 언제 무슨 일이 있었는지의 기록은 영상보다 오래 간다.
    클립 경로만 비워서 화면이 '영상 없음'으로 그리게 한다.
    """
    if supabase is None:
        return 0

    rows = (
        supabase.table("events").select("id, clip_storage_path, thumbnail_storage_path")
        .lt("clip_expires_at", _iso(now))
        .not_.is_("clip_storage_path", "null")
        .limit(BATCH_SIZE).execute().data or []
    )
    if not rows:
        return 0

    paths = [p for r in rows for p in (r.get("clip_storage_path"), r.get("thumbnail_storage_path")) if p]
    if paths:
        try:
            supabase.storage.from_("clips").remove(paths)
        except Exception as error:  # noqa: BLE001
            print(f"[backend] 클립 Storage 삭제 실패: {error}")

    supabase.table("events").update(
        {"clip_storage_path": None, "thumbnail_storage_path": None}
    ).in_("id", [r["id"] for r in rows]).execute()
    print(f"[backend] 클립 {len(rows)}건 정리")
    return len(rows)


def sweep_once(now: Any = None) -> dict[str, int]:
    moment = now or datetime.now(timezone.utc)
    return {"segments": sweep_segments(moment), "clips": sweep_clips(moment)}


async def retention_loop() -> None:
    """앱 수명 동안 도는 정리 루프. 예외로 죽으면 보관 정책이 조용히 멈추므로
    무엇이든 삼키고 다음 주기를 기다린다."""
    while True:
        try:
            await asyncio.to_thread(sweep_once)
        except Exception as error:  # noqa: BLE001
            print(f"[backend] 보관 정책 정리 실패: {error}")
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
