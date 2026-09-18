"""SSE 실시간 채널 + ingest-worker 가 두드리는 내부 엔드포인트
(요구사항 4.2 · 2.4, docs/api-contract.md 6절).

새 위험 이벤트는 backend 안에서 만들어 바로 흘린다 (app/anomaly_ingest.py).
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config
from ..deps import require_store_access
from ..realtime import bus, event_stream

router = APIRouter(tags=["stream"])


@router.get("/stores/{store_id}/stream")
def stream(store_id: str = Depends(require_store_access)) -> StreamingResponse:
    return StreamingResponse(
        event_stream(store_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx 가 응답을 모아뒀다 흘리면 SSE 가 실시간이 아니게 된다.
            # nginx.conf 에도 proxy_buffering off 가 필요하다.
            "X-Accel-Buffering": "no",
        },
    )


def _require_internal_token(x_internal_token: Optional[str] = Header(None)) -> None:
    """내부 호출 전용. 스택 내부 네트워크에서만 닿지만(nginx 가 /internal 을
    라우팅하지 않는다) 토큰도 같이 본다 — 네트워크 격리 하나에만 기대지 않는다."""
    if not config.INTERNAL_API_TOKEN:
        raise HTTPException(status_code=503, detail="INTERNAL_API_TOKEN 이 설정되지 않았습니다")
    if x_internal_token != config.INTERNAL_API_TOKEN:
        raise HTTPException(status_code=401, detail="내부 토큰이 올바르지 않습니다")


class CameraStateChanged(BaseModel):
    storeId: str
    cameraId: str
    state: str
    lastFrameAt: Optional[str] = None


@router.post("/internal/cameras/state", dependencies=[Depends(_require_internal_token)])
def camera_state_changed(body: CameraStateChanged) -> dict[str, Any]:
    """ingest-worker 의 하트비트가 카메라 상태 변화를 감지했을 때 (요구사항 2.4)."""
    bus.publish(body.storeId, "camera.state", {
        "cameraId": body.cameraId, "state": body.state, "lastFrameAt": body.lastFrameAt,
    })
    return {"ok": True}
