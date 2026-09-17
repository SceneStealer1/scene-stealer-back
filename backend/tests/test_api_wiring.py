"""라우트 배선 검증 — 인증 게이팅, 에러 모양, SSE 팬아웃.

Supabase 에 실제로 붙지는 않는다. 여기서 잡으려는 건 "쿼리가 맞는가"가 아니라
"요청이 엉뚱한 곳으로 새지 않는가"다 — 인증 없이 통과하는 라우트, 500 으로
새는 에러, 계약과 다른 에러 모양 같은 것들.
"""

import asyncio
import json
import threading
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.realtime import EventBus, format_sse

client = TestClient(app)

PROTECTED_ROUTES = [
    ("GET", "/stores"),
    ("POST", "/stores"),
    ("GET", "/stores/s1"),
    ("PATCH", "/stores/s1"),
    ("GET", "/stores/s1/cameras"),
    ("POST", "/stores/s1/cameras"),
    ("GET", "/stores/s1/devices"),
    ("POST", "/stores/s1/devices"),
    ("GET", "/stores/s1/monitoring"),
    ("GET", "/stores/s1/events"),
    ("GET", "/stores/s1/events/timeline?date=2026-09-16"),
    ("GET", "/stores/s1/events/unconfirmed-count"),
    ("GET", "/stores/s1/events/summary"),
    ("GET", "/stores/s1/segments"),
    ("GET", "/stores/s1/stream"),
    ("GET", "/stores/s1/notification-settings"),
    ("PUT", "/stores/s1/notification-settings"),
    ("GET", "/events/e1"),
    ("PATCH", "/events/e1/state"),
    ("PATCH", "/events/e1/memo"),
    ("GET", "/events/e1/clip"),
    ("GET", "/events/e1/nearby-cameras"),
    ("DELETE", "/cameras/c1"),
    ("DELETE", "/devices/d1"),
    ("POST", "/push/devices"),
]


class TestPublicRoutes:
    def test_healthz_needs_no_auth(self):
        assert client.get("/healthz").json() == {"ok": True}

    def test_root_needs_no_auth(self):
        assert client.get("/").status_code == 200


class TestAuthGating:
    @pytest.mark.parametrize("method, path", PROTECTED_ROUTES)
    def test_no_token_is_rejected(self, method, path):
        """토큰 없이 통과하는 라우트가 하나라도 있으면 남의 매장이 열린다."""
        response = client.request(method, path, json={})
        assert response.status_code in (401, 503), f"{method} {path} -> {response.status_code}"

    @pytest.mark.parametrize("method, path", PROTECTED_ROUTES)
    def test_garbage_token_is_rejected(self, method, path):
        response = client.request(
            method, path, json={}, headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert response.status_code in (401, 503)

    def test_error_body_matches_the_contract(self):
        """계약 1.3 — {"error": "..."} 모양을 ingest-worker 와 맞춘다."""
        body = client.get("/stores").json()
        assert set(body) == {"error"}
        assert isinstance(body["error"], str)

    def test_token_signed_with_wrong_secret_is_rejected(self, monkeypatch):
        monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", "the-real-secret")
        forged = jwt.encode(
            {"sub": "11111111-1111-1111-1111-111111111111", "aud": "authenticated"},
            "a-different-secret",
            algorithm="HS256",
        )
        response = client.get("/stores", headers={"Authorization": f"Bearer {forged}"})
        assert response.status_code == 401


CAMERA_STATE = {"storeId": "s1", "cameraId": "c1", "state": "disconnected"}


class TestInternalRoutes:
    """ingest-worker 전용. 사용자 JWT 가 아니라 공유 비밀값으로 막는다."""

    def test_event_publish_door_is_gone(self):
        """위험 이벤트는 backend 가 anomaly_events 를 읽어 직접 만든다. 밖에서 두드릴 문이 없어야 한다."""
        assert client.post("/internal/events/published", json={"eventId": "e1"}).status_code == 404

    def test_rejected_without_internal_token(self, monkeypatch):
        monkeypatch.setattr(config, "INTERNAL_API_TOKEN", "s3cret")
        response = client.post("/internal/cameras/state", json=CAMERA_STATE)
        assert response.status_code == 401

    def test_rejected_with_wrong_internal_token(self, monkeypatch):
        monkeypatch.setattr(config, "INTERNAL_API_TOKEN", "s3cret")
        response = client.post(
            "/internal/cameras/state", json=CAMERA_STATE,
            headers={"X-Internal-Token": "wrong"},
        )
        assert response.status_code == 401

    def test_unconfigured_internal_token_does_not_open_the_door(self, monkeypatch):
        """토큰 미설정을 '검사 안 함'으로 읽으면 내부 API 가 통째로 열린다."""
        monkeypatch.setattr(config, "INTERNAL_API_TOKEN", None)
        response = client.post(
            "/internal/cameras/state", json=CAMERA_STATE,
            headers={"X-Internal-Token": "anything"},
        )
        assert response.status_code == 503

    def test_user_jwt_does_not_open_internal_routes(self, monkeypatch):
        monkeypatch.setattr(config, "INTERNAL_API_TOKEN", "s3cret")
        monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", "jwt-secret")
        token = jwt.encode({"sub": "u1", "aud": "authenticated"}, "jwt-secret", algorithm="HS256")
        response = client.post(
            "/internal/cameras/state", json=CAMERA_STATE,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401


class TestEventBus:
    def test_publish_from_another_thread_wakes_the_subscriber(self):
        """sync 라우트와 이벤트 변환 작업은 스레드에서 publish 한다. 루프를 깨우지 않으면
        구독자는 다른 일이 생길 때까지(운영에선 ping 주기 15초) 못 받는다."""
        async def scenario():
            bus = EventBus()
            queue = bus.subscribe("store-1")
            threading.Timer(0.05, bus.publish, args=("store-1", "camera.state", {"cameraId": "c1"})).start()
            started = time.monotonic()
            item = await asyncio.wait_for(queue.get(), timeout=2)
            return item, time.monotonic() - started

        item, elapsed = asyncio.run(scenario())
        assert item == ("camera.state", {"cameraId": "c1"})
        assert elapsed < 0.5

    def test_publish_before_anyone_subscribed_is_a_no_op(self):
        EventBus().publish("store-1", "event.created", {"id": "e1"})

    def test_subscriber_receives_published_event(self):
        async def scenario():
            bus = EventBus()
            queue = bus.subscribe("store-1")
            bus.publish("store-1", "event.created", {"id": "e1"})
            return await asyncio.wait_for(queue.get(), timeout=1)

        assert asyncio.run(scenario()) == ("event.created", {"id": "e1"})

    def test_other_stores_do_not_see_the_event(self):
        """매장 경계가 SSE 에서도 지켜져야 한다."""
        async def scenario():
            bus = EventBus()
            mine = bus.subscribe("store-1")
            theirs = bus.subscribe("store-2")
            bus.publish("store-1", "event.created", {"id": "e1"})
            return mine.qsize(), theirs.qsize()

        assert asyncio.run(scenario()) == (1, 0)

    def test_every_subscriber_of_a_store_receives_it(self):
        async def scenario():
            bus = EventBus()
            pc = bus.subscribe("store-1")
            mobile = bus.subscribe("store-1")
            bus.publish("store-1", "event.updated", {"id": "e1"})
            return pc.qsize(), mobile.qsize()

        assert asyncio.run(scenario()) == (1, 1)

    def test_unsubscribe_stops_delivery(self):
        async def scenario():
            bus = EventBus()
            queue = bus.subscribe("store-1")
            bus.unsubscribe("store-1", queue)
            bus.publish("store-1", "event.created", {"id": "e1"})
            return queue.qsize(), bus.subscriber_count("store-1")

        assert asyncio.run(scenario()) == (0, 0)

    def test_slow_subscriber_is_dropped_instead_of_growing_forever(self):
        """느린 클라이언트 하나가 서버 메모리를 먹으면 안 된다."""
        async def scenario():
            bus = EventBus()
            bus.subscribe("store-1")
            for i in range(500):
                bus.publish("store-1", "event.created", {"id": i})
            return bus.subscriber_count("store-1")

        assert asyncio.run(scenario()) == 0

    def test_publish_to_nobody_is_not_an_error(self):
        EventBus().publish("store-nobody", "event.created", {"id": "e1"})


class TestSseFormat:
    def test_frame_shape(self):
        assert format_sse("ping", {}) == 'event: ping\ndata: {}\n\n'

    def test_korean_is_not_escaped(self):
        """한글이 \\uXXXX 로 나가면 사람이 로그를 못 읽는다."""
        frame = format_sse("event.created", {"description": "계산대 앞에서"})
        assert "계산대 앞에서" in frame

    def test_payload_is_valid_json(self):
        frame = format_sse("event.created", {"id": "e1", "risk": "high"})
        payload = frame.split("data: ", 1)[1].strip()
        assert json.loads(payload) == {"id": "e1", "risk": "high"}
