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


ClipStatus = Literal["unconfirmed", "confirmed", "false_positive"]


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
    riskLevel: Optional[str] = None
    status: ClipStatus = "unconfirmed"
    note: Optional[str] = None
    reportedToPolice: bool = False
    confirmedBy: Optional[str] = None
    confirmedAt: Optional[str] = None
    clipUrl: Optional[str] = None
    thumbnailUrl: Optional[str] = None
    createdAt: str


class ClipUpdateRequest(BaseModel):
    status: Optional[ClipStatus] = None
    note: Optional[str] = None
    reportedToPolice: Optional[bool] = None


class ClipResponse(BaseModel):
    clip: ClipDto


class VideoDetailResponse(BaseModel):
    video: VideoDetail
    clips: List[ClipDto]


class ClipsResponse(BaseModel):
    clips: List[ClipDto]


class ProfileDto(BaseModel):
    id: str
    contactName: Optional[str] = None
    phoneNumber: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str


# ---------------------------------------------------------------------------
# 멀티매장/디바이스/카메라 (docs/ux-backend-design.md). risk_type 분류가 빠진 것처럼
# 여기 DTO들도 그 부분(위험 종류별 알림 설정 등)은 만들지 않았다.
# ---------------------------------------------------------------------------


class StoreCreateRequest(BaseModel):
    name: str
    address: Optional[str] = None


class StoreUpdateRequest(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    operatingHoursStart: Optional[str] = None  # "HH:MM" — time 컬럼에 그대로 들어감
    operatingHoursEnd: Optional[str] = None
    quietHoursStart: Optional[str] = None
    quietHoursEnd: Optional[str] = None
    monitoringPaused: Optional[bool] = None
    segmentIntervalSec: Optional[int] = None  # 30 | 60 | 300 — DB check 제약이 최종 검증
    clipRetentionDays: Optional[int] = None
    pcPopupEnabled: Optional[bool] = None
    mobilePushEnabled: Optional[bool] = None


class StoreListItem(BaseModel):
    id: str
    name: str
    address: Optional[str] = None
    cameraCount: int
    deviceLabel: Optional[str] = None
    deviceConnected: bool = False
    hasIssue: bool = False  # 카메라 끊김 등 — status 엔드포인트에서 자세히


class StoresResponse(BaseModel):
    stores: List[StoreListItem]


class StoreDetail(BaseModel):
    id: str
    ownerUserId: str
    name: str
    address: Optional[str] = None
    operatingHoursStart: Optional[str] = None
    operatingHoursEnd: Optional[str] = None
    quietHoursStart: Optional[str] = None
    quietHoursEnd: Optional[str] = None
    monitoringPaused: bool
    segmentIntervalSec: int
    clipRetentionDays: int
    segmentRetentionDays: int
    cameraLimit: int
    pcPopupEnabled: bool
    mobilePushEnabled: bool
    createdAt: str


class StoreResponse(BaseModel):
    store: StoreDetail


class StoreStatus(BaseModel):
    storeId: str
    camerasConnected: int
    camerasTotal: int
    lastAnalysisAt: Optional[str] = None
    segmentIntervalSec: int
    monitoringPaused: bool


LocationTag = Literal["checkout", "entrance", "shelf", "dining", "storage", "other"]
CameraQuality = Literal["standard", "high"]
CameraStatus = Literal["connected", "reconnecting", "disconnected"]


class CameraCreateRequest(BaseModel):
    name: str
    locationTag: LocationTag = "other"
    quality: CameraQuality = "standard"
    deviceId: Optional[str] = None


class CameraUpdateRequest(BaseModel):
    name: Optional[str] = None
    locationTag: Optional[LocationTag] = None
    quality: Optional[CameraQuality] = None


class CameraReorderRequest(BaseModel):
    cameraIds: List[str]


class CameraHeartbeatRequest(BaseModel):
    status: CameraStatus


class CameraDto(BaseModel):
    id: str
    storeId: str
    deviceId: Optional[str] = None
    name: str
    locationTag: str
    quality: str
    sortOrder: int
    lastStatus: Optional[str] = None
    lastSeenAt: Optional[str] = None


class CamerasResponse(BaseModel):
    cameras: List[CameraDto]


class CameraResponse(BaseModel):
    camera: CameraDto


class DeviceDto(BaseModel):
    id: str
    storeId: str
    label: Optional[str] = None
    platform: str
    lastSeenAt: Optional[str] = None
    revoked: bool = False


class DeviceCreateRequest(BaseModel):
    label: Optional[str] = None
    platform: str = "electron"


class PairingClaimRequest(BaseModel):
    storeId: str


class DeviceCommandCreateRequest(BaseModel):
    command: Literal["restart"]


class DeviceCommandDto(BaseModel):
    id: str
    deviceId: str
    command: str
    status: str
    createdAt: str


class DeviceCommandsResponse(BaseModel):
    commands: List[DeviceCommandDto]


class DeviceResponse(BaseModel):
    device: DeviceDto


class DeviceIssuedDto(BaseModel):
    """디바이스 토큰 평문은 발급 시 딱 한 번만 이 모양으로 내려간다."""

    deviceId: str
    deviceToken: str
    storeId: str


class PairingStartResponse(BaseModel):
    pairingCode: str
    expiresAt: str


class PushTokenAck(BaseModel):
    ok: bool = True


class PushTokenCreateRequest(BaseModel):
    platform: Literal["ios", "android"]
    token: str
