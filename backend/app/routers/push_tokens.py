"""모바일 푸시 토큰 등록/해제 (2i). 저장까지만 — 실제 FCM/APNs 발송은 아직 없다
(docs/ux-backend-design.md 5장 질문 6, Firebase 프로젝트 등 인프라가 준비되면 추가)."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError

from ..auth import get_current_user_id
from ..deps import require_supabase
from ..schemas import PushTokenAck, PushTokenCreateRequest

router = APIRouter(prefix="/me/push-tokens", tags=["push-tokens"])


@router.post("", response_model=PushTokenAck)
def register_push_token(
    body: PushTokenCreateRequest, user_id: str = Depends(get_current_user_id)
) -> Dict[str, Any]:
    sb = require_supabase()
    try:
        sb.table("push_tokens").upsert(
            {"user_id": user_id, "platform": body.platform, "token": body.token},
            on_conflict="user_id,token",
        ).execute()
    except APIError as error:
        print(f"[backend] push token register failed: {error}")
        raise HTTPException(status_code=500, detail="등록 실패") from error
    return {"ok": True}


@router.delete("/{token}", response_model=PushTokenAck)
def delete_push_token(token: str, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    try:
        sb.table("push_tokens").delete().eq("user_id", user_id).eq("token", token).execute()
    except APIError as error:
        print(f"[backend] push token delete failed: {error}")
        raise HTTPException(status_code=500, detail="삭제 실패") from error
    return {"ok": True}
