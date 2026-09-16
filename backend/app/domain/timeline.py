"""하루 타임라인의 '영상 없음' 구간 계산 (요구사항 4.4).

2e 타임라인은 카메라별 행에 이벤트를 색으로 찍고, 영상이 아예 없는 구간은
점선으로 그린다. "그 시간엔 아무 일도 없었다"와 "그 시간은 못 봤다"는 전혀
다른 이야기고, 후자를 숨기면 사장님이 시스템을 잘못 믿게 된다.
"""

from datetime import datetime
from typing import Iterable

Interval = tuple[datetime, datetime]

# 조각 경계는 벽시계에 정렬되지만(alignToClock) 인코딩 오차로 밀리초~1초 단위로
# 어긋난다. 그걸 공백이라 부르면 타임라인이 점선으로 뒤덮인다.
DEFAULT_TOLERANCE_SEC = 2

# 이보다 짧은 공백은 화면에서 픽셀 하나다. 그릴 가치가 없다.
DEFAULT_MIN_GAP_SEC = 0


def compute_gaps(
    covered: Iterable[Interval],
    window_start: datetime,
    window_end: datetime,
    tolerance_sec: float = DEFAULT_TOLERANCE_SEC,
    min_gap_sec: float = DEFAULT_MIN_GAP_SEC,
) -> list[Interval]:
    """`covered`(조각이 있는 구간들)를 뒤집어 `window` 안의 빈 구간을 낸다.

    입력은 정렬돼 있지 않아도 되고 겹쳐도 된다 — 카메라가 재연결되면서 경계가
    겹치는 조각이 올라오는 일이 실제로 있다.
    """
    if window_start >= window_end:
        return []

    clipped = sorted(
        (max(start, window_start), min(end, window_end))
        for start, end in covered
        if min(end, window_end) > max(start, window_start)
    )

    merged: list[Interval] = []
    for start, end in clipped:
        if merged and (start - merged[-1][1]).total_seconds() <= tolerance_sec:
            # 이어진 것으로 본다. 앞 구간이 더 길 수도 있어 max 로 늘린다.
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    gaps: list[Interval] = []
    cursor = window_start
    for start, end in merged:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < window_end:
        gaps.append((cursor, window_end))

    return [g for g in gaps if (g[1] - g[0]).total_seconds() >= min_gap_sec]
