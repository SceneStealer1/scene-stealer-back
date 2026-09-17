import os
import sys
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Windows 콘솔 기본 코드페이지(cp949)에서는 한글/이모지 print()가 UnicodeEncodeError로
# 죽을 수 있어서, 다른 모듈이 뭔가 찍기 전에 stdout/stderr를 UTF-8로 강제한다.
# (Docker 컨테이너 안에서는 보통 이미 UTF-8이라 별 효과 없음 — 로컬 Windows 개발용.)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

PORT = int(os.environ.get("PORT", "8081"))

# 아직 Supabase 프로젝트가 없을 수 있어서 선택값 — supabase_client.py 참고.
SUPABASE_URL = os.environ.get("SUPABASE_URL") or None
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or None

# 클라이언트(PC 앱·모바일)가 여는 Supabase 주소. backend 가 Supabase 를 내부 주소로
# 부를 때만 넣는다 — 서명 URL 이 SUPABASE_URL 로 만들어지므로 그 앞부분을 이 값으로
# 바꿔서 내준다. 로컬 도커에서는 컨테이너 주소(supabase_kong_…:8000)와 PC 가 여는
# 주소(127.0.0.1:54321)가 다르다 (docker-compose.local.yml). 비우면 바꾸지 않는다.
SUPABASE_PUBLIC_URL = os.environ.get("SUPABASE_PUBLIC_URL") or None

# 클립/썸네일 signed URL 유효시간 (초).
SIGNED_URL_TTL_SEC = int(os.environ.get("SIGNED_URL_TTL_SEC", "3600"))

# Supabase Auth가 발급하는 JWT(HS256) 서명 검증용 비밀값 — Supabase 대시보드의
# Project Settings > API > JWT Settings > JWT Secret. 없으면 로그인 필요한 조회
# 라우트가 전부 503을 반환한다 (app/auth.py 참고).
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET") or None

# ingest-worker 가 backend 의 /internal/* 를 부를 때 쓰는 공유 비밀값.
# nginx 는 /internal 을 외부로 라우팅하지 않지만, 네트워크 격리 하나에만 기대지
# 않으려고 토큰도 같이 본다 (app/routers/stream.py).
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN") or None

# ai-worker 가 남긴 anomaly_events 를 위험 이벤트로 옮기는 주기 (app/anomaly_ingest.py).
# 조각이 1분 단위고 분석에도 수십 초가 걸려서, 몇 초 늦는 건 알림 체감에 묻힌다.
ANOMALY_POLL_SEC = float(os.environ.get("ANOMALY_POLL_SEC", "5"))

# FCM 서버 키. 없으면 푸시를 보내지 않고 로그만 남긴다 (app/push.py) —
# 조용히 성공한 척하면 "알림이 안 온다"를 추적할 수 없다.
FCM_SERVER_KEY = os.environ.get("FCM_SERVER_KEY") or None

# 영업시간·수면시간 판정은 벽시계 기준이라 매장 현지시각이 필요하다.
# 지금은 전 매장이 한국이라 전역 설정으로 둔다 — 해외 매장이 생기면 stores 에
# timezone 컬럼을 추가하고 매장별로 읽어야 한다.
STORE_TIMEZONE = ZoneInfo(os.environ.get("STORE_TIMEZONE", "Asia/Seoul"))
