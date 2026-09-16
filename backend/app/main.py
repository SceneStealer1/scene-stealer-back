"""
scene-stealer 백엔드 API (FastAPI). ingest-worker 가 받아서 ai-worker 가 분석해 Supabase 에
쌓아둔 결과(videos, anomaly_events)를 프론트가 조회하는 창구다.
nginx가 "/" 이하 전부를 이 서비스로 라우팅한다 (nginx/nginx.conf 참고).
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from .auth import get_current_user_id
from .clips import to_clip_dto
from .deps import clamp_limit, require_supabase
from .schemas import (
    ClipResponse,
    ClipsResponse,
    ClipUpdateRequest,
    ProfileDto,
    VideoDetailResponse,
    VideosResponse,
)
from .routers import cameras as cameras_router
from .routers import devices as devices_router
from .routers import push_tokens as push_tokens_router
from .routers import stores as stores_router

app = FastAPI(title="scene-stealer-backend")

app.include_router(stores_router.router)
app.include_router(cameras_router.router)
app.include_router(devices_router.router)
app.include_router(push_tokens_router.router)


# server.ts 와 동일한 에러 응답 모양({"error": "..."}) 을 유지한다.
@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.get("/healthz")
def healthz() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/")
def root() -> Dict[str, str]:
    return {"service": "scene-stealer-backend", "status": "ok"}


# 로그인한 본인의 개인정보 프로필(profiles 테이블 — 담당자명/연락처만, 매장 정보는
# /stores 로 옮겨갔다). 수정은 RLS로 이미 허용돼 있어서 프론트가 supabase-js로 직접
# update 하면 된다 — 여기선 조회만 다른 라우트와 같은 패턴으로 통일해둔다.
@app.get("/me", response_model=ProfileDto)
def get_me(user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()

    try:
        profile = sb.table("profiles").select("*").eq("id", user_id).maybe_single().execute().data
    except APIError as error:
        print(f"[backend] /me query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if not profile:
        raise HTTPException(status_code=404, detail="프로필을 찾을 수 없습니다")

    return {
        "id": profile["id"],
        "contactName": profile.get("contact_name"),
        "phoneNumber": profile.get("phone_number"),
    }


# 최근 영상 목록 + 영상별 이상행동 건수. 로그인한 본인 소유만 보인다.
@app.get("/videos", response_model=VideosResponse)
def list_videos(
    limit: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    user_id: str = Depends(get_current_user_id),
) -> Dict[str, Any]:
    sb = require_supabase()
    lim = clamp_limit(limit, 20, 100)

    query = (
        sb.table("videos")
        .select("*")
        .eq("user_id", user_id)
        .order("recorded_started_at", desc=True)
        .limit(lim)
    )
    if status:
        query = query.eq("status", status)

    try:
        videos = query.execute().data or []
    except APIError as error:
        print(f"[backend] /videos query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error

    video_ids = [v["id"] for v in videos]
    count_by_video_id: Dict[str, int] = {}
    if video_ids:
        try:
            events = (
                sb.table("anomaly_events").select("video_id").in_("video_id", video_ids).execute().data
                or []
            )
            for e in events:
                count_by_video_id[e["video_id"]] = count_by_video_id.get(e["video_id"], 0) + 1
        except APIError as error:
            print(f"[backend] /videos anomaly count query failed: {error}")

    return {
        "videos": [
            {
                "id": v["id"],
                "userId": v["user_id"],
                "storeId": v["store_id"],
                "cameraLocation": v.get("camera_location"),
                "status": v["status"],
                "progress": v["progress"],
                "recordedStartedAt": v["recorded_started_at"],
                "recordedEndedAt": v["recorded_ended_at"],
                "durationSec": v.get("duration_sec"),
                "anomalyCount": count_by_video_id.get(v["id"], 0),
            }
            for v in videos
        ]
    }


# 영상 하나 + 그 영상의 하이라이트 클립들(signed URL 포함). 남의 영상이면 404로 숨긴다.
@app.get("/videos/{video_id}", response_model=VideoDetailResponse)
def get_video(video_id: str, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()

    try:
        video = (
            sb.table("videos")
            .select("*")
            .eq("id", video_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
            .data
        )
    except APIError as error:
        print(f"[backend] /videos/:id query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if not video:
        raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다")

    try:
        events = (
            sb.table("anomaly_events")
            .select("*")
            .eq("video_id", video["id"])
            .order("start_time_sec")
            .execute()
            .data
            or []
        )
    except APIError as error:
        print(f"[backend] /videos/:id anomaly_events query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error

    video_ref = {
        "store_id": video["store_id"],
        "camera_location": video.get("camera_location"),
        "recorded_started_at": video["recorded_started_at"],
    }
    clips = [to_clip_dto(event, video_ref) for event in events]

    return {
        "video": {
            "id": video["id"],
            "userId": video["user_id"],
            "storeId": video["store_id"],
            "cameraLocation": video.get("camera_location"),
            "status": video["status"],
            "progress": video["progress"],
            "recordedStartedAt": video["recorded_started_at"],
            "recordedEndedAt": video["recorded_ended_at"],
            "durationSec": video.get("duration_sec"),
        },
        "clips": clips,
    }


# 로그인한 본인 매장/카메라를 가로지르는 하이라이트 클립 피드 — 프론트 대시보드가 주로 쓸 API.
@app.get("/clips", response_model=ClipsResponse)
def list_clips(
    limit: Optional[int] = Query(None),
    user_id: str = Depends(get_current_user_id),
) -> Dict[str, Any]:
    sb = require_supabase()
    lim = clamp_limit(limit, 20, 100)

    query = (
        sb.table("anomaly_events")
        .select("*, videos!inner(store_id, camera_location, recorded_started_at, user_id)")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(lim)
    )

    try:
        rows = query.execute().data or []
    except APIError as error:
        print(f"[backend] /clips query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error

    clips = [to_clip_dto(row, row["videos"]) for row in rows]
    return {"clips": clips}


def _fetch_own_clip_row(sb, clip_id: str, user_id: str) -> Dict[str, Any]:
    """clip 하나를 videos 조인해서 가져오고, 본인 소유가 아니면 404로 숨긴다."""
    try:
        row = (
            sb.table("anomaly_events")
            .select("*, videos!inner(store_id, camera_location, recorded_started_at, user_id)")
            .eq("id", clip_id)
            .maybe_single()
            .execute()
            .data
        )
    except APIError as error:
        print(f"[backend] /clips/:id query failed: {error}")
        raise HTTPException(status_code=500, detail="조회 실패") from error
    if not row or row["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="클립을 찾을 수 없습니다")
    return row


# 클립 상세 하나 (2d 팝업/2f 클립 상세용).
@app.get("/clips/{clip_id}", response_model=ClipResponse)
def get_clip(clip_id: str, user_id: str = Depends(get_current_user_id)) -> Dict[str, Any]:
    sb = require_supabase()
    row = _fetch_own_clip_row(sb, clip_id, user_id)
    return {"clip": to_clip_dto(row, row["videos"])}


# 확인/오탐 처리, 메모, 신고 여부 — PC/모바일 어느 쪽에서 호출해도 같은 행을 바꾸는
# 것뿐이라 "동기화"는 별도 로직 없이 이걸로 끝난다(둘 다 이 API로 같은 DB row를 본다).
@app.patch("/clips/{clip_id}", response_model=ClipResponse)
def update_clip(
    clip_id: str, body: ClipUpdateRequest, user_id: str = Depends(get_current_user_id)
) -> Dict[str, Any]:
    sb = require_supabase()
    _fetch_own_clip_row(sb, clip_id, user_id)  # 존재 + 소유 확인 (404 처리 포함)

    changes: Dict[str, Any] = {}
    if body.status is not None:
        changes["status"] = body.status
        if body.status in ("confirmed", "false_positive"):
            changes["confirmed_by"] = user_id
            changes["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        else:  # 'unconfirmed'로 되돌리면 확인 기록도 같이 지운다
            changes["confirmed_by"] = None
            changes["confirmed_at"] = None
    if body.note is not None:
        changes["note"] = body.note
    if body.reportedToPolice is not None:
        changes["reported_to_police"] = body.reportedToPolice

    if not changes:
        raise HTTPException(status_code=422, detail="변경할 필드가 없습니다")

    try:
        updated = (
            sb.table("anomaly_events")
            .update(changes)
            .eq("id", clip_id)
            .select("*, videos!inner(store_id, camera_location, recorded_started_at, user_id)")
            .execute()
            .data
        )
    except APIError as error:
        print(f"[backend] /clips/:id update failed: {error}")
        raise HTTPException(status_code=500, detail="수정 실패") from error
    if not updated:
        raise HTTPException(status_code=500, detail="수정 실패")

    row = updated[0]
    return {"clip": to_clip_dto(row, row["videos"])}
