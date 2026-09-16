# 2026hackathon

scene-stealer 백엔드. 네 서비스로 구성된다.

```
                    ┌────────────────────────┐
 cctv-agent  ──80──▶│ nginx                  │
 대시보드    ──80──▶│  /v1/segments → ingest-worker:8080
                    │  /*           → backend:8081
                    └────────────────────────┘

 ingest-worker ──(로컬 디스크에 저장)
               └─(Supabase 있으면)─▶ Storage 'videos' + videos 테이블(status='uploaded')
                                              │
                                     ai-worker 가 폴링
                                              ▼
                          포즈추출 → 이상행동탐지 → 클립(±5초) 추출
                                              │
                                     Storage 'clips' + anomaly_events 테이블
                                              ▲
                                     backend 가 조회해서 프론트에 제공
```

| 폴더 | 역할 |
|---|---|
| [`ingest-worker/`](ingest-worker/README.md) | `cctv-agent-electron` 이 보내는 5분 mp4 조각을 받아 로컬 디스크에 저장하고, Supabase 로 ai-worker 에 핸드오프 |
| [`ai-worker/`](ai-worker/README.md) | 포즈 추출(YOLO11-pose) → 이상행동 탐지(오토인코더) → 하이라이트 클립(±5초 패딩) 추출 |
| `backend/` | `videos`/`anomaly_events` 조회 API. 대시보드가 이걸 통해 결과를 본다 |
| `nginx/` | 리버스 프록시 — 외부에 열리는 유일한 포트(80) |

`ingest-worker`/`ai-worker`/`backend` 는 스택 내부 네트워크에서만 보이고, 호스트에
포트를 열지 않는다 (`ai-worker` 는 애초에 HTTP 서버가 아니라 폴링 데몬).

## `POST /v1/segments` 응답에 대해

에이전트(cctv-agent-electron)는 응답 body를 읽지 않고 **상태 코드만** 본다
(`201`=성공/삭제, `409`=중복, `401/403`=중단, `5xx`=재시도 — 계약은
`cctv-agent-electron/docs/protocol-flow.md` 5절). 그래서 `analysis: "pending"`
같은 필드를 body에 넣어도 에이전트 동작에는 영향이 없다 — 사람이 로그/curl로
확인하기 위한 것일 뿐이다.

실제 분석 결과("이상하다/아니다")는 이 응답으로 오는 게 아니라 **비동기로** 처리된다:
5분 영상 분석에는 수 분이 걸릴 수 있는데, 응답 전에 기다리면 에이전트가 카메라를
돌아가며 한 번에 하나씩만 올리는 순차 업로드 큐 전체가 막히기 때문이다
(protocol-flow.md 6절). 그래서 ingest-worker는 로컬 저장이 끝나면 즉시 `201`을
보내고, Supabase 핸드오프 + 분석은 백그라운드에서 진행한다. 프론트는 이 결과를
`backend`의 조회 API로 나중에 가져간다.

## backend API

전체 명세는 **[`docs/api-contract.md`](docs/api-contract.md)** 에 있다 — 프론트(`cctv-agent`)와
같은 사본을 들고 있는 단일 출처이고, 한쪽을 고치면 다른 쪽도 같이 고친다.

### 도메인 API (제품 화면이 쓰는 것)

| | |
|---|---|
| `GET/POST /stores`, `GET/PATCH /stores/:id` | 매장 |
| `POST /stores/:id/devices` | PC 등록 → 기기 토큰 발급 (평문은 이때 한 번만) |
| `GET/POST /stores/:id/cameras`, `PATCH/DELETE /cameras/:id` | 카메라 |
| `GET /stores/:id/monitoring` | 감시 상태 — "감시 중 4/5대", PC 온라인 |
| `GET /stores/:id/events` | 이벤트 목록 (날짜·카메라·종류·위험도·상태 필터) |
| `GET /events/:id`, `PATCH /events/:id/state`, `PATCH /events/:id/memo` | 이벤트 상세·확인/오탐·메모 |
| `GET /stores/:id/events/timeline` | 하루 타임라인 + **영상 없음 구간** |
| `GET /events/:id/nearby-cameras` | 같은 시각 다른 카메라 |
| `GET /stores/:id/stream` | **SSE** — 새 이벤트·상태 변경·카메라 상태 |
| `GET/PUT /stores/:id/notification-settings` | 종류별 on/off·민감도·조용한 구간 |
| `POST /push/devices` | FCM/APNs 토큰 |

### 에이전트 API (기기 토큰)

| | |
|---|---|
| `POST /v1/segments` | 조각 업로드 (기존 계약 그대로) |
| `POST /v1/devices/heartbeat` | PC 상태 + 카메라 런타임 상태 |

### 저수준 조회 (AI 파이프라인 기록 — 디버깅용)

| | |
|---|---|
| `GET /videos?limit=&status=` | 최근 영상 목록 + 영상별 이상행동 건수 |
| `GET /videos/:id` | 영상 하나 + 그 영상의 하이라이트 클립들(signed URL 포함) |
| `GET /clips?limit=&userId=` | 매장/카메라를 가로지르는 하이라이트 클립 피드 |

### AI 게이트가 아직 없다

`ai-worker` 는 오토인코더 이상점수만 낸다 — **위험 종류(절도/쓰러짐 등 7종)를 판정하지
못한다.** 그 자리는 별도 게이트가 채우고, 붙일 위치와 인터페이스는
**[`docs/ai-gate-contract.md`](docs/ai-gate-contract.md)** 에 있다. 게이트가 없어도
`kind='unknown'` + 점수 기반 위험도로 채워져 파이프라인은 끝까지 돈다.

클립 응답 모양(`ClipDto`, `backend/src/clips.ts`):

```jsonc
{
  "id": "...", "videoId": "...", "userId": "owner-1", "storeId": "store-gangnam-01",
  "cameraLocation": "계산대",
  "startAt": "2026-09-07T14:32:10.000Z",  // videos.recorded_started_at + start_time_sec
  "endAt": "2026-09-07T14:32:25.000Z",
  "startTimeSec": 130.0, "endTimeSec": 145.0,   // 원본 5분 조각 기준 상대시간(패딩 전)
  "anomalyScore": 0.83, "threshold": 0.61,
  "clipUrl": "https://.../clips/...?token=...",     // ±5초 패딩된 실제 클립, signed URL
  "thumbnailUrl": "https://.../clips/...?token=...",
  "createdAt": "2026-09-07T14:40:02.000Z"
}
```

Supabase가 아직 설정 안 됐으면(`.env`의 `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` 공백)
이 세 라우트는 `503`을 반환한다 — ingest-worker의 업로드 수신 자체는 계속 동작한다.

## 로컬 개발

```bash
docker compose up --build
curl http://localhost/healthz     # -> backend
```

## 배포 (Docker Compose + Swarm)

```bash
cp .env.example .env   # DEVICE_TOKENS 채우고, Supabase 준비되면 SUPABASE_URL/KEY 채우기
./build.sh               # 네 이미지 빌드
./deploy.sh              # docker stack deploy (필요하면 swarm 자동 init)
```

Supabase 프로젝트를 새로 만들면 `supabase/schema.sql` 을 그 프로젝트 SQL Editor에서
먼저 실행해야 한다 (`videos`, `anomaly_events` 테이블 + `videos`/`clips` Storage 버킷 생성).

- `docker-compose.yml` 은 `docker stack deploy` 로 올리는 컴포즈 파일이다. `deploy:` 섹션
  (재시작 정책, 자원 제한)은 스웜 모드에서만 적용된다.
- `deploy.sh` 는 매번 서비스를 지우지 않고 `docker stack deploy` 로 갱신한다. 같은 스택
  이름(`scene-stealer`)으로 다시 실행하면 롤링 업데이트된다.
- 조각은 이름 있는 볼륨 `ingest_segments` 에 쌓인다. 내용물 확인:
  ```bash
  docker run --rm -v scene-stealer_ingest_segments:/data alpine ls -R /data
  ```
- `ai-worker` 는 CPU로도 돌아가지만 5분 영상 하나 분석에 수 분 걸릴 수 있다 — 메모리
  제한(`docker-compose.yml`의 `ai-worker.deploy.resources.limits.memory`, 기본 2G)을
  실제 서버 사양에 맞춰 조정할 것.
- 여러 노드로 구성된 스웜이라면 이미지를 레지스트리에 푸시하고 `.env` 의
  `INGEST_WORKER_IMAGE`/`AI_WORKER_IMAGE`/`BACKEND_IMAGE`/`NGINX_IMAGE` 를 그 태그로
  바꿔야 한다. 단일 노드 스웜(기본 전제)에서는 로컬 빌드만으로 충분하다.
- **TLS 없음.** 지금은 nginx 가 80 포트로 평문 HTTP만 받는다. 외부 인터넷에 노출하기 전에
  도메인을 잡고 `nginx/nginx.conf` 에 443 서버 블록 + 인증서(예: certbot)를 추가할 것.

상태 확인 / 로그 / 종료:

```bash
docker stack services scene-stealer
docker service logs -f scene-stealer_ingest-worker
docker service logs -f scene-stealer_ai-worker
docker stack rm scene-stealer
```
