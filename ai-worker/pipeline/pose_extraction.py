"""YOLO11-pose + ByteTrack 기반 사람 포즈 추출.

영상의 각 프레임에서 사람을 탐지하고(YOLO11-pose), ByteTrack으로 프레임 간
동일 인물에 person_id(track_id)를 부여한다. person당 프레임별 COCO-17
keypoint 좌표/신뢰도 시퀀스를 만든다 (PoseLift 스타일 트랙 포맷의 입력).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from ultralytics import YOLO

# 카메라가 고정된 CCTV/매장 영상을 가정하므로 카메라 움직임 보정이 필요 없는
# 가벼운 ByteTrack을 기본 트래커로 쓴다 (움직이는 카메라라면 botsort.yaml로 교체).
DEFAULT_TRACKER = "bytetrack.yaml"


@dataclass
class VideoMeta:
    fps: float
    frame_count: int
    width: int
    height: int


@dataclass
class TrackFrame:
    frame_idx: int
    keypoints_xy: np.ndarray  # (17, 2) pixel 좌표
    keypoints_conf: np.ndarray  # (17,)
    bbox_xyxy: np.ndarray  # (4,)


@dataclass
class PersonTrack:
    track_id: int
    frames: list[TrackFrame] = field(default_factory=list)


def read_video_meta(video_path: str) -> VideoMeta:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {video_path}")
    meta = VideoMeta(
        fps=cap.get(cv2.CAP_PROP_FPS) or 25.0,
        frame_count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )
    cap.release()
    return meta


def extract_pose_tracks(
    video_path: str,
    model_name: str = "yolo11n-pose.pt",
    tracker: str = DEFAULT_TRACKER,
    conf: float = 0.3,
    frame_count: int = 0,
    on_frame=None,
) -> dict[int, PersonTrack]:
    """영상 전체를 순회하며 person_id별 포즈 시퀀스를 만든다.

    on_frame: (frame_idx, frame_count) -> None. 프레임마다 호출되는 진행률 콜백.
    """

    model = YOLO(model_name)
    results = model.track(
        source=video_path,
        stream=True,
        persist=True,
        tracker=tracker,
        conf=conf,
        verbose=False,
    )

    tracks: dict[int, PersonTrack] = {}

    for frame_idx, r in enumerate(results):
        if on_frame is not None:
            on_frame(frame_idx, frame_count)
        if r.boxes is None or r.boxes.id is None or r.keypoints is None:
            continue

        ids = r.boxes.id.cpu().numpy().astype(int)
        boxes = r.boxes.xyxy.cpu().numpy()
        kpts_xy = r.keypoints.xy.cpu().numpy()  # (N, 17, 2)
        kpts_conf = (
            r.keypoints.conf.cpu().numpy()
            if r.keypoints.conf is not None
            else np.ones(kpts_xy.shape[:2], dtype=np.float32)
        )

        for i, track_id in enumerate(ids):
            track_id = int(track_id)
            track = tracks.setdefault(track_id, PersonTrack(track_id=track_id))
            track.frames.append(
                TrackFrame(
                    frame_idx=frame_idx,
                    keypoints_xy=kpts_xy[i],
                    keypoints_conf=kpts_conf[i],
                    bbox_xyxy=boxes[i],
                )
            )

    return tracks
