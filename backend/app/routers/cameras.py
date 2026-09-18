"""카메라 · 매장 감시 상태 (요구사항 2.1 ~ 2.5, docs/api-contract.md 4.3 · 4.4)."""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_user_id
from ..deps import iso, require_store_access, require_supabase
from .stores import DEVICE_ONLINE_WINDOW_SEC, is_online

router = APIRouter(tags=["cameras"])

#: 매장당 활성 카메라 상한. PC 한 대가 동시에 물 수 있는 실질 한계다.
MAX_CAMERAS_PER_STORE = 8

LOCATION_TAGS = ("checkout", "entrance", "shelf", "dining", "storage", "other")


class CameraCreate(BaseModel):
    agentCameraId: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    locationTag: Optional[str] = None
    sortOrder: int = 0
    streamProfile: Optional[str] = None


class CameraUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    locationTag: Optional[str] = None
    sortOrder: Optional[int] = None


def to_camera_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "storeId": row["store_id"],
        "agentCameraId": row["agent_camera_id"],
        "name": row["name"],
        "locationTag": row.get("location_tag"),
        "sortOrder": row["sort_order"],
        "streamProfile": row.get("stream_profile"),
        "state": row["runtime_state"],
        "lastFrameAt": row.get("last_frame_at"),
        "lastSegmentAt": row.get("last_segment_at"),
    }


def _validate_location_tag(tag: Optional[str]) -> None:
    if tag is not None and tag not in LOCATION_TAGS:
        raise HTTPException(
            status_code=400,
            detail=f"위치 태그는 {', '.join(LOCATION_TAGS)} 중 하나여야 합니다",
        )


@router.get("/stores/{store_id}/cameras")
def list_cameras(store_id: str = Depends(require_store_access)) -> dict[str, Any]:
    sb = require_supabase()
    rows = (
        sb.table("cameras").select("*").eq("store_id", store_id)
        .is_("deleted_at", "null").order("sort_order").execute().data or []
    )
    return {"cameras": [to_camera_dto(row) for row in rows]}


@router.post("/stores/{store_id}/cameras", status_code=201)
def create_camera(body: CameraCreate, store_id: str = Depends(require_store_access)) -> dict[str, Any]:
    """카메라를 등록한다.

    같은 agentCameraId 로 다시 부르면 409 가 아니라 **기존 행을 갱신해서
    돌려준다** — PC 를 재설치하면 같은 ONVIF 식별자로 다시 올라오는데, 그때마다
    실패하면 사용자는 뭘 해야 할지 알 수 없다.
    """
    sb = require_supabase()
    _validate_location_tag(body.locationTag)

    existing = (
        sb.table("cameras").select("*").eq("store_id", store_id)
        .eq("agent_camera_id", body.agentCameraId).limit(1).execute().data or []
    )

    payload = {
        "store_id": store_id,
        "agent_camera_id": body.agentCameraId,
        "name": body.name,
        "location_tag": body.locationTag,
        "sort_order": body.sortOrder,
        "stream_profile": body.streamProfile,
        "deleted_at": None,   # 지웠던 카메라를 다시 붙이는 경우 되살린다
    }

    if existing:
        sb.table("cameras").update(payload).eq("id", existing[0]["id"]).execute()
        return {"camera": to_camera_dto({**existing[0], **payload})}

    active = (
        sb.table("cameras").select("id").eq("store_id", store_id)
        .is_("deleted_at", "null").execute().data or []
    )
    if len(active) >= MAX_CAMERAS_PER_STORE:
        raise HTTPException(
            status_code=400,
            detail=f"카메라는 매장당 {MAX_CAMERAS_PER_STORE}대까지 등록할 수 있습니다",
        )

    created = sb.table("cameras").insert(payload).execute().data
    if not created:
        raise HTTPException(status_code=500, detail="카메라 등록에 실패했습니다")
    return {"camera": to_camera_dto(created[0])}


def _load_camera_for_user(camera_id: str, user_id: str) -> dict[str, Any]:
    sb = require_supabase()
    rows = sb.table("cameras").select("*").eq("id", camera_id).limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다")
    require_store_access(store_id=rows[0]["store_id"], user_id=user_id)
    return rows[0]


@router.patch("/cameras/{camera_id}")
def update_camera(camera_id: str, body: CameraUpdate,
                  user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    sb = require_supabase()
    camera = _load_camera_for_user(camera_id, user_id)
    _validate_location_tag(body.locationTag)

    patch = {
        column: value
        for column, value in (
            ("name", body.name),
            ("location_tag", body.locationTag),
            ("sort_order", body.sortOrder),
        )
        if value is not None
    }
    if not patch:
        raise HTTPException(status_code=400, detail="바꿀 항목이 없습니다")

    sb.table("cameras").update(patch).eq("id", camera_id).execute()
    return {"camera": to_camera_dto({**camera, **patch})}


@router.delete("/cameras/{camera_id}", status_code=204)
def delete_camera(camera_id: str, user_id: str = Depends(get_current_user_id)) -> None:
    """soft delete. 지난 이벤트가 이 카메라를 참조하고 있어서 물리 삭제하면
    기록이 끊긴다."""
    sb = require_supabase()
    _load_camera_for_user(camera_id, user_id)
    sb.table("cameras").update({"deleted_at": iso(datetime.now(timezone.utc))}).eq("id", camera_id).execute()


@router.get("/stores/{store_id}/monitoring")
def get_monitoring(store_id: str = Depends(require_store_access)) -> dict[str, Any]:
    """2c 상단 '감시 중 4/5대', 모바일 2k 헤더, 2m 'PC 꺼짐 2시간'의 근거."""
    sb = require_supabase()
    now = datetime.now(timezone.utc)

    devices = (
        sb.table("devices").select("*").eq("store_id", store_id)
        .is_("revoked_at", "null").order("created_at", desc=True).limit(1).execute().data or []
    )
    device = devices[0] if devices else None

    store_rows = sb.table("stores").select("segment_seconds").eq("id", store_id).limit(1).execute().data or []
    segment_seconds = store_rows[0]["segment_seconds"] if store_rows else 60

    camera_rows = (
        sb.table("cameras").select("*").eq("store_id", store_id)
        .is_("deleted_at", "null").order("sort_order").execute().data or []
    )

    cameras = []
    for row in camera_rows:
        dto = to_camera_dto(row)
        # 모바일이 "창고 끊김 13분"을 말하려면 얼마나 끊겼는지가 필요하다.
        disconnected_for = None
        if row["runtime_state"] in ("disconnected", "auth_failed", "reconnecting"):
            since = row.get("state_updated_at") or row.get("last_frame_at")
            if since:
                parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
                disconnected_for = int((now - parsed).total_seconds())
        cameras.append({**dto, "disconnectedForSec": disconnected_for})

    # '마지막 AI 분석'은 ai-worker 가 조각 분석을 끝낸 시각이다. 위험 이벤트가 생긴 시각으로
    # 재면 이상이 없는 동안(대부분의 시간) 분석이 멈춘 것처럼 보인다.
    last_analyzed = (
        sb.table("videos").select("processed_at").eq("store_uuid", store_id)
        .not_.is_("processed_at", "null").order("processed_at", desc=True).limit(1).execute().data or []
    )

    monitoring_count = sum(1 for c in cameras if c["state"] == "connected")

    return {
        "device": {
            "online": is_online(device.get("last_heartbeat_at")) if device else False,
            "lastHeartbeatAt": device.get("last_heartbeat_at") if device else None,
            "agentVersion": device.get("agent_version") if device else None,
            "spoolBytes": device.get("spool_bytes") if device else None,
            "uploadedBytesToday": device.get("uploaded_bytes_today") if device else None,
            "onlineWindowSec": DEVICE_ONLINE_WINDOW_SEC,
        } if device else None,
        "segmentSeconds": segment_seconds,
        "cameras": cameras,
        "lastAnalyzedAt": last_analyzed[0]["processed_at"] if last_analyzed else None,
        "monitoringCount": monitoring_count,
        "totalCount": len(cameras),
    }
