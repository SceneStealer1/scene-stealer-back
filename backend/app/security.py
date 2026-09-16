"""디바이스 토큰(PC 앱용) 발급/검증 유틸리티.

평문 토큰은 발급 시 응답으로 딱 한 번만 내려주고, DB(devices.token_hash)에는 해시만
저장한다 — 이후 인증은 들어온 Authorization 헤더를 같은 방식으로 해시해서 비교한다.
"""
import hashlib
import secrets


def generate_device_token() -> str:
    return secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_pairing_code() -> str:
    # 사람이 QR 대신 눈으로 옮겨 적을 일은 없지만(항상 QR 스캔), 그래도 URL-safe하게.
    return secrets.token_urlsafe(16)
