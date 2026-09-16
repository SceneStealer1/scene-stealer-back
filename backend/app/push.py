"""푸시 발송 (요구사항 6.4).

**지금은 실제로 보내지 않는다.** FCM 서버 키가 없으면 로그만 남기고 False 를
돌려준다 — 조용히 성공한 척하면 "알림이 안 온다"는 문의를 추적할 수 없다.

붙이는 방법: FCM_SERVER_KEY 를 환경변수로 넣으면 `_send_fcm` 경로를 탄다.
APNs 는 FCM 을 통해 보낸다(iOS 토큰도 FCM 에 등록) — 별도 APNs 인증서 경로를
쓰려면 `_send_apns` 를 추가하고 platform 으로 갈라라.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Iterable, Optional

from . import config

FCM_ENDPOINT = "https://fcm.googleapis.com/fcm/send"
_TIMEOUT_SEC = 10


def send_push(
    tokens: Iterable[dict[str, str]],
    title: str,
    body: str,
    data: Optional[dict[str, Any]] = None,
    *,
    sound: bool = True,
    thumbnail_url: Optional[str] = None,
) -> bool:
    """등록된 기기들에 알림을 보낸다. 하나라도 성공하면 True."""
    token_values = [t["token"] for t in tokens if t.get("token")]
    if not token_values:
        return False

    if not config.FCM_SERVER_KEY:
        print(
            f"[backend] FCM_SERVER_KEY 미설정 — 푸시를 보내지 않습니다. "
            f"title={title!r} devices={len(token_values)}"
        )
        return False

    ok = False
    for token in token_values:
        ok = _send_fcm(token, title, body, data or {}, sound, thumbnail_url) or ok
    return ok


def _send_fcm(token: str, title: str, body: str, data: dict[str, Any],
              sound: bool, thumbnail_url: Optional[str]) -> bool:
    notification: dict[str, Any] = {"title": title, "body": body}
    if sound:
        notification["sound"] = "default"
    if thumbnail_url:
        # 리치 푸시 썸네일 (요구사항 6.4). Android 는 image, iOS 는 앱의
        # notification service extension 이 data 의 imageUrl 을 받아 붙인다.
        notification["image"] = thumbnail_url
        data = {**data, "imageUrl": thumbnail_url}

    payload = json.dumps({
        "to": token,
        "notification": notification,
        "data": {k: str(v) for k, v in data.items()},
        "priority": "high",
    }).encode("utf-8")

    request = urllib.request.Request(
        FCM_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"key={config.FCM_SERVER_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as response:
            return 200 <= response.status < 300
    except urllib.error.URLError as error:
        # 푸시 실패로 이벤트 생성 자체가 롤백되면 안 된다. 로그만 남긴다.
        print(f"[backend] 푸시 발송 실패 token={token[:12]}...: {error}")
        return False
