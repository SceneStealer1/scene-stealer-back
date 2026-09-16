"""매장 CRUD/상태 (2a 매장 선택, 2c/2k 상태 요약, 2g/2m 설정 일부).

위험 종류별 알림 설정(store_alert_rules)은 만들지 않았다 — risk_type 분류가 아직
없어서(docs/ux-backend-design.md 5장 질문 1) 저장해봐야 쓸 데가 없다.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError

from ..auth import get_current_user_id
from ..deps import require_supabase
from ..schemas import StoreCreateRequest, StoreResponse, StoresResponse, StoreStatus, StoreUpdateRequest

router = APIRouter(prefix="/stores", tags=["stores"])

# PC/카메라 heartbeat 주기(설계상 30초 가정, docs 2장)보다 넉넉하게 잡은 "연결됨" 판정 창.
DEVICE_ONLINE_WINDOW_SEC = 120


def _store_to_detail(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "ownerUserId": row["owner_user_id"],
        "name": row["name"],
        "address": row.get("address"),
        "operatingHoursStart": row.get("operating_hours_start"),
        "operatingHoursEnd": row.get("operating_hours_end"),
        "quietHoursStart": row.get("quiet_hours_start"),
        "quietHoursEnd": row.get("quiet_hours_end"),
        "monitoringPaused": row["monitoring_paused"],
        "segmentIntervalSec": row["segment_interval_sec"],
        "clipRetentionDays": row["clip_retention_days"],
        "segmentRetentionDays": row["segment_retention_days"],
        "cameraLimit": row["camera_limit"],
        "pcPopupEnabled": row["pc_popup_enabled"],
        "mobilePushEnabled": row["mobile_push_enabled"],
        "createdAt": row["created_at"],
    }


def get_owned_store(sb, store_id: str, user_id: str) -> Dict[str, Any]:
    """다른 라우터(cameras/devices)에서도 "이 매장이 내 것인지" 확인할 때 재사용한다."""
    try:
        store = (
            sb.table("stores")
            .select("*")
            .eq("id", store_id)
            .eq("owner_user_id", user_id)
            .maybe_single()
            .execute()
            .data
        )
    except APIError as error:
        print(f"[backend] store lookup failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다")
    return store


def _is_recent(ts: Optional[str]) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() <= DEVICE_ONLINE_WINDOW_SEC


@router.get("", response_model=StoresResponse)
def list_stores(user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    try:
        stores = (
            sb.table("stores").select("*").eq("owner_user_id", user_id).order("created_at").execute().data
            or []
        )
    except APIError as error:
        print(f"[backend] /stores query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error

    store_ids = [s["id"] for s in stores]
    cameras_by_store: Dict[str, List[Dict[str, Any]]] = {}
    devices_by_store: Dict[str, List[Dict[str, Any]]] = {}
    if store_ids:
        try:
            cams = (
                sb.table("cameras")
                .select("store_id, last_status")
                .in_("store_id", store_ids)
                .is_("deleted_at", "null")
                .execute()
                .data
                or []
            )
            for c in cams:
                cameras_by_store.setdefault(c["store_id"], []).append(c)
        except APIError as error:
            print(f"[backend] /stores camera lookup failed: {error}")
        try:
            devs = (
                sb.table("devices")
                .select("store_id, label, last_seen_at, revoked_at")
                .in_("store_id", store_ids)
                .is_("revoked_at", "null")
                .execute()
                .data
                or []
            )
            for d in devs:
                devices_by_store.setdefault(d["store_id"], []).append(d)
        except APIError as error:
            print(f"[backend] /stores device lookup failed: {error}")

    result = []
    for s in stores:
        cams = cameras_by_store.get(s["id"], [])
        devs = devices_by_store.get(s["id"], [])
        device = devs[0] if devs else None
        device_connected = bool(device and _is_recent(device.get("last_seen_at")))
        has_issue = (not device_connected) or any(c.get("last_status") != "connected" for c in cams)
        result.append(
            {
                "id": s["id"],
                "name": s["name"],
                "address": s.get("address"),
                "cameraCount": len(cams),
                "deviceLabel": device.get("label") if device else None,
                "deviceConnected": device_connected,
                "hasIssue": has_issue,
            }
        )
    return {"stores": result}


@router.post("", response_model=StoreResponse)
def create_store(body: StoreCreateRequest, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    try:
        created = (
            sb.table("stores")
            .insert({"owner_user_id": user_id, "name": body.name, "address": body.address})
            .execute()
            .data
        )
    except APIError as error:
        print(f"[backend] /stores create failed: {error}")
        raise HTTPException(status_code=500, detail="매장 생성 실패") from error
    if not created:
        raise HTTPException(status_code=500, detail="매장 생성 실패")
    return {"store": _store_to_detail(created[0])}


@router.get("/{store_id}", response_model=StoreResponse)
def get_store(store_id: str, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    return {"store": _store_to_detail(get_owned_store(sb, store_id, user_id))}


@router.patch("/{store_id}", response_model=StoreResponse)
def update_store(
    store_id: str, body: StoreUpdateRequest, user_id: str = Depends(get_current_user_id)
) -> Dict[str, Any]:
    sb = require_supabase()
    get_owned_store(sb, store_id, user_id)  # 존재/소유 확인 (404 처리 포함)

    field_map = {
        "name": "name",
        "address": "address",
        "operatingHoursStart": "operating_hours_start",
        "operatingHoursEnd": "operating_hours_end",
        "quietHoursStart": "quiet_hours_start",
        "quietHoursEnd": "quiet_hours_end",
        "monitoringPaused": "monitoring_paused",
        "segmentIntervalSec": "segment_interval_sec",
        "clipRetentionDays": "clip_retention_days",
        "pcPopupEnabled": "pc_popup_enabled",
        "mobilePushEnabled": "mobile_push_enabled",
    }
    changes = {col: value for field, col in field_map.items() if (value := getattr(body, field)) is not None}
    if not changes:
        raise HTTPException(status_code=422, detail="변경할 필드가 없습니다")
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        updated = sb.table("stores").update(changes).eq("id", store_id).execute().data
    except APIError as error:
        print(f"[backend] /stores/:id update failed: {error}")
        raise HTTPException(status_code=500, detail="수정 실패") from error
    if not updated:
        raise HTTPException(status_code=500, detail="수정 실패")
    return {"store": _store_to_detail(updated[0])}


@router.get("/{store_id}/status", response_model=StoreStatus)
def get_store_status(store_id: str, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    store = get_owned_store(sb, store_id, user_id)

    try:
        cams = (
            sb.table("cameras")
            .select("last_status")
            .eq("store_id", store_id)
            .is_("deleted_at", "null")
            .execute()
            .data
            or []
        )
    except APIError as error:
        print(f"[backend] /stores/:id/status camera query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error

    connected = sum(1 for c in cams if c.get("last_status") == "connected")

    # "마지막 AI 분석 시각" — videos 가 아직 store_id로 정규화 전이라(알려진 한계,
    # docs/ux-backend-design.md 참고) 매장이 아니라 소유주 user_id 전체 중 최신값으로
    # 근사한다. 매장을 하나만 쓰면 정확하고, 여러 개면 다소 부정확할 수 있다.
    last_analysis_at = None
    try:
        latest = (
            sb.table("videos")
            .select("processed_at")
            .eq("user_id", user_id)
            .not_.is_("processed_at", "null")
            .order("processed_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if latest:
            last_analysis_at = latest[0]["processed_at"]
    except APIError as error:
        print(f"[backend] /stores/:id/status last-analysis query failed: {error}")

    return {
        "storeId": store_id,
        "camerasConnected": connected,
        "camerasTotal": len(cams),
        "lastAnalysisAt": last_analysis_at,
        "segmentIntervalSec": store["segment_interval_sec"],
        "monitoringPaused": store["monitoring_paused"],
    }
