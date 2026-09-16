"""위험 이벤트 · 조각 조회 (요구사항 4.x · 3.3 · 5.1, docs/api-contract.md 5절)."""

import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import get_current_user_id
from ..deps import (
    clamp_limit, iso, parse_iso, require_event_access, require_store_access,
    require_supabase, signed_url, signed_url_expires_at,
)
from ..domain.timeline import compute_gaps
from ..realtime import bus

router = APIRouter(tags=["events"])

EVENT_STATES = ("unconfirmed", "confirmed", "false_positive")

# 이벤트 시각 기준으로 "같은 때의 다른 카메라"를 찾을 때, 조각 경계를 살짝
# 넘어간 이벤트도 잡히도록 두는 여유.
NEARBY_WINDOW_SEC = 300


class StateChange(BaseModel):
    state: str
    reason: Optional[str] = None
    source: str = "pc"


class MemoUpdate(BaseModel):
    memo: str = Field(default="", max_length=2000)


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> int:
    """커서는 오프셋이다. 매장·날짜 단위 이벤트는 많아야 수백 건이라 키셋
    페이지네이션의 복잡도를 살 이유가 없다. 계약상 불투명한 값이므로 나중에
    키셋으로 바꿔도 클라이언트는 그대로다."""
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor.encode()).decode()))
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="커서가 올바르지 않습니다") from error


def to_event_dto(row: dict[str, Any], camera: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    started = parse_iso(row["started_at"])
    ended = parse_iso(row["ended_at"])
    duration = int((ended - started).total_seconds()) if started and ended else None
    return {
        "id": row["id"],
        "storeId": row["store_id"],
        "cameraId": row["camera_id"],
        "cameraName": (camera or {}).get("name"),
        "locationTag": (camera or {}).get("location_tag"),
        "kind": row["kind"],
        "risk": row["risk"],
        "state": row["state"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
        "durationSec": duration,
        "description": row.get("description"),
        "thumbnailUrl": signed_url("clips", row.get("thumbnail_storage_path")),
        "aiGateStatus": row["ai_gate_status"],
        "createdAt": row["created_at"],
    }


def _cameras_by_id(sb, store_id: str) -> dict[str, dict[str, Any]]:
    rows = sb.table("cameras").select("*").eq("store_id", store_id).execute().data or []
    return {row["id"]: row for row in rows}


@router.get("/stores/{store_id}/events")
def list_events(
    store_id: str = Depends(require_store_access),
    date: Optional[str] = Query(None, description="YYYY-MM-DD (UTC 기준 하루)"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    cameraId: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    risk: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    cursor: Optional[str] = Query(None),
) -> dict[str, Any]:
    sb = require_supabase()
    lim = clamp_limit(limit, 20, 100)
    offset = _decode_cursor(cursor)

    query = sb.table("events").select("*").eq("store_id", store_id)

    if date:
        day_start = parse_iso(f"{date}T00:00:00Z")
        query = query.gte("started_at", iso(day_start)).lt("started_at", iso(day_start + timedelta(days=1)))
    if from_:
        query = query.gte("started_at", iso(parse_iso(from_)))
    if to:
        query = query.lt("started_at", iso(parse_iso(to)))
    if cameraId:
        query = query.eq("camera_id", cameraId)
    if kind:
        query = query.eq("kind", kind)
    if risk:
        query = query.eq("risk", risk)
    if state:
        query = query.eq("state", state)

    # "미확인 우선, 그 다음 최신순" (계약 5.1).
    #
    # state 오름차순이 곧 미확인 우선인 이유는 event_state enum 을
    # ('unconfirmed','confirmed','false_positive') 순서로 정의했기 때문이다 —
    # Postgres 의 enum 정렬은 선언 순서를 따른다. supabase/schema.sql 의 그
    # 순서는 이 정렬을 떠받치고 있으니 바꾸지 말 것.
    rows = (
        query.order("state").order("started_at", desc=True)
        .range(offset, offset + lim)   # 한 건 더 받아 다음 페이지 유무를 본다
        .execute().data or []
    )

    has_more = len(rows) > lim
    page = rows[:lim]
    cameras = _cameras_by_id(sb, store_id)

    return {
        "items": [to_event_dto(row, cameras.get(row["camera_id"])) for row in page],
        "nextCursor": _encode_cursor(offset + lim) if has_more else None,
    }


@router.get("/stores/{store_id}/events/unconfirmed-count")
def unconfirmed_count(
    store_id: str = Depends(require_store_access),
    scope: str = Query("all", pattern="^(today|all)$"),
) -> dict[str, int]:
    sb = require_supabase()
    query = sb.table("events").select("id").eq("store_id", store_id).eq("state", "unconfirmed")
    if scope == "today":
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.gte("started_at", iso(today))
    return {"count": len(query.execute().data or [])}


@router.get("/stores/{store_id}/events/timeline")
def events_timeline(
    store_id: str = Depends(require_store_access),
    date: str = Query(..., description="YYYY-MM-DD (UTC 기준 하루)"),
) -> dict[str, Any]:
    """카메라별 이벤트 구간 + **영상 없음 구간** (요구사항 4.4).

    공백을 보여주지 않으면 "그 시간엔 아무 일도 없었다"와 "그 시간은 못 봤다"가
    화면에서 구분되지 않는다.
    """
    sb = require_supabase()
    day_start = parse_iso(f"{date}T00:00:00Z")
    day_end = day_start + timedelta(days=1)
    now = datetime.now(timezone.utc)
    # 오늘이면 아직 오지 않은 시간을 '영상 없음'이라고 부르지 않는다.
    window_end = min(day_end, now) if day_start <= now < day_end else day_end

    cameras = (
        sb.table("cameras").select("*").eq("store_id", store_id)
        .is_("deleted_at", "null").order("sort_order").execute().data or []
    )

    events = (
        sb.table("events").select("*").eq("store_id", store_id)
        .gte("started_at", iso(day_start)).lt("started_at", iso(day_end))
        .order("started_at").execute().data or []
    )
    videos = (
        sb.table("videos").select("camera_uuid, recorded_started_at, recorded_ended_at")
        .eq("store_uuid", store_id)
        .gte("recorded_started_at", iso(day_start - timedelta(hours=1)))
        .lt("recorded_started_at", iso(day_end))
        .execute().data or []
    )

    events_by_camera: dict[str, list] = {}
    for row in events:
        events_by_camera.setdefault(row["camera_id"], []).append(row)

    covered_by_camera: dict[str, list] = {}
    for row in videos:
        camera_uuid = row.get("camera_uuid")
        if not camera_uuid:
            continue  # 도메인 연결 전에 올라온 옛날 조각
        covered_by_camera.setdefault(camera_uuid, []).append(
            (parse_iso(row["recorded_started_at"]), parse_iso(row["recorded_ended_at"]))
        )

    return {
        "date": date,
        "windowStart": iso(day_start),
        "windowEnd": iso(window_end),
        "cameras": [
            {
                "cameraId": camera["id"],
                "name": camera["name"],
                "locationTag": camera.get("location_tag"),
                "events": [
                    {
                        "id": e["id"], "startedAt": e["started_at"], "endedAt": e["ended_at"],
                        "risk": e["risk"], "kind": e["kind"], "state": e["state"],
                    }
                    for e in events_by_camera.get(camera["id"], [])
                ],
                "gaps": [
                    {"from": iso(start), "to": iso(end), "reason": "no_segment"}
                    for start, end in compute_gaps(
                        covered_by_camera.get(camera["id"], []), day_start, window_end
                    )
                ],
            }
            for camera in cameras
        ],
    }


@router.get("/stores/{store_id}/events/summary")
def events_summary(
    store_id: str = Depends(require_store_access),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
) -> dict[str, Any]:
    """주간 요약 (요구사항 4.10) — 2e 하단, 모바일 2l 요약 카드."""
    sb = require_supabase()
    end = parse_iso(to) or datetime.now(timezone.utc)
    start = parse_iso(from_) or (end - timedelta(days=7))

    rows = (
        sb.table("events").select("state, started_at, memo").eq("store_id", store_id)
        .gte("started_at", iso(start)).lt("started_at", iso(end)).execute().data or []
    )

    by_weekday = [0] * 7
    by_hour = [0] * 24
    for row in rows:
        started = parse_iso(row["started_at"])
        by_weekday[started.weekday()] += 1
        by_hour[started.hour] += 1

    return {
        "from": iso(start),
        "to": iso(end),
        "total": len(rows),
        "falsePositive": sum(1 for r in rows if r["state"] == "false_positive"),
        # 112 접수번호를 메모에 남기는 게 유일한 신고 흔적이다 (요구사항 4.7).
        "reported": sum(1 for r in rows if (r.get("memo") or "").strip()),
        "byWeekday": by_weekday,
        "byHour": by_hour,
    }


@router.get("/events/{event_id}")
def get_event(event_id: str, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    sb = require_supabase()
    event = require_event_access(event_id, user_id)

    cameras = _cameras_by_id(sb, event["store_id"])
    history = (
        sb.table("event_state_changes").select("*").eq("event_id", event_id)
        .order("created_at").execute().data or []
    )

    segments = []
    if event.get("video_id"):
        rows = (
            sb.table("videos").select("*").eq("id", event["video_id"]).limit(1).execute().data or []
        )
        segments = [
            {
                "videoId": v["id"],
                "startedAt": v["recorded_started_at"],
                "endedAt": v["recorded_ended_at"],
                "playbackUrl": signed_url("videos", v.get("storage_path")),
            }
            for v in rows
        ]

    return {
        "event": {
            **to_event_dto(event, cameras.get(event["camera_id"])),
            "appearance": event.get("appearance"),
            "boundingBoxes": event.get("bounding_boxes"),
            "anomalyScore": event.get("anomaly_score"),
            "memo": event.get("memo"),
            "falsePositiveReason": event.get("false_positive_reason"),
            "aiGateError": event.get("ai_gate_error"),
            "clipUrl": signed_url("clips", event.get("clip_storage_path")),
            "clipExpiresAt": event.get("clip_expires_at"),
            "segments": segments,
            "history": [
                {
                    "toState": h["to_state"], "fromState": h.get("from_state"),
                    "changedBy": h.get("changed_by"), "source": h["source"],
                    "reason": h.get("reason"), "createdAt": h["created_at"],
                }
                for h in history
            ],
        }
    }


@router.patch("/events/{event_id}/state")
def change_event_state(event_id: str, body: StateChange,
                       user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """확인됨 / 오탐 / 미확인 복귀 (요구사항 4.6). PC·모바일 어디서 바꿔도 양쪽 동기."""
    sb = require_supabase()
    event = require_event_access(event_id, user_id)

    if body.state not in EVENT_STATES:
        raise HTTPException(status_code=400, detail=f"상태는 {', '.join(EVENT_STATES)} 중 하나여야 합니다")
    if body.source not in ("pc", "mobile", "system"):
        raise HTTPException(status_code=400, detail="source 는 pc / mobile / system 중 하나여야 합니다")

    now = iso(datetime.now(timezone.utc))
    patch = {
        "state": body.state,
        "state_changed_at": now,
        "state_changed_by": user_id,
        # 오탐이 아닌 상태로 옮기면 사유를 지운다 — 남겨 두면 다음에 오탐으로
        # 바꿨을 때 옛 사유가 되살아난다.
        "false_positive_reason": body.reason if body.state == "false_positive" else None,
    }
    sb.table("events").update(patch).eq("id", event_id).execute()

    sb.table("event_state_changes").insert({
        "event_id": event_id,
        "from_state": event["state"],
        "to_state": body.state,
        "changed_by": user_id,
        "source": body.source,
        "reason": body.reason,
    }).execute()

    updated = {**event, **patch}
    cameras = _cameras_by_id(sb, event["store_id"])
    dto = to_event_dto(updated, cameras.get(event["camera_id"]))
    bus.publish(event["store_id"], "event.updated", dto)
    return {"event": dto}


@router.patch("/events/{event_id}/memo")
def update_memo(event_id: str, body: MemoUpdate,
                user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """112 접수번호·피해액 등 (요구사항 4.7)."""
    sb = require_supabase()
    event = require_event_access(event_id, user_id)
    sb.table("events").update({"memo": body.memo}).eq("id", event_id).execute()

    cameras = _cameras_by_id(sb, event["store_id"])
    dto = to_event_dto({**event, "memo": body.memo}, cameras.get(event["camera_id"]))
    bus.publish(event["store_id"], "event.updated", dto)
    return {"event": {**dto, "memo": body.memo}}


@router.get("/events/{event_id}/clip")
def get_clip(event_id: str, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    event = require_event_access(event_id, user_id)
    url = signed_url("clips", event.get("clip_storage_path"))
    if not url:
        raise HTTPException(status_code=404, detail="클립이 없습니다")
    return {"url": url, "expiresAt": signed_url_expires_at()}


@router.get("/events/{event_id}/nearby-cameras")
def nearby_cameras(event_id: str, user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """이벤트 시각의 다른 카메라 화면 (요구사항 4.8) — 2d '다른 카메라', 2f 4분할.

    조각 경계가 벽시계에 정렬돼 있어서(alignToClock) 같은 시각의 조각을
    카메라별로 하나씩 고르면 그대로 나란히 놓을 수 있다.
    """
    sb = require_supabase()
    event = require_event_access(event_id, user_id)
    at = parse_iso(event["started_at"])

    rows = (
        sb.table("videos").select("*").eq("store_uuid", event["store_id"])
        .gte("recorded_started_at", iso(at - timedelta(seconds=NEARBY_WINDOW_SEC)))
        .lte("recorded_started_at", iso(at))
        .order("recorded_started_at", desc=True).execute().data or []
    )

    cameras = _cameras_by_id(sb, event["store_id"])
    best_per_camera: dict[str, dict[str, Any]] = {}
    for row in rows:
        camera_uuid = row.get("camera_uuid")
        if not camera_uuid or camera_uuid == event["camera_id"]:
            continue
        ended = parse_iso(row["recorded_ended_at"])
        if ended < at:
            continue  # 이벤트 시각을 품지 못하는 조각
        # 위에서 recorded_started_at 내림차순이라 첫 번째가 가장 가까운 조각이다.
        best_per_camera.setdefault(camera_uuid, row)

    return {
        "cameras": [
            {
                "cameraId": camera_uuid,
                "name": (cameras.get(camera_uuid) or {}).get("name"),
                "locationTag": (cameras.get(camera_uuid) or {}).get("location_tag"),
                "playbackUrl": signed_url("videos", row.get("storage_path")),
                "segmentStartedAt": row["recorded_started_at"],
                # 플레이어가 이 지점부터 재생하면 메인 화면과 시각이 맞는다.
                "offsetSec": (at - parse_iso(row["recorded_started_at"])).total_seconds(),
            }
            for camera_uuid, row in best_per_camera.items()
        ]
    }


@router.get("/stores/{store_id}/segments")
def list_segments(
    store_id: str = Depends(require_store_access),
    cameraId: Optional[str] = Query(None),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
) -> dict[str, Any]:
    """조각 목록 + 재생 URL (요구사항 3.3) — 2f 의 '◂ 더 이전 / 더 이후'."""
    sb = require_supabase()
    lim = clamp_limit(limit, 50, 200)

    query = sb.table("videos").select("*").eq("store_uuid", store_id)
    if cameraId:
        query = query.eq("camera_uuid", cameraId)
    if from_:
        query = query.gte("recorded_started_at", iso(parse_iso(from_)))
    if to:
        query = query.lt("recorded_started_at", iso(parse_iso(to)))

    rows = query.order("recorded_started_at", desc=True).limit(lim).execute().data or []

    return {
        "segments": [
            {
                "videoId": row["id"],
                "cameraId": row.get("camera_uuid"),
                "startedAt": row["recorded_started_at"],
                "endedAt": row["recorded_ended_at"],
                "durationSec": row.get("duration_sec"),
                "status": row["status"],
                "playbackUrl": signed_url("videos", row.get("storage_path")),
            }
            for row in rows
        ]
    }
