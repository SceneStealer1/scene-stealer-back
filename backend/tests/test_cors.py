"""CORS — 같은 최상위 도메인의 https 페이지만 이 API 를 부를 수 있다 (app/cors.py)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cors import install_cors, normalize_domain


def client_for(domain):
    app = FastAPI()

    @app.get("/stores")
    def stores():
        return {"stores": []}

    install_cors(app, domain)
    return TestClient(app)


def preflight(client, origin):
    return client.options(
        "/stores",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


@pytest.mark.parametrize(
    "origin",
    ["https://example.com", "https://demo.example.com", "https://a.b.example.com"],
)
def test_같은_최상위_도메인의_https_는_허용한다(origin):
    client = client_for("example.com")

    response = preflight(client, origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "authorization" in response.headers["access-control-allow-headers"].lower()

    # 실제 요청에도 허용 헤더가 붙어야 브라우저가 응답을 스크립트에 넘긴다.
    actual = client.get("/stores", headers={"Origin": origin})
    assert actual.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://cctv-agent-electron-pc.vercel.app",  # 다른 도메인
        "https://example.com.evil.io",  # 앞부분만 같은 도메인
        "https://evilexample.com",  # 끝부분만 같은 도메인
        "http://demo.example.com",  # https 가 아니다
        "https://example.co",
    ],
)
def test_다른_출처는_허용하지_않는다(origin):
    client = client_for("example.com")

    assert "access-control-allow-origin" not in preflight(client, origin).headers
    assert "access-control-allow-origin" not in client.get("/stores", headers={"Origin": origin}).headers


def test_설정이_없으면_CORS_를_열지_않는다():
    client = client_for(None)

    assert "access-control-allow-origin" not in preflight(client, "https://demo.example.com").headers
    # 같은 출처(nginx 뒤 PC 앱·curl)의 평범한 요청은 그대로 된다.
    assert client.get("/stores").status_code == 200


def test_도메인_값을_다듬는다():
    assert normalize_domain(" Example.COM ") == "example.com"
    assert normalize_domain(".example.com") == "example.com"
    assert normalize_domain("") is None
    assert normalize_domain(None) is None


@pytest.mark.parametrize("bad", ["https://example.com", "example", "*.example.com", "example.com/api"])
def test_도메인이_아닌_값은_기동할_때_막는다(bad):
    with pytest.raises(ValueError):
        normalize_domain(bad)
