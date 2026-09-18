"""서명 URL 을 클라이언트가 여는 주소로 바꾸기 (config.SUPABASE_PUBLIC_URL)."""

from app import config
from app.supabase_client import to_public_url

SIGNED = "http://supabase_kong_scene-stealer:8000/storage/v1/object/sign/clips/u1/v1/event_000.mp4?token=abc"


def test_internal_prefix_becomes_public(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "http://supabase_kong_scene-stealer:8000")
    monkeypatch.setattr(config, "SUPABASE_PUBLIC_URL", "http://127.0.0.1:54321/")
    assert to_public_url(SIGNED) == "http://127.0.0.1:54321/storage/v1/object/sign/clips/u1/v1/event_000.mp4?token=abc"


def test_unchanged_without_public_url(monkeypatch):
    """상용처럼 같은 주소를 쓰면 설정하지 않는다 — 그대로 둔다."""
    monkeypatch.setattr(config, "SUPABASE_URL", "http://supabase_kong_scene-stealer:8000")
    monkeypatch.setattr(config, "SUPABASE_PUBLIC_URL", None)
    assert to_public_url(SIGNED) == SIGNED


def test_other_hosts_are_left_alone(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "http://supabase_kong_scene-stealer:8000")
    monkeypatch.setattr(config, "SUPABASE_PUBLIC_URL", "http://127.0.0.1:54321")
    other = "https://cdn.example.com/clip.mp4"
    assert to_public_url(other) == other
