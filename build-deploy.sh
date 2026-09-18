#!/usr/bin/env bash
# 네 이미지(ingest-worker, ai-worker, backend, nginx)를 전부 빌드하고 바로 배포한다.
# 코드가 바뀌었을 때 사용. 사용법: ./build-deploy.sh
# .env만 바뀌고 코드/이미지는 그대로면 ./deploy.sh 만 실행하면 된다.
set -euo pipefail
cd "$(dirname "$0")"

docker compose build

echo "[build] done."
docker images --filter "reference=scene-stealer-back-*" --format "  {{.Repository}}:{{.Tag}}"

./deploy.sh
