#!/usr/bin/env bash
# 로컬 스택을 내린다. 데이터(DB · 저장소 파일 · 조각 볼륨)는 남는다.
# 사용법: ./scripts/local-down.sh           데이터까지 지우기: ./scripts/local-down.sh --reset
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=scene-stealer-local
ENV_ARGS=()
[ -f .env.local ] && ENV_ARGS=(--env-file .env.local)
COMPOSE=(docker compose -p "$PROJECT" "${ENV_ARGS[@]}" -f docker-compose.yml -f docker-compose.local.yml)

if [ "${1:-}" = "--reset" ]; then
  "${COMPOSE[@]}" down -v
  supabase stop --no-backup
else
  "${COMPOSE[@]}" down
  supabase stop
fi
