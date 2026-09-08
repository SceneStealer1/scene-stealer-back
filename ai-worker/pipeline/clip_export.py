"""ffmpeg으로 이상행동 구간 클립 + 썸네일 추출."""

from __future__ import annotations

import subprocess
from pathlib import Path


def extract_clip(
    video_path: str, start_sec: float, end_sec: float, out_path: str | Path, pad_sec: float = 1.0
) -> None:
    """[start_sec, end_sec] 구간을 앞뒤로 pad_sec만큼 여유를 두고 잘라낸다.

    -ss를 -i 앞에 둬서(키프레임 단위 fast seek) 긴 원본 영상에서도 seek 지점까지
    전체를 디코딩하지 않고 바로 점프한다. 클립 앞뒤 pad_sec(기본 1초) 패딩이 있어서
    키프레임 단위 오차(보통 1~2초 이내)는 클립 내용에 실질적 영향이 없다.
    """

    start = max(0.0, start_sec - pad_sec)
    duration = (end_sec - start_sec) + 2 * pad_sec

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", video_path,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def extract_thumbnail(video_path: str, at_sec: float, out_path: str | Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{max(0.0, at_sec):.3f}",
        "-i", video_path,
        "-frames:v", "1",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
