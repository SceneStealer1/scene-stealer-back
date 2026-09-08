"""PersonTrack -> PoseLift 스타일 정규화 시퀀스 변환.

이상행동 탐지 오토인코더는 절대 픽셀 좌표가 아니라, bbox 기준으로 정규화되고
골반(hip) 중심으로 원점이 옮겨진 상대 좌표를 입력으로 쓴다. 이렇게 해야
카메라와의 거리/사람 위치에 관계없이 "동작 패턴" 자체를 학습할 수 있다.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

from .pose_extraction import PersonTrack

# COCO-17 keypoint 인덱스
L_HIP, R_HIP = 11, 12


def normalize_track(track: PersonTrack) -> tuple[np.ndarray, np.ndarray]:
    """track.frames -> (frame_indices, normalized_keypoints)

    normalized_keypoints shape: (T, 17, 2), 각 프레임마다
    - 골반 중점을 원점으로 이동
    - bbox 대각선 길이로 스케일 정규화
    """

    frame_indices = np.array([f.frame_idx for f in track.frames], dtype=np.int64)
    seq = np.zeros((len(track.frames), 17, 2), dtype=np.float32)

    for t, f in enumerate(track.frames):
        kpts = f.keypoints_xy.copy()
        hip_center = (kpts[L_HIP] + kpts[R_HIP]) / 2.0

        x1, y1, x2, y2 = f.bbox_xyxy
        scale = max(np.hypot(x2 - x1, y2 - y1), 1e-6)

        seq[t] = (kpts - hip_center) / scale

    return frame_indices, seq


def track_to_poselift_dict(track: PersonTrack, video_id: str) -> dict:
    frame_indices, seq = normalize_track(track)
    return {
        "video_id": video_id,
        "track_id": track.track_id,
        "frame_indices": frame_indices,
        "keypoints": seq,  # (T, 17, 2) normalized
        "keypoints_conf": np.stack([f.keypoints_conf for f in track.frames]),
        "bbox_xyxy": np.stack([f.bbox_xyxy for f in track.frames]),
    }


def save_poselift_pkl(tracks: dict[int, PersonTrack], video_id: str, out_path: str | Path) -> None:
    """PoseLift 포맷 pkl로 저장 (선택 사항 — 디버깅/재사용을 위한 중간 산출물)."""

    payload = {tid: track_to_poselift_dict(tr, video_id) for tid, tr in tracks.items()}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        pickle.dump(payload, fh)
