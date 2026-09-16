"""
API 응답 모양(DTO). 필드명은 프론트가 그대로 쓰는 camelCase — Supabase 행(snake_case)은
clips.py/main.py 에서 변환해서 채운다. root README.md 의 ClipDto 설명과 1:1로 맞아야 한다.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel

VideoStatus = Literal["uploaded", "processing", "done", "failed"]


class VideoListItem(BaseModel):
    id: str
    userId: str
    storeId: str
    cameraLocation: Optional[str] = None
    status: VideoStatus
    progress: int
    recordedStartedAt: str
    recordedEndedAt: str
    durationSec: Optional[float] = None
    anomalyCount: int


class VideosResponse(BaseModel):
    videos: List[VideoListItem]


class VideoDetail(BaseModel):
    id: str
    userId: str
    storeId: str
    cameraLocation: Optional[str] = None
    status: VideoStatus
    progress: int
    recordedStartedAt: str
    recordedEndedAt: str
    durationSec: Optional[float] = None


class ClipDto(BaseModel):
    id: str
    videoId: str
    userId: str
    storeId: Optional[str] = None
    cameraLocation: Optional[str] = None
    startAt: str
    endAt: str
    startTimeSec: float
    endTimeSec: float
    anomalyScore: float
    threshold: float
    clipUrl: Optional[str] = None
    thumbnailUrl: Optional[str] = None
    createdAt: str


class VideoDetailResponse(BaseModel):
    video: VideoDetail
    clips: List[ClipDto]


class ClipsResponse(BaseModel):
    clips: List[ClipDto]


class ProfileDto(BaseModel):
    id: str
    storeId: Optional[str] = None
    storeName: Optional[str] = None
    contactName: Optional[str] = None
    phoneNumber: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
