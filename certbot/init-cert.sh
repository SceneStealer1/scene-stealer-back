#!/usr/bin/env bash
# 최초 1회: api.scene-stealer.site 용 Let's Encrypt 인증서를 발급한다.
#
# nginx는 443을 열려면 인증서 파일이 이미 있어야 기동된다 (없으면 config 로드 자체가
# 실패해서 80도 같이 죽는다). 그래서 순서가 이렇다:
#   1) 더미(자체서명) 인증서를 certbot_conf 볼륨에 먼저 만들어둔다.
#   2) 그 상태로 스택을 배포한다 (nginx가 더미 인증서로 443을 일단 띄움).
#   3) certbot으로 webroot 방식 실제 인증서를 발급받는다 (80의 /.well-known/... 를
#      nginx가 서빙해줘야 해서 2번이 먼저 필요하다).
#   4) nginx에 reload 신호를 보내 실제 인증서를 적용한다.
#
# 사용법:
#   CERTBOT_EMAIL=you@example.com ./certbot/init-cert.sh
#
# 사전 조건: DNS의 api.scene-stealer.site A 레코드가 이 EC2의 퍼블릭 IP를 가리키고,
# 보안그룹에서 80/443 인바운드가 열려 있어야 한다 (letsencrypt가 외부에서 접근함).
set -euo pipefail
cd "$(dirname "$0")/.."

STACK_NAME=scene-stealer
DOMAIN="${1:-${DOMAIN:-api.scene-stealer.site}}"
EMAIL="${CERTBOT_EMAIL:-}"
CONF_VOL="${STACK_NAME}_certbot_conf"
WWW_VOL="${STACK_NAME}_certbot_www"

if [ -z "$EMAIL" ]; then
  echo "[init-cert] CERTBOT_EMAIL 환경변수가 필요하다 (인증서 만료 경고 수신용)." >&2
  echo "  예: CERTBOT_EMAIL=you@example.com $0" >&2
  exit 1
fi

echo "[init-cert] domain=${DOMAIN} email=${EMAIL}"

echo "[init-cert] 1/4 더미 인증서 생성 (${CONF_VOL})"
docker run --rm --entrypoint sh \
  -v "${CONF_VOL}:/etc/letsencrypt" \
  alpine:3.20 -c "
    set -e
    mkdir -p /etc/letsencrypt/live/${DOMAIN}
    apk add --no-cache openssl >/dev/null
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
      -keyout /etc/letsencrypt/live/${DOMAIN}/privkey.pem \
      -out /etc/letsencrypt/live/${DOMAIN}/fullchain.pem \
      -subj '/CN=${DOMAIN}'
  "

echo "[init-cert] 2/4 스택 빌드 + 배포 (더미 인증서로 443 기동)"
./build-deploy.sh

echo "[init-cert] nginx가 뜰 시간을 좀 준다"
sleep 5

echo "[init-cert] 3/4 certbot으로 실제 인증서 발급 (webroot, ${WWW_VOL})"
docker run --rm \
  -v "${CONF_VOL}:/etc/letsencrypt" \
  -v "${WWW_VOL}:/var/www/certbot" \
  certbot/certbot certonly \
    --webroot --webroot-path /var/www/certbot \
    --email "${EMAIL}" --agree-tos --no-eff-email \
    --cert-name "${DOMAIN}" \
    -d "${DOMAIN}"

echo "[init-cert] 4/4 nginx reload (실제 인증서 적용)"
CID="$(docker ps -q -f "name=${STACK_NAME}_nginx")"
if [ -z "$CID" ]; then
  echo "[init-cert] 실행 중인 ${STACK_NAME}_nginx 컨테이너를 못 찾았다 — 수동으로 확인할 것." >&2
  exit 1
fi
docker exec "$CID" nginx -s reload

echo "[init-cert] done. https://${DOMAIN} 확인해볼 것."
echo "[init-cert] 갱신은 crontab에 certbot/renew.sh 를 등록해서 처리한다 (README 참고)."
