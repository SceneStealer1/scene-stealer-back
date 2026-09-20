"""위험도 규칙.

위험 종류(절도·배회 …) 분류는 하지 않는다 — AI 는 지금 파이프라인 그대로
"평소와 다른 움직임" 구간과 그 점수만 준다 (ai-worker, anomaly_events).
events.risk 는 그 점수로 정한다. 화면이 전부 위험도로 색을 칠하고 알림도
위험도로 거르기 때문에 not null 이다.
"""

from typing import Literal, Optional

RiskLevel = Literal["high", "medium", "low"]

RISK_LEVELS: tuple[RiskLevel, ...] = ("low", "medium", "high")

# 점수/임계값 비율의 경계. 부동소수 비교라 아주 작은 여유를 둔다 —
# 0.9/0.6 같은 값이 1.4999999... 로 떨어져 경계에서 뒤집히는 걸 막는다.
# 데모용으로 낮춰둔 값 — anomaly_events 는 이미 ratio > 1 인 구간만 남기므로,
# 감지된 구간이 거의 다 medium 이상으로 뜨게 한다. 실사용 매장에 붙일 때는
# 다시 1.5/1.2 근처로 올려야 한다.
_HIGH_RATIO = 1.15
_MEDIUM_RATIO = 1.02
_EPSILON = 1e-9


def derive_risk_from_score(score: Optional[float], threshold: Optional[float]) -> RiskLevel:
    """이상 점수로 위험도를 정한다.

    오토인코더는 "그 영상 안에서 얼마나 튀는가"만 알려주므로, 임계값 대비
    몇 배인지로 등급을 나눈다. 절대 점수는 영상마다 스케일이 달라 쓸 수 없다.
    anomaly_events 는 임계값을 넘은 구간만 남기므로 비율은 보통 1 이상이다.
    """
    if score is None or threshold is None:
        return "medium"
    if threshold <= 0:
        # 임계값 0 = 분포가 퇴화한 영상. 비율을 낼 수 없으니 판단을 보류한다.
        return "medium"

    ratio = score / threshold
    if ratio >= _HIGH_RATIO - _EPSILON:
        return "high"
    if ratio >= _MEDIUM_RATIO - _EPSILON:
        return "medium"
    return "low"
