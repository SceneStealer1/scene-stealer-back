"""이상 구간 하나를 '위험 이벤트'로 승격시켜 저장하고 실시간으로 알린다.

`pipeline/` 은 순수 분석이고, 여기는 DB·HTTP 같은 바깥세상이라 분리한다.

흐름 (docs/ai-gate-contract.md 2절):
    anomaly_events insert  →  AI 게이트 호출  →  events insert
                                                      ↓
                                     backend /internal/events/published
                                                      ↓
                                            SSE 팬아웃 + 푸시
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from supabase import Client

from pipeline.risk_gate import RiskGateRequest, classify

# 스택 내부에서만 보이는 주소. nginx 를 거치지 않는다.
BACKEND_INTERNAL_URL = os.environ.get("BACKEND_INTERNAL_URL", "http://backend:8081")
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN") or None
_NOTIFY_TIMEOUT_SEC = 10

# 클립 보관 기간. 매장별 설정(stores.clip_retention_days)이 있으면 그걸 쓴다.
DEFAULT_CLIP_RETENTION_DAYS = 30


def _to_absolute(recorded_started_at: str, offset_sec: float) -> str:
    base = datetime.fromisoformat(recorded_started_at.replace("Z", "+00:00"))
    moment = base.astimezone(timezone.utc) + timedelta(seconds=offset_sec)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_camera(sb: Client, video_row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """영상이 어느 카메라 행에 속하는지 찾는다.

    ingest-worker 가 camera_uuid 를 채우기 시작한 뒤의 조각은 바로 잡히고,
    그 전에 올라온 조각은 (store_uuid, agent_camera_id) 로 뒤늦게 잇는다.
    """
    camera_uuid = video_row.get("camera_uuid")
    if camera_uuid:
        rows = sb.table("cameras").select("*").eq("id", camera_uuid).limit(1).execute().data or []
        if rows:
            return rows[0]

    store_uuid = video_row.get("store_uuid")
    agent_camera_id = video_row.get("camera_id")
    if store_uuid and agent_camera_id:
        rows = (
            sb.table("cameras").select("*").eq("store_id", store_uuid)
            .eq("agent_camera_id", agent_camera_id).limit(1).execute().data or []
        )
        if rows:
            return rows[0]
    return None


def _clip_expires_at(sb: Client, store_id: str) -> str:
    rows = sb.table("stores").select("clip_retention_days").eq("id", store_id).limit(1).execute().data or []
    days = rows[0]["clip_retention_days"] if rows else DEFAULT_CLIP_RETENTION_DAYS
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    return expires.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def publish_event(
    sb: Client,
    video_row: dict[str, Any],
    seg: Any,
    anomaly_event_id: Optional[str],
    clip_path: Path,
    thumb_path: Path,
    clip_storage_path: str,
    thumb_storage_path: str,
) -> Optional[str]:
    """이상 구간 하나를 events 행으로 만든다. 실패해도 예외를 던지지 않는다 —
    이벤트 하나 때문에 영상 전체 처리가 실패로 표시되면 안 된다."""
    try:
        camera = _resolve_camera(sb, video_row)
        store_id = video_row.get("store_uuid") or (camera or {}).get("store_id")
        if not store_id or not camera:
            # 도메인(매장·카메라)이 아직 등록되지 않은 조각이다. anomaly_events
            # 에는 남아 있으니 나중에 이어 붙일 수 있다.
            print(
                f"[ai-worker] 매장/카메라 미등록 — events 생략 video_id={video_row['id']} "
                f"store_uuid={store_id} camera_id={video_row.get('camera_id')}"
            )
            return None

        started_at = _to_absolute(video_row["recorded_started_at"], seg.start_time_sec)
        ended_at = _to_absolute(video_row["recorded_started_at"], seg.end_time_sec)

        verdict = classify(RiskGateRequest(
            clip_path=clip_path,
            thumbnail_path=thumb_path,
            started_at=started_at,
            ended_at=ended_at,
            camera_name=camera["name"],
            location_tag=camera.get("location_tag"),
            anomaly_score=seg.score,
            threshold=seg.threshold,
        ))

        created = sb.table("events").insert({
            "store_id": store_id,
            "camera_id": camera["id"],
            "anomaly_event_id": anomaly_event_id,
            "video_id": video_row["id"],
            "started_at": started_at,
            "ended_at": ended_at,
            "anomaly_score": seg.score,
            "clip_storage_path": clip_storage_path,
            "thumbnail_storage_path": thumb_storage_path,
            "clip_expires_at": _clip_expires_at(sb, store_id),
            **verdict.to_event_columns(),
        }).execute().data or []

        if not created:
            print(f"[ai-worker] events insert 가 행을 돌려주지 않았습니다 video_id={video_row['id']}")
            return None

        event_id = created[0]["id"]
        print(
            f"[ai-worker] event 생성 id={event_id} kind={verdict.kind} risk={verdict.risk} "
            f"gate={verdict.status}"
        )
        _notify_backend(event_id)
        return event_id

    except Exception as error:  # noqa: BLE001
        print(f"[ai-worker] event 생성 실패 video_id={video_row.get('id')}: {error}")
        return None


def _notify_backend(event_id: str) -> None:
    """backend 가 SSE 로 흘리고 푸시를 보내도록 두드린다.

    backend 가 죽어 있으면 이 알림은 사라진다 — 행은 DB 에 남아 있고,
    클라이언트는 재연결 후 목록 조회로 복구한다 (docs/api-contract.md 6절).
    그래서 실패를 재시도하지 않고 로그만 남긴다.
    """
    if not INTERNAL_API_TOKEN:
        print("[ai-worker] INTERNAL_API_TOKEN 미설정 — 실시간 알림을 건너뜁니다")
        return

    request = urllib.request.Request(
        f"{BACKEND_INTERNAL_URL.rstrip('/')}/internal/events/published",
        data=json.dumps({"eventId": event_id}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Internal-Token": INTERNAL_API_TOKEN},
    )
    try:
        with urllib.request.urlopen(request, timeout=_NOTIFY_TIMEOUT_SEC) as response:
            if not 200 <= response.status < 300:
                print(f"[ai-worker] 실시간 알림 실패 status={response.status}")
    except urllib.error.URLError as error:
        print(f"[ai-worker] 실시간 알림 실패 event_id={event_id}: {error}")
