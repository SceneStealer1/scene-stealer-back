from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import config
from .supabase_client import supabase, to_public_url


def to_absolute_time(recorded_started_at: str, offset_sec: float) -> str:
    """videos.recorded_started_at + 상대 오프셋(초) -> 실제 촬영 절대시각 (ISO 8601, Z)."""
    base = datetime.fromisoformat(recorded_started_at.replace("Z", "+00:00"))
    result = base.astimezone(timezone.utc) + timedelta(seconds=offset_sec)
    return result.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def signed_url(bucket: str, path: Optional[str]) -> Optional[str]:
    if supabase is None or not path:
        return None
    try:
        data = supabase.storage.from_(bucket).create_signed_url(path, config.SIGNED_URL_TTL_SEC)
        # supabase-py 버전에 따라 키 표기가 다를 수 있어(signedURL/signedUrl) 둘 다 받는다.
        url = data.get("signedURL") or data.get("signedUrl")
        return to_public_url(url) if url else None
    except Exception as error:  # noqa: BLE001 - 외부 API 실패는 로깅만 하고 None으로 흡수
        print(f"[backend] signed url failed bucket={bucket} path={path}: {error}")
        return None


def to_clip_dto(event: Dict[str, Any], video: Dict[str, Any]) -> Dict[str, Any]:
    """anomaly_events 행 하나를 프론트가 바로 쓸 수 있는 모양으로 변환한다 (signed URL 포함).

    risk_type(위험 종류 분류)/ai_description(자연어 설명)은 아직 없다 — docs/
    ux-backend-design.md 5장 질문 1·2, 이번 작업 범위에서 뺐다.
    """
    return {
        "id": event["id"],
        "videoId": event["video_id"],
        "userId": event["user_id"],
        "storeId": video.get("store_id"),
        "cameraLocation": video.get("camera_location"),
        "startAt": to_absolute_time(video["recorded_started_at"], event["start_time_sec"]),
        "endAt": to_absolute_time(video["recorded_started_at"], event["end_time_sec"]),
        "startTimeSec": event["start_time_sec"],
        "endTimeSec": event["end_time_sec"],
        "anomalyScore": event["anomaly_score"],
        "threshold": event["threshold"],
        "riskLevel": event.get("risk_level"),
        "status": event.get("status", "unconfirmed"),
        "note": event.get("note"),
        "reportedToPolice": event.get("reported_to_police", False),
        "confirmedBy": event.get("confirmed_by"),
        "confirmedAt": event.get("confirmed_at"),
        "clipUrl": signed_url("clips", event.get("clip_storage_path")),
        "thumbnailUrl": signed_url("clips", event.get("thumbnail_storage_path")),
        "createdAt": event["created_at"],
    }
