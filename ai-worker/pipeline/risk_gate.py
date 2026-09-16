"""AI 게이트 — 위험 종류·위험도·한글 설명·인상착의를 채우는 자리.

**이 파일이 게이트를 붙이는 접점이다.** 계약은 `docs/ai-gate-contract.md`.

오토인코더(anomaly_detection.py)는 "평소와 다르다"는 점수만 낸다. 절도인지
쓰러짐인지 청소부가 이상하게 움직인 건지는 모른다. 그런데 제품의 모든 화면이
종류·위험도를 키로 쓰고, 알림 설정(요구사항 6.2)은 아예 종류별 on/off 다.

게이트를 만드는 작업자는 `classify()` 의 시그니처만 지키면 된다 — 프로세스 안
라이브러리로 구현하든, RISK_GATE_URL 로 별도 서비스를 띄우든 상관없다.

**게이트가 없어도 파이프라인은 끝까지 돈다.** 미설정이면 kind='unknown' +
점수 기반 위험도로 채우고 status='skipped' 를 남긴다. 나중에 게이트가 붙으면
그 행들만 골라 재분석할 수 있다 (계약 6절).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

RiskLevel = Literal["high", "medium", "low"]

#: DB 의 risk_kind enum 과 1:1. 여기 없는 값을 events 에 넣으면 insert 가 깨진다.
#: 새 종류가 필요하면 supabase/schema.sql 의 enum 부터 고쳐야 한다.
RISK_KINDS: frozenset[str] = frozenset(
    ("theft", "vandalism", "dine_and_dash", "underage_purchase",
     "loitering", "sleeping", "collapse", "unknown")
)

#: 종류별 기본 위험도. backend/app/domain/risk.py 의 DEFAULT_RISK_BY_KIND 와
#: 같은 표다 — 두 서비스가 별도 컨테이너라 import 할 수 없어 사본을 둔다.
#: 한쪽을 고치면 다른 쪽도 고칠 것.
DEFAULT_RISK_BY_KIND: dict[str, RiskLevel] = {
    "collapse": "high",
    "vandalism": "high",
    "theft": "high",
    "underage_purchase": "medium",
    "dine_and_dash": "medium",
    "loitering": "low",
    "sleeping": "low",
    "unknown": "medium",
}

RISK_LEVELS: frozenset[str] = frozenset(("high", "medium", "low"))

RISK_GATE_URL = os.environ.get("RISK_GATE_URL") or None
RISK_GATE_TOKEN = os.environ.get("RISK_GATE_TOKEN") or None

# 5분 조각 하나에서 이벤트가 여러 개 나올 수 있고 워커는 영상을 하나씩 처리하는
# 직렬 루프다 (worker.py:main). 게이트가 느리면 큐 전체가 밀린다.
RISK_GATE_TIMEOUT_SEC = float(os.environ.get("RISK_GATE_TIMEOUT_SEC", "30"))

_HIGH_RATIO = 1.5
_MEDIUM_RATIO = 1.2
_EPSILON = 1e-9


@dataclass(frozen=True)
class RiskGateRequest:
    clip_path: Path          # 이상 구간 ±5초 패딩된 하이라이트 mp4
    thumbnail_path: Path     # 구간 중앙 프레임 jpg
    started_at: str          # ISO8601 UTC — 절대 촬영 시각
    ended_at: str
    camera_name: str         # "계산대"
    location_tag: Optional[str]  # checkout|entrance|shelf|dining|storage|other
    anomaly_score: float
    threshold: float


@dataclass(frozen=True)
class RiskGateVerdict:
    kind: str
    risk: RiskLevel
    description: Optional[str]
    appearance: Optional[str]
    bounding_boxes: Optional[list[dict[str, Any]]]
    status: Literal["done", "failed", "skipped"]
    error: Optional[str] = None

    def to_event_columns(self) -> dict[str, Any]:
        """events 테이블에 그대로 펼쳐 넣을 수 있는 모양."""
        return {
            "kind": self.kind,
            "risk": self.risk,
            "description": self.description,
            "appearance": self.appearance,
            "bounding_boxes": self.bounding_boxes,
            "ai_gate_status": self.status,
            "ai_gate_error": self.error,
        }


def derive_risk_from_score(score: Optional[float], threshold: Optional[float]) -> RiskLevel:
    """게이트 미연결 시의 위험도 추정 (계약 5절).

    절대 점수는 영상마다 스케일이 달라 쓸 수 없어서 임계값 대비 배율로 나눈다.
    backend/app/domain/risk.py 의 같은 이름 함수와 동일한 규칙이다.
    """
    if score is None or threshold is None or threshold <= 0:
        return "medium"  # 모른다 ≠ 안전하다
    ratio = score / threshold
    if ratio >= _HIGH_RATIO - _EPSILON:
        return "high"
    if ratio >= _MEDIUM_RATIO - _EPSILON:
        return "medium"
    return "low"


def _fallback(request: RiskGateRequest, status: str = "skipped",
              error: Optional[str] = None) -> RiskGateVerdict:
    return RiskGateVerdict(
        kind="unknown",
        risk=derive_risk_from_score(request.anomaly_score, request.threshold),
        description=None, appearance=None, bounding_boxes=None,
        status=status, error=error,
    )


def normalize_verdict(payload: dict[str, Any], request: RiskGateRequest) -> RiskGateVerdict:
    """게이트 응답을 DB 가 받아들이는 모양으로 좁힌다.

    enum 밖의 값은 거부하지 말고 **강등**한다 — 게이트가 'shoplifting' 을
    보냈다고 이벤트를 통째로 버리면 사장님은 영상조차 못 본다. 원본 값은
    error 에 남겨서 게이트 쪽에서 고칠 수 있게 한다.
    """
    kind = payload.get("kind")
    error: Optional[str] = None
    if kind not in RISK_KINDS:
        error = f"게이트가 알 수 없는 kind 를 반환했습니다: {kind!r}"
        kind = "unknown"

    risk = payload.get("risk")
    if risk not in RISK_LEVELS:
        risk = DEFAULT_RISK_BY_KIND.get(kind, "medium")

    boxes = payload.get("boundingBoxes") or payload.get("bounding_boxes")
    if boxes is not None and not isinstance(boxes, list):
        boxes = None

    return RiskGateVerdict(
        kind=kind,
        risk=risk,
        description=(payload.get("description") or None),
        appearance=(payload.get("appearance") or None),
        bounding_boxes=boxes,
        status="failed" if error else "done",
        error=error,
    )


def classify(request: RiskGateRequest) -> RiskGateVerdict:
    """이 구간이 무엇인지 판정한다.

    **게이트 작업자가 갈아끼울 함수.** 지금 구현은 RISK_GATE_URL 이 있으면
    HTTP 로 넘기고, 없으면 점수 기반 폴백이다.

    절대 예외를 던지지 않는다 — 여기서 터지면 이벤트가 만들어지지 않고,
    클립은 이미 있는데 사장님 화면에는 아무것도 안 뜬다.
    """
    if not RISK_GATE_URL:
        return _fallback(request)

    try:
        return _classify_over_http(request)
    except Exception as error:  # noqa: BLE001 - 게이트 실패로 이벤트를 잃지 않는다
        print(f"[ai-worker] 위험 게이트 호출 실패: {error}")
        return _fallback(request, status="failed", error=str(error)[:500])


def _classify_over_http(request: RiskGateRequest) -> RiskGateVerdict:
    """multipart 로 클립 + 메타를 넘긴다 (계약 4절 (b))."""
    boundary = "----scene-stealer-risk-gate"
    meta = json.dumps({
        "startedAt": request.started_at,
        "endedAt": request.ended_at,
        "cameraName": request.camera_name,
        # 같은 동작도 진열대 앞이면 절도 의심, 창고면 정상 업무다.
        # 게이트는 이 값을 반드시 판정 컨텍스트로 써야 한다.
        "locationTag": request.location_tag,
        "anomalyScore": request.anomaly_score,
        "threshold": request.threshold,
    }, ensure_ascii=False)

    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="meta"\r\n')
    body.extend(b"Content-Type: application/json\r\n\r\n")
    body.extend(meta.encode("utf-8"))
    body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="clip"; filename="{request.clip_path.name}"\r\n'.encode()
    )
    body.extend(b"Content-Type: video/mp4\r\n\r\n")
    body.extend(request.clip_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if RISK_GATE_TOKEN:
        headers["Authorization"] = f"Bearer {RISK_GATE_TOKEN}"

    http_request = urllib.request.Request(
        RISK_GATE_URL.rstrip("/") + "/classify", data=bytes(body), headers=headers
    )
    with urllib.request.urlopen(http_request, timeout=RISK_GATE_TIMEOUT_SEC) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return normalize_verdict(payload, request)
