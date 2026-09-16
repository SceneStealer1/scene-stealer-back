# Scene Stealer — API 계약 (프론트·백엔드 공유 단일 출처)

이 문서는 **두 레포가 같은 사본을 들고 있다.** 한쪽을 고치면 다른 쪽도 같이 고친다.

| 레포 | 경로 | 역할 |
|---|---|---|
| `scene-stealer-back` | `docs/api-contract.md` | 이 계약을 **구현**한다 |
| `cctv-agent` | `docs/api-contract.md` | 이 계약에 **붙는다** (mock → 실제) |

근거 문서: `handoff/API-요구사항.md` (기능 번호 1.1 ~ 7.2는 그 문서의 번호를 그대로 쓴다).

---

## 0. 지금 있는 것 / 새로 만드는 것

기존 백엔드(`scene-stealer-back` main)에 실제로 있던 것은 이게 전부다:

- 테이블 `videos`, `anomaly_events`
- `POST /v1/segments` (ingest-worker), `GET /videos`, `GET /videos/:id`, `GET /clips`
- 인증: Supabase Auth 이메일 JWT / 기기는 `DEVICE_TOKENS` **환경변수 하드코딩**

요구사항 43개 중 매핑되는 것은 3.1·3.2·3.4·5.1뿐이었다. 나머지는 이 계약에서 새로 만든다.

**보존 원칙**

- `POST /v1/segments`의 **요청 모양(SegmentMeta)과 상태 코드 계약은 바꾸지 않는다.** 이미 배포된 에이전트가 깨진다.
  단, 토큰 검증만 환경변수 → `devices` 테이블 조회로 바꾼다 (§4.2).
- `videos` / `anomaly_events`는 **그대로 둔다.** AI 파이프라인이 쓰는 저수준 기록이다.
  사용자에게 보여주는 "위험 이벤트"는 그 위에 얹히는 새 테이블 `events`다 (§2.5).

---

## 1. 공통 규약

### 1.1 베이스 URL / 라우팅

```
https://<host>/v1/segments   → ingest-worker:8080   (조각 업로드. 기존 그대로)
https://<host>/v1/*          → ingest-worker:8080   (기기 토큰으로 인증하는 에이전트 전용 경로)
https://<host>/*             → backend:8081         (그 외 전부. 사용자 JWT)
```

`/internal/*`은 nginx가 **404로 막는다** — 워커들이 스택 내부 네트워크에서
`backend:8081`을 직접 부르므로 외부에 열 이유가 없다 (§7, `nginx/nginx.conf`).

### 1.2 인증 — 두 종류

| 주체 | 헤더 | 검증 |
|---|---|---|
| 사용자 (PC 앱 사용자, 모바일) | `Authorization: Bearer <Supabase JWT>` | HS256, `SUPABASE_JWT_SECRET`, `aud=authenticated` → `sub` = `auth.users.id` |
| 기기 (PC 수집기 백그라운드) | `Authorization: Bearer <deviceToken>` | `devices.token_hash` 조회 (§4.2) |

**기기 토큰은 사용자 JWT가 아니다.** PC 앱은 둘 다 들고 있다 — 화면은 사용자 JWT로,
업로드·하트비트는 기기 토큰으로 호출한다.

### 1.3 에러 응답

기존 모양을 유지한다.

```json
{ "error": "사람이 읽는 한글 메시지" }
```

| 코드 | 의미 |
|---|---|
| 400 | 요청 스키마 위반 |
| 401 | 토큰 없음/만료/무효 |
| 403 | 토큰은 유효하나 그 매장/자원에 권한 없음 |
| 404 | 없음 **또는 남의 것** (존재 여부를 숨긴다) |
| 409 | 중복 (멱등 재전송 포함) |
| 413 | 조각이 너무 큼 |
| 503 | Supabase 미설정 |

### 1.4 필드 표기

- 응답 JSON은 **camelCase**, DB는 snake_case. 변환은 백엔드 책임.
- 시각은 전부 **ISO 8601 UTC, 밀리초, `Z`** (`2026-09-16T05:32:10.000Z`).
- **날짜(`date=YYYY-MM-DD`)는 매장 현지 하루다** — UTC 하루가 아니다. 서버는 `STORE_TIMEZONE`
  (기본 `Asia/Seoul`)의 자정~다음 자정으로 자른다. UTC 로 자르면 한국 매장의 자정~오전 9시
  이벤트(새벽 노숙·취침 등)가 전날 기록으로 빠진다. 클라이언트가 경계를 직접 정하고 싶으면
  `from`/`to` 에 UTC 시각을 넣는다.
- id는 전부 **uuid 문자열**. 단 `agentCameraId`·`deviceId`는 PC가 만드는 **text**다 (§2.3, §2.4).

### 1.5 페이지네이션

커서 방식. 응답에 `nextCursor`가 있으면 더 있다.

```
?limit=20&cursor=<opaque>
→ { "items": [...], "nextCursor": "..." | null }
```

---

## 2. 데이터 모델

```
auth.users ──< store_members >── stores ──< devices
                                    │
                                    ├──< cameras ──< videos ──< anomaly_events
                                    │                   │            │
                                    │                   └──────┬─────┘
                                    └──< events ◀──────────────┘
                                          │        (AI 게이트가 종류·위험도를 채운다)
                                          └──< event_state_changes
```

### 2.1 `stores` — 매장 (1.2, 1.3, 2.6)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | uuid PK | |
| `name` | text not null | |
| `address` | text | **112 신고 안내문(5.4)에 들어간다** |
| `opens_at` / `closes_at` | time | 운영시간. 조용한 구간(6.3) 판정에 쓴다 |
| `segment_seconds` | int not null default 60 | 30 / 60 / 300. UI 문구는 **'알림 빠르기'** |
| `clip_retention_days` | int not null default 30 | 5.5 |
| `created_at` / `updated_at` | timestamptz | |

### 2.2 `store_members` — 계정 1 : 매장 N

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `store_id` | uuid FK stores | PK 일부 |
| `user_id` | uuid FK auth.users | PK 일부 |
| `role` | text | `owner` / `staff` |

> 기존 `videos.user_id`는 소유자를 직접 들고 있어 매장 여러 개·직원 계정을 표현할 수 없었다.
> 권한 판정은 이제 **전부 이 테이블을 통한다.**

### 2.3 `devices` — 매장 PC 수집기 (1.4, 7.1)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | uuid PK | |
| `store_id` | uuid FK stores | |
| `device_id` | text not null | PC가 최초 실행 시 만든 값. `AgentConfig.deviceId` |
| `token_hash` | text not null | **평문 저장 금지.** sha256 |
| `label` | text | "강남점 PC" |
| `agent_version` | text | 하트비트가 갱신 |
| `last_heartbeat_at` | timestamptz | PC 온라인 판정 근거 (2.5, 6.6) |
| `spool_bytes` | bigint | |
| `uploaded_bytes_today` | bigint | |
| `revoked_at` | timestamptz | PC 교체 시 기존 것 |
| | | unique `(store_id, device_id)` |

**매장당 PC 1대.** 새 PC 등록 시 기존 활성 기기를 `revoked_at` 처리한다 (§3.3).

### 2.4 `cameras` — 카메라 (2.1 ~ 2.5)

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | uuid PK | 서버 카메라ID |
| `store_id` | uuid FK stores | |
| `agent_camera_id` | text not null | PC의 ONVIF XAddr 기반 안정 id = `SegmentMeta.camera.id` |
| `name` | text not null | |
| `location_tag` | text | `checkout` 계산대 / `entrance` 출입문 / `shelf` 진열대 / `dining` 취식대 / `storage` 창고 / `other` 기타 — **AI 컨텍스트로 쓴다** |
| `sort_order` | int default 0 | |
| `stream_profile` | text | `main` / `sub` |
| `runtime_state` | text default `unknown` | `connected` / `reconnecting` / `disconnected` / `auth_failed` |
| `last_frame_at` | timestamptz | 2.4 |
| `last_segment_at` | timestamptz | 마지막 업로드 |
| `state_updated_at` | timestamptz | |
| `deleted_at` | timestamptz | soft delete — 지난 이벤트가 카메라를 참조한다 |
| | | unique `(store_id, agent_camera_id)` |

**매장당 활성 카메라 ≤ 8.** 초과 시 `POST`는 400.

> RTSP URL·카메라 자격증명은 **서버에 올리지 않는다.** PC 로컬에만 둔다 (2.1).

### 2.5 `events` — 위험 이벤트 (4.1) ★ 핵심

`anomaly_events`(오토인코더 점수 구간)가 저수준 근거고, 이 테이블이 **사용자에게 보이는 것**이다.

```sql
create type risk_kind as enum (
  'theft',              -- 절도(미결제 반출)
  'vandalism',          -- 기물파손/폭력
  'dine_and_dash',      -- 취식 후 미결제
  'underage_purchase',  -- 미성년자 주류/담배
  'loitering',          -- 장시간 배회
  'sleeping',           -- 노숙/취침
  'collapse',           -- 쓰러짐 (응급 — 알림을 끌 수 없다)
  'unknown'             -- AI 게이트 미연결/실패 시 기본값
);
create type risk_level  as enum ('high','medium','low');
create type event_state as enum ('unconfirmed','confirmed','false_positive');
```

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | uuid PK | |
| `store_id` / `camera_id` | uuid FK | |
| `anomaly_event_id` | uuid FK anomaly_events, null | 근거 |
| `video_id` | uuid FK videos, null | 관련 조각 |
| `started_at` / `ended_at` | timestamptz not null | 절대시각 |
| `kind` | risk_kind default `unknown` | **AI 게이트가 채운다** |
| `risk` | risk_level not null | 게이트 미연결 시 점수 구간 추정 (§7) |
| `description` | text | AI 한글 설명 |
| `appearance` | text | 인상착의 |
| `bounding_boxes` | jsonb | `[{ "t": 1.2, "x": .., "y": .., "w": .., "h": .. }]` 정규화 0~1 |
| `ai_gate_status` | text default `pending` | `pending`/`done`/`failed`/`skipped` — §7 |
| `ai_gate_error` | text | |
| `anomaly_score` | double precision | |
| `state` | event_state default `unconfirmed` | 4.6 |
| `state_changed_at` / `state_changed_by` | | |
| `false_positive_reason` | text | |
| `memo` | text | 4.7 — 112 접수번호, 피해액 |
| `clip_storage_path` / `thumbnail_storage_path` | text | Storage `clips` 버킷 |
| `clip_expires_at` | timestamptz | `stores.clip_retention_days` 기준 |
| `created_at` | timestamptz | |

인덱스: `(store_id, started_at desc)`, `(store_id, state)`, `(camera_id, started_at desc)`

### 2.6 `event_state_changes` — 처리 이력 (4.5)

`event_id`, `from_state`, `to_state`, `changed_by`(uuid), `source`(`pc`/`mobile`/`system`), `reason`, `created_at`

### 2.7 `push_devices` (6.1)

`id`, `user_id`, `platform`(`ios`/`android`), `token`, `last_seen_at`, unique `(user_id, token)`

### 2.8 `notification_settings` (6.2)

`(store_id, user_id, kind)` PK, `enabled` bool, `sensitivity`(`low`/`medium`/`high`)

> **`kind='collapse'`는 `enabled`를 false로 바꿀 수 없다.** UI에서도 disabled 토글이고 API도 400.

### 2.9 `notification_quiet_hours` (6.3)

`(store_id, user_id)` PK, `business_hours_high_only` bool, `sleep_start`/`sleep_end` time,
`sleep_emergency_only` bool, `override_dnd_for_high` bool

### 2.10 기존 테이블 변경

| 테이블 | 변경 |
|---|---|
| `videos` | `store_id text` → `store_uuid uuid FK stores`, `camera_id text` → `camera_uuid uuid FK cameras` 컬럼 **추가**(기존 text 컬럼은 남겨 둔다 — 이미 들어간 행이 있다). 신규 insert는 둘 다 채운다. |
| `anomaly_events` | 변경 없음 |

---

## 3. 인증 · 계정 · 매장

### 3.1 휴대폰 번호 인증 (1.1) — **backend에 엔드포인트 없음**

Supabase Auth가 직접 처리한다. 프론트가 `supabase-js`로:

```ts
await supabase.auth.signInWithOtp({ phone: '+821012345678' })
await supabase.auth.verifyOtp({ phone, token: '123456', type: 'sms' })
// → session.access_token 을 이후 모든 요청의 Authorization 에 쓴다
```

> **사전 작업 필요:** Supabase 대시보드에서 Phone Auth 활성화 + SMS 공급자(Twilio 등) 설정.
> 미설정이면 OTP 발송이 실패한다. 기존 이메일 로그인도 그대로 동작한다.

토큰 갱신·로그아웃(1.6)도 `supabase-js`가 처리한다 (`refreshSession`, `signOut`).

### 3.2 매장 (1.2, 1.3)

| | |
|---|---|
| `GET /stores` | 내 매장 목록 |
| `POST /stores` | 생성 → 호출자를 `owner`로 `store_members`에 넣는다 |
| `GET /stores/:storeId` | |
| `PATCH /stores/:storeId` | `name`, `address`, `opensAt`, `closesAt`, `segmentSeconds`, `clipRetentionDays` |

`GET /stores` 응답:

```jsonc
{ "stores": [{
  "id": "uuid", "name": "강남점", "address": "서울시 ...",
  "opensAt": "09:00", "closesAt": "22:00",
  "segmentSeconds": 60,
  "cameraCount": 5,
  "device": { "id": "uuid", "deviceId": "pc-abc", "label": "강남점 PC",
              "online": true, "lastHeartbeatAt": "...Z" } | null,
  "unconfirmedCount": 2          // 4.9 — 목록에서 바로 배지를 그린다
}] }
```

### 3.3 PC 기기 등록 / 교체 (1.4)

| | |
|---|---|
| `POST /stores/:storeId/devices` | 사용자 JWT. body `{ deviceId, label?, agentVersion?, replaceExisting? }` |
| `GET /stores/:storeId/devices` | |
| `DELETE /devices/:id` | 폐기 (`revoked_at`) |

```jsonc
// 201
{ "id": "uuid", "deviceId": "pc-abc",
  "deviceToken": "ss_dev_xxxxxxxx",   // ★ 평문은 이때만 반환. 서버는 해시만 저장한다
  "replacedDeviceId": "uuid" | null }
```

매장에 이미 활성 기기가 있고 `replaceExisting`이 없으면 **409** + `{ "error": "...", "existingDevice": {...} }`.
PC 앱은 이걸 받아 "기존 PC가 있습니다. 교체할까요?"를 띄운다 (2a).

### 3.4 QR 페어링 (1.5) — **이번 범위 밖**

우선순위 2순위. 스키마 자리만 비워 둔다.

---

## 4. 기기 · 카메라 · 조각 (에이전트 경로)

### 4.1 하트비트 + 카메라 상태 보고 (7.1, 2.4)

**기기 토큰.** PC가 주기적으로(권장 30초) 한 번에 보낸다.

```
POST /v1/devices/heartbeat
{
  "agentVersion": "1.0.0",
  "spoolBytes": 123456789,
  "uploadedBytesToday": 987654321,
  "cameras": [
    { "agentCameraId": "onvif-...", "state": "connected",
      "lastFrameAt": "2026-09-16T05:31:58.000Z" }
  ]
}
→ 200 { "ok": true, "serverTime": "...Z", "segmentSeconds": 60 }
```

`state`: `connected` / `reconnecting` / `disconnected` / `auth_failed`
→ `cameras.runtime_state`, `last_frame_at`, `devices.last_heartbeat_at`을 갱신하고
변화가 있으면 **SSE로 `camera.state`를 발행한다** (§6).

응답의 `segmentSeconds`로 PC가 **서버 설정을 따라간다** (2.6 — 매장 설정이 단일 출처).

### 4.2 조각 업로드 (3.1, 3.2) — 기존 유지

```
POST /v1/segments
Authorization: Bearer <deviceToken>
Idempotency-Key: <meta.segmentId>
multipart: meta(json) + video(mp4)
```

**요청 모양·상태 코드는 바꾸지 않는다** (`201`/`409`/`401`/`403`/`413`/`5xx`).
`SegmentMeta`의 단일 출처는 `cctv-agent/src/shared/types.ts`다.

바뀌는 것은 **토큰 검증뿐**: `DEVICE_TOKENS` 환경변수 → `devices` 테이블 조회.
`meta.storeId`는 이제 `stores.id`(uuid)여야 한다. 토큰이 가리키는 매장과 다르면 403.

> 이행: 환경변수 방식을 즉시 없애지 말고, 테이블에 없으면 환경변수로 폴백하는 기간을 둔다.

### 4.3 카메라 등록 / 조회 / 수정 (2.1 ~ 2.3)

| | | 인증 |
|---|---|---|
| `POST /stores/:storeId/cameras` | `{ agentCameraId, name, locationTag, sortOrder?, streamProfile }` → `{ id }` | 사용자 JWT |
| `GET /stores/:storeId/cameras` | | 사용자 JWT |
| `PATCH /cameras/:id` | `name`, `locationTag`, `sortOrder` | 사용자 JWT |
| `DELETE /cameras/:id` | soft delete | 사용자 JWT |

같은 `(storeId, agentCameraId)`로 다시 POST하면 **409가 아니라 기존 행을 반환**한다 (재설치 멱등).
활성 카메라가 이미 8대면 400.

### 4.4 매장 감시 상태 (2.5)

```
GET /stores/:storeId/monitoring
→ {
  "device": { "online": true, "lastHeartbeatAt": "...Z", "agentVersion": "1.0.0",
              "spoolBytes": 0, "uploadedBytesToday": 0 } | null,
  "segmentSeconds": 60,
  "cameras": [{ "id": "uuid", "name": "계산대", "locationTag": "checkout",
                "state": "connected", "lastFrameAt": "...Z", "lastSegmentAt": "...Z",
                "disconnectedForSec": null }],
  "lastAnalyzedAt": "...Z" | null,
  "monitoringCount": 4, "totalCount": 5
}
```

2c 상단("감시 중 4/5대"), 모바일 2k 헤더, 2m("PC 꺼짐 2시간")이 전부 이걸 쓴다.

### 4.5 조각 조회 (3.3)

```
GET /stores/:storeId/segments?cameraId=&from=&to=&limit=
→ { "segments": [{ "videoId": "uuid", "cameraId": "uuid",
                   "sequence": 1284,        // (PC, 카메라)별 조각 번호 — 2f '조각 #1284'
                   "startedAt": "...Z", "endedAt": "...Z",
                   "durationSec": 60, "playbackUrl": "<signed>", "status": "done" }] }
```

2f의 "◂ 더 이전 / 더 이후" 조각 단위 이동과 동시각 다른 카메라(4.8)가 쓴다.
**원본 조각은 7일 보관** 후 삭제 (`videos` + Storage). 일 1회 정리 작업.

---

## 5. 위험 이벤트

### 5.1 목록 (4.3)

```
GET /stores/:storeId/events
  ?date=2026-09-16        (매장 현지 하루. 또는 from/to UTC 시각)
  &cameraId=&kind=&risk=&state=
  &limit=20&cursor=
→ { "items": [EventListItem], "nextCursor": null }
```

정렬은 **미확인 우선, 그 다음 최신순**.

```jsonc
// EventListItem
{ "id": "uuid",
  "cameraId": "uuid", "cameraName": "계산대", "locationTag": "checkout",
  "kind": "theft", "risk": "high", "state": "unconfirmed",
  "startedAt": "...Z", "endedAt": "...Z", "durationSec": 31,
  "description": "계산대 앞에서 한 명이 물건을 가방에 넣고 결제 없이 나갔습니다.",
  "thumbnailUrl": "<signed>",
  "aiGateStatus": "done" }
```

### 5.2 상세 (4.5)

```
GET /events/:id
→ { "event": { ...EventListItem,
      "appearance": "검은 후드티, 흰 운동화, 20대 남성 추정",
      "boundingBoxes": [...],
      "anomalyScore": 0.83,
      "memo": "112 접수 2026-1234",
      "clipUrl": "<signed>", "clipExpiresAt": "...Z",
      "segments": [{ "videoId": "...", "startedAt": "...", "playbackUrl": "..." }],
      "history": [{ "toState": "confirmed", "changedBy": "uuid",
                    "changedByName": "홍길동", "source": "mobile", "createdAt": "...Z" }] } }
```

### 5.3 상태 변경 (4.6)

```
PATCH /events/:id/state
{ "state": "confirmed" | "false_positive" | "unconfirmed",
  "reason": "사람 오인",        // false_positive일 때만
  "source": "pc" | "mobile" }
→ 200 { "event": {...} }
```

`event_state_changes`에 이력을 남기고 **SSE `event.updated`를 발행**한다 → PC/모바일 양쪽 동기.
`false_positive`는 재학습 큐(4.11)에도 적재한다 (내부, 지금은 테이블에 쌓기만).

### 5.4 메모 (4.7)

```
PATCH /events/:id/memo   { "memo": "..." }  → 200
```

### 5.5 하루 타임라인 (4.4)

```
GET /stores/:storeId/events/timeline?date=2026-09-16   (매장 현지 하루)
→ { "cameras": [{
      "cameraId": "uuid", "name": "계산대",
      "events": [{ "id": "...", "startedAt": "...", "endedAt": "...", "risk": "high", "kind": "theft" }],
      "gaps":   [{ "from": "...Z", "to": "...Z", "reason": "camera_disconnected" | "pc_offline" }]
    }] }
```

**`gaps`(영상 없음 구간)가 이 엔드포인트의 존재 이유다.** `videos` 행이 비어 있는 구간을
`stores.segment_seconds` 간격 기준으로 계산한다. 2e 타임라인이 공백을 정직하게 점선으로 그린다.

### 5.6 미확인 카운트 (4.9)

```
GET /stores/:storeId/events/unconfirmed-count?scope=today|all
→ { "count": 2 }
```

`GET /stores`에도 이미 들어 있다 — 내비 배지는 그걸 쓰고, 이건 폴링/갱신용.

### 5.7 동시각 다른 카메라 (4.8)

```
GET /events/:id/nearby-cameras
→ { "cameras": [{ "cameraId": "uuid", "name": "출입문",
                  "playbackUrl": "<signed>", "offsetSec": 12.4 }] }
```

이벤트 `started_at`을 포함하는 조각을 매장 내 모든 카메라에서 하나씩 찾아 준다.
`offsetSec` = 조각 시작 기준 이벤트 시각까지의 오프셋 → 플레이어가 그 지점부터 재생한다.
2d "다른 카메라", 2f 4분할이 쓴다.

### 5.8 주간 요약 (4.10) — 후순위

```
GET /stores/:storeId/events/summary?from=&to=
→ { "total": 12, "falsePositive": 3, "reported": 1,
    "byWeekday": [...], "byHour": [...] }
```

### 5.9 클립 (5.1)

```
GET /events/:id/clip  → { "url": "<signed>", "expiresAt": "...Z" }
```

서명 URL 만료는 `SIGNED_URL_TTL_SEC`(기본 3600). 클립 보관은 `stores.clip_retention_days`(기본 30일).

---

## 6. 실시간 채널 (4.2) — SSE

```
GET /stores/:storeId/stream
Accept: text/event-stream
Authorization: Bearer <사용자 JWT>
```

> **SSE를 고른 이유:** 서버→클라 단방향이면 충분하고(클라의 상태 변경은 평범한 PATCH다),
> Electron·RN 양쪽에서 재연결이 단순하고, nginx 설정이 웹소켓보다 가볍다.
> nginx에 `proxy_buffering off; proxy_read_timeout 1h;`가 필요하다.

| 이벤트 | data | 상태 |
|---|---|---|
| `ready` | `{ storeId }` — 연결 직후 1회 | ✅ |
| `event.created` | `EventListItem` — 2d 팝업, 2c 피드, 배지 | ✅ |
| `event.updated` | `EventListItem` — 상태/메모 변경 양방향 동기 | ✅ |
| `camera.state` | `{ cameraId, state, lastFrameAt }` — 타일 갱신 | ✅ |
| `ping` | 15초마다. 끊김 감지용 | ✅ |
| `device.state` | `{ deviceId, online, lastHeartbeatAt }` | ❌ 미구현 |

> `device.state` 는 "하트비트가 N초간 없음"을 감지하는 주기 작업이 있어야 하는데
> 그건 요구사항 6.6(시스템 알림)이고 2순위다. 그때까지 PC 온/오프라인은
> `GET /stores/:id/monitoring` 의 `device.online` 을 폴링해서 본다.

클라이언트는 끊기면 **지수 백오프로 재연결**하고, 그동안 상단에 "서버 연결 끊김" 배너를 띄운다.
재연결 후에는 `GET /stores/:id/events?...`로 놓친 구간을 다시 읽는다 (SSE는 재생을 보장하지 않는다).

---

## 7. AI 게이트 연결점 ★ 다른 작업자용

**현재 `ai-worker`는 종류를 판정하지 못한다.** 스켈레톤 오토인코더로 `anomaly_score`만 낸다
(`ai-worker/pipeline/anomaly_detection.py` 참고 — 주석에 "콜드스타트 데모에 적합한 근사치"라고 적혀 있다).

`kind`(7종) · `risk` · `description`(한글) · `appearance`(인상착의)는 **별도 AI 게이트**가 채운다.
**상세 계약은 `docs/ai-gate-contract.md`에 있다.** 파이프라인 위치만 요약하면:

```
ai-worker: 포즈추출 → 이상탐지 → 클립 추출 → anomaly_events insert
                                                    │
                                          ┌─────────┴─────────┐
                                          │  ⬅ 게이트 호출 지점 │
                                          └─────────┬─────────┘
                                                    ▼
                                      events insert (kind/risk/설명/인상착의)
                                                    ▼
                                         SSE 발행 → 푸시 발송
```

**게이트가 붙기 전에도 파이프라인은 끝까지 돈다.** 미연결 시:

| 필드 | 값 |
|---|---|
| `kind` | `unknown` |
| `risk` | `anomaly_score`/`threshold` 비율로 추정 — `≥1.5배`=high, `≥1.2배`=medium, 그 외 low |
| `description` / `appearance` | null |
| `ai_gate_status` | `skipped` |

프론트는 `kind === 'unknown'`이면 종류 태그 자리에 **"분석 중"**을 표시하고,
`aiGateStatus === 'done'`이 SSE `event.updated`로 오면 교체한다.

---

## 8. 푸시 (6.1 ~ 6.4, 6.7)

| | |
|---|---|
| `POST /push/devices` | `{ platform, token }` |
| `DELETE /push/devices` | `{ token }` |
| `GET /stores/:storeId/notification-settings` | 종류별 on/off + 민감도 + 조용한 구간 |
| `PUT /stores/:storeId/notification-settings` | 즉시 저장 (저장 버튼 없음) |
| `POST /stores/:storeId/notification-settings/test` | 6.7 "지금 보내기" |

```jsonc
// GET 응답
{ "kinds": [{ "kind": "theft", "enabled": true, "sensitivity": "medium" }, ...],
  "quietHours": { "businessHoursHighOnly": true,
                  "sleepStart": "23:00", "sleepEnd": "07:00",
                  "sleepEmergencyOnly": true, "overrideDndForHigh": true } }
```

**`collapse`는 `enabled: false`를 받으면 400.** 응급이라 끌 수 없다 (요구사항 명시).

발송(6.4)은 서버 내부. 리치 푸시에 썸네일 + 액션 2개("클립 보기" / "112"),
본문은 `매장명 · 카메라명 · 종류` 한 줄.

재알림(6.5)·시스템 알림(6.6)은 2순위 — 스키마는 `events.state`와 `devices.last_heartbeat_at`으로 충분하다.

---

## 9. 프론트가 화면별로 쓰는 것

| 화면 | 엔드포인트 |
|---|---|
| 2a 로그인·매장 | `supabase.auth` OTP → `GET /stores` → `POST /stores` / `POST /stores/:id/devices` |
| 2b 카메라 추가 | (ONVIF·probe는 PC 로컬) → `POST /stores/:id/cameras`, `PATCH /stores/:id`(알림 빠르기) |
| 2c 실시간 | `GET /stores/:id/monitoring`, `GET /stores/:id/events?date=today`, **SSE** |
| 2d 위험 팝업 | SSE `event.created` → `GET /events/:id`, `PATCH /events/:id/state`, `GET /events/:id/nearby-cameras` |
| 2e 위험 기록 | `GET /stores/:id/events`(필터), `GET /stores/:id/events/timeline`, `GET /stores/:id/events/summary` |
| 2f 상세·증거 | `GET /events/:id`, `GET /stores/:id/segments`, `GET /events/:id/clip`, `PATCH .../memo` |
| 2g 설정 | `PATCH /stores/:id`, `GET/PUT .../notification-settings`, `GET /stores/:id/cameras` |

라이브 미리보기(2b ②, 2c 격자)는 **서버를 거치지 않는다** — PC 로컬 RTSP다 (3.4).
기존 `src/main/services/preview-stream.ts`를 그대로 쓴다.

---

## 10. 이 계약을 바꿀 때

1. 이 파일을 고친다.
2. **두 레포 모두**에 같은 내용을 반영한다.
3. `SegmentMeta`를 바꾸는 경우 `cctv-agent/src/shared/types.ts` ·
   `scene-stealer-back/ingest-worker/src/segmentMeta.ts` · `cctv-agent/docs/protocol-flow.md`를 함께 맞춘다.


---

## 11. 구현 상태 (2026-09-16)

`scene-stealer-back` 브랜치 `feat/scene-stealer-domain-api` 기준.

### ✅ 구현됨 — MVP 필수 전부

| 요구사항 | 어디에 |
|---|---|
| 1.2 · 1.3 매장 | `backend/app/routers/stores.py` |
| 1.4 PC 등록·교체 | 같은 파일 + `backend/app/device_token.py` |
| 2.1 ~ 2.3 카메라 | `backend/app/routers/cameras.py` |
| 2.4 카메라 상태 보고 | `ingest-worker/src/heartbeat.ts` |
| 2.5 감시 상태 | `cameras.py:get_monitoring` |
| 2.6 매장 설정 | `stores.py:update_store` |
| 3.1 · 3.2 조각 업로드 | `ingest-worker/src/server.ts` (기존 그대로) |
| 3.3 조각 조회 · 7일 정리 | `events.py:list_segments`, `backend/app/retention.py` |
| 4.1 이벤트 생성 | `ai-worker/event_sink.py` |
| 4.2 실시간 채널 | `backend/app/realtime.py`, `routers/stream.py` |
| 4.3 ~ 4.10 이벤트 | `backend/app/routers/events.py` |
| 5.1 클립 URL | `events.py:get_clip` |
| 6.1 ~ 6.4 · 6.7 푸시 | `backend/app/routers/notifications.py`, `backend/app/push.py` |
| 7.1 하트비트 | `ingest-worker/src/heartbeat.ts` |

### ⚠️ 코드는 됐지만 설정이 남은 것

| | 필요한 조치 |
|---|---|
| 1.1 휴대폰 인증 | Supabase 대시보드에서 **Phone Auth 활성화 + SMS 공급자(Twilio 등) 설정**. 코드 쪽 작업은 없다 (§3.1) |
| 6.4 푸시 발송 | `FCM_SERVER_KEY` 를 채워야 실제로 나간다. 없으면 로그만 남긴다 |
| 4.1 종류 판정 | **AI 게이트 미연결.** `kind='unknown'` + 점수 기반 위험도로 채워진다 (`docs/ai-gate-contract.md`) |

### ❌ 이번 범위 밖 (요구사항 우선순위 2 · 3순위)

1.5 QR 페어링 · 4.11 재학습 큐(행은 쌓이지만 소비자 없음) · 5.2 공유 링크 ·
5.3 증거 묶음 · 5.4 112 안내문 · 6.5 재알림 · 6.6 시스템 알림 · 6.8 원격 재시작

### 검증 현황

| | |
|---|---|
| 스키마 | 로컬 Postgres 16 + Supabase 스텁에 3회 연속 실행 — 에러 0. 제약 조건 데이터로 확인 |
| backend | pytest 120개 통과 (도메인 로직 + 라우트 배선·인증 게이팅) |
| ai-worker | pytest 22개 통과 (게이트 경계) |
| ingest-worker | `npm run typecheck` 통과 |
| nginx | **미검증** — 이 환경에 nginx CLI·Docker 가 없다. 컨테이너 빌드 시 `nginx -t` 필요 |
| 통합 | **미검증** — Supabase 프로젝트가 없어 실제 요청 경로를 끝까지 태우지 못했다 |
