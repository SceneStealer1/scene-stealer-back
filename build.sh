#!/usr/bin/env bash
# 세 이미지(ingest-worker, backend, nginx)를 전부 빌드한다. 사용법: ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

docker compose build

echo "[build] done."
docker images --filter "reference=scene-stealer-back-*" --format "  {{.Repository}}:{{.Tag}}"
