"""
대시보드 로그인은 이 backend 가 아니라 Supabase Auth 가 직접 처리한다(프론트가
supabase-js 로 회원가입/로그인해서 access token 을 받는다). 이 backend 는 그렇게
발급된 JWT 를 각 요청의 `Authorization: Bearer <token>` 헤더로 받아서 검증만 하고,
그 안의 sub(=auth.users.id) 로 조회 쿼리를 필터링한다 — service role 키로 RLS 를
우회해서 DB에 접근하기 때문에, "본인 데이터만 보이게" 하는 책임은 여기(각 라우트의
user_id 필터)에 있다.

Supabase Auth 의 사용자 토큰 서명은 두 가지다. 지금의 Supabase(새 프로젝트, 최신 로컬
CLI)는 비대칭 키(ES256)로 서명해서 공개키 목록(JWKS)으로 검증하고, 예전 프로젝트의 토큰은
공유 비밀값(HS256, SUPABASE_JWT_SECRET)으로 검증한다. 토큰 헤더의 alg 로 고르되 방식마다
허용 알고리즘을 따로 묶는다 — 공개키를 HS256 비밀값처럼 쓰게 만드는 바꿔치기를 막으려는 것.
"""
from functools import lru_cache
from typing import Any, Optional

import jwt
from fastapi import Header, HTTPException

from . import config

#: JWKS 로 검증하는 알고리즘. HS256 은 절대 여기에 넣지 않는다.
ASYMMETRIC_ALGORITHMS = ["ES256", "RS256"]


@lru_cache(maxsize=1)
def _jwks_client() -> Optional[jwt.PyJWKClient]:
    """키는 바뀔 때만 달라져서 한 번 받아 두고, 모르는 kid 가 오면 PyJWKClient 가 다시 받는다."""
    if not config.SUPABASE_URL:
        return None
    return jwt.PyJWKClient(
        f"{config.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
        lifespan=3600,
        timeout=5,
    )


def _verification_key(token: str) -> tuple[Any, list[str]]:
    """(검증 키, 허용 알고리즘). 토큰 모양이 틀리면 401, 서버 설정·연결 문제면 503."""
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다") from error

    if alg == "HS256":
        if not config.SUPABASE_JWT_SECRET:
            raise HTTPException(status_code=503, detail="인증이 설정되지 않았습니다")
        return config.SUPABASE_JWT_SECRET, ["HS256"]

    if alg in ASYMMETRIC_ALGORITHMS:
        client = _jwks_client()
        if client is None:
            raise HTTPException(status_code=503, detail="인증이 설정되지 않았습니다")
        try:
            return client.get_signing_key_from_jwt(token).key, ASYMMETRIC_ALGORITHMS
        except jwt.PyJWKClientConnectionError as error:
            # 인증 서버에 못 닿은 것 — 토큰 탓이 아니라 401(다시 로그인)로 답하지 않는다.
            raise HTTPException(status_code=503, detail="인증 서버에 연결할 수 없습니다") from error
        except jwt.PyJWKClientError as error:
            raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다") from error

    raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    """Authorization 헤더를 검증하고 로그인한 유저의 uuid(auth.users.id)를 반환한다."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    key, algorithms = _verification_key(token)
    try:
        payload = jwt.decode(token, key, algorithms=algorithms, audience="authenticated")
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다") from error

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다")
    return user_id
