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
| `backend/` | (FastAPI) `videos`/`anomaly_events` 조회 API + 매장/카메라/디바이스 관리 API. Supabase Auth 로그인 필요 — 대시보드가 이걸 통해 결과를 본다 |
| `nginx/` | 리버스 프록시 — 외부에 열리는 유일한 포트(80) |
| `supabase/schema.sql` | DB 스키마(테이블 + RLS + Storage 버킷). 새 Supabase 프로젝트의 SQL Editor에서 한 번 실행 |
| [`docs/ux-backend-design.md`](docs/ux-backend-design.md) | 프론트 UX 와이어프레임(2a~2m) 기준 API/실시간/데이터모델 설계 문서 — 뭐가 됐고 뭐가 아직 안 됐는지는 여기가 최신 |

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

## backend 조회 API

로그인은 backend가 아니라 **Supabase Auth**가 처리한다 — 프론트가 supabase-js로
회원가입/로그인해서 access token(JWT)을 받고, 아래 세 라우트를 호출할 때
`Authorization: Bearer <token>` 헤더로 실어 보낸다. backend는 그 토큰을 검증해서
`sub`(=`auth.users.id`)를 뽑아내고, 그 유저 소유의 `videos`/`anomaly_events`만 보여준다
— 다른 유저의 `videos/:id`를 요청하면 `404`로 숨긴다.

| | |
|---|---|
| `GET /videos?limit=&status=` | (로그인 필요) 내 영상 목록 + 영상별 이상행동 건수 |
| `GET /videos/:id` | (로그인 필요) 내 영상 하나 + 그 영상의 하이라이트 클립들(signed URL 포함) |
| `GET /clips?limit=` | (로그인 필요) 내 매장/카메라를 가로지르는 하이라이트 클립 피드 (프론트가 주로 쓸 API) |
| `GET /me` | (로그인 필요) 내 계정 프로필(`profiles` 테이블 — 담당자명/연락처) |

클립 응답 모양(`ClipDto`, `backend/app/clips.py`):

```jsonc
{
  "id": "...", "videoId": "...", "userId": "3fa2...-uuid", "storeId": "store-gangnam-01",
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

- `Authorization` 헤더가 없거나 토큰이 유효하지 않으면(만료 포함) `401`.
- Supabase가 아직 설정 안 됐으면(`.env`의 `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` 공백)
  이 네 라우트는 `503`을 반환한다 — ingest-worker의 업로드 수신 자체는 계속 동작한다.
- `profiles`(담당자/연락처) 수정은 backend에 API가 없다 — RLS로 본인 행 update가
  이미 허용돼 있어서, 프론트가 `supabase-js`로 직접 `update`하면 된다.
- `SUPABASE_JWT_SECRET`이 비어있어도 같은 이유로 `503`(Supabase 대시보드 > Project
  Settings > API > JWT Settings 에서 확인).
- FastAPI가 자동 생성하는 API 문서: `http://localhost/docs` (Swagger UI).

## 매장 / 카메라 / 디바이스(PC 앱) 관리 API

유저 1명이 매장을 여러 개 가질 수 있다(`stores.owner_user_id`). 전부 로그인 필요,
본인 소유 매장만 보인다. 전체 설계 배경/열린 질문은
[`docs/ux-backend-design.md`](docs/ux-backend-design.md) 참고 — 위험 종류
분류(`risk_type`)·AI 자연어 설명·실제 FCM 발송·증거 묶음(PDF) 생성은 아직 없다.

| | |
|---|---|
| `GET/POST /stores`, `GET/PATCH /stores/:id` | 매장 목록/생성/조회/설정 변경 |
| `GET /stores/:id/status` | 카메라 연결 수, 마지막 분석 시각, 알림 빠르기, 일시중지 여부 |
| `GET/POST /stores/:id/cameras`, `PATCH/DELETE .../cameras/:id`, `PATCH .../cameras/reorder` | 카메라 CRUD (매장당 `camera_limit`, 기본 8대) |
| `POST /stores/:id/cameras/:id/heartbeat` | PC 앱이 **디바이스 토큰**으로 카메라 연결 상태 보고 |
| `POST /stores/:id/devices/pairing`, `?replace=true` | 이 PC를 매장에 등록해 디바이스 토큰 발급(이미 있으면 409, replace로 교체) |
| `POST /pc/pairing/start`, `GET /pc/pairing/:code`, `POST /pc/pairing/:code/claim` | QR 페어링 — 로그인 안 한 PC가 코드를 띄우면 모바일이 스캔해서 매장에 연결 |
| `GET /devices/:id`, `POST /devices/:id/commands`, `GET /devices/:id/commands/pending` | 디바이스 조회, 원격 명령(재시작) 등록/PC가 **디바이스 토큰**으로 수신 |
| `POST/DELETE /me/push-tokens` | 모바일 푸시 토큰 등록/해제 (저장만 — 발송 미구현) |
| `GET/PATCH /clips/:id` | 클립 하나 조회, 확인/오탐 처리·메모·신고 여부 (`status`/`note`/`reportedToPolice`) |

**두 가지 인증 방식이 있다**: 사람이 로그인해서 쓰는 라우트는 지금까지와 같은 Supabase
JWT(`Authorization: Bearer <user-jwt>`)를 쓰고, PC 앱이 스스로 보내는 heartbeat/명령
폴링은 위에서 발급받은 **디바이스 토큰**(`Authorization: Bearer <device-token>`)을
쓴다 — 서로 다른 토큰이고 바꿔 쓸 수 없다.

## 로그인 / 매장 운영자 계정

로그인 화면(회원가입/로그인 폼) 자체는 이 리포에 없다 — 프론트가 `supabase-js`로
Supabase Auth를 직접 호출하는 구조라, 이 리포는 "그렇게 발급된 토큰을 검증하는 쪽"만
맡는다.

**기본 흐름(권장)**: 회원가입 → 로그인 → `POST /stores`로 매장 생성 → 그 매장에서
`POST /stores/:id/devices/pairing`을 호출해 PC용 디바이스 토큰 발급 → PC 앱이 그
토큰으로 `/v1/segments`에 업로드. 매장이 여러 개면 매장마다 반복.

**개발/테스트용 대체 경로(레거시)**: `.env`의 `DEVICE_TOKENS`에 토큰을 정적으로
박아두는 방식도 계속 동작한다(ingest-worker가 두 경로를 다 본다 — 정적 먼저, 없으면
DB 조회). 이 경로를 쓰려면:

1. Supabase 대시보드 > Authentication > Users > **Add user**로 매장 운영자 계정을
   하나 만든다(`auth.users` 트리거가 자동으로 `public.profiles` 빈 행을 만든다).
2. 방금 만든 유저의 **User UID**(uuid)를 복사한다.
3. Supabase SQL Editor에서 `insert into public.stores (owner_user_id, name) values ('<uuid>', '강남 1호점')`로 매장을 하나 만들고 그 `id`를 확인한다(정적 경로는 `/stores` API를 안 거치므로 수동으로 넣어야 한다).
4. `.env`의 `DEVICE_TOKENS`에서 해당 항목의 `userId`를 이 uuid로, `storeId`를 방금 만든
   `stores.id`로 채운다 (`.env.example` 참고 — `userId`가 임의 문자열이면 영상 업로드 시
   `auth.users` FK 위반으로 insert가 실패한다).
5. 그 계정으로 로그인하면 `backend`의 `/videos`, `/videos/:id`, `/clips`가 그 매장
   소유 데이터만 보여준다.

## 로컬 개발

```bash
docker compose up --build
curl http://localhost/healthz     # -> backend
```

`backend`만 따로 띄우고 싶으면(Supabase 프로젝트는 이미 있다는 전제):

```bash
cd backend
pip install -r requirements.txt
cp ../.env.example ../.env   # SUPABASE_URL/SUPABASE_SERVICE_KEY/SUPABASE_JWT_SECRET 채우기
uvicorn app.main:app --reload --port 8081
```

## 배포 (Docker Compose + Swarm)

```bash
cp .env.example .env   # DEVICE_TOKENS(userId는 auth.users uuid) 채우고, Supabase 준비되면
                        # SUPABASE_URL/SUPABASE_SERVICE_KEY/SUPABASE_JWT_SECRET 채우기
./build-deploy.sh        # 코드가 바뀌었을 때: 네 이미지 빌드 후 바로 배포
./deploy.sh              # .env만 바뀌었을 때: 재빌드 없이 docker stack deploy만 (필요하면 swarm 자동 init)
```

Supabase 프로젝트를 새로 만들면 `supabase/schema.sql` 을 그 프로젝트 SQL Editor에서
먼저 실행해야 한다 (`videos`, `anomaly_events` 테이블 + `videos`/`clips` Storage 버킷 생성).

이미 예전 버전(`user_id`가 `text`였던 시절)의 `schema.sql`로 만들어둔 프로젝트라면,
`schema.sql`을 다시 실행하기 전에 `supabase/migrate_user_id_to_auth_uuid.sql`을 먼저
실행해서 `user_id`를 `auth.users` FK(uuid)로 안전하게 옮겨야 한다 — 파일 상단 주석에
안전장치(유효하지 않은 값 있으면 아무것도 안 바꾸고 에러로 멈춤)와 사용법이 있다.

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
