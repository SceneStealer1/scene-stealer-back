# HTTPS 인증서 (Let's Encrypt / certbot)

`nginx`를 컨테이너로만 돌리는 구조라, EC2 호스트에 certbot을 직접 설치하지 않고
`certbot/certbot` 공식 이미지를 `docker run`으로 그때그때 실행하는 방식을 쓴다.
webroot 방식(80번 포트로 도메인 소유를 증명)이라 nginx를 잠깐 내리거나 별도
스탠드얼론 서버를 띄울 필요가 없다.

인증서(`certbot_conf`)와 ACME challenge 파일(`certbot_www`)은 docker-compose.yml에
정의된 이름 있는 볼륨이고, 스택 배포 시 `scene-stealer_certbot_conf` /
`scene-stealer_certbot_www` 로 생성된다. nginx 컨테이너와 이 스크립트들이 같은
볼륨을 마운트해서 파일을 주고받는다.

## 사전 조건

- 가비아에서 설정한 DNS A 레코드(`api.scene-stealer.site`)가 이 EC2 인스턴스의
  퍼블릭 IP를 가리키고 있어야 한다 (전파까지 몇 분~몇 시간 걸릴 수 있음).
- EC2 보안그룹 인바운드에 **80, 443**이 열려 있어야 한다 (Let's Encrypt가 80으로
  도메인 소유를 확인하러 외부에서 접근한다).
- Docker Swarm이 초기화돼 있어야 한다 (`deploy.sh`가 자동으로 해준다).

## 최초 발급

```bash
CERTBOT_EMAIL=you@example.com ./certbot/init-cert.sh
```

내부적으로: 더미 인증서 생성 → `./build-deploy.sh`로 스택 배포(더미 인증서로 443
기동) → certbot webroot로 실제 인증서 발급 → nginx에 `nginx -s reload` 신호.
끝나면 `https://api.scene-stealer.site`가 열려야 한다.

## 갱신

Let's Encrypt 인증서는 90일짜리다. crontab에 등록해서 주기적으로 돌려둔다:

```bash
crontab -e
# 매일 새벽 3시에 체크 (만료 30일 이내일 때만 실제로 갱신함)
0 3 * * * cd /path/to/scene-stealer-back && ./certbot/renew.sh >> /var/log/certbot-renew.log 2>&1
```

## 코드가 바뀌어서 `./build-deploy.sh`를 다시 돌릴 때

인증서는 `certbot_conf` 볼륨에 그대로 남아있으니 다시 발급받을 필요 없다. nginx
이미지를 새로 빌드/배포해도 볼륨 마운트는 그대로 유지된다.

## http(80)를 https로 강제 리다이렉트하고 싶다면

지금은 80도 443과 동일하게 계속 평문으로 서비스한다 — `cctv-agent-electron`
쪽 설정이 아직 `http://`로 박혀 있는 매장 PC가 있을 수 있어서, 그것들이 갑자기
끊기지 않게 하려는 의도다. 모든 매장 PC의 에이전트 설정을 `https://`로 옮기고
나면, `nginx/nginx.conf`의 80 서버 블록에서 `/v1/segments`와 `/` location을
지우고 다음으로 바꾸면 된다:

```nginx
location / {
    return 301 https://$host$request_uri;
}
```

(단, 리다이렉트는 GET 기준으로 동작하는 클라이언트가 있어 `/v1/segments`의
POST 업로드가 깨질 수 있다 — 에이전트가 실제로 `https://` 엔드포인트로 요청을
보내는지 먼저 확인하고 바꿀 것.)
