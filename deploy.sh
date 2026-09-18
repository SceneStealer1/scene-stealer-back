#!/usr/bin/env bash
# ingest-worker + ai-worker + backend + nginx 스택을 docker swarm에 (재)배포. 사용법: 코드가 바뀌었으면 ./build-deploy.sh, .env 만 바뀌었으면 ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

STACK_NAME=scene-stealer
ENV_FILE=.env
COMPOSE_FILE=docker-compose.yml

if [ ! -f "$ENV_FILE" ]; then
  echo "[deploy] $ENV_FILE not found in $(pwd)" >&2
  exit 1
fi

if ! docker info 2>/dev/null | grep -q "Swarm: active"; then
  echo "[deploy] swarm mode inactive, initializing"
  docker swarm init
fi

# docker stack deploy 는 docker compose up 과 달리 .env 파일을 자동으로 읽지 않는다.
# 그래서 셸 환경으로 직접 로드한 뒤 스택을 올린다.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

docker stack deploy -c "$COMPOSE_FILE" "$STACK_NAME"

echo "[deploy] done."
echo "[deploy] status: docker stack services $STACK_NAME"
echo "[deploy] logs:   docker service logs -f ${STACK_NAME}_ingest-worker"
echo "[deploy]         docker service logs -f ${STACK_NAME}_ai-worker"
echo "[deploy]         docker service logs -f ${STACK_NAME}_backend"
echo "[deploy]         docker service logs -f ${STACK_NAME}_nginx"
