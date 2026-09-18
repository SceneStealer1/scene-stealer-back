"""푸시 발송 판정 (요구사항 6.2 · 6.3 · 6.4).

"알림이 안 왔다"와 "한밤중에 사소한 알림으로 깨웠다"는 둘 다 신뢰를 깎는다.
그 경계가 전부 이 모듈에 있다.
"""

from datetime import time

import pytest

from app.domain.notify import QuietHours, StoreHours, decide_push

OPEN_9_TO_22 = StoreHours(opens_at=time(9, 0), closes_at=time(22, 0))
SLEEP_23_TO_7 = QuietHours(
    business_hours_high_only=True,
    sleep_start=time(23, 0),
    sleep_end=time(7, 0),
    sleep_high_only=True,
    override_dnd_for_high=True,
)
NO_QUIET = QuietHours(
    business_hours_high_only=False, sleep_start=None, sleep_end=None,
    sleep_high_only=True, override_dnd_for_high=True,
)


def decide(risk, at, min_risk="low", quiet=SLEEP_23_TO_7, hours=OPEN_9_TO_22):
    return decide_push(risk=risk, now_local=at, min_risk=min_risk, quiet=quiet, store_hours=hours)


class TestMinRisk:
    @pytest.mark.parametrize("min_risk, risk, expected", [
        ("low", "low", True),         # 기본값 — 전부 받는다
        ("medium", "low", False),
        ("medium", "medium", True),
        ("medium", "high", True),
        ("high", "medium", False),
        ("high", "high", True),
    ])
    def test_min_risk_sets_the_floor(self, min_risk, risk, expected):
        assert decide(risk, time(14, 0), min_risk=min_risk, quiet=NO_QUIET).deliver is expected


class TestBusinessHours:
    def test_medium_is_suppressed_during_business_hours(self):
        """영업시간엔 '높음'만 — 사장님이 매장에 있고 눈으로 본다."""
        assert decide("medium", time(14, 0)).deliver is False

    def test_high_is_delivered_during_business_hours(self):
        assert decide("high", time(14, 0)).deliver is True

    def test_medium_is_delivered_outside_business_hours(self):
        assert decide("medium", time(22, 30)).deliver is True

    def test_business_hours_filter_can_be_turned_off(self):
        assert decide("medium", time(14, 0), quiet=NO_QUIET).deliver is True

    def test_store_without_hours_skips_the_filter(self):
        hours = StoreHours(opens_at=None, closes_at=None)
        assert decide("medium", time(14, 0), hours=hours).deliver is True

    def test_overnight_business_hours(self):
        """24시 무인매장이 22:00~06:00 영업이면 자정을 넘는다."""
        hours = StoreHours(opens_at=time(22, 0), closes_at=time(6, 0))
        assert decide("medium", time(2, 0), hours=hours).deliver is False
        assert decide("medium", time(12, 0), hours=hours).deliver is True


class TestSleepHours:
    QUIET = QuietHours(
        business_hours_high_only=False, sleep_start=time(23, 0), sleep_end=time(7, 0),
        sleep_high_only=True, override_dnd_for_high=True,
    )

    def test_low_risk_is_silent_during_sleep(self):
        """수면시간엔 높음만 소리. 나머지는 배지로 남기되 깨우지 않는다."""
        decision = decide("low", time(3, 0), quiet=self.QUIET)
        assert decision.deliver is True
        assert decision.sound is False

    def test_high_risk_makes_sound_during_sleep(self):
        assert decide("high", time(3, 0), quiet=self.QUIET).sound is True

    def test_sleep_window_crossing_midnight_covers_both_sides(self):
        assert decide("low", time(23, 30), quiet=self.QUIET).sound is False
        assert decide("low", time(6, 30), quiet=self.QUIET).sound is False
        assert decide("low", time(8, 0), quiet=self.QUIET).sound is True

    def test_sound_is_on_outside_sleep_hours(self):
        assert decide("low", time(15, 0), quiet=self.QUIET).sound is True


class TestDoNotDisturb:
    def test_high_overrides_dnd_when_configured(self):
        assert decide("high", time(3, 0)).override_dnd is True

    def test_low_never_overrides_dnd(self):
        assert decide("low", time(3, 0)).override_dnd is False

    def test_override_can_be_disabled(self):
        quiet = QuietHours(
            business_hours_high_only=True, sleep_start=time(23, 0), sleep_end=time(7, 0),
            sleep_high_only=True, override_dnd_for_high=False,
        )
        assert decide("high", time(3, 0), quiet=quiet).override_dnd is False


class TestReason:
    """왜 안 보냈는지 로그에 남아야 한다 — '알림이 안 온다'는 문의의 유일한 단서다."""

    def test_reason_names_the_rule_that_suppressed(self):
        assert decide("low", time(3, 0), min_risk="medium").reason == "below_min_risk"
        assert decide("medium", time(14, 0)).reason == "business_hours_high_only"

    def test_delivered_decision_has_a_reason_too(self):
        assert decide("high", time(14, 0)).reason == "delivered"
