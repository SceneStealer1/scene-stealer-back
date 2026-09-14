"""
대시보드 로그인은 이 backend 가 아니라 Supabase Auth 가 직접 처리한다(프론트가
supabase-js 로 회원가입/로그인해서 access token 을 받는다). 이 backend 는 그렇게
발급된 JWT 를 각 요청의 `Authorization: Bearer <token>` 헤더로 받아서 검증만 하고,
그 안의 sub(=auth.users.id) 로 조회 쿼리를 필터링한다 — service role 키로 RLS 를
우회해서 DB에 접근하기 때문에, "본인 데이터만 보이게" 하는 책임은 여기(각 라우트의
user_id 필터)에 있다.
"""
from typing import Optional

import jwt
from fastapi import Header, HTTPException

from . import config


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    """Authorization 헤더를 검증하고 로그인한 유저의 uuid(auth.users.id)를 반환한다."""
    if not config.SUPABASE_JWT_SECRET:
        raise HTTPException(status_code=503, detail="인증이 설정되지 않았습니다")

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    try:
        payload = jwt.decode(
            token,
            config.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다") from error

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")
    return user_id
