"""푸시 기기 · 알림 설정 (요구사항 6.1 ~ 6.3 · 6.7, docs/api-contract.md 8절)."""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_user_id
from ..deps import iso, require_store_access, require_supabase
from ..domain.notify import EMERGENCY_KIND
from ..domain.risk import RISK_KINDS
from ..push import send_push

router = APIRouter(tags=["notifications"])

SENSITIVITIES = ("low", "medium", "high")

DEFAULT_QUIET_HOURS = {
    "businessHoursHighOnly": True,
    "sleepStart": None,
    "sleepEnd": None,
    "sleepEmergencyOnly": True,
    "overrideDndForHigh": True,
}


class PushDeviceBody(BaseModel):
    platform: str
    token: str = Field(min_length=1, max_length=500)


class KindSettingBody(BaseModel):
    kind: str
    enabled: bool = True
    sensitivity: str = "medium"


class QuietHoursBody(BaseModel):
    businessHoursHighOnly: bool = True
    sleepStart: Optional[str] = None
    sleepEnd: Optional[str] = None
    sleepEmergencyOnly: bool = True
    overrideDndForHigh: bool = True


class NotificationSettingsBody(BaseModel):
    kinds: list[KindSettingBody]
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


@router.get("/stores/{store_id}/notification-settings")
def get_notification_settings(store_id: str = Depends(require_store_access),
                              user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """설정이 없는 종류는 기본값(켜짐, 보통)으로 채워 돌려준다 — 설정 화면이
    빈 표를 그리지 않도록."""
    sb = require_supabase()
    rows = (
        sb.table("notification_settings").select("*")
        .eq("store_id", store_id).eq("user_id", user_id).execute().data or []
    )
    saved = {row["kind"]: row for row in rows}

    quiet_rows = (
        sb.table("notification_quiet_hours").select("*")
        .eq("store_id", store_id).eq("user_id", user_id).limit(1).execute().data or []
    )
    quiet = quiet_rows[0] if quiet_rows else None

    return {
        "kinds": [
            {
                "kind": kind,
                "enabled": saved.get(kind, {}).get("enabled", True),
                "sensitivity": saved.get(kind, {}).get("sensitivity", "medium"),
                # 쓰러짐은 끌 수 없다 — UI 가 토글을 disabled 로 그리는 근거.
                "locked": kind == EMERGENCY_KIND,
            }
            for kind in sorted(RISK_KINDS)
        ],
        "quietHours": {
            "businessHoursHighOnly": quiet["business_hours_high_only"],
            "sleepStart": quiet.get("sleep_start"),
            "sleepEnd": quiet.get("sleep_end"),
            "sleepEmergencyOnly": quiet["sleep_emergency_only"],
            "overrideDndForHigh": quiet["override_dnd_for_high"],
        } if quiet else DEFAULT_QUIET_HOURS,
    }


@router.put("/stores/{store_id}/notification-settings")
def put_notification_settings(body: NotificationSettingsBody,
                              store_id: str = Depends(require_store_access),
                              user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """2g 는 저장 버튼이 없다 — 토글을 만지면 바로 이게 불린다."""
    sb = require_supabase()
    now = iso(datetime.now(timezone.utc))

    for item in body.kinds:
        if item.kind not in RISK_KINDS:
            raise HTTPException(status_code=400, detail=f"알 수 없는 위험 종류입니다: {item.kind}")
        if item.sensitivity not in SENSITIVITIES:
            raise HTTPException(status_code=400, detail="민감도는 low / medium / high 중 하나여야 합니다")
        if item.kind == EMERGENCY_KIND and not item.enabled:
            # DB 제약(collapse_always_on)도 막지만, 여기서 막아야 사용자가
            # 읽을 수 있는 이유를 본다.
            raise HTTPException(status_code=400, detail="쓰러짐(응급) 알림은 끌 수 없습니다")

    sb.table("notification_settings").upsert(
        [
            {
                "store_id": store_id, "user_id": user_id, "kind": item.kind,
                "enabled": item.enabled, "sensitivity": item.sensitivity, "updated_at": now,
            }
            for item in body.kinds
        ],
        on_conflict="store_id,user_id,kind",
    ).execute()

    sb.table("notification_quiet_hours").upsert(
        {
            "store_id": store_id, "user_id": user_id,
            "business_hours_high_only": body.quietHours.businessHoursHighOnly,
            "sleep_start": body.quietHours.sleepStart,
            "sleep_end": body.quietHours.sleepEnd,
            "sleep_emergency_only": body.quietHours.sleepEmergencyOnly,
            "override_dnd_for_high": body.quietHours.overrideDndForHigh,
            "updated_at": now,
        },
        on_conflict="store_id,user_id",
    ).execute()

    return get_notification_settings(store_id=store_id, user_id=user_id)


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
