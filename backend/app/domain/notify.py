"""푸시 발송 판정 (요구사항 6.2 · 6.3 · 6.4).

"알림이 안 왔다"와 "한밤중에 배회 알림으로 깨웠다"는 둘 다 신뢰를 깎는다.
그 경계를 전부 여기 모아 둔다 — 발송 경로 곳곳에 if 를 흩뿌리면 왜 안 왔는지
아무도 설명할 수 없게 된다. `PushDecision.reason` 이 그 설명이다.
"""

from dataclasses import dataclass
from datetime import time
from typing import Literal, Mapping, Optional

from .risk import RiskLevel

Sensitivity = Literal["low", "medium", "high"]

#: 응급. 설정으로 끌 수 없고, 방해금지도 뚫고, 수면시간에도 소리를 낸다.
EMERGENCY_KIND = "collapse"

_RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

#: 민감도가 정하는 "이 위험도 미만은 안 보냄" 바닥값.
#:
#: 낮음만 한 칸 올라가고 보통/높음은 같다 — 위험도가 3단계뿐이라 더 잘게 나눌
#: 게 없다. 민감도의 나머지 효과(AI 가 무엇을 이벤트로 볼지)는 게이트 쪽 몫이다.
_MIN_RISK_BY_SENSITIVITY: dict[str, str] = {
    "high": "low",
    "medium": "low",
    "low": "medium",
}


@dataclass(frozen=True)
class KindSetting:
    enabled: bool
    sensitivity: Sensitivity


@dataclass(frozen=True)
class QuietHours:
    business_hours_high_only: bool
    sleep_start: Optional[time]
    sleep_end: Optional[time]
    sleep_emergency_only: bool
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
    kind: str,
    risk: RiskLevel,
    now_local: time,
    settings: Mapping[str, KindSetting],
    quiet: QuietHours,
    store_hours: StoreHours,
) -> PushDecision:
    """이 이벤트를 지금 보낼지, 소리를 낼지, 방해금지를 뚫을지 정한다."""

    # 1) 응급은 모든 규칙보다 먼저다. 설정이 꺼져 있어도, 새벽이어도 보낸다.
    #    DB 제약(collapse_always_on)으로도 막지만, 이 판정이 설정보다 나중에
    #    오는 경로라 여기서도 막는다.
    if kind == EMERGENCY_KIND:
        return PushDecision(deliver=True, sound=True, override_dnd=True, reason="emergency")

    setting = settings.get(kind)
    if setting is not None and not setting.enabled:
        return PushDecision(False, False, False, "kind_disabled")

    sensitivity = setting.sensitivity if setting else "medium"
    floor = _MIN_RISK_BY_SENSITIVITY.get(sensitivity, "low")
    if _RISK_ORDER[risk] < _RISK_ORDER[floor]:
        return PushDecision(False, False, False, "below_sensitivity_floor")

    # 2) 영업시간엔 '높음'만 — 사장님이 매장에 있고 눈으로 본다.
    if (
        quiet.business_hours_high_only
        and _is_within(now_local, store_hours.opens_at, store_hours.closes_at)
        and risk != "high"
    ):
        return PushDecision(False, False, False, "business_hours_high_only")

    # 3) 수면시간엔 응급·높음만 소리. 나머지는 배지로 남기되 깨우지 않는다.
    in_sleep = _is_within(now_local, quiet.sleep_start, quiet.sleep_end)
    sound = not (in_sleep and quiet.sleep_emergency_only and risk != "high")

    override_dnd = risk == "high" and quiet.override_dnd_for_high

    return PushDecision(deliver=True, sound=sound, override_dnd=override_dnd, reason="delivered")
