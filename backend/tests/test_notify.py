"""푸시 발송 판정 (요구사항 6.2 · 6.3 · 6.4).

"알림이 안 왔다"와 "한밤중에 배회 알림으로 깨웠다"는 둘 다 신뢰를 깎는다.
그 경계가 전부 이 모듈에 있다.
"""

from datetime import time

import pytest

from app.domain.notify import KindSetting, QuietHours, StoreHours, decide_push

ALL_ON = {
    kind: KindSetting(enabled=True, sensitivity="medium")
    for kind in ("theft", "vandalism", "dine_and_dash", "underage_purchase",
                 "loitering", "sleeping", "collapse", "unknown")
}

OPEN_9_TO_22 = StoreHours(opens_at=time(9, 0), closes_at=time(22, 0))
SLEEP_23_TO_7 = QuietHours(
    business_hours_high_only=True,
    sleep_start=time(23, 0),
    sleep_end=time(7, 0),
    sleep_emergency_only=True,
    override_dnd_for_high=True,
)


def decide(kind, risk, at, settings=None, quiet=SLEEP_23_TO_7, hours=OPEN_9_TO_22):
    return decide_push(
        kind=kind, risk=risk, now_local=at,
        settings=settings or ALL_ON, quiet=quiet, store_hours=hours,
    )


class TestKindToggle:
    def test_disabled_kind_is_not_delivered(self):
        settings = {**ALL_ON, "loitering": KindSetting(enabled=False, sensitivity="medium")}
        assert decide("loitering", "low", time(14, 0), settings).deliver is False

    def test_collapse_is_delivered_even_if_settings_say_disabled(self):
        """쓰러짐은 끌 수 없다. DB 제약으로도 막지만 여기서도 막는다 —
        이 판정이 설정보다 나중에 오는 안전망이라서."""
        settings = {**ALL_ON, "collapse": KindSetting(enabled=False, sensitivity="medium")}
        decision = decide("collapse", "high", time(3, 0), settings)
        assert decision.deliver is True
        assert decision.sound is True

    def test_unknown_kind_is_delivered(self):
        # AI 게이트 미연결이라고 알림을 막으면 사장님은 아무것도 못 본다.
        assert decide("unknown", "high", time(14, 0)).deliver is True


class TestBusinessHours:
    def test_medium_is_suppressed_during_business_hours(self):
        """영업시간엔 '높음'만 — 사장님이 매장에 있고 눈으로 본다."""
        assert decide("theft", "medium", time(14, 0)).deliver is False

    def test_high_is_delivered_during_business_hours(self):
        assert decide("theft", "high", time(14, 0)).deliver is True

    def test_medium_is_delivered_outside_business_hours(self):
        assert decide("theft", "medium", time(22, 30)).deliver is True

    def test_business_hours_filter_can_be_turned_off(self):
        quiet = QuietHours(
            business_hours_high_only=False, sleep_start=None, sleep_end=None,
            sleep_emergency_only=True, override_dnd_for_high=True,
        )
        assert decide("theft", "medium", time(14, 0), quiet=quiet).deliver is True

    def test_store_without_hours_skips_the_filter(self):
        hours = StoreHours(opens_at=None, closes_at=None)
        assert decide("theft", "medium", time(14, 0), hours=hours).deliver is True

    def test_overnight_business_hours(self):
        """24시 무인매장이 22:00~06:00 영업이면 자정을 넘는다."""
        hours = StoreHours(opens_at=time(22, 0), closes_at=time(6, 0))
        assert decide("theft", "medium", time(2, 0), hours=hours).deliver is False
        assert decide("theft", "medium", time(12, 0), hours=hours).deliver is True


class TestSleepHours:
    def test_low_risk_is_silent_during_sleep(self):
        """수면시간엔 응급·높음만 소리. 나머지는 배지로 남기되 깨우지 않는다."""
        quiet = QuietHours(
            business_hours_high_only=False, sleep_start=time(23, 0), sleep_end=time(7, 0),
            sleep_emergency_only=True, override_dnd_for_high=True,
        )
        decision = decide("loitering", "low", time(3, 0), quiet=quiet)
        assert decision.deliver is True
        assert decision.sound is False

    def test_high_risk_makes_sound_during_sleep(self):
        quiet = QuietHours(
            business_hours_high_only=False, sleep_start=time(23, 0), sleep_end=time(7, 0),
            sleep_emergency_only=True, override_dnd_for_high=True,
        )
        assert decide("theft", "high", time(3, 0), quiet=quiet).sound is True

    def test_sleep_window_crossing_midnight_covers_both_sides(self):
        quiet = QuietHours(
            business_hours_high_only=False, sleep_start=time(23, 0), sleep_end=time(7, 0),
            sleep_emergency_only=True, override_dnd_for_high=True,
        )
        assert decide("loitering", "low", time(23, 30), quiet=quiet).sound is False
        assert decide("loitering", "low", time(6, 30), quiet=quiet).sound is False
        assert decide("loitering", "low", time(8, 0), quiet=quiet).sound is True

    def test_sound_is_on_outside_sleep_hours(self):
        quiet = QuietHours(
            business_hours_high_only=False, sleep_start=time(23, 0), sleep_end=time(7, 0),
            sleep_emergency_only=True, override_dnd_for_high=True,
        )
        assert decide("loitering", "low", time(15, 0), quiet=quiet).sound is True


class TestDoNotDisturb:
    def test_high_overrides_dnd_when_configured(self):
        assert decide("theft", "high", time(3, 0)).override_dnd is True

    def test_low_never_overrides_dnd(self):
        assert decide("loitering", "low", time(3, 0)).override_dnd is False

    def test_collapse_always_overrides_dnd(self):
        """응급은 설정과 무관하게 뚫는다."""
        quiet = QuietHours(
            business_hours_high_only=True, sleep_start=time(23, 0), sleep_end=time(7, 0),
            sleep_emergency_only=True, override_dnd_for_high=False,
        )
        assert decide("collapse", "high", time(3, 0), quiet=quiet).override_dnd is True

    def test_override_can_be_disabled_for_non_emergency_high(self):
        quiet = QuietHours(
            business_hours_high_only=True, sleep_start=time(23, 0), sleep_end=time(7, 0),
            sleep_emergency_only=True, override_dnd_for_high=False,
        )
        assert decide("theft", "high", time(3, 0), quiet=quiet).override_dnd is False


class TestReason:
    """왜 안 보냈는지 로그에 남아야 한다 — '알림이 안 온다'는 문의의 유일한 단서다."""

    def test_reason_names_the_rule_that_suppressed(self):
        settings = {**ALL_ON, "loitering": KindSetting(enabled=False, sensitivity="medium")}
        assert decide("loitering", "low", time(14, 0), settings).reason == "kind_disabled"
        assert decide("theft", "medium", time(14, 0)).reason == "business_hours_high_only"

    def test_delivered_decision_has_a_reason_too(self):
        assert decide("theft", "high", time(14, 0)).reason == "delivered"


class TestSensitivity:
    @pytest.mark.parametrize("sensitivity, risk, expected", [
        ("high", "low", True),      # 민감도 높음 = 낮은 위험도도 받는다
        ("medium", "low", True),
        ("low", "low", False),      # 민감도 낮음 = 낮은 위험도는 거른다
        ("low", "medium", True),
        ("low", "high", True),
    ])
    def test_sensitivity_sets_the_floor(self, sensitivity, risk, expected):
        settings = {**ALL_ON, "theft": KindSetting(enabled=True, sensitivity=sensitivity)}
        quiet = QuietHours(
            business_hours_high_only=False, sleep_start=None, sleep_end=None,
            sleep_emergency_only=True, override_dnd_for_high=True,
        )
        assert decide("theft", risk, time(14, 0), settings, quiet=quiet).deliver is expected

    def test_collapse_ignores_sensitivity_floor(self):
        settings = {**ALL_ON, "collapse": KindSetting(enabled=True, sensitivity="low")}
        quiet = QuietHours(
            business_hours_high_only=False, sleep_start=None, sleep_end=None,
            sleep_emergency_only=True, override_dnd_for_high=True,
        )
        assert decide("collapse", "low", time(14, 0), settings, quiet=quiet).deliver is True
