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

# docker compose build 가 :latest 태그를 새 이미지로 옮겨서, 재배포 전 이미지는
# 태그 없는(dangling) 상태로 남는다. 실행 중인 컨테이너/다른 태그가 쓰는 이미지는
# dangling이 아니라서 안 지워지니 안전하다 — 매 배포마다 자동으로 치워준다.
echo "[cleanup] 태그 없는(dangling) 이미지 정리"
docker image prune -f
