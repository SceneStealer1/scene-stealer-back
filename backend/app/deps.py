"""여러 라우터가 공유하는 자잘한 의존성/헬퍼. main.py 와 routers/* 양쪽에서 쓴다
(routers 가 main 을 import하면 순환참조가 나서 따로 뺐다)."""
from typing import Optional

from fastapi import HTTPException

from .supabase_client import supabase

SUPABASE_UNAVAILABLE_MSG = "Supabase 가 설정되지 않았습니다"


def require_supabase():
    if supabase is None:
        raise HTTPException(status_code=503, detail=SUPABASE_UNAVAILABLE_MSG)
    return supabase


def clamp_limit(raw: Optional[int], fallback: int, maximum: int) -> int:
    if raw is None or raw <= 0:
        return fallback
    return min(raw, maximum)
