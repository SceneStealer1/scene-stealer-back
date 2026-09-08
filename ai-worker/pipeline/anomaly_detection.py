"""스켈레톤 시퀀스 오토인코더 기반 이상행동 탐지.

접근 방식 (unsupervised, per-video):
  1. 각 person track의 정규화된 keypoint 시퀀스를 길이 W의 슬라이딩 윈도우로 자른다.
  2. 영상 내 모든 track의 모든 윈도우를 모아 작은 오토인코더를 이 영상에 한해 학습시킨다.
     - 가정: 한 영상 안에서 "정상" 동작(걷기, 서있기, 물건 집기 등)이 절대다수이고,
       이상행동(쓰러짐, 몸싸움, 배회 등 튀는 동작)은 소수 구간이다.
     - 따라서 오토인코더는 절대다수인 정상 패턴을 우선적으로 잘 재구성하도록 수렴하고,
       소수의 이질적인 동작 윈도우는 재구성 오차가 상대적으로 커진다.
  3. 윈도우별 재구성 오차(MSE)를 구하고, 오차 분포의 (평균 + k*표준편차)를 임계값으로
     그 이상인 윈도우를 이상행동 후보로 플래그한다.
  4. 같은 track에서 인접/겹치는 이상 윈도우를 하나의 구간(segment)으로 병합한다.

주의: 이 방식은 사람별 사전학습된 "정상 행동" 레퍼런스 모델이 없는 콜드스타트 데모에
적합한 근사치다. 실제 매장 환경에 적용할 때는 정상 영상들로 미리 학습시킨 모델을
불러와 재사용하는 편이 훨씬 정확하다 (이 모듈의 SkeletonAutoencoder는 그대로 두고
train_autoencoder 호출부만 "불러오기"로 바꾸면 됨).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from torch import nn

NUM_KEYPOINTS = 17
FEATURE_DIM = NUM_KEYPOINTS * 2


@dataclass
class AnomalySegment:
    track_id: int
    start_frame: int
    end_frame: int
    start_time_sec: float
    end_time_sec: float
    score: float
    threshold: float


class SkeletonAutoencoder(nn.Module):
    """윈도우(W프레임 x 34차원)를 통째로 flatten해 재구성하는 단순 MLP 오토인코더."""

    def __init__(self, window_frames: int, bottleneck: int = 32):
        super().__init__()
        input_dim = window_frames * FEATURE_DIM
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.ReLU(inplace=True),
            nn.Linear(128, bottleneck),
            nn.ReLU(inplace=True),
        )
        self.decoder = nn.Sequential(
            nn.Linear(bottleneck, 128),
            nn.ReLU(inplace=True),
            nn.Linear(128, input_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.encoder(x)
        return self.decoder(z)


def _make_windows(
    frame_indices: np.ndarray, seq: np.ndarray, window_frames: int, stride_frames: int
) -> list[tuple[int, int, np.ndarray]]:
    """(윈도우 시작 frame_idx, 윈도우 끝 frame_idx, flatten된 윈도우 벡터) 리스트.

    track의 keypoint 시퀀스가 연속 프레임이 아닐 수 있으므로(탐지 누락 등),
    frame_indices상 실제로 연속인 구간에서만 윈도우를 자른다.
    """

    windows = []
    n = len(frame_indices)
    if n < window_frames:
        return windows

    i = 0
    while i + window_frames <= n:
        idx_slice = frame_indices[i : i + window_frames]
        # 연속성 체크: 마지막-첫 프레임 인덱스 차이가 window_frames-1 이어야 gap이 없는 것.
        if idx_slice[-1] - idx_slice[0] == window_frames - 1:
            window_vec = seq[i : i + window_frames].reshape(-1)
            windows.append((int(idx_slice[0]), int(idx_slice[-1]), window_vec))
            i += stride_frames
        else:
            i += 1  # gap이 있으면 한 프레임씩 밀어서 연속 구간을 다시 탐색

    return windows


def train_autoencoder(
    window_vectors: np.ndarray, window_frames: int, epochs: int = 60, lr: float = 1e-3
) -> SkeletonAutoencoder:
    model = SkeletonAutoencoder(window_frames=window_frames)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    x = torch.from_numpy(window_vectors).float()
    model.train()
    for _ in range(epochs):
        optimizer.zero_grad()
        recon = model(x)
        loss = loss_fn(recon, x)
        loss.backward()
        optimizer.step()

    model.eval()
    return model


def detect_anomalies_for_video(
    normalized_tracks: dict[int, tuple[np.ndarray, np.ndarray]],
    fps: float,
    window_frames: int = 32,
    stride_frames: int = 8,
    threshold_std: float = 2.5,
    min_segment_frames: int = 16,
    epochs: int = 60,
) -> list[AnomalySegment]:
    """normalized_tracks: {track_id: (frame_indices, keypoints (T,17,2))}"""

    per_track_windows: dict[int, list[tuple[int, int, np.ndarray]]] = {}
    all_vectors: list[np.ndarray] = []

    for track_id, (frame_indices, seq) in normalized_tracks.items():
        windows = _make_windows(frame_indices, seq, window_frames, stride_frames)
        if not windows:
            continue
        per_track_windows[track_id] = windows
        all_vectors.extend(w[2] for w in windows)

    # 오토인코더를 학습하기엔 윈도우 수가 너무 적으면(짧은 영상 등) 탐지를 건너뛴다.
    if len(all_vectors) < 8:
        return []

    x = np.stack(all_vectors).astype(np.float32)
    model = train_autoencoder(x, window_frames=window_frames, epochs=epochs)

    with torch.no_grad():
        recon = model(torch.from_numpy(x).float()).numpy()
    errors = np.mean((recon - x) ** 2, axis=1)

    threshold = float(errors.mean() + threshold_std * errors.std())

    segments: list[AnomalySegment] = []
    cursor = 0
    for track_id, windows in per_track_windows.items():
        n_windows = len(windows)
        track_errors = errors[cursor : cursor + n_windows]
        cursor += n_windows

        flagged = track_errors > threshold
        segments.extend(
            _merge_flagged_windows(
                track_id, windows, flagged, track_errors, threshold, fps, min_segment_frames
            )
        )

    return segments


def _merge_flagged_windows(
    track_id: int,
    windows: list[tuple[int, int, np.ndarray]],
    flagged: np.ndarray,
    errors: np.ndarray,
    threshold: float,
    fps: float,
    min_segment_frames: int,
) -> list[AnomalySegment]:
    segments: list[AnomalySegment] = []
    start_frame = None
    end_frame = None
    max_score = 0.0

    def flush():
        if start_frame is None or end_frame is None:
            return
        if end_frame - start_frame + 1 < min_segment_frames:
            return
        segments.append(
            AnomalySegment(
                track_id=track_id,
                start_frame=start_frame,
                end_frame=end_frame,
                start_time_sec=start_frame / fps,
                end_time_sec=end_frame / fps,
                score=float(max_score),
                threshold=threshold,
            )
        )

    for (w_start, w_end, _), is_flagged, err in zip(windows, flagged, errors):
        if not is_flagged:
            flush()
            start_frame = end_frame = None
            max_score = 0.0
            continue

        if start_frame is None:
            start_frame = w_start
        end_frame = max(end_frame or w_end, w_end)
        max_score = max(max_score, float(err))

    flush()
    return segments
