"""CORS — 브라우저가 다른 주소의 페이지에서 이 API 를 직접 부를 때 (웹 체험판 /wanted-test).

허용하는 출처는 **같은 최상위 도메인**뿐이다. CORS_ALLOWED_DOMAIN=example.com 이면
https://example.com 과 https://<하위>.example.com 만 받는다. 다른 도메인(xxx.vercel.app 포함)과
http 페이지는 받지 않는다. 비워 두면 CORS 를 아예 열지 않는다 — PC 앱(Electron)은 메인
프로세스가 대신 부르고 모바일 앱은 네이티브라, 둘 다 CORS 를 타지 않으므로 영향이 없다.

ingest-worker(src/cors.ts)도 같은 규칙이다. 바꾸면 양쪽을 같이 고친다.
"""

import re
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 브라우저가 실제로 보내는 헤더만 연다. idempotency-key 는 조각 업로드(ingest-worker)에서 쓰지만
# 두 서비스의 규칙을 한 벌로 두려고 같이 적는다.
ALLOWED_HEADERS = ["authorization", "content-type", "idempotency-key"]
ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
# 브라우저가 사전 확인(OPTIONS) 결과를 기억하는 시간. 조각 업로드·폴링마다 한 번 더 왕복하지 않게.
PREFLIGHT_MAX_AGE_SEC = 600

# 점으로 나뉜 라벨 두 개 이상. 한글 도메인은 퓨니코드(xn--…)로 적는다.
_HOSTNAME = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]{2,}$")


def normalize_domain(domain: Optional[str]) -> Optional[str]:
    """' Example.COM ' → 'example.com'. 비었으면 None, 도메인 모양이 아니면 ValueError.

    잘못 적은 값을 조용히 넘기면 '왜 CORS 가 안 열리지'를 추적할 수 없다 — 기동할 때 바로 죽인다.
    """
    value = (domain or "").strip().lower().strip(".")
    if not value:
        return None
    if not _HOSTNAME.match(value):
        raise ValueError(
            f"CORS_ALLOWED_DOMAIN 은 'example.com' 같은 도메인이어야 합니다 (받은 값: {domain!r})"
        )
    return value


def origin_regex(domain: str) -> str:
    """그 도메인과 하위 도메인의 https 출처. 'example.com.evil.io' · 'evilexample.com' 은 걸리지 않는다."""
    return rf"^https://(?:[a-z0-9-]+\.)*{re.escape(domain)}$"


def install_cors(app: FastAPI, domain: Optional[str]) -> None:
    normalized = normalize_domain(domain)
    if not normalized:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=origin_regex(normalized),
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        # 인증은 Authorization 헤더로 한다. 쿠키를 쓰지 않으므로 자격 증명 모드는 열지 않는다.
        allow_credentials=False,
        max_age=PREFLIGHT_MAX_AGE_SEC,
    )
