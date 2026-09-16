"""AI 게이트 경계 — 게이트가 무엇을 보내든 events insert 가 깨지지 않아야 한다.

게이트는 이 레포 밖에서 만들어진다. 그쪽이 오타를 내거나 죽어도 사장님 화면에
영상은 떠야 한다는 게 이 테스트들의 전제다 (docs/ai-gate-contract.md 5절).
"""

from pathlib import Path

import pytest

from pipeline.risk_gate import (
    DEFAULT_RISK_BY_KIND, RISK_KINDS, RiskGateRequest, classify,
    derive_risk_from_score, normalize_verdict,
)


def make_request(score: float = 1.0, threshold: float = 1.0) -> RiskGateRequest:
    return RiskGateRequest(
        clip_path=Path("/tmp/clip.mp4"), thumbnail_path=Path("/tmp/thumb.jpg"),
        started_at="2026-09-16T05:00:00.000Z", ended_at="2026-09-16T05:00:31.000Z",
        camera_name="계산대", location_tag="checkout",
        anomaly_score=score, threshold=threshold,
    )


class TestDeriveRiskFromScore:
    """backend/app/domain/risk.py 의 같은 함수와 규칙이 일치해야 한다."""

    @pytest.mark.parametrize("score, threshold, expected", [
        (1.50, 1.0, "high"), (2.00, 1.0, "high"), (1.49, 1.0, "medium"),
        (1.20, 1.0, "medium"), (1.19, 1.0, "low"), (0.90, 0.6, "high"),
    ])
    def test_ratio_buckets(self, score, threshold, expected):
        assert derive_risk_from_score(score, threshold) == expected

    def test_degenerate_inputs_are_medium(self):
        assert derive_risk_from_score(5.0, 0.0) == "medium"
        assert derive_risk_from_score(None, 1.0) == "medium"
        assert derive_risk_from_score(1.0, None) == "medium"


class TestFallbackWhenGateIsAbsent:
    def test_classify_without_gate_url_is_skipped_not_failed(self, monkeypatch):
        """'아직 안 붙였다'와 '붙였는데 터졌다'는 구분돼야 한다 — 재분석 대상을
        고를 때 기준이 된다."""
        monkeypatch.setattr("pipeline.risk_gate.RISK_GATE_URL", None)
        verdict = classify(make_request(score=2.0, threshold=1.0))
        assert verdict.status == "skipped"
        assert verdict.kind == "unknown"
        assert verdict.risk == "high"
        assert verdict.error is None

    def test_fallback_still_produces_a_risk(self, monkeypatch):
        """events.risk 는 not null 이다. 어떤 경로로도 값이 나와야 한다."""
        monkeypatch.setattr("pipeline.risk_gate.RISK_GATE_URL", None)
        assert classify(make_request(score=0.1, threshold=1.0)).risk in ("high", "medium", "low")

    def test_gate_exception_becomes_failed_not_a_crash(self, monkeypatch):
        monkeypatch.setattr("pipeline.risk_gate.RISK_GATE_URL", "http://gate:9000")
        monkeypatch.setattr(
            "pipeline.risk_gate._classify_over_http",
            lambda request: (_ for _ in ()).throw(TimeoutError("게이트 응답 없음")),
        )
        verdict = classify(make_request())
        assert verdict.status == "failed"
        assert verdict.kind == "unknown"
        assert "게이트 응답 없음" in verdict.error


class TestNormalizeVerdict:
    def test_well_formed_response_passes_through(self):
        verdict = normalize_verdict({
            "kind": "theft", "risk": "high",
            "description": "계산대 앞에서 한 명이 결제 없이 나갔습니다.",
            "appearance": "검은 후드티, 20대 남성 추정",
            "boundingBoxes": [{"t": 1.2, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}],
        }, make_request())
        assert (verdict.kind, verdict.risk, verdict.status) == ("theft", "high", "done")
        assert verdict.appearance.startswith("검은 후드티")

    def test_unknown_kind_is_demoted_not_rejected(self):
        """게이트가 'shoplifting' 을 보냈다고 이벤트를 버리면 사장님은 영상조차
        못 본다. 강등하고 원본 값은 error 에 남긴다."""
        verdict = normalize_verdict({"kind": "shoplifting", "risk": "high"}, make_request())
        assert verdict.kind == "unknown"
        assert verdict.status == "failed"
        assert "shoplifting" in verdict.error

    def test_every_kind_in_the_enum_is_accepted(self):
        for kind in RISK_KINDS:
            assert normalize_verdict({"kind": kind, "risk": "high"}, make_request()).kind == kind

    def test_invalid_risk_falls_back_to_the_kind_default(self):
        verdict = normalize_verdict({"kind": "collapse", "risk": "catastrophic"}, make_request())
        assert verdict.risk == DEFAULT_RISK_BY_KIND["collapse"] == "high"

    def test_missing_risk_falls_back_to_the_kind_default(self):
        assert normalize_verdict({"kind": "loitering"}, make_request()).risk == "low"

    def test_snake_case_bounding_boxes_are_accepted(self):
        """게이트 작성자가 camelCase 를 놓칠 수 있다. 둘 다 받는다."""
        verdict = normalize_verdict({"kind": "theft", "bounding_boxes": [{"t": 0}]}, make_request())
        assert verdict.bounding_boxes == [{"t": 0}]

    def test_malformed_bounding_boxes_are_dropped_not_stored(self):
        """jsonb 컬럼에 리스트가 아닌 값이 들어가면 나중에 읽는 쪽이 터진다."""
        verdict = normalize_verdict(
            {"kind": "theft", "risk": "high", "boundingBoxes": "박스아님"}, make_request()
        )
        assert verdict.bounding_boxes is None

    def test_empty_strings_become_none(self):
        verdict = normalize_verdict(
            {"kind": "theft", "risk": "high", "description": "", "appearance": ""}, make_request()
        )
        assert verdict.description is None
        assert verdict.appearance is None

    def test_empty_response_does_not_crash(self):
        verdict = normalize_verdict({}, make_request())
        assert verdict.kind == "unknown"
        assert verdict.risk in ("high", "medium", "low")


class TestEventColumns:
    def test_columns_match_the_events_table(self):
        """supabase/schema.sql 의 events 컬럼명과 정확히 같아야 한다 — 하나라도
        어긋나면 insert 가 통째로 실패한다."""
        columns = normalize_verdict({"kind": "theft", "risk": "high"}, make_request()).to_event_columns()
        assert set(columns) == {
            "kind", "risk", "description", "appearance",
            "bounding_boxes", "ai_gate_status", "ai_gate_error",
        }

    def test_status_is_one_of_the_allowed_check_values(self):
        """events.ai_gate_status 에 check 제약이 걸려 있다."""
        for payload in ({"kind": "theft"}, {"kind": "없는종류"}):
            status = normalize_verdict(payload, make_request()).to_event_columns()["ai_gate_status"]
            assert status in ("pending", "done", "failed", "skipped")

    def test_kind_is_always_a_valid_enum_value(self):
        for payload in ({}, {"kind": None}, {"kind": 123}, {"kind": "shoplifting"}):
            assert normalize_verdict(payload, make_request()).to_event_columns()["kind"] in RISK_KINDS
