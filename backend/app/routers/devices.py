"""디바이스(PC 앱) 등록/페어링/원격 명령 (2a 로그인·매장 연결, 2m 원격 재시작).

DEVICE_TOKENS(.env, 정적) 는 그대로 남아있고 ingest-worker 가 그쪽을 먼저 본다 —
여기서 발급하는 토큰은 DB 조회 경로로 별도 검증된다(ingest-worker/src/deviceAuth.ts
참고). 그래서 기존 정적 배포도 안 깨진다.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from postgrest.exceptions import APIError

from ..auth import get_current_device, get_current_user_id
from ..deps import require_supabase
from ..schemas import (
    DeviceCommandCreateRequest,
    DeviceCommandDto,
    DeviceCommandsResponse,
    DeviceCreateRequest,
    DeviceIssuedDto,
    DeviceResponse,
    PairingClaimRequest,
    PairingStartResponse,
    PushTokenAck,
)
from ..security import generate_device_token, generate_pairing_code, hash_device_token
from .stores import get_owned_store

router = APIRouter(tags=["devices"])

PAIRING_CODE_TTL_MIN = 5


def _device_to_dto(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "storeId": row["store_id"],
        "label": row.get("label"),
        "platform": row["platform"],
        "lastSeenAt": row.get("last_seen_at"),
        "revoked": bool(row.get("revoked_at")),
    }


def _issue_device(sb, store_id: str, label: Optional[str], platform: str = "electron") -> Dict[str, Any]:
    token = generate_device_token()
    try:
        created = (
            sb.table("devices")
            .insert(
                {
                    "store_id": store_id,
                    "label": label,
                    "token_hash": hash_device_token(token),
                    "platform": platform,
                }
            )
            .execute()
            .data
        )
    except APIError as error:
        print(f"[backend] device issue failed: {error}")
        raise HTTPException(status_code=500, detail="디바이스 발급 실패") from error
    if not created:
        raise HTTPException(status_code=500, detail="디바이스 발급 실패")
    return {"deviceId": created[0]["id"], "deviceToken": token, "storeId": store_id}


# ---------------------------------------------------------------------------
# 2a 기본 흐름: PC가 이미 로그인돼 있고(같은 계정), 매장만 골라서 바로 발급받는다.
# ---------------------------------------------------------------------------
@router.post("/stores/{store_id}/devices/pairing", response_model=DeviceIssuedDto)
def issue_device_for_store(
    store_id: str,
    body: DeviceCreateRequest,
    replace: bool = Query(False),
    user_id: str = Depends(get_current_user_id),
) -> Dict[str, Any]:
    sb = require_supabase()
    get_owned_store(sb, store_id, user_id)

    try:
        existing = (
            sb.table("devices")
            .select("id, label, last_seen_at")
            .eq("store_id", store_id)
            .is_("revoked_at", "null")
            .execute()
            .data
            or []
        )
    except APIError as error:
        print(f"[backend] existing device lookup failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error

    if existing and not replace:
        raise HTTPException(
            status_code=409,
            detail=f"이미 연결된 PC가 있습니다({existing[0].get('label') or '이름 없음'}) — "
            "교체하려면 ?replace=true 로 다시 요청하세요",
        )
    if existing and replace:
        now = datetime.now(timezone.utc).isoformat()
        try:
            sb.table("devices").update({"revoked_at": now}).eq("id", existing[0]["id"]).execute()
        except APIError as error:
            print(f"[backend] device revoke failed: {error}")
            raise HTTPException(status_code=500, detail="기존 디바이스 해제 실패") from error

    return _issue_device(sb, store_id, body.label, body.platform)


# ---------------------------------------------------------------------------
# QR 페어링: PC가 로그인 안 한 상태에서 코드를 발급받고, 모바일(로그인됨)이 스캔해서
# claim 하면, PC가 폴링(GET)하다가 그 순간 딱 한 번 토큰을 받아간다.
# ---------------------------------------------------------------------------
@router.post("/pc/pairing/start", response_model=PairingStartResponse)
def start_pairing() -> Dict[str, Any]:
    sb = require_supabase()
    code = generate_pairing_code()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=PAIRING_CODE_TTL_MIN)).isoformat()
    try:
        sb.table("device_pairing_codes").insert(
            {"code": code, "status": "pending", "expires_at": expires_at}
        ).execute()
    except APIError as error:
        print(f"[backend] pairing start failed: {error}")
        raise HTTPException(status_code=500, detail="페어링 코드 발급 실패") from error
    return {"pairingCode": code, "expiresAt": expires_at}


@router.post("/pc/pairing/{code}/claim", response_model=PushTokenAck)
def claim_pairing(code: str, body: PairingClaimRequest, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    get_owned_store(sb, body.storeId, user_id)  # 내 매장인지 확인

    try:
        pairing = sb.table("device_pairing_codes").select("*").eq("code", code).maybe_single().execute().data
    except APIError as error:
        print(f"[backend] pairing claim lookup failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if not pairing:
        raise HTTPException(status_code=404, detail="페어링 코드를 찾을 수 없습니다")
    if pairing["status"] != "pending":
        raise HTTPException(status_code=409, detail="이미 사용되었거나 만료된 코드입니다")
    if datetime.fromisoformat(pairing["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="만료된 코드입니다")

    try:
        sb.table("device_pairing_codes").update(
            {
                "status": "claimed",
                "claimed_store_id": body.storeId,
                "claimed_by_user_id": user_id,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("code", code).execute()
    except APIError as error:
        print(f"[backend] pairing claim update failed: {error}")
        raise HTTPException(status_code=500, detail="처리 실패") from error
    return {"ok": True}


@router.get("/pc/pairing/{code}")
def poll_pairing(code: str) -> Dict[str, Any]:
    """PC가 주기적으로 호출. claimed 상태를 처음 본 순간 디바이스를 발급하고 토큰을
    같이 돌려준다 — 그 다음부터는 'issued'로 바뀌어서 같은 토큰이 두 번 나가지 않는다.
    """
    sb = require_supabase()
    try:
        pairing = sb.table("device_pairing_codes").select("*").eq("code", code).maybe_single().execute().data
    except APIError as error:
        print(f"[backend] pairing poll lookup failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if not pairing:
        raise HTTPException(status_code=404, detail="페어링 코드를 찾을 수 없습니다")

    if pairing["status"] == "pending":
        if datetime.fromisoformat(pairing["expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return {"status": "expired"}
        return {"status": "pending"}

    if pairing["status"] == "issued":
        raise HTTPException(status_code=410, detail="이미 사용된 코드입니다")

    if pairing["status"] == "expired":
        return {"status": "expired"}

    # status == 'claimed' — 지금 이 요청에서 디바이스를 발급한다(1회성).
    store_id = pairing["claimed_store_id"]
    store = sb.table("stores").select("name").eq("id", store_id).maybe_single().execute().data
    issued = _issue_device(sb, store_id, label=None, platform="electron")

    try:
        sb.table("device_pairing_codes").update(
            {
                "status": "issued",
                "issued_device_id": issued["deviceId"],
                "issued_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("code", code).execute()
    except APIError as error:
        # 발급된 devices 행은 이미 만들어졌으니(토큰도 응답에 실림), 이 UPDATE가 실패해도
        # 치명적이지 않다 — 다음 poll에서 status가 여전히 claimed면 새 디바이스가 하나 더
        # 생기는 정도의 부작용이라 로그만 남긴다.
        print(f"[backend] pairing mark-issued failed (device already issued): {error}")

    return {
        "status": "claimed",
        "deviceId": issued["deviceId"],
        "deviceToken": issued["deviceToken"],
        "storeId": store_id,
        "storeName": (store or {}).get("name"),
    }


# ---------------------------------------------------------------------------
# 디바이스 조회 / 원격 명령
# ---------------------------------------------------------------------------
def _get_owned_device(sb, device_id: str, user_id: str) -> Dict[str, Any]:
    try:
        device = sb.table("devices").select("*").eq("id", device_id).maybe_single().execute().data
    except APIError as error:
        print(f"[backend] device lookup failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if not device:
        raise HTTPException(status_code=404, detail="디바이스를 찾을 수 없습니다")
    get_owned_store(sb, device["store_id"], user_id)  # 소유 확인 (아니면 404)
    return device


@router.get("/devices/{device_id}", response_model=DeviceResponse)
def get_device(device_id: str, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    return {"device": _device_to_dto(_get_owned_device(sb, device_id, user_id))}


@router.post("/devices/{device_id}/commands", response_model=DeviceCommandDto)
def create_device_command(
    device_id: str, body: DeviceCommandCreateRequest, user_id: str = Depends(get_current_user_id)
) -> Dict[str, Any]:
    sb = require_supabase()
    _get_owned_device(sb, device_id, user_id)
    try:
        created = (
            sb.table("device_commands").insert({"device_id": device_id, "command": body.command}).execute().data
        )
    except APIError as error:
        print(f"[backend] device command create failed: {error}")
        raise HTTPException(status_code=500, detail="명령 등록 실패") from error
    if not created:
        raise HTTPException(status_code=500, detail="명령 등록 실패")
    row = created[0]
    return {
        "id": row["id"],
        "deviceId": row["device_id"],
        "command": row["command"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


# PC가 device 토큰으로 폴링(heartbeat 응답에 얹거나 별도 주기로). 받은 즉시 acked로
# 표시해서 다음 폴링에 같은 명령이 중복으로 나가지 않게 한다.
@router.get("/devices/{device_id}/commands/pending", response_model=DeviceCommandsResponse)
def get_pending_commands(device_id: str, device: Dict[str, Any] = Depends(get_current_device)) -> Dict[str, Any]:
    if device["id"] != device_id:
        raise HTTPException(status_code=403, detail="이 디바이스의 명령이 아닙니다")

    sb = require_supabase()
    try:
        pending = (
            sb.table("device_commands")
            .select("*")
            .eq("device_id", device_id)
            .eq("status", "pending")
            .order("created_at")
            .execute()
            .data
            or []
        )
        if pending:
            ids = [c["id"] for c in pending]
            sb.table("device_commands").update(
                {"status": "acked", "acked_at": datetime.now(timezone.utc).isoformat()}
            ).in_("id", ids).execute()
    except APIError as error:
        print(f"[backend] pending commands fetch failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error

    return {
        "commands": [
            {
                "id": c["id"],
                "deviceId": c["device_id"],
                "command": c["command"],
                "status": "acked",
                "createdAt": c["created_at"],
            }
            for c in pending
        ]
    }
