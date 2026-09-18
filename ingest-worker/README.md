# ingest-worker

`cctv-agent-electron` 이 매장에서 보내는 5분 단위 mp4 조각(`POST /v1/segments`)을 받아
로컬 디스크에 쌓아 두는 인제스트 워커. 지금은 뒷단 백엔드(DB, 분석 파이프라인)가 없는
상태라 **받아서 저장하는 것까지만** 한다 — 나중에 저장소/분석이 붙으면
`src/segmentStore.ts` 의 `saveSegment` 호출부만 바꾸면 된다.

`scene-stiller`, `cctv-agent-electron` 저장소는 건드리지 않는다 — 완전히 독립된 서비스다.

빌드/배포는 이 폴더가 아니라 **저장소 루트(`scene-stealer-back/`)** 에서 한다 — `backend`,
`nginx` 와 한 스택으로 묶여 있다. 자세한 건 루트 `README.md` 참고.

## 계약

`cctv-agent-electron/docs/protocol-flow.md` 5절이 원본이다. 이 워커는 그 계약을 그대로 구현한다.

```http
POST /v1/segments
Content-Type: multipart/form-data
Authorization: Bearer <deviceToken>
Idempotency-Key: <segmentId>

  part "meta"  : application/json  (SegmentMeta, src/segmentMeta.ts 참고)
  part "video" : video/mp4
```

| 응답 | 의미 |
|---|---|
| `201` | 정상 수신 — 디스크에 저장 완료 |
| `409` | 이미 받은 `segmentId` (멱등) |
| `400` | meta 스키마 위반 / Idempotency-Key 불일치 |
| `401` | 토큰 없음 또는 `DEVICE_TOKENS` 에 없는 토큰 |
| `403` | `storeId` 가 토큰에 등록된 매장과 다름 |
| `413` | 조각이 `MAX_SEGMENT_BYTES` 초과 |
| `5xx` | 저장 실패 — 에이전트가 지수 백오프로 재시도 |

## 저장 방식

- 디바이스 인증: DB 없이 `DEVICE_TOKENS` 환경변수(JSON 배열)로 토큰 → `storeId` 를 매핑한다.
  루트 `.env.example` 참고. 나중에 실제 발급/폐기 절차가 필요해지면 `src/deviceAuth.ts` 만
  DB 조회로 바꾸면 된다.
- 조각 저장: `STORAGE_DIR/<storeId>/<camera.id>/<segmentId>.mp4` + 같은 이름의 `.json`
  (meta 원본, 참고용). 멱등 처리는 mp4 파일의 배타적 생성(`wx` 플래그)에 기댄다 — 이미
  존재하면 즉시 `409`.

## 로컬에서 이 서비스만 띄우기

```bash
npm install
PORT=8080 STORAGE_DIR=./data DEVICE_TOKENS='[{"token":"dev","storeId":"store-1"}]' npm run dev
```

전체 스택(nginx 포함)을 로컬에서 띄우려면 루트에서 `docker compose up --build` 를 쓴다.

## 배포

루트 `README.md` 의 "배포" 절 참고 — `scene-stealer-back/build-deploy.sh` 로
`ingest-worker` + `backend` + `nginx` 를 한 번에 스웜에 올린다.
