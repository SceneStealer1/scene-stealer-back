"""이상 구간 → 위험 이벤트 변환 (backend 가 anomaly_events 를 읽어 events 를 만든다)."""

from datetime import datetime, timezone

from app.domain.anomaly import event_row_from_anomaly, resolve_camera

NOW = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)

CAMERA = {"id": "cam-uuid-1", "store_id": "store-1", "agent_camera_id": "urn:uuid:agent-01"}
OTHER_STORE_SAME_AGENT_ID = {"id": "cam-uuid-9", "store_id": "store-9", "agent_camera_id": "urn:uuid:agent-01"}

VIDEO = {
    "id": "video-1",
    "recorded_started_at": "2026-09-17T02:31:00.000Z",
    "store_uuid": "store-1",
    "camera_uuid": "cam-uuid-1",
    "camera_id": "urn:uuid:agent-01",
}

ANOMALY = {
    "id": "anomaly-1",
    "video_id": "video-1",
    "start_time_sec": 10.0,
    "end_time_sec": 41.5,
    "anomaly_score": 0.9,
    "threshold": 0.6,
    "clip_storage_path": "u1/video-1/clip_0.mp4",
    "thumbnail_storage_path": "u1/video-1/thumb_0.jpg",
}


class TestResolveCamera:
    def test_camera_uuid_wins(self):
        assert resolve_camera(VIDEO, [OTHER_STORE_SAME_AGENT_ID, CAMERA]) == CAMERA

    def test_old_segment_is_matched_by_store_and_agent_camera_id(self):
        """camera_uuid 를 채우기 전에 올라온 조각."""
        video = {**VIDEO, "camera_uuid": None}
        assert resolve_camera(video, [OTHER_STORE_SAME_AGENT_ID, CAMERA]) == CAMERA

    def test_agent_camera_id_alone_does_not_cross_stores(self):
        """에이전트 카메라 id 는 매장마다 겹칠 수 있다. 매장이 다르면 남의 카메라다."""
        video = {**VIDEO, "camera_uuid": None}
        assert resolve_camera(video, [OTHER_STORE_SAME_AGENT_ID]) is None

    def test_unregistered_segment_has_no_camera(self):
        video = {**VIDEO, "camera_uuid": None, "store_uuid": None}
        assert resolve_camera(video, [CAMERA]) is None


class TestEventRowFromAnomaly:
    def test_offsets_become_wall_clock_times(self):
        row = event_row_from_anomaly(ANOMALY, VIDEO, CAMERA, 30, NOW)
        assert row["started_at"] == "2026-09-17T02:31:10.000Z"
        assert row["ended_at"] == "2026-09-17T02:31:41.500Z"

    def test_risk_comes_from_score_ratio(self):
        # 0.9 / 0.6 = 1.5배 → 높음
        assert event_row_from_anomaly(ANOMALY, VIDEO, CAMERA, 30, NOW)["risk"] == "high"
        low = {**ANOMALY, "anomaly_score": 0.65}
        assert event_row_from_anomaly(low, VIDEO, CAMERA, 30, NOW)["risk"] == "low"

    def test_links_back_to_the_anomaly_and_keeps_the_numbers(self):
        """anomaly_events 는 원본 조각과 함께 지워진다. 점수·임계값은 이벤트에 남겨 둔다."""
        row = event_row_from_anomaly(ANOMALY, VIDEO, CAMERA, 30, NOW)
        assert row["anomaly_event_id"] == "anomaly-1"
        assert (row["anomaly_score"], row["anomaly_threshold"]) == (0.9, 0.6)
        assert (row["store_id"], row["camera_id"], row["video_id"]) == ("store-1", "cam-uuid-1", "video-1")

    def test_clip_expiry_follows_store_retention(self):
        assert event_row_from_anomaly(ANOMALY, VIDEO, CAMERA, 7, NOW)["clip_expires_at"] == "2026-09-24T03:00:00.000Z"

    def test_clip_expiry_defaults_to_30_days(self):
        assert event_row_from_anomaly(ANOMALY, VIDEO, CAMERA, None, NOW)["clip_expires_at"] == "2026-10-17T03:00:00.000Z"

    def test_no_classification_columns(self):
        """위험 종류·AI 설명은 만들지 않는다 — AI 는 구간과 점수만 준다."""
        row = event_row_from_anomaly(ANOMALY, VIDEO, CAMERA, 30, NOW)
        assert not {"kind", "description", "appearance", "bounding_boxes"} & set(row)
