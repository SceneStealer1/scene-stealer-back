"""기기 토큰 발급·검증.

평문 토큰은 발급 응답에서 **한 번만** 돌려주고 저장하지 않는다. DB 가 새도
매장 PC 를 사칭할 수는 없어야 한다.

ingest-worker(TypeScript)도 같은 방식으로 해시해서 대조한다 —
`ingest-worker/src/deviceAuth.ts`. 해시 방식을 바꾸면 양쪽을 같이 고쳐야 한다.
"""

import hashlib
import secrets

TOKEN_PREFIX = "ss_dev_"
_TOKEN_BYTES = 32


def issue_token() -> tuple[str, str]:
    """(평문, 해시). 평문은 호출자가 응답에 한 번 싣고 버린다."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
