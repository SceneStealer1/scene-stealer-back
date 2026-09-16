"""위험 종류·위험도의 규칙.

AI 게이트(docs/ai-gate-contract.md)가 붙기 전까지 events.kind 는 'unknown' 이고
events.risk 는 오토인코더 점수로 추정한다. events.risk 는 not null 이라 —
화면이 전부 위험도로 색을 칠하기 때문에 — 어떤 경로로도 값이 나와야 한다.
"""

from typing import Literal, Optional

RiskLevel = Literal["high", "medium", "low"]
RiskKind = Literal[
    "theft", "vandalism", "dine_and_dash", "underage_purchase",
    "loitering", "sleeping", "collapse", "unknown",
]

#: DB 의 risk_kind enum 과 1:1 이어야 한다 (supabase/schema.sql).
RISK_KINDS: frozenset[str] = frozenset(
    ("theft", "vandalism", "dine_and_dash", "underage_purchase",
     "loitering", "sleeping", "collapse", "unknown")
)

#: 종류별 기본 위험도 (요구사항 0절 — "종류별 기본값 있고 매장 설정으로 덮어씀").
#: AI 게이트는 이 값을 반환하면 되고, 확신이 낮으면 한 단계 내린다.
DEFAULT_RISK_BY_KIND: dict[str, RiskLevel] = {
    "collapse": "high",            # 응급
    "vandalism": "high",
    "theft": "high",
    "underage_purchase": "medium",
    "dine_and_dash": "medium",
    "loitering": "low",
    "sleeping": "low",
    "unknown": "medium",           # 모른다 ≠ 안전하다
}

# 점수/임계값 비율의 경계. 부동소수 비교라 아주 작은 여유를 둔다 —
# 0.9/0.6 같은 값이 1.4999999... 로 떨어져 경계에서 뒤집히는 걸 막는다.
_HIGH_RATIO = 1.5
_MEDIUM_RATIO = 1.2
_EPSILON = 1e-9


def default_risk_for_kind(kind: Optional[str]) -> RiskLevel:
    """종류의 기본 위험도. 모르는 종류는 medium — 놓치는 쪽보다 낫다."""
    return DEFAULT_RISK_BY_KIND.get(kind or "", "medium")


def derive_risk_from_score(score: Optional[float], threshold: Optional[float]) -> RiskLevel:
    """AI 게이트 미연결 시의 위험도 추정 (docs/ai-gate-contract.md 5절).

    오토인코더는 "그 영상 안에서 얼마나 튀는가"만 알려주므로, 임계값 대비
    몇 배인지로 등급을 나눈다. 절대 점수는 영상마다 스케일이 달라 쓸 수 없다.
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
