"""ai-worker 가 남긴 이상 구간을 위험 이벤트로 옮기고 알린다.

ai-worker 는 지금 파이프라인 그대로 anomaly_events 만 남기고 backend 를 모른다.
그래서 backend 가 주기적으로(config.ANOMALY_POLL_SEC) 새 행을 읽어 events 를
만들고 SSE·푸시로 흘린다.

- 늦게 보이는 행: created_at 은 트랜잭션 시작 시각이라, 방금 읽은 행보다 조금
  앞선 시각의 행이 나중에 보일 수 있다. 그래서 커서를 SETTLE 만큼 뒤에 두고
  겹쳐 읽는다. 이미 이벤트가 된 구간은 건너뛴다.
- 두 번 만들지 않는다: events.anomaly_event_id 에 unique 가 있고 insert 는 충돌을
  무시한다. 실제로 새로 들어간 행만 알린다.
- 재시작: 처음에는 CATCH_UP 만큼 거슬러 읽는다 — backend 가 내려가 있던 동안의
  구간도 이벤트가 된다. 다만 끝난 지 STALE_PUSH 가 지난 건 푸시하지 않는다
  (목록·배지에는 뜬다). 몇 시간 전 일로 한밤중에 알림이 몰려 울리면 안 된다.
- replicas: 1 전제는 realtime.py · retention.py 와 같다.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config
from .domain.anomaly import event_row_from_anomaly, resolve_camera
from .event_publish import publish_created_event
from .supabase_client import supabase

SETTLE = timedelta(seconds=30)
CATCH_UP = timedelta(hours=24)
STALE_PUSH = timedelta(minutes=30)
BATCH_SIZE = 200

#: 매장·카메라에 연결되지 않은 구간(도메인 연결 전 조각, 등록되지 않은 카메라).
#: 겹쳐 읽을 때마다 같은 로그를 찍지 않으려고 기억해 둔다.
_unlinked: set[str] = set()
_UNLINKED_LIMIT = 10_000


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ingest_once(cursor: datetime, now: datetime) -> tuple[datetime, int, bool]:
    """cursor 이후에 생긴 이상 구간을 이벤트로 만든다. (다음 cursor, 만든 수, 더 남았는지)."""
    sb = supabase
    if sb is None:
        return cursor, 0, False

    rows = (
        sb.table("anomaly_events").select("*")
        .gt("created_at", _iso(cursor)).order("created_at").order("id")
        .limit(BATCH_SIZE).execute().data or []
    )
    if not rows:
        return max(cursor, now - SETTLE), 0, False

    done = {
        row["anomaly_event_id"]
        for row in sb.table("events").select("anomaly_event_id")
        .in_("anomaly_event_id", [r["id"] for r in rows]).execute().data or []
    }
    pending = [r for r in rows if r["id"] not in done and r["id"] not in _unlinked]
    created = _create_events(sb, pending, now) if pending else 0

    last = _parse(rows[-1]["created_at"])
    if len(rows) == BATCH_SIZE:
        # 따라잡는 중. 겹쳐 읽으면 같은 묶음만 계속 읽으니 끝까지 당긴다 (같은 시각 행 1µs 여유).
        return last - timedelta(microseconds=1), created, True
    return max(cursor, min(last, now - SETTLE)), created, False


def _create_events(sb, anomalies: list[dict[str, Any]], now: datetime) -> int:
    videos = {
        v["id"]: v
        for v in sb.table("videos").select("id, recorded_started_at, store_uuid, camera_uuid, camera_id")
        .in_("id", sorted({a["video_id"] for a in anomalies})).execute().data or []
    }
    camera_ids = sorted({v["camera_uuid"] for v in videos.values() if v.get("camera_uuid")})
    store_ids = sorted({v["store_uuid"] for v in videos.values() if v.get("store_uuid")})
    cameras = [
        *(sb.table("cameras").select("*").in_("id", camera_ids).execute().data or [] if camera_ids else []),
        *(sb.table("cameras").select("*").in_("store_id", store_ids).execute().data or [] if store_ids else []),
    ]

    linked = []
    for anomaly in anomalies:
        video = videos.get(anomaly["video_id"])
        camera = resolve_camera(video, cameras) if video else None
        if camera is None:
            if len(_unlinked) >= _UNLINKED_LIMIT:
                _unlinked.clear()
            _unlinked.add(anomaly["id"])
            print(
                f"[backend] 매장·카메라에 연결되지 않은 이상 구간이라 이벤트를 만들지 않습니다 "
                f"anomaly_id={anomaly['id']} video_id={anomaly['video_id']}"
            )
            continue
        linked.append((anomaly, video, camera))
    if not linked:
        return 0

    retention = {
        s["id"]: s.get("clip_retention_days")
        for s in sb.table("stores").select("id, clip_retention_days")
        .in_("id", sorted({camera["store_id"] for _, _, camera in linked})).execute().data or []
    }
    inserted = sb.table("events").upsert(
        [
            event_row_from_anomaly(anomaly, video, camera, retention.get(camera["store_id"]), now)
            for anomaly, video, camera in linked
        ],
        on_conflict="anomaly_event_id",
        ignore_duplicates=True,
    ).execute().data or []

    for event in inserted:
        fresh = _parse(event["ended_at"]) >= now - STALE_PUSH
        try:
            pushed = publish_created_event(sb, event, push=fresh)
        except Exception as error:  # noqa: BLE001 - 알림 실패가 다음 이벤트 생성을 막으면 안 된다
            print(f"[backend] 이벤트 알림 실패 event_id={event['id']}: {error}")
            continue
        print(
            f"[backend] 이벤트 생성 id={event['id']} risk={event['risk']} pushed={pushed}"
            + ("" if fresh else " (끝난 지 오래된 구간이라 푸시 안 함)")
        )
    return len(inserted)


async def anomaly_ingest_loop() -> None:
    """lifespan 에서 돈다. 한 번 실패해도 멈추지 않는다."""
    cursor = datetime.now(timezone.utc) - CATCH_UP
    while True:
        has_more = False
        try:
            cursor, _, has_more = await asyncio.to_thread(ingest_once, cursor, datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - DB 가 잠깐 끊겨도 다음 주기에 다시 한다
            print(f"[backend] 이상 구간 → 이벤트 변환 실패: {error}")
        if not has_more:
            await asyncio.sleep(config.ANOMALY_POLL_SEC)
