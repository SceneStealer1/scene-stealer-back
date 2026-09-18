"""위험도 산출 규칙. 이상 구간이 어떤 위험도의 이벤트로 뜨는지를 정한다."""

import pytest

from app.domain.risk import derive_risk_from_score


class TestDeriveRiskFromScore:
    """이상 점수 ÷ 임계값 비율로 나눈다 (1.5배 이상 높음, 1.2배 이상 보통)."""

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
