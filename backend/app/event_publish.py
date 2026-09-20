"""새 위험 이벤트를 SSE 로 흘리고 푸시를 보낸다 (요구사항 4.2 · 6.4)."""

from datetime import datetime, time
from typing import Any, Optional

from . import config
from .deps import signed_url
from .domain.notify import QuietHours, StoreHours, decide_push
from .domain.risk import RiskLevel
from .push import send_push
from .realtime import bus
from .routers.events import _cameras_by_id, to_event_dto

RISK_LABEL = {"high": "높음", "medium": "보통", "low": "낮음"}

#: 사용자가 알림 설정을 한 번도 저장하지 않았을 때의 값 (schema.sql 기본값과 같다).
DEFAULT_MIN_RISK: RiskLevel = "low"
DEFAULT_QUIET_HOURS = QuietHours(
    business_hours_high_only=True,
    sleep_start=None,
    sleep_end=None,
    sleep_high_only=True,
    override_dnd_for_high=True,
)


def parse_time(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def settings_from_row(row: Optional[dict[str, Any]]) -> tuple[RiskLevel, QuietHours]:
    """notification_settings 한 행 → (알림 받을 최소 위험도, 조용한 시간)."""
    if not row:
        return DEFAULT_MIN_RISK, DEFAULT_QUIET_HOURS
    return row.get("min_risk") or DEFAULT_MIN_RISK, QuietHours(
        business_hours_high_only=row.get("business_hours_high_only", True),
        sleep_start=parse_time(row.get("sleep_start")),
        sleep_end=parse_time(row.get("sleep_end")),
        sleep_high_only=row.get("sleep_high_only", True),
        override_dnd_for_high=row.get("override_dnd_for_high", True),
    )


def publish_created_event(sb, event: dict[str, Any], push: bool = True) -> int:
    """막 만든 events 행을 매장 구독자에게 흘리고, push 면 구성원에게 푸시한다.
    푸시를 보낸 사람 수를 돌려준다."""
    cameras = _cameras_by_id(sb, event["store_id"])
    camera = cameras.get(event["camera_id"])
    bus.publish(event["store_id"], "event.created", to_event_dto(event, camera))
    return _push_for_event(sb, event, camera) if push else 0


def _push_for_event(sb, event: dict[str, Any], camera: Optional[dict[str, Any]]) -> int:
    """알림 설정을 반영해 매장 구성원들에게 푸시를 보낸다 (요구사항 6.4).

    구성원마다 설정이 다르므로 판정도 사람마다 한다.
    """
    store_rows = sb.table("stores").select("*").eq("id", event["store_id"]).limit(1).execute().data or []
    if not store_rows:
        return 0
    store = store_rows[0]
    store_hours = StoreHours(
        opens_at=parse_time(store.get("opens_at")), closes_at=parse_time(store.get("closes_at"))
    )

    members = (
        sb.table("store_members").select("user_id").eq("store_id", event["store_id"]).execute().data or []
    )
    # 판정은 매장 현지시각으로 해야 한다 — 영업시간·수면시간이 전부 벽시계다.
    now_local = datetime.now(config.STORE_TIMEZONE).time()

    camera_name = (camera or {}).get("name") or "카메라"
    title = f"{store['name']} · {camera_name}"
    body_text = f"이상 행동이 감지되었습니다 · 위험도 {RISK_LABEL.get(event['risk'], event['risk'])}"
    thumbnail = signed_url("clips", event.get("thumbnail_storage_path"))

    pushed = 0
    for member in members:
        user_id = member["user_id"]
        rows = (
            sb.table("notification_settings").select("*")
            .eq("store_id", event["store_id"]).eq("user_id", user_id).limit(1).execute().data or []
        )
        min_risk, quiet = settings_from_row(rows[0] if rows else None)

        decision = decide_push(
            risk=event["risk"], now_local=now_local, min_risk=min_risk,
            quiet=quiet, store_hours=store_hours,
        )
        if not decision.deliver:
            config.log(
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
                "risk": event["risk"], "overrideDnd": decision.override_dnd,
            },
            sound=decision.sound, thumbnail_url=thumbnail,
        ):
            pushed += 1

    return pushed
