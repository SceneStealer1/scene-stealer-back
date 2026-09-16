"""위험도 산출 규칙. AI 게이트가 없을 때 이벤트가 어떤 위험도로 뜨는지를 정한다."""

import pytest

from app.domain.risk import DEFAULT_RISK_BY_KIND, RISK_KINDS, default_risk_for_kind, derive_risk_from_score


class TestDeriveRiskFromScore:
    """게이트 미연결 폴백 — docs/ai-gate-contract.md 5절의 비율 규칙."""

    @pytest.mark.parametrize(
        "score, threshold, expected",
        [
            (1.50, 1.0, "high"),    # 경계값 정확히 1.5배
            (2.00, 1.0, "high"),
            (1.49, 1.0, "medium"),
            (1.20, 1.0, "medium"),  # 경계값 정확히 1.2배
            (1.19, 1.0, "low"),
            (0.50, 1.0, "low"),
            (0.90, 0.6, "high"),    # threshold 가 1이 아닌 경우도 비율로 본다
        ],
    )
    def test_ratio_buckets(self, score, threshold, expected):
        assert derive_risk_from_score(score, threshold) == expected

    def test_threshold_zero_does_not_divide_by_zero(self):
        # 오토인코더가 임계값을 0으로 낸 영상이 있을 수 있다. 죽지 말고 보수적으로.
        assert derive_risk_from_score(5.0, 0.0) == "medium"

    def test_missing_values_fall_back_to_medium(self):
        # 점수가 없으면 "모른다"는 뜻이지 "안전하다"는 뜻이 아니다.
        assert derive_risk_from_score(None, 1.0) == "medium"
        assert derive_risk_from_score(1.0, None) == "medium"

    def test_negative_score_is_low(self):
        assert derive_risk_from_score(-1.0, 1.0) == "low"


class TestDefaultRiskForKind:
    def test_collapse_and_violence_are_high(self):
        assert default_risk_for_kind("collapse") == "high"
        assert default_risk_for_kind("vandalism") == "high"
        assert default_risk_for_kind("theft") == "high"

    def test_loitering_and_sleeping_are_low(self):
        assert default_risk_for_kind("loitering") == "low"
        assert default_risk_for_kind("sleeping") == "low"

    def test_every_kind_has_a_default(self):
        # kind 를 추가하고 기본값을 빠뜨리면 이벤트가 만들어지지 않는다 (risk 는 not null).
        assert set(DEFAULT_RISK_BY_KIND) == RISK_KINDS

    def test_unknown_kind_falls_back_to_medium(self):
        assert default_risk_for_kind("unknown") == "medium"
        assert default_risk_for_kind("무언가이상한값") == "medium"
