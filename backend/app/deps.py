"""라우터들이 공유하는 의존성 — Supabase 핸들, 매장 권한, 공통 헬퍼.

권한 판정은 전부 여기를 지난다. backend 는 service role 키로 RLS 를 우회해서
DB 에 접근하므로(supabase_client.py), "본인 매장만 보인다"를 지키는 책임이
쿼리가 아니라 이 모듈에 있다.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, HTTPException, Path

from . import config
from .auth import get_current_user_id
from .supabase_client import supabase, to_public_url

SUPABASE_UNAVAILABLE_MSG = "Supabase 가 설정되지 않았습니다"


def require_supabase():
    if supabase is None:
        raise HTTPException(status_code=503, detail=SUPABASE_UNAVAILABLE_MSG)
    return supabase


def clamp_limit(raw: Optional[int], fallback: int, maximum: int) -> int:
    if raw is None or raw <= 0:
        return fallback
    return min(raw, maximum)


def require_store_access(
    store_id: str = Path(..., description="매장 uuid"),
    user_id: str = Depends(get_current_user_id),
) -> str:
    """이 매장의 구성원인지 확인하고 store_id 를 돌려준다.

    남의 매장은 403 이 아니라 **404** 다 — 403 은 "그런 매장이 있긴 하다"를
    알려주는 정보 노출이다 (docs/api-contract.md 1.3).
    """
    sb = require_supabase()
    rows = (
        sb.table("store_members")
        .select("role")
        .eq("store_id", store_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
    return store_id


def store_ids_for_user(user_id: str) -> list[str]:
    sb = require_supabase()
    rows = sb.table("store_members").select("store_id").eq("user_id", user_id).execute().data or []
    return [r["store_id"] for r in rows]


def require_event_access(event_id: str, user_id: str) -> dict[str, Any]:
    """이벤트를 읽고, 그 매장의 구성원이 아니면 404."""
    sb = require_supabase()
    rows = sb.table("events").select("*").eq("id", event_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다")
    event = rows[0]
    require_store_access(store_id=event["store_id"], user_id=user_id)
    return event


def signed_url(bucket: str, path: Optional[str]) -> Optional[str]:
    """Storage signed URL. 외부 호출 실패는 로그만 남기고 None 으로 흡수한다 —
    URL 하나 때문에 목록 전체가 500 이 되면 안 된다."""
    if supabase is None or not path:
        return None
    try:
        data = supabase.storage.from_(bucket).create_signed_url(path, config.SIGNED_URL_TTL_SEC)
        # supabase-py 버전에 따라 키 표기가 다르다 (signedURL / signedUrl).
        url = data.get("signedURL") or data.get("signedUrl")
        return to_public_url(url) if url else None
    except Exception as error:  # noqa: BLE001
        print(f"[backend] signed url failed bucket={bucket} path={path}: {error}")
        return None


def signed_url_expires_at() -> str:
    return iso(datetime.now(timezone.utc) + timedelta(seconds=config.SIGNED_URL_TTL_SEC))


def iso(dt: Optional[datetime]) -> Optional[str]:
    """ISO 8601 UTC, 밀리초, Z (docs/api-contract.md 1.4)."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"시각 형식이 올바르지 않습니다: {value}") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
