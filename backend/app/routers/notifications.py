"""푸시 기기 · 알림 설정 (요구사항 6.1 ~ 6.3 · 6.7, docs/api-contract.md 8절)."""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_user_id
from ..deps import iso, require_store_access, require_supabase
from ..domain.risk import RISK_LEVELS
from ..event_publish import DEFAULT_MIN_RISK, DEFAULT_QUIET_HOURS
from ..push import send_push

router = APIRouter(tags=["notifications"])


class PushDeviceBody(BaseModel):
    platform: str
    token: str = Field(min_length=1, max_length=500)


class QuietHoursBody(BaseModel):
    businessHoursHighOnly: bool = True
    sleepStart: Optional[str] = None
    sleepEnd: Optional[str] = None
    sleepHighOnly: bool = True
    overrideDndForHigh: bool = True


class NotificationSettingsBody(BaseModel):
    minRisk: str = DEFAULT_MIN_RISK
    quietHours: QuietHoursBody


@router.post("/push/devices", status_code=201)
def register_push_device(body: PushDeviceBody,
                         user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    sb = require_supabase()
    if body.platform not in ("ios", "android"):
        raise HTTPException(status_code=400, detail="platform 은 ios / android 중 하나여야 합니다")

    now = iso(datetime.now(timezone.utc))
    existing = (
        sb.table("push_devices").select("id").eq("user_id", user_id)
        .eq("token", body.token).limit(1).execute().data or []
    )
    if existing:
        # 같은 토큰 재등록은 앱을 다시 켤 때마다 일어난다. 실패가 아니다.
        sb.table("push_devices").update({"last_seen_at": now, "platform": body.platform}) \
            .eq("id", existing[0]["id"]).execute()
        return {"id": existing[0]["id"], "platform": body.platform}

    created = sb.table("push_devices").insert(
        {"user_id": user_id, "platform": body.platform, "token": body.token, "last_seen_at": now}
    ).execute().data
    if not created:
        raise HTTPException(status_code=500, detail="푸시 기기 등록에 실패했습니다")
    return {"id": created[0]["id"], "platform": body.platform}


@router.delete("/push/devices", status_code=204)
def unregister_push_device(body: PushDeviceBody,
                           user_id: str = Depends(get_current_user_id)) -> None:
    sb = require_supabase()
    sb.table("push_devices").delete().eq("user_id", user_id).eq("token", body.token).execute()


def _hhmm(value: Optional[str]) -> Optional[str]:
    """DB time 은 '09:00:00' 으로 온다. 계약은 'HH:MM' 이다."""
    return value[:5] if value else None


def to_settings_dto(row: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not row:
        return {
            "minRisk": DEFAULT_MIN_RISK,
            "quietHours": {
                "businessHoursHighOnly": DEFAULT_QUIET_HOURS.business_hours_high_only,
                "sleepStart": None,
                "sleepEnd": None,
                "sleepHighOnly": DEFAULT_QUIET_HOURS.sleep_high_only,
                "overrideDndForHigh": DEFAULT_QUIET_HOURS.override_dnd_for_high,
            },
        }
    return {
        "minRisk": row["min_risk"],
        "quietHours": {
            "businessHoursHighOnly": row["business_hours_high_only"],
            "sleepStart": _hhmm(row.get("sleep_start")),
            "sleepEnd": _hhmm(row.get("sleep_end")),
            "sleepHighOnly": row["sleep_high_only"],
            "overrideDndForHigh": row["override_dnd_for_high"],
        },
    }


@router.get("/stores/{store_id}/notification-settings")
def get_notification_settings(store_id: str = Depends(require_store_access),
                              user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """저장한 적이 없으면 기본값을 돌려준다 — 설정 화면이 빈 칸을 그리지 않도록."""
    sb = require_supabase()
    rows = (
        sb.table("notification_settings").select("*")
        .eq("store_id", store_id).eq("user_id", user_id).limit(1).execute().data or []
    )
    return to_settings_dto(rows[0] if rows else None)


@router.put("/stores/{store_id}/notification-settings")
def put_notification_settings(body: NotificationSettingsBody,
                              store_id: str = Depends(require_store_access),
                              user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """2g 는 저장 버튼이 없다 — 값을 바꾸면 바로 이게 불린다."""
    if body.minRisk not in RISK_LEVELS:
        raise HTTPException(status_code=400, detail="minRisk 는 low / medium / high 중 하나여야 합니다")

    sb = require_supabase()
    saved = sb.table("notification_settings").upsert(
        {
            "store_id": store_id, "user_id": user_id,
            "min_risk": body.minRisk,
            "business_hours_high_only": body.quietHours.businessHoursHighOnly,
            "sleep_start": body.quietHours.sleepStart,
            "sleep_end": body.quietHours.sleepEnd,
            "sleep_high_only": body.quietHours.sleepHighOnly,
            "override_dnd_for_high": body.quietHours.overrideDndForHigh,
            "updated_at": iso(datetime.now(timezone.utc)),
        },
        on_conflict="store_id,user_id",
    ).execute().data or []
    return to_settings_dto(saved[0] if saved else None)


@router.post("/stores/{store_id}/notification-settings/test")
def send_test_notification(store_id: str = Depends(require_store_access),
                           user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """2g / 2m 의 '지금 보내기' (요구사항 6.7).

    설정 필터를 일부러 건너뛴다 — 테스트는 "설정이 맞는지"가 아니라 "기기까지
    닿는지"를 보는 것이다.
    """
    sb = require_supabase()
    stores = sb.table("stores").select("name").eq("id", store_id).limit(1).execute().data or []
    store_name = stores[0]["name"] if stores else "매장"

    tokens = (
        sb.table("push_devices").select("platform, token").eq("user_id", user_id).execute().data or []
    )
    if not tokens:
        raise HTTPException(status_code=400, detail="등록된 푸시 기기가 없습니다")

    sent = send_push(
        tokens=tokens,
        title=f"{store_name} · 테스트 알림",
        body="알림이 정상적으로 도착했습니다.",
        data={"type": "test", "storeId": store_id},
        sound=True,
    )
    return {"sent": sent, "deviceCount": len(tokens)}
