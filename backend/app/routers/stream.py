"""SSE 실시간 채널 + ai-worker 가 두드리는 내부 엔드포인트
(요구사항 4.2 · 6.4, docs/api-contract.md 6절)."""

from datetime import datetime, time, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config
from ..deps import iso, require_store_access, require_supabase, signed_url
from ..domain.notify import KindSetting, QuietHours, StoreHours, decide_push
from ..push import send_push
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


class PublishedEvent(BaseModel):
    """ai-worker 가 events insert 직후 알려 주는 것. 전체 행이 아니라 id 만
    받고 여기서 다시 읽는다 — 워커가 만든 DTO 와 조회 API 의 DTO 가 어긋나는
    일을 원천적으로 막는다."""
    eventId: str


def _require_internal_token(x_internal_token: Optional[str] = Header(None)) -> None:
    """내부 호출 전용. 스택 내부 네트워크에서만 닿지만(nginx 가 /internal 을
    라우팅하지 않는다) 토큰도 같이 본다 — 네트워크 격리 하나에만 기대지 않는다."""
    if not config.INTERNAL_API_TOKEN:
        raise HTTPException(status_code=503, detail="INTERNAL_API_TOKEN 이 설정되지 않았습니다")
    if x_internal_token != config.INTERNAL_API_TOKEN:
        raise HTTPException(status_code=401, detail="내부 토큰이 올바르지 않습니다")


def _parse_time(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


@router.post("/internal/events/published", dependencies=[Depends(_require_internal_token)])
def event_published(body: PublishedEvent) -> dict[str, Any]:
    """새 이벤트를 SSE 로 흘리고 푸시를 보낸다.

    ai-worker 와 backend 는 다른 프로세스라 in-process pub/sub 으로는 이어지지
    않는다. 그래서 워커가 이 문을 두드린다 (docs/ai-gate-contract.md 2절).
    """
    from .events import to_event_dto, _cameras_by_id  # 순환 임포트 회피

    sb = require_supabase()
    rows = sb.table("events").select("*").eq("id", body.eventId).limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다")
    event = rows[0]

    cameras = _cameras_by_id(sb, event["store_id"])
    camera = cameras.get(event["camera_id"])
    dto = to_event_dto(event, camera)

    bus.publish(event["store_id"], "event.created", dto)
    pushed = _push_for_event(sb, event, camera)

    return {"ok": True, "subscribers": bus.subscriber_count(event["store_id"]), "pushed": pushed}


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


def _push_for_event(sb, event: dict[str, Any], camera: Optional[dict[str, Any]]) -> int:
    """알림 설정을 반영해 매장 구성원들에게 푸시를 보낸다 (요구사항 6.4).

    구성원마다 설정이 다르므로 판정도 사람마다 한다.
    """
    store_rows = sb.table("stores").select("*").eq("id", event["store_id"]).limit(1).execute().data or []
    if not store_rows:
        return 0
    store = store_rows[0]
    store_hours = StoreHours(
        opens_at=_parse_time(store.get("opens_at")), closes_at=_parse_time(store.get("closes_at"))
    )

    members = (
        sb.table("store_members").select("user_id").eq("store_id", event["store_id"]).execute().data or []
    )
    # 판정은 매장 현지시각으로 해야 한다 — 영업시간·수면시간이 전부 벽시계다.
    now_local = datetime.now(config.STORE_TIMEZONE).time()

    kind_label = event["kind"]
    camera_name = (camera or {}).get("name") or "카메라"
    title = f"{store['name']} · {camera_name}"
    body_text = event.get("description") or f"{kind_label} 의심 신호가 감지되었습니다."
    thumbnail = signed_url("clips", event.get("thumbnail_storage_path"))

    pushed = 0
    for member in members:
        user_id = member["user_id"]
        settings_rows = (
            sb.table("notification_settings").select("*")
            .eq("store_id", event["store_id"]).eq("user_id", user_id).execute().data or []
        )
        settings = {
            row["kind"]: KindSetting(enabled=row["enabled"], sensitivity=row["sensitivity"])
            for row in settings_rows
        }
        quiet_rows = (
            sb.table("notification_quiet_hours").select("*")
            .eq("store_id", event["store_id"]).eq("user_id", user_id).limit(1).execute().data or []
        )
        q = quiet_rows[0] if quiet_rows else {}
        quiet = QuietHours(
            business_hours_high_only=q.get("business_hours_high_only", True),
            sleep_start=_parse_time(q.get("sleep_start")),
            sleep_end=_parse_time(q.get("sleep_end")),
            sleep_emergency_only=q.get("sleep_emergency_only", True),
            override_dnd_for_high=q.get("override_dnd_for_high", True),
        )

        decision = decide_push(
            kind=event["kind"], risk=event["risk"], now_local=now_local,
            settings=settings, quiet=quiet, store_hours=store_hours,
        )
        if not decision.deliver:
            print(
                f"[backend] 푸시 억제 event_id={event['id']} user_id={user_id} "
                f"reason={decision.reason}"
            )
            continue

        tokens = (
            sb.table("push_devices").select("platform, token").eq("user_id", user_id).execute().data or []
        )
        if send_push(
            tokens=tokens, title=title, body=body_text,
            data={
                "type": "event", "eventId": event["id"], "storeId": event["store_id"],
                "kind": event["kind"], "risk": event["risk"],
                "overrideDnd": decision.override_dnd,
            },
            sound=decision.sound, thumbnail_url=thumbnail,
        ):
            pushed += 1

    return pushed
