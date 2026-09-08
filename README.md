# 2026hackathon

scene-stealer 백엔드. 세 서비스를 nginx 뒤에 두고 하나의 스택으로 배포한다.

```
                    ┌────────────────────────┐
 cctv-agent  ──80──▶│ nginx                  │
 대시보드    ──80──▶│  /v1/segments → ingest-worker:8080
                    │  /*           → backend:8081
                    └────────────────────────┘
```

| 폴더 | 역할 |
|---|---|
| [`ingest-worker/`](ingest-worker/README.md) | `cctv-agent-electron` 이 보내는 5분 mp4 조각을 받아 로컬 디스크에 저장 |
| `backend/` | 대시보드/분석 파이프라인용 API. 지금은 헬스체크뿐인 스켈레톤 |
| `nginx/` | 리버스 프록시 — 외부에 열리는 유일한 포트(80) |

`ingest-worker`, `backend` 는 스택 내부 네트워크에서만 보이고, 호스트에 포트를 열지 않는다.

## 로컬 개발

```bash
docker compose up --build
curl http://localhost/healthz     # -> backend
```

## 배포 (Docker Compose + Swarm)

```bash
cp .env.example .env   # DEVICE_TOKENS 채우기 (JSON 값은 작은따옴표로 감쌀 것)
./build.sh               # 세 이미지 빌드
./deploy.sh              # docker stack deploy (필요하면 swarm 자동 init)
```

- `docker-compose.yml` 은 `docker stack deploy` 로 올리는 컴포즈 파일이다. `deploy:` 섹션
  (재시작 정책, 자원 제한)은 스웜 모드에서만 적용된다.
- `deploy.sh` 는 매번 서비스를 지우지 않고 `docker stack deploy` 로 갱신한다. 같은 스택
  이름(`scene-stealer`)으로 다시 실행하면 롤링 업데이트된다.
- 조각은 이름 있는 볼륨 `ingest_segments` 에 쌓인다. 내용물 확인:
  ```bash
  docker run --rm -v scene-stealer_ingest_segments:/data alpine ls -R /data
  ```
- 여러 노드로 구성된 스웜이라면 이미지를 레지스트리에 푸시하고 `.env` 의
  `INGEST_WORKER_IMAGE`/`BACKEND_IMAGE`/`NGINX_IMAGE` 를 그 태그로 바꿔야 한다.
  단일 노드 스웜(기본 전제)에서는 로컬 빌드만으로 충분하다.
- **TLS 없음.** 지금은 nginx 가 80 포트로 평문 HTTP만 받는다. 외부 인터넷에 노출하기 전에
  도메인을 잡고 `nginx/nginx.conf` 에 443 서버 블록 + 인증서(예: certbot)를 추가할 것.

상태 확인 / 로그 / 종료:

```bash
docker stack services scene-stealer
docker service logs -f scene-stealer_ingest-worker
docker stack rm scene-stealer
```
