import os
import sys

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

# 클립/썸네일 signed URL 유효시간 (초).
SIGNED_URL_TTL_SEC = int(os.environ.get("SIGNED_URL_TTL_SEC", "3600"))

# Supabase Auth가 발급하는 JWT(HS256) 서명 검증용 비밀값 — Supabase 대시보드의
# Project Settings > API > JWT Settings > JWT Secret. 없으면 로그인 필요한 조회
# 라우트가 전부 503을 반환한다 (app/auth.py 참고).
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET") or None
