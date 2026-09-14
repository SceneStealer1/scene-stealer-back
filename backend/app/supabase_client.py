from typing import Optional

from supabase import Client, create_client

from . import config

# Supabase 미설정이면 None — 조회 라우트가 503 으로 응답한다 (main.py 참고).
supabase: Optional[Client] = (
    create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    if config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY
    else None
)

if supabase is None:
    print("[backend] SUPABASE_URL/SUPABASE_SERVICE_KEY 미설정 — 조회 API가 503을 반환합니다")
