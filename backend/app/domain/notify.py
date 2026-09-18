"""푸시 발송 판정 (요구사항 6.2 · 6.3 · 6.4).

"알림이 안 왔다"와 "한밤중에 사소한 알림으로 깨웠다"는 둘 다 신뢰를 깎는다.
그 경계를 전부 여기 모아 둔다 — 발송 경로 곳곳에 if 를 흩뿌리면 왜 안 왔는지
아무도 설명할 수 없게 된다. `PushDecision.reason` 이 그 설명이다.

위험 종류를 나누지 않으므로 판정의 축은 위험도 하나다 (domain/risk.py).
"""

from dataclasses import dataclass
from datetime import time
from typing import Optional

from .risk import RiskLevel

_RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class QuietHours:
    business_hours_high_only: bool
    sleep_start: Optional[time]
    sleep_end: Optional[time]
    sleep_high_only: bool
    override_dnd_for_high: bool


@dataclass(frozen=True)
class StoreHours:
    opens_at: Optional[time]
    closes_at: Optional[time]


@dataclass(frozen=True)
class PushDecision:
    deliver: bool
    sound: bool
    override_dnd: bool
    #: 억제된 규칙의 이름. 발송 로그에 남는다 — "알림이 안 온다"는 문의에
    #: 답할 수 있는 유일한 단서다.
    reason: str


def _is_within(now: time, start: Optional[time], end: Optional[time]) -> bool:
    """`now` 가 [start, end) 안인지. start > end 면 자정을 넘는 구간이다."""
    if start is None or end is None:
        return False
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # 23:00~07:00 같은 구간


def decide_push(
    risk: RiskLevel,
    now_local: time,
    min_risk: RiskLevel,
    quiet: QuietHours,
    store_hours: StoreHours,
) -> PushDecision:
    """이 이벤트를 지금 보낼지, 소리를 낼지, 방해금지를 뚫을지 정한다."""

    # 1) 사용자가 고른 위험도 미만은 보내지 않는다 (2g '알림 받을 위험도').
    if _RISK_ORDER[risk] < _RISK_ORDER.get(min_risk, 0):
        return PushDecision(False, False, False, "below_min_risk")

    # 2) 영업시간엔 '높음'만 — 사장님이 매장에 있고 눈으로 본다.
    if (
        quiet.business_hours_high_only
        and _is_within(now_local, store_hours.opens_at, store_hours.closes_at)
        and risk != "high"
    ):
        return PushDecision(False, False, False, "business_hours_high_only")

    # 3) 수면시간엔 높음만 소리. 나머지는 배지로 남기되 깨우지 않는다.
    in_sleep = _is_within(now_local, quiet.sleep_start, quiet.sleep_end)
    sound = not (in_sleep and quiet.sleep_high_only and risk != "high")

    override_dnd = risk == "high" and quiet.override_dnd_for_high

    return PushDecision(deliver=True, sound=sound, override_dnd=override_dnd, reason="delivered")
