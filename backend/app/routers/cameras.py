"""카메라 CRUD (2b 등록 위저드, 2g/2m 관리) + 연결 상태 heartbeat (2c)."""
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError

from ..auth import get_current_device, get_current_user_id
from ..deps import require_supabase
from ..schemas import (
    CameraCreateRequest,
    CameraHeartbeatRequest,
    CameraReorderRequest,
    CameraResponse,
    CamerasResponse,
    CameraUpdateRequest,
)
from .stores import get_owned_store

router = APIRouter(prefix="/stores/{store_id}/cameras", tags=["cameras"])


def _camera_to_dto(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "storeId": row["store_id"],
        "deviceId": row.get("device_id"),
        "name": row["name"],
        "locationTag": row["location_tag"],
        "quality": row["quality"],
        "sortOrder": row["sort_order"],
        "lastStatus": row.get("last_status"),
        "lastSeenAt": row.get("last_seen_at"),
    }


def _get_owned_camera(sb, store_id: str, camera_id: str, user_id: str) -> Dict[str, Any]:
    get_owned_store(sb, store_id, user_id)  # 매장 소유 확인 (404)
    try:
        camera = (
            sb.table("cameras")
            .select("*")
            .eq("id", camera_id)
            .eq("store_id", store_id)
            .is_("deleted_at", "null")
            .maybe_single()
            .execute()
            .data
        )
    except APIError as error:
        print(f"[backend] camera lookup failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if not camera:
        raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다")
    return camera


@router.get("", response_model=CamerasResponse)
def list_cameras(store_id: str, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    get_owned_store(sb, store_id, user_id)
    try:
        cams = (
            sb.table("cameras")
            .select("*")
            .eq("store_id", store_id)
            .is_("deleted_at", "null")
            .order("sort_order")
            .execute()
            .data
            or []
        )
    except APIError as error:
        print(f"[backend] /stores/:id/cameras query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    return {"cameras": [_camera_to_dto(c) for c in cams]}


@router.post("", response_model=CameraResponse)
def create_camera(
    store_id: str, body: CameraCreateRequest, user_id: str = Depends(get_current_user_id)
) -> Dict[str, Any]:
    sb = require_supabase()
    store = get_owned_store(sb, store_id, user_id)

    try:
        existing_count = (
            sb.table("cameras")
            .select("id")
            .eq("store_id", store_id)
            .is_("deleted_at", "null")
            .execute()
            .data
            or []
        )
    except APIError as error:
        print(f"[backend] camera count check failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if len(existing_count) >= store["camera_limit"]:
        raise HTTPException(
            status_code=422, detail=f"카메라는 매장당 최대 {store['camera_limit']}대까지 등록할 수 있습니다"
        )

    try:
        created = (
            sb.table("cameras")
            .insert(
                {
                    "store_id": store_id,
                    "device_id": body.deviceId,
                    "name": body.name,
                    "location_tag": body.locationTag,
                    "quality": body.quality,
                    "sort_order": len(existing_count),
                }
            )
            .execute()
            .data
        )
    except APIError as error:
        print(f"[backend] camera create failed: {error}")
        raise HTTPException(status_code=500, detail="카메라 등록 실패") from error
    if not created:
        raise HTTPException(status_code=500, detail="카메라 등록 실패")
    return {"camera": _camera_to_dto(created[0])}


# 문자 그대로의 "reorder" 경로를 {camera_id} 동적 경로보다 먼저 등록해야, FastAPI가
# "reorder"를 camera_id로 잘못 매칭하지 않는다(라우트는 등록 순서대로 매칭됨).
@router.patch("/reorder", response_model=CamerasResponse)
def reorder_cameras(
    store_id: str, body: CameraReorderRequest, user_id: str = Depends(get_current_user_id)
) -> Dict[str, Any]:
    sb = require_supabase()
    get_owned_store(sb, store_id, user_id)

    for index, camera_id in enumerate(body.cameraIds):
        try:
            sb.table("cameras").update({"sort_order": index}).eq("id", camera_id).eq(
                "store_id", store_id
            ).execute()
        except APIError as error:
            print(f"[backend] camera reorder failed: {error}")
            raise HTTPException(status_code=500, detail="순서 변경 실패") from error

    try:
        cams = (
            sb.table("cameras")
            .select("*")
            .eq("store_id", store_id)
            .is_("deleted_at", "null")
            .order("sort_order")
            .execute()
            .data
            or []
        )
    except APIError as error:
        print(f"[backend] camera reorder re-read failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    return {"cameras": [_camera_to_dto(c) for c in cams]}


@router.patch("/{camera_id}", response_model=CameraResponse)
def update_camera(
    store_id: str,
    camera_id: str,
    body: CameraUpdateRequest,
    user_id: str = Depends(get_current_user_id),
) -> Dict[str, Any]:
    sb = require_supabase()
    _get_owned_camera(sb, store_id, camera_id, user_id)

    field_map = {"name": "name", "locationTag": "location_tag", "quality": "quality"}
    changes = {col: value for field, col in field_map.items() if (value := getattr(body, field)) is not None}
    if not changes:
        raise HTTPException(status_code=422, detail="변경할 필드가 없습니다")

    try:
        updated = sb.table("cameras").update(changes).eq("id", camera_id).execute().data
    except APIError as error:
        print(f"[backend] camera update failed: {error}")
        raise HTTPException(status_code=500, detail="수정 실패") from error
    if not updated:
        raise HTTPException(status_code=500, detail="수정 실패")
    return {"camera": _camera_to_dto(updated[0])}


@router.delete("/{camera_id}", status_code=204)
def delete_camera(store_id: str, camera_id: str, user_id: str = Depends(get_current_user_id)) -> None:
    sb = require_supabase()
    _get_owned_camera(sb, store_id, camera_id, user_id)
    try:
        sb.table("cameras").update({"deleted_at": datetime.now(timezone.utc).isoformat()}).eq(
            "id", camera_id
        ).execute()
    except APIError as error:
        print(f"[backend] camera delete failed: {error}")
        raise HTTPException(status_code=500, detail="삭제 실패") from error


# PC 앱이 device 토큰으로 호출 — 로그인한 사람이 아니라 PC 소프트웨어 자체가 보내는
# 요청이라 get_current_device 를 쓴다(app/auth.py).
@router.post("/{camera_id}/heartbeat", status_code=204)
def camera_heartbeat(
    store_id: str,
    camera_id: str,
    body: CameraHeartbeatRequest,
    device: Dict[str, Any] = Depends(get_current_device),
) -> None:
    if device["store_id"] != store_id:
        raise HTTPException(status_code=403, detail="이 디바이스의 매장이 아닙니다")

    sb = require_supabase()
    now = datetime.now(timezone.utc).isoformat()
    try:
        updated = (
            sb.table("cameras")
            .update({"last_status": body.status, "last_seen_at": now})
            .eq("id", camera_id)
            .eq("store_id", store_id)
            .is_("deleted_at", "null")
            .execute()
            .data
        )
        if not updated:
            raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다")
        sb.table("camera_status_events").insert(
            {"camera_id": camera_id, "status": body.status, "occurred_at": now}
        ).execute()
        sb.table("devices").update({"last_seen_at": now}).eq("id", device["id"]).execute()
    except APIError as error:
        print(f"[backend] camera heartbeat failed: {error}")
        raise HTTPException(status_code=500, detail="갱신 실패") from error
