#!/usr/bin/env bash
# 로컬 도커에 전체 스택을 띄운다.
#   Supabase(supabase start) → schema.sql 적용 → ingest-worker · ai-worker · backend · nginx
#
# 필요한 것: Docker Desktop, Supabase CLI (brew install supabase/tap/supabase)
# 사용법: ./scripts/local-up.sh    내리기: ./scripts/local-down.sh [--reset]
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=scene-stealer-local
ENV_FILE=.env.local
COMPOSE=(docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.local.yml)
# 이 스택이 쓰지 않는 Supabase 서비스는 띄우지 않는다 — 도커 메모리를 ai-worker 에 남긴다.
EXCLUDE=imgproxy,mailpit,logflare,vector,supavisor,edge-runtime,realtime

supabase start -x "$EXCLUDE"

echo "[local] schema.sql 적용 (여러 번 돌려도 안전하다)"
docker exec -i supabase_db_scene-stealer psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
  < supabase/schema.sql > /dev/null

# supabase status 의 키로 .env.local 을 만든다. 내부 토큰은 한 번 만들면 계속 쓴다.
status="$(supabase status -o env)"
value() { printf '%s\n' "$status" | sed -n "s/^$1=\"\(.*\)\"$/\1/p"; }
internal_token=""
[ -f "$ENV_FILE" ] && internal_token="$(sed -n 's/^INTERNAL_API_TOKEN=//p' "$ENV_FILE")"
[ -n "$internal_token" ] || internal_token="$(openssl rand -hex 32)"

cat > "$ENV_FILE" <<EOF
# scripts/local-up.sh 가 만든다. 다음 실행 때 덮어쓰니 손으로 고치지 말 것.
SUPABASE_URL=http://supabase_kong_scene-stealer:8000
SUPABASE_PUBLIC_URL=$(value API_URL)
SUPABASE_SERVICE_KEY=$(value SERVICE_ROLE_KEY)
SUPABASE_JWT_SECRET=$(value JWT_SECRET)
INTERNAL_API_TOKEN=$internal_token
DEVICE_TOKENS=
STORE_TIMEZONE=Asia/Seoul
EOF

"${COMPOSE[@]}" up -d --build

cat <<EOF

[local] 떴습니다.
  API (nginx)        http://localhost          → curl http://localhost/healthz
  Supabase API       $(value API_URL)
  Supabase Studio    $(value STUDIO_URL)       (DB·저장소 파일 보기)

  PC 앱 설정 ▸ 고급
    서버 주소          http://localhost
    Supabase 주소      $(value API_URL)
    Supabase anon 키   $(value ANON_KEY)
  로그인              010-1234-5678 / 인증번호 123456 (supabase/config.toml 의 test_otp)

  로그   docker compose -p $PROJECT logs -f backend ai-worker ingest-worker
EOF
