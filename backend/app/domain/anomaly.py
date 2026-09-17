"""이상 구간(anomaly_events) → 위험 이벤트(events) 변환 규칙.

ai-worker 는 조각 영상 안의 상대 시각(초)으로 구간을 남긴다. 화면·알림은 벽시계
시각이 필요하므로 조각의 녹화 시작 시각에 더한다. 이 모듈은 DB 를 모른다 —
읽고 쓰는 건 app/anomaly_ingest.py 몫이다.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .risk import derive_risk_from_score

#: 매장 설정(stores.clip_retention_days)이 없을 때의 클립 보관 기간 (요구사항 5.5).
DEFAULT_CLIP_RETENTION_DAYS = 30


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def resolve_camera(video: dict[str, Any], cameras: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """영상이 어느 카메라 행에 속하는지 찾는다. 못 찾으면 None.

    ingest-worker 가 camera_uuid 를 채우기 시작한 뒤의 조각은 바로 잡히고,
    그 전에 올라온 조각은 (store_uuid, 에이전트 카메라 id) 로 뒤늦게 잇는다.
    """
    candidates = tuple(cameras)
    camera_uuid = video.get("camera_uuid")
    if camera_uuid:
        found = next((c for c in candidates if c["id"] == camera_uuid), None)
        if found:
            return found

    store_uuid = video.get("store_uuid")
    agent_camera_id = video.get("camera_id")
    if store_uuid and agent_camera_id:
        return next(
            (c for c in candidates if c["store_id"] == store_uuid and c.get("agent_camera_id") == agent_camera_id),
            None,
        )
    return None


def event_row_from_anomaly(
    anomaly: dict[str, Any],
    video: dict[str, Any],
    camera: dict[str, Any],
    clip_retention_days: Optional[int],
    now: datetime,
) -> dict[str, Any]:
    """events 에 넣을 행. camera 는 resolve_camera 로 이 영상의 카메라임을 확인한 행이다."""
    recorded = _parse(video["recorded_started_at"])
    days = clip_retention_days if clip_retention_days is not None else DEFAULT_CLIP_RETENTION_DAYS
    return {
        "store_id": camera["store_id"],
        "camera_id": camera["id"],
        "anomaly_event_id": anomaly["id"],
        "video_id": video["id"],
        "started_at": _iso(recorded + timedelta(seconds=anomaly["start_time_sec"])),
        "ended_at": _iso(recorded + timedelta(seconds=anomaly["end_time_sec"])),
        "anomaly_score": anomaly.get("anomaly_score"),
        "anomaly_threshold": anomaly.get("threshold"),
        "risk": derive_risk_from_score(anomaly.get("anomaly_score"), anomaly.get("threshold")),
        "clip_storage_path": anomaly.get("clip_storage_path"),
        "thumbnail_storage_path": anomaly.get("thumbnail_storage_path"),
        "clip_expires_at": _iso(now + timedelta(days=days)),
    }
