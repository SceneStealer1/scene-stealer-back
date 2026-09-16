"""매장 · PC 기기 (요구사항 1.2 · 1.3 · 1.4, docs/api-contract.md 3절)."""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_user_id
from ..deps import iso, require_store_access, require_supabase, store_ids_for_user
from ..device_token import issue_token

router = APIRouter(tags=["stores"])

# 하트비트가 이 시간 안에 왔으면 PC 가 켜져 있는 것으로 본다. 권장 주기 30초의
# 네 배 — 한 번 걸렀다고 "꺼짐"이라고 말하면 화면이 깜빡인다.
DEVICE_ONLINE_WINDOW_SEC = 120


def is_online(last_heartbeat_at: Optional[str]) -> bool:
    if not last_heartbeat_at:
        return False
    seen = datetime.fromisoformat(last_heartbeat_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - seen <= timedelta(seconds=DEVICE_ONLINE_WINDOW_SEC)


def to_device_dto(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": row["id"],
        "deviceId": row["device_id"],
        "label": row.get("label"),
        "agentVersion": row.get("agent_version"),
        "online": is_online(row.get("last_heartbeat_at")),
        "lastHeartbeatAt": row.get("last_heartbeat_at"),
        "spoolBytes": row.get("spool_bytes"),
        "uploadedBytesToday": row.get("uploaded_bytes_today"),
    }


def to_store_dto(row: dict[str, Any], *, camera_count: int, device: Optional[dict[str, Any]],
                 unconfirmed_count: int) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "address": row.get("address"),
        "opensAt": row.get("opens_at"),
        "closesAt": row.get("closes_at"),
        "segmentSeconds": row["segment_seconds"],
        "clipRetentionDays": row["clip_retention_days"],
        "cameraCount": camera_count,
        "device": to_device_dto(device),
        "unconfirmedCount": unconfirmed_count,
    }


class StoreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address: Optional[str] = None
    opensAt: Optional[str] = None
    closesAt: Optional[str] = None


class StoreUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    address: Optional[str] = None
    opensAt: Optional[str] = None
    closesAt: Optional[str] = None
    segmentSeconds: Optional[int] = None
    clipRetentionDays: Optional[int] = None


class DeviceRegister(BaseModel):
    deviceId: str = Field(min_length=1, max_length=200)
    label: Optional[str] = None
    agentVersion: Optional[str] = None
    #: 매장에 이미 활성 PC 가 있을 때만 의미가 있다. 없으면 409 로 되묻는다.
    replaceExisting: bool = False


def _counts_for_stores(sb, store_ids: list[str]) -> tuple[dict[str, int], dict[str, int], dict[str, dict]]:
    """목록 화면 하나를 그리는 데 필요한 집계를 한 번에 모은다."""
    if not store_ids:
        return {}, {}, {}

    cameras = (
        sb.table("cameras").select("store_id").in_("store_id", store_ids)
        .is_("deleted_at", "null").execute().data or []
    )
    camera_counts: dict[str, int] = {}
    for row in cameras:
        camera_counts[row["store_id"]] = camera_counts.get(row["store_id"], 0) + 1

    unconfirmed = (
        sb.table("events").select("store_id").in_("store_id", store_ids)
        .eq("state", "unconfirmed").execute().data or []
    )
    unconfirmed_counts: dict[str, int] = {}
    for row in unconfirmed:
        unconfirmed_counts[row["store_id"]] = unconfirmed_counts.get(row["store_id"], 0) + 1

    devices = (
        sb.table("devices").select("*").in_("store_id", store_ids)
        .is_("revoked_at", "null").execute().data or []
    )
    device_by_store = {row["store_id"]: row for row in devices}

    return camera_counts, unconfirmed_counts, device_by_store


@router.get("/stores")
def list_stores(user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    sb = require_supabase()
    store_ids = store_ids_for_user(user_id)
    if not store_ids:
        return {"stores": []}

    rows = sb.table("stores").select("*").in_("id", store_ids).order("created_at").execute().data or []
    camera_counts, unconfirmed_counts, device_by_store = _counts_for_stores(sb, store_ids)

    return {
        "stores": [
            to_store_dto(
                row,
                camera_count=camera_counts.get(row["id"], 0),
                device=device_by_store.get(row["id"]),
                unconfirmed_count=unconfirmed_counts.get(row["id"], 0),
            )
            for row in rows
        ]
    }


@router.post("/stores", status_code=201)
def create_store(body: StoreCreate, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    sb = require_supabase()
    created = (
        sb.table("stores")
        .insert({
            "name": body.name,
            "address": body.address,
            "opens_at": body.opensAt,
            "closes_at": body.closesAt,
        })
        .execute()
        .data
    )
    if not created:
        raise HTTPException(status_code=500, detail="매장 생성에 실패했습니다")
    store = created[0]

    # 만든 사람이 주인이다. 이 행이 없으면 방금 만든 매장이 본인에게도 안 보인다.
    sb.table("store_members").insert(
        {"store_id": store["id"], "user_id": user_id, "role": "owner"}
    ).execute()

    return {"store": to_store_dto(store, camera_count=0, device=None, unconfirmed_count=0)}


@router.get("/stores/{store_id}")
def get_store(store_id: str = Depends(require_store_access)) -> dict[str, Any]:
    sb = require_supabase()
    rows = sb.table("stores").select("*").eq("id", store_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
    camera_counts, unconfirmed_counts, device_by_store = _counts_for_stores(sb, [store_id])
    return {
        "store": to_store_dto(
            rows[0],
            camera_count=camera_counts.get(store_id, 0),
            device=device_by_store.get(store_id),
            unconfirmed_count=unconfirmed_counts.get(store_id, 0),
        )
    }


@router.patch("/stores/{store_id}")
def update_store(body: StoreUpdate, store_id: str = Depends(require_store_access)) -> dict[str, Any]:
    sb = require_supabase()

    if body.segmentSeconds is not None and body.segmentSeconds not in (30, 60, 300):
        # UI 에서는 '알림 빠르기'다. 값이 셋뿐인 이유는 조각 경계를 벽시계에
        # 정렬해야 카메라 간 동시각 재생이 성립하기 때문이다.
        raise HTTPException(status_code=400, detail="알림 빠르기는 30초 / 1분 / 5분 중 하나여야 합니다")
    if body.clipRetentionDays is not None and not 1 <= body.clipRetentionDays <= 365:
        raise HTTPException(status_code=400, detail="클립 보관 기간은 1~365일이어야 합니다")

    patch = {
        column: value
        for column, value in (
            ("name", body.name),
            ("address", body.address),
            ("opens_at", body.opensAt),
            ("closes_at", body.closesAt),
            ("segment_seconds", body.segmentSeconds),
            ("clip_retention_days", body.clipRetentionDays),
        )
        if value is not None
    }
    if not patch:
        raise HTTPException(status_code=400, detail="바꿀 항목이 없습니다")
    patch["updated_at"] = iso(datetime.now(timezone.utc))

    sb.table("stores").update(patch).eq("id", store_id).execute()
    return get_store(store_id=store_id)


# ---------------------------------------------------------------------------
# PC 기기 (1.4)
# ---------------------------------------------------------------------------


@router.get("/stores/{store_id}/devices")
def list_devices(store_id: str = Depends(require_store_access)) -> dict[str, Any]:
    sb = require_supabase()
    rows = (
        sb.table("devices").select("*").eq("store_id", store_id)
        .order("created_at", desc=True).execute().data or []
    )
    return {"devices": [to_device_dto(row) for row in rows]}


@router.post("/stores/{store_id}/devices", status_code=201)
def register_device(body: DeviceRegister, store_id: str = Depends(require_store_access)) -> dict[str, Any]:
    """PC 를 매장에 연결하고 기기 토큰을 발급한다.

    매장당 PC 1대가 전제다. 이미 있으면 409 로 되묻고, replaceExisting 이 오면
    기존 기기를 폐기한 뒤 새로 발급한다 — 2a 의 "기존 PC 가 있습니다" 확인 흐름.
    """
    sb = require_supabase()
    now = iso(datetime.now(timezone.utc))

    active = (
        sb.table("devices").select("*").eq("store_id", store_id)
        .is_("revoked_at", "null").execute().data or []
    )

    # 같은 PC 의 재설치는 교체가 아니라 토큰 재발급이다.
    same_device = next((d for d in active if d["device_id"] == body.deviceId), None)
    others = [d for d in active if d["device_id"] != body.deviceId]

    if others and not body.replaceExisting:
        raise HTTPException(
            status_code=409,
            detail="이 매장에는 이미 연결된 PC 가 있습니다. 교체하려면 replaceExisting 을 보내세요",
        )

    replaced_device_id = None
    if others and body.replaceExisting:
        for device in others:
            sb.table("devices").update({"revoked_at": now}).eq("id", device["id"]).execute()
        replaced_device_id = others[0]["id"]

    token, token_hash = issue_token()
    payload = {
        "store_id": store_id,
        "device_id": body.deviceId,
        "token_hash": token_hash,
        "label": body.label,
        "agent_version": body.agentVersion,
        "revoked_at": None,
    }

    if same_device:
        sb.table("devices").update(payload).eq("id", same_device["id"]).execute()
        device_row = {**same_device, **payload}
    else:
        created = sb.table("devices").insert(payload).execute().data
        if not created:
            raise HTTPException(status_code=500, detail="기기 등록에 실패했습니다")
        device_row = created[0]

    return {
        **to_device_dto(device_row),
        # 평문은 여기서만 나간다. 서버는 해시만 들고 있다.
        "deviceToken": token,
        "replacedDeviceId": replaced_device_id,
    }


@router.delete("/devices/{device_id}", status_code=204)
def revoke_device(device_id: str, user_id: str = Depends(get_current_user_id)) -> None:
    sb = require_supabase()
    rows = sb.table("devices").select("store_id").eq("id", device_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="기기를 찾을 수 없습니다")
    require_store_access(store_id=rows[0]["store_id"], user_id=user_id)

    sb.table("devices").update({"revoked_at": iso(datetime.now(timezone.utc))}).eq("id", device_id).execute()
