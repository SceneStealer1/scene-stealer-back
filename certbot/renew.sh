#!/usr/bin/env bash
# 인증서 갱신. crontab에 등록해서 주기적으로 돌린다 (Let's Encrypt 인증서는 90일
# 유효, certbot은 만료 30일 이내일 때만 실제로 갱신하고 나머지는 그냥 종료한다).
#
# crontab -e 예시 (매일 새벽 3시):
#   0 3 * * * cd /path/to/scene-stealer-back && ./certbot/renew.sh >> /var/log/certbot-renew.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

STACK_NAME=scene-stealer
CONF_VOL="${STACK_NAME}_certbot_conf"
WWW_VOL="${STACK_NAME}_certbot_www"

docker run --rm \
  -v "${CONF_VOL}:/etc/letsencrypt" \
  -v "${WWW_VOL}:/var/www/certbot" \
  certbot/certbot renew \
    --webroot --webroot-path /var/www/certbot \
    --quiet

# nginx -s reload는 워커를 새로 띄우는 graceful reload라 기존 연결을 끊지 않는다.
# 갱신이 실제로 안 일어난 날에도 그냥 reload하는 쪽이 "갱신됐는지" 체크하는 것보다
# 단순하고, reload 자체는 비용이 거의 없다.
CID="$(docker ps -q -f "name=${STACK_NAME}_nginx")"
if [ -z "$CID" ]; then
  echo "[renew] 실행 중인 ${STACK_NAME}_nginx 컨테이너를 못 찾았다 — 인증서는 갱신됐어도 nginx에 반영 안 됐을 수 있다." >&2
  exit 1
fi
docker exec "$CID" nginx -s reload

echo "[renew] done."
