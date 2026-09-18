"""사용자 토큰 검증 (app/auth.py).

지금의 Supabase Auth 는 ES256(비대칭 키)로 서명하고, 예전 프로젝트는 HS256(공유 비밀값)이다.
둘 다 받되, 방식을 바꿔치기해서 들어오는 토큰은 막아야 한다.
"""

import base64
import hashlib
import hmac
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app import auth, config

SUPABASE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
HS_SECRET = "legacy-hs256-secret-with-enough-length-0000"


class FakeJwks:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token):
        return type("SigningKey", (), {"key": self.public_key})()


@pytest.fixture
def jwks(monkeypatch):
    monkeypatch.setattr(auth, "_jwks_client", lambda: FakeJwks(SUPABASE_KEY.public_key()))
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", HS_SECRET)


def es256(key, **claims):
    return jwt.encode({"sub": "user-es", "aud": "authenticated", **claims}, key, algorithm="ES256", headers={"kid": "k1"})


def status_of(token):
    with pytest.raises(HTTPException) as caught:
        auth.get_current_user_id(f"Bearer {token}")
    return caught.value.status_code


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class TestAsymmetric:
    def test_es256_token_from_supabase_auth_is_accepted(self, jwks):
        assert auth.get_current_user_id(f"Bearer {es256(SUPABASE_KEY)}") == "user-es"

    def test_token_signed_by_another_key_is_rejected(self, jwks):
        assert status_of(es256(OTHER_KEY)) == 401

    def test_wrong_audience_is_rejected(self, jwks):
        assert status_of(es256(SUPABASE_KEY, aud="anon")) == 401

    def test_unreachable_auth_server_is_503_not_401(self, monkeypatch):
        """401 이면 PC 앱이 세션이 끝난 줄 알고 로그아웃시킨다. 서버 쪽 문제는 503 이다."""
        class Down:
            def get_signing_key_from_jwt(self, _token):
                raise jwt.PyJWKClientConnectionError("down")

        monkeypatch.setattr(auth, "_jwks_client", lambda: Down())
        assert status_of(es256(SUPABASE_KEY)) == 503


class TestLegacy:
    def test_hs256_token_still_works(self, jwks):
        token = jwt.encode({"sub": "user-hs", "aud": "authenticated"}, HS_SECRET, algorithm="HS256")
        assert auth.get_current_user_id(f"Bearer {token}") == "user-hs"

    def test_hs256_without_secret_is_503(self, jwks, monkeypatch):
        monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", None)
        token = jwt.encode({"sub": "user-hs", "aud": "authenticated"}, HS_SECRET, algorithm="HS256")
        assert status_of(token) == 503


class TestAlgorithmConfusion:
    def test_public_key_used_as_hs256_secret_is_rejected(self, jwks):
        """공개키는 누구나 받는다. 그걸 HMAC 비밀값으로 써서 만든 토큰이 통과하면 누구든 위조할 수 있다."""
        pem = SUPABASE_KEY.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        signing_input = f'{b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())}.{b64(json.dumps({"sub": "attacker", "aud": "authenticated"}).encode())}'
        forged = f"{signing_input}.{b64(hmac.new(pem, signing_input.encode(), hashlib.sha256).digest())}"
        assert status_of(forged) == 401

    def test_alg_none_is_rejected(self, jwks):
        token = jwt.encode({"sub": "attacker", "aud": "authenticated"}, key=None, algorithm="none")
        assert status_of(token) == 401
