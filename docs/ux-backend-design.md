# UX 뼈대 → 백엔드 설계

`CCTV 위험감시 UX 뼈대.pdf`(2026.09.12, 2a~2m)를 역산해서 필요한 API/실시간/데이터모델을
정리한 문서.

## 구현 현황 (2026-09-16)

아래 설계 중 **5장 질문 1(위험 종류 분류)과 2(AI 자연어 설명)를 뺀 나머지 기반**은
구현했다:

- ✅ DB: `stores`/`devices`/`device_pairing_codes`/`cameras`/`camera_status_events`/
  `push_tokens`/`device_commands` 테이블, `anomaly_events`에 `status`/`risk_level`(간이
  심각도)/`confirmed_by`/`confirmed_at`/`note`/`reported_to_police`/`reminder_sent_at`
  추가. `profiles`의 `store_id`/`store_name`은 `stores`로 옮기고 제거.
- ✅ backend API: `/stores`, `/stores/:id/cameras`, `/stores/:id/devices/pairing`,
  `/pc/pairing/*`(QR), `/devices/:id`, `/devices/:id/commands`, `/me/push-tokens`,
  `/clips/:id`(GET/PATCH) — 1장 표 중 이 항목들은 실제로 존재한다.
- ✅ ingest-worker: `DEVICE_TOKENS`(정적) 유지 + DB `devices` 테이블(동적 발급) 둘 다
  인증 경로로 지원 — 하나가 없어도 다른 하나는 동작.
- ✅ ai-worker: `anomaly_score`/`threshold` 비율로 `risk_level`(low/medium/high) 계산해서
  저장 (분류는 아니고 기존 점수 재활용).
- ❌ **risk_type 분류, ai_description(자연어 설명)** — 질문 1·2, 스키마/API 둘 다 아직
  없음.
- ❌ **실제 FCM/APNs 발송** — `push_tokens` 저장까지만, 발송 로직 없음(질문 6, Firebase
  프로젝트 등 인프라 필요).
- ❌ **5분 미확인 재알림 스케줄러** — `reminder_sent_at` 컬럼만 있고 그걸 검사해서 재알림
  보내는 주기 작업은 없음.
- ❌ **증거 묶음(영상+PDF) export**, **`/stores/:id/timeline`·`/cameras/:id/timeline`·
  `/cameras/:id/segments`(멀티카메라 동기 재생/조각 탐색)** — 2f 관련 엔드포인트들은
  설계만 하고 안 만듦.
- ❌ **`store_alert_rules`(위험 종류별 on/off·민감도, 2g)** — risk_type이 없어서 같이 보류.
- ⚠️ `videos`/`anomaly_events`는 여전히 `store_id`/`camera_id`가 자유 텍스트다 — 새
  `stores`/`cameras` 테이블과 FK로 연결하지 않았다(테스트/CCTV 파이프라인 쪽 리스크가
  커서 이번엔 손대지 않음). 그래서 `GET /clips`에 `storeId` 필터가 없다 — 매장이
  1개뿐이면 문제 없지만, 여러 매장을 쓰는 유저는 지금 `/clips`가 전체 매장을 합쳐서
  보여준다.

나머지(아래 원본 설계)는 위 구현 현황과 맞지 않는 부분이 있을 수 있다 — 실제 코드가
기준이고, 이 문서는 "왜 이렇게 설계했는지"의 기록으로 남겨둔다.

## 0. 전제와 가장 큰 구조 변화

와이어프레임을 보고 나니 지금 repo(`scene-stealer-back`)의 기존 전제 두 가지가 깨진다.
이후 모든 설계가 이 위에서 갈린다.

1. **"유저 1명 = 매장 1개"가 아니다.** 2a에 "이 PC가 있는 매장을 고르세요"에 매장이
   여러 개(강남 1호점/역삼점) 뜨고, 2m에 "+ 매장 추가"가 있다 — **유저 1명이 매장
   여러 개를 소유**할 수 있다. 지금 스키마는 `videos.store_id`가 자유 텍스트고,
   지지난 세션에 만든 `profiles.store_id/store_name`도 유저당 매장 하나를 가정한
   설계다. → **`stores` 테이블을 새로 만들고, `profiles`의 매장 관련 컬럼은 걷어내야
   한다** (3장 참고).

2. **`DEVICE_TOKENS` 정적 env var 방식이 이 UX와 안 맞는다.** 지금은 PC 1대를
   추가하려면 `.env`의 `DEVICE_TOKENS`를 수동으로 고치고 재배포해야 하는데,
   2a는 "사장님이 로그인 → 매장 선택 → 자동으로 이 PC가 그 매장에 연결"을 기대한다.
   → **디바이스(=PC 앱 설치 1건)를 DB로 동적 발급/조회하는 API가 필요하다.** 기존
   `DEVICE_TOKENS`는 "고급: 서버 주소·토큰 직접 입력"(2a 하단)으로 남겨두면 개발자용
   백도어로는 계속 쓸 수 있다 — 5장 질문 참고.

---

## 1. 화면별 필요 API

로그인/회원가입 자체(전화번호 OTP)는 백엔드가 만들지 않는다 — 지금 구조대로
프론트(Electron/모바일)가 Supabase Auth를 직접 호출한다(`signInWithOtp`/`verifyOtp`,
Phone 프로바이더 활성화 필요 — 5장 질문). 아래 API는 전부 발급받은 Supabase JWT를
`Authorization: Bearer` 로 받는다고 가정(기존 `/videos` 등과 동일 패턴).

### 2a — PC 로그인 · 매장 연결

| 메서드 | 경로 | 설명 | 비고 |
|---|---|---|---|
| — | (Supabase Auth 직접) | 휴대폰 OTP 로그인 | 백엔드 API 아님 |
| `GET` | `/stores` | 내 매장 목록 (이름, 카메라 수, 연결된 PC 라벨/상태) | |
| `POST` | `/stores` | 새 매장 만들기 `{name, address?}` | |
| `POST` | `/stores/{storeId}/devices/pairing` | 이 PC를 그 매장의 디바이스로 등록 — 디바이스 토큰 발급 `{label, platform}` → `{deviceId, deviceToken}` | 이미 연결된 PC가 있으면 `409` + 기존 디바이스 정보(교체 확인용) |
| `POST` | `/stores/{storeId}/devices/pairing?replace=true` | 기존 PC 연결을 끊고 새로 발급(교체 확인 후) | 기존 `deviceToken` 즉시 폐기 |
| `POST` | `/pc/pairing/start` | QR 발급 — PC가 미로그인 상태에서 pairing code 생성 `{}` → `{pairingCode, qrPayload, expiresAt}` | 5분 등 짧은 TTL |
| `GET` | `/pc/pairing/{pairingCode}` | PC가 폴링 — 모바일이 아직 안 긁었으면 `pending`, 긁으면 `{status:'claimed', deviceToken, storeId}` | 폴링(2~3초 간격)이면 충분, 2장 참고 |
| `POST` | `/pc/pairing/{pairingCode}/claim` | 모바일 앱(이미 로그인)이 QR 스캔 후 호출 — 이 pairing을 자기 계정/매장에 연결 `{storeId}` | 모바일 쪽 JWT 필요 |

### 2b — 카메라 추가 위저드

ONVIF 탐색·RTSP 연결·비밀번호 확인은 **PC의 로컬 네트워크 작업**이라 백엔드가 관여하지
않는다(같은 공유기 안에서만 가능). 백엔드는 "등록 결과 저장"과 "중복 확인"만 맡는다.

| 메서드 | 경로 | 설명 | 요청 | 비고 |
|---|---|---|---|---|
| `GET` | `/stores/{storeId}/cameras` | 이미 등록된 카메라 목록 (①에서 "이미 '출입문'으로 등록됨" 표시용) | | |
| `POST` | `/stores/{storeId}/cameras` | 카메라 등록 ③ 완료 시 | `{name, locationTag, quality, deviceId}` | 8대 초과 시 `422` |
| `PATCH` | `/stores/{storeId}/settings` | "알림 빠르기"(30초/1분/5분) 등 매장 공통 설정 | `{segmentIntervalSec}` | 카메라 개별이 아니라 매장 전체 공통(와이어프레임 명시) |

RTSP 주소/카메라 비밀번호는 백엔드로 전송하지 않는다고 가정(PC 로컬에만 보관) — 5장
질문에서 확인 필요.

### 2c — PC 홈 (실시간 대시보드)

라이브 영상 타일 자체는 PC가 로컬 RTSP를 직접 그리는 것이라 API가 필요 없다. 아래는
"연결됨/재연결 중" 상태, 위험 피드, 상단 배너용.

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/stores/{storeId}/status` | 감시 중 N/M대, 서버 전송 상태, 마지막 AI 분석 시각, 알림 지연(=segmentIntervalSec) |
| `POST` | `/stores/{storeId}/cameras/{cameraId}/heartbeat` | PC가 주기적으로(예: 30초) 카메라별 연결 상태 보고 `{status: 'connected'\|'reconnecting'}` |
| `GET` | `/clips?storeId=&status=unconfirmed&limit=` | 우측 위험 피드 (기존 `/clips`에 `storeId`/`status` 필터 추가) |
| `PATCH` | `/stores/{storeId}/monitoring` | "일시 중지(영업 중)" 토글 `{paused: boolean}` |

실시간 갱신(카메라 상태/새 위험 이벤트)은 REST 폴링이 아니라 Realtime 구독 권장 — 2장.

### 2d — 위험 감지 팝업

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/clips/{id}` | 팝업에 필요한 전체 정보 — AI 설명, 매장 주소(112 신고용), 클립/썸네일 signed URL |
| `PATCH` | `/clips/{id}` | 확인/오탐 처리 `{status: 'confirmed'\|'false_positive', note?}` |
| `POST` | `/clips/{id}/share` | "클립 저장·공유" 링크 발급 `{ttlDays}` → `{url}` (7일 유효 등, 기존 signed URL보다 긴 TTL) |
| `GET` | `/stores/{storeId}/timeline?at=&window=` | "같은 시각 다른 카메라" — 그 시각 각 카메라의 클립/세그먼트 참조 |
| `GET` | `/cameras/{cameraId}/timeline?from=&to=` | "전후 영상 더 보기" — 조각 경계를 넘나드는 세그먼트 목록 |

푸시 발송(모바일)과 5분 후 미확인 재알림은 API가 아니라 서버 쪽 트리거 — 2장 참고.

### 2e — 위험 기록 (PC)

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/stores/{storeId}/events?date=&camera=&type=&status=` | 하루 타임라인 + 리스트 (막대그래프 데이터 겸용) |
| `GET` | `/cameras/{cameraId}/uptime?date=` | "카메라 끊김(점선)" 구간 — `camera_status_events`에서 계산 |
| `PATCH` | `/clips/{id}` | 오탐/확인, 메모(`note`) — 2d와 동일 엔드포인트 재사용 |
| `GET` | `/stores/{storeId}/summary?period=week` | "이 주 요약"(위험 N건·오탐 N건·신고 N건) |
| `PATCH` | `/stores/{storeId}/settings` | 클립 보관기간 변경 `{clipRetentionDays}` |

### 2f — 클립 상세

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/clips/{id}` | 기본 상세 (2d와 공유) |
| `GET` | `/clips/{id}/adjacent` | 이전/다음 사건 이동 |
| `GET` | `/stores/{storeId}/timeline?at=&cameras=all` | 멀티카메라 동기 재생 — 카메라별 해당 시각 세그먼트 |
| `GET` | `/cameras/{cameraId}/segments?from=&to=` | 조각 리스트(#1282 #1283…) + signed URL, "영상 없음(끊김)" 구간 포함 |
| `POST` | `/clips/{id}/export-bundle` | 증거 묶음(선택 카메라·구간 영상 + PDF) 생성 시작 → `{jobId}` |
| `GET` | `/export-jobs/{jobId}` | 묶음 생성 상태 폴링 → `done`이면 `{downloadUrl}` |
| `PATCH` | `/clips/{id}` | 상태/메모 (재사용) |

증거 묶음 생성은 영상 합치기 + PDF 렌더링이라 무거운 비동기 작업 — 2장/5장 참고.

### 2g — PC 설정

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET`/`PATCH` | `/stores/{storeId}/settings/alert-rules` | 위험 종류별 on/off + 민감도 `{theft:{enabled,sensitivity}, violence:{...}, ...}` |
| `GET`/`PATCH` | `/stores/{storeId}/settings/notifications` | PC 팝업/소리, 모바일 푸시, 운영시간 중 '높음만', 수면시간 |
| `POST` | `/stores/{storeId}/notifications/test` | 테스트 알림 보내기 |
| `PATCH` | `/stores/{storeId}` | 운영시간, 매장 주소(112 신고용) |
| `GET`/`PATCH`/`DELETE` | `/stores/{storeId}/cameras/{cameraId}` | 카메라 이름/위치태그/순서 수정, 삭제 |
| `GET` | `/stores/{storeId}/usage` | 저장공간·오늘 전송량 |
| `GET` | `/devices/{deviceId}` | 고급: 서버 주소/토큰/기기ID 표시(마스킹) |

### 2i / 2j — 모바일 푸시 · 상세 대응

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/me/push-tokens` | 모바일 기기 FCM/APNs 토큰 등록 `{platform, token}` |
| `DELETE` | `/me/push-tokens/{token}` | 로그아웃/기기 변경 시 해제 |
| `GET` | `/clips/{id}` | 푸시 탭 시 상세(2d와 동일 응답) |
| `PATCH` | `/clips/{id}` | 확인/오탐 (PC와 동일 엔드포인트 — 동기화는 자동으로 됨, 2장) |

푸시 발송 자체(APNs "중요 알림" 포함)는 API가 아니라 서버가 이벤트 생성 시 호출하는
FCM/APNs 연동 — 2장.

### 2k — 모바일 홈

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/stores` | 매장 스위처 칩 — 매장별 감시 상태 요약, 문제 매장 표시(`hasIssue`) |
| `GET` | `/stores/{storeId}/status` | "감시 중 4/5대", 카메라별 상태, 마지막 분석 시각 (2c와 공유) |
| `GET` | `/clips?storeId=&status=unconfirmed` | 오늘 위험 신호 |

### 2l — 모바일 기록

2e와 동일 엔드포인트(`/stores/{storeId}/events`) 재사용, 페이지네이션만 모바일에 맞게.

### 2m — 모바일 설정

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET`/`PATCH` | `/stores/{storeId}/settings/notifications` | 2g와 동일(매장 단위 설정이라 PC/모바일 공유) |
| `GET` | `/stores` | "내 매장" 목록 — PC 연결 여부/마지막 접속(`devices.last_seen_at`) |
| `PATCH` | `/stores/{storeId}` | 매장 주소, 운영시간 |
| `PATCH` | `/stores/{storeId}/cameras/order` | 카메라 순서 변경 `{cameraIds: [...]}` |
| `POST` | `/devices/{deviceId}/commands` | "PC 앱 원격 재시작" `{type:'restart'}` — PC는 이 명령을 Realtime 구독으로 받음 |
| `POST` | `/pc/pairing/start`(모바일이 대신 생성) 또는 2a의 pairing claim | "+ 매장 추가(PC에서 QR 스캔)" |

---

## 2. 실시간성 지점 & 기술 선택

| 지점 | 요구사항 | 제안 |
|---|---|---|
| PC 대시보드 — 카메라 상태/위험 피드 실시간 갱신 (2c) | 새 이벤트/상태변화 즉시 반영 | **Supabase Realtime**(Postgres Changes) 구독. `anomaly_events`/`cameras` 테이블에 이미 RLS로 "본인 것만" 걸려있어서, PC 앱이 유저 JWT로 직접 구독하면 백엔드가 WS를 새로 만들 필요가 없다 |
| PC 위험 감지 팝업 (2d) | AI 분석 완료 즉시 팝업 | 같은 Realtime 채널의 `anomaly_events` INSERT 이벤트 구독 (PC 앱이 상시 실행 중이므로 소켓 유지 가능) |
| PC/모바일 '확인' 상태 동기화 (2d·2j 주석: "PC와 모바일 어느 쪽에서든 확인하면 양쪽 모두 해소") | 양방향 즉시 반영 | 별도 동기화 로직 불필요 — `PATCH /clips/{id}`가 그냥 DB row를 UPDATE하고, 양쪽 다 Realtime으로 같은 row를 구독 중이라 **자동으로** 해소됨 |
| 모바일 푸시 (2i) | 앱이 백그라운드/종료 상태에서도 도착 | Realtime은 앱이 떠 있을 때만 동작 → **FCM(안드로이드) + APNs(iOS, 필요시 FCM 경유)** 필수. `anomaly_events` insert 시점에 서버가 트리거해야 함(아래) |
| 5분 미확인 시 1회 재알림 (2d 주석) | 지연 트리거 | 상시 폴링 서비스 필요 — 옵션: (a) `ai-worker`에 붙이지 말고 별도 경량 `notifier` 컨테이너를 하나 추가해 1분 간격으로 `status='unconfirmed' and created_at < now()-5min and reminder_sent=false`를 스캔 (b) Supabase `pg_cron`으로 주기 함수 실행. 새 컨테이너 안 늘리려면 (b), repo 구조/언어 통일 원하면 (a) 권장 |
| 카메라 연결 상태(재연결 중 N분) | 근실시간, 초 단위까진 불필요 | PC가 REST heartbeat만 주기 전송(2c 표), 오프라인 판정은 "마지막 heartbeat로부터 N초 경과"를 **읽는 쪽에서 계산** — 별도 상태 필드/스윕 잡 불필요, 가장 단순 |
| PC 앱 원격 재시작 (2m) | PC가 명령을 즉시 수신 | `device_commands` insert 후 PC가 Realtime으로 자기 `device_id` 채널 구독 — 폴링도 대안(PC는 어차피 heartbeat를 주기적으로 보내니 그 응답에 pending command를 얹어도 됨) |
| 증거 묶음 생성 (2f) | 무거운 비동기 작업, 완료까지 수초~수십초 | REST 폴링(`GET /export-jobs/{jobId}`)으로 충분 — 실시간 소켓 불필요 |

**요약 원칙**: 클라이언트가 직접 Supabase에 물어볼 수 있는 건(같은 유저 소유 데이터의
상태 변화) Realtime으로 넘기고, 우리 백엔드가 "생성/판단"해야 하는 것만(FCM 발송, 재알림
타이머, 파일 합성) 서버 쪽 컴포넌트로 남긴다 — WebSocket을 백엔드에 직접 구현할 필요는
지금 요구사항에선 없어 보인다.

---

## 3. 데이터 모델 후보 (요약)

상세 컬럼은 7장 SQL 초안 참고. 여기서는 기존 스키마 대비 뭐가 새로 생기고 뭐가
바뀌는지만.

**새로 추가**
- `stores` — 매장 마스터(소유자, 이름, 주소, 운영시간, 알림 빠르기, 모니터링 일시중지 등)
- `devices` — PC 앱 설치 1건 = 1행 (매장 FK, 토큰, 마지막 접속)
- `device_pairing_codes` — QR 페어링 임시 코드
- `cameras` — 카메라 마스터(매장 FK, 이름, 위치태그, 화질, 순서)
- `camera_status_events` — 카메라 연결/끊김 이력(끊김 구간 타임라인용)
- `store_alert_rules` — 매장 × 위험종류 별 on/off + 민감도
- `push_tokens` — 유저별 모바일 푸시 토큰
- `device_commands` — PC에 보내는 원격 명령(재시작 등)
- `clip_shares` (선택) — 공유 링크 발급 이력/TTL

**기존 테이블 변경**
- `videos.camera_id`: 지금은 자유 텍스트 → `cameras.id` FK로 정규화. `store_id`도 자유
  텍스트 → `stores.id` FK로.
- `anomaly_events`: `status`(unconfirmed/confirmed/false_positive, 기본
  unconfirmed), `risk_type`(theft/violence/eating_no_pay/minor_alcohol/loitering/
  sleeping/fall), `risk_level`(low/medium/high), `ai_description`(text),
  `confirmed_by`/`confirmed_at`, `note`, `reported_to_police`(boolean),
  `reminder_sent_at` 컬럼 추가.
- `profiles`: **`store_id`/`store_name` 컬럼을 걷어낸다** — 매장이 여러 개일 수 있으니
  이 정보는 이제 `stores`가 갖는다. `contact_name`/`phone_number`는 계정 개인정보라
  `profiles`에 남겨도 됨.

---

## 4. 기존 ingest/AI 파이프라인과의 인터페이스

### 지금 흐름 (실측, `ingest-worker`/`ai-worker` 코드 기준)

```
cctv-agent-electron
  → POST /v1/segments (Bearer=디바이스 토큰, multipart: meta json + video mp4)
  → ingest-worker: DEVICE_TOKENS로 토큰 인증 → 로컬 디스크 저장 → 즉시 201
  → (백그라운드) Storage 'videos' 업로드 + videos row insert (status='uploaded')
  → ai-worker: 5초 간격 폴링 → status='uploaded' 하나씩 집어감
      → YOLO11-pose + ByteTrack 포즈 추출
      → 영상 1개당 오토인코더를 그 자리에서 학습(cold-start, 사전학습 없음)
      → 재구성오차 > 평균+2.5*표준편차인 구간을 "이상행동"으로 플래그 (구간 종류 구분 없음)
      → ffmpeg로 ±5초 클립/썸네일 추출 → Storage 'clips' 업로드
      → anomaly_events row insert
  → backend(FastAPI): JWT 검증 + user_id 필터링해서 조회 API 제공
```

### 이 UX가 요구하는 것과 지금 파이프라인의 차이

| UX 요구 | 지금 파이프라인 | 차이 |
|---|---|---|
| 위험 종류 분류(절도/폭력/취식후미결제/미성년자주류담배/배회/노숙/쓰러짐) | 단일 "이상행동" 점수만 있고 종류 구분 없음 | **분류기 또는 규칙 기반 후처리가 새로 필요** (5장 질문) |
| 매장/위험종류별 민감도(낮음/보통/높음) 설정이 실제 탐지에 반영 | 임계값(`threshold_std`)이 전역 env var 하나 | 임계값을 `store_alert_rules`에서 읽어와 카메라/유형별로 다르게 적용하도록 ai-worker 수정 필요 |
| AI 자연어 설명("남성 1명, 검은 상의…") | 없음 (점수·구간만) | 새 캡션 생성 단계 필요(예: 키프레임을 VLM에 태우기) — 5장 질문 |
| 위치 태그(진열대→절도, 취식대→미결제 등)를 AI 판단에 사용 | `camera_location`이 표시용 텍스트일 뿐, 분석 로직엔 안 들어감 | `cameras.location_tag`를 ai-worker 입력으로 전달 필요 |
| "알림 빠르기"(30초/1분/5분, 가변) | `cctv-agent-electron`이 5분 고정 조각을 올리는 것으로 보임(README 표현) | **프론트(cctv-agent-electron) 쪽 캡처 청크 크기 자체를 가변으로 바꿔야 함** — 이건 이 repo 단독으로 못 정한다, 그쪽 프로토콜(`docs/protocol-flow.md`) 변경 필요 (5장 질문) |
| 이벤트 생성 즉시 모바일 푸시 | 없음(백엔드가 폴링 조회만 제공) | ai-worker가 `anomaly_events` insert 직후 FCM 호출 추가(가장 손 적게 가는 위치) |
| 멀티카메라 벽시계 동기 재생 (2f) | `videos.recorded_started_at`이 이미 있어 계산은 가능 | 새 로직 아님 — `/stores/{id}/timeline` 엔드포인트만 얹으면 됨 |
| 디바이스 동적 등록(2a) | `DEVICE_TOKENS` 정적 env, ingest-worker 재배포해야 반영 | ingest-worker의 `deviceAuth.ts`를 DB(`devices` 테이블) 조회로 교체 필요 |

### 추천 변경 순서 (제안)

1. `stores`/`devices`/`cameras` 도입 + ingest-worker 인증을 DB 기반으로 교체 (2a 플로우의
   전제조건 — 나머지는 이거 없이도 부분적으로 진행 가능)
2. `anomaly_events`에 `status`/`risk_level` 등 컬럼 추가 + backend에 확인/오탐 API →
   2d/2e/2f의 절반은 이걸로 이미 동작(분류는 아직 "일반 이상행동"으로 뭉뚱그려도 UI는 붙음)
3. ai-worker → FCM 발송 연결 (2i)
4. 위험 종류 분류/자연어 설명 — 모델/규칙 설계가 필요해서 제일 오래 걸림, 별도 스파이크로
   분리 권장

---

## 5. 열린 질문 (확인 필요, 구현 전에 답 필요)

1. **위험 종류 분류를 어떻게 만들 것인가?** 지금 AI는 "이상하다/아니다" 점수 하나뿐이고
   절도/폭력/배회 같은 카테고리 개념이 없다. (a) 별도 분류 모델을 학습시킬지 (b) 포즈+
   위치태그+지속시간 기반 규칙(있는 자원)으로 근사할지 (c) 1차는 "이상행동(종류 미상)"
   으로만 내보내고 UX 쪽엔 카테고리 없이 보여줄지 — 방향이 전혀 다른 작업량이라 먼저
   정해야 함.
2. **AI 자연어 설명("남성 1명, 검은 상의…")은 이번 스코프에 포함인가?** VLM 호출(예:
   클립 키프레임을 멀티모달 모델에 태우기) 같은 새 구성요소가 필요하다. 빠지면 2d/2f의
   "AI 설명" 박스는 값 없이(또는 정적 문구로) 비워야 함.
3. **"알림 빠르기"(세그먼트 길이 30초/1분/5분 가변)는 cctv-agent-electron도 같이 바꿔야
   하는 사안인데, 그쪽 작업 범위인가 확인 필요.** 이 repo(`scene-stealer-back`)만으로는
   못 끝난다.
4. **카메라 RTSP 주소/비밀번호가 백엔드로 아예 전송되지 않는다는 전제가 맞는지** — PC
   로컬에만 저장하고 클라우드에는 "등록됐다"는 사실과 이름/위치태그만 올린다고
   가정하고 설계함. 맞는지 확인 필요(보안상 그게 맞다고 보지만, PC 재설치 시 카메라
   설정을 통째로 잃는다는 트레이드오프가 있음 — 괜찮은지).
5. **매장 1개를 여러 직원 계정이 같이 볼 수 있어야 하는가, 아니면 소유자 1명뿐인가?**
   지금 와이어프레임은 "사장님 계정" 1개만 등장 — 일단 1 owner = N stores, 1 store = 1
   owner로 설계했는데 나중에 "직원 계정 초대" 같은 게 필요하면 `store_members`(N:N)
   테이블을 따로 추가해야 함.
6. **FCM/APNs 인프라 준비 주체.** Firebase 프로젝트 생성, iOS APNs 인증서/키 발급은
   보통 모바일 앱 쪽에서 갖고 있는 게 자연스러운데, 이 repo(백엔드)가 Firebase Admin
   SDK로 발송을 담당하려면 gi 자격증명을 백엔드가 받아야 함 — 누가 그 프로젝트를
   만들지 확인 필요.
7. **증거 묶음(영상 합치기 + PDF) 생성을 어디서 돌릴 것인가.** ffmpeg 합성 + PDF
   렌더링은 CPU/시간을 좀 먹는 작업이라, 기존 `ai-worker` 컨테이너에 얹을지 새 워커를
   만들지 정해야 함(지금은 `ai-worker`가 이미 ffmpeg를 쓰고 있어서 거기 얹는 쪽이
   자연스러워 보이지만, 분석 큐와 export 큐가 섞이면 사건 분석이 export 때문에 밀릴
   수 있음).
8. **`DEVICE_TOKENS` 정적 방식을 완전히 없앨지, "고급" 옵션으로 남길지.** 완전히
   대체하면 ingest-worker의 인증 로직을 통째로 바꿔야 하고(레거시 배포 스크립트도
   영향), 병행 유지하면 코드 경로가 두 개가 됨.
9. **PC 원격 재시작 명령의 오남용 방지.** 아무나 `device_commands`를 insert 못 하게(본인
   소유 매장만) RLS/백엔드 검증은 당연히 걸겠지만, 실수로 눌러 영업 중 감시가 잠깐
   끊기는 것에 대한 UX적 확인(모바일에 "정말 재시작?" 확인 다이얼로그) 여부는 프론트
   몫으로 남겨도 되는지.
10. **카메라 최대 8대·매장당 제한이 하드코딩 값인지, 요금제/플랜에 따라 달라질 수
    있는지.** 지금은 8로 고정해서 설계했는데 플랜 개념이 이후에 생기면 `stores`에
    `camera_limit` 같은 컬럼이 필요해짐 — 지금 넣어둘지 나중에 넣을지.

---

## 6. 용어 매핑 (와이어프레임 → API 필드명)

| 와이어프레임 표현 | API/DB 필드(안) |
|---|---|
| 알림 빠르기(30초/1분/5분) | `stores.segment_interval_sec` |
| 위치 태그(계산대/출입문/진열대/취식대/창고/기타) | `cameras.location_tag` |
| 오탐 처리 | `anomaly_events.status = 'false_positive'` |
| 확인함 | `anomaly_events.status = 'confirmed'` |
| 위험도(높음/보통) | `anomaly_events.risk_level` |
| 위험 종류(절도/배회/…) | `anomaly_events.risk_type` |
| 일시 중지(영업 중) | `stores.monitoring_paused` |
| 신고 | `anomaly_events.reported_to_police` |
| 클립 보관 기간 | `stores.clip_retention_days` |
| PC 연결됨/꺼짐 | `devices.last_seen_at` 경과시간으로 계산 |

---

## 7. DB 스키마 초안 (SQL, 미적용)

기존 `supabase/schema.sql`에 이어 붙일 수 있는 형태로 짰다. **아직 `supabase/schema.sql`에
반영하지 않았다** — 5장 질문들이 정리된 뒤 실제 파일로 옮기고 마이그레이션 스크립트도
따로 준비하는 게 안전하다(특히 `videos.store_id`/`camera_id`를 텍스트에서 FK로 바꾸는
부분은 기존 `user_id` 마이그레이션 때처럼 기존 데이터 정리가 필요할 수 있음).

```sql
-- ============================================================================
-- 매장 마스터. "유저 1명 = 매장 N개" 를 반영 — profiles 에 있던 store_id/store_name
-- 은 여기로 옮겨오고 profiles 에서는 제거한다(마이그레이션 시).
-- ============================================================================
create table if not exists public.stores (
  id                    uuid primary key default gen_random_uuid(),
  owner_user_id         uuid not null references auth.users(id) on delete cascade,
  name                  text not null,
  address               text,
  operating_hours_start time,
  operating_hours_end   time,
  quiet_hours_start     time,
  quiet_hours_end       time,
  monitoring_paused     boolean not null default false,
  segment_interval_sec  integer not null default 60
                          check (segment_interval_sec in (30, 60, 300)),  -- "알림 빠르기"
  clip_retention_days   integer not null default 30,
  segment_retention_days integer not null default 7,
  camera_limit          integer not null default 8,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create index if not exists stores_owner_user_id_idx on public.stores(owner_user_id);

-- ----------------------------------------------------------------------------
-- devices: PC 앱 설치 1건 = 1행. 지금 DEVICE_TOKENS(.env) 를 대체하는 동적 버전.
-- ----------------------------------------------------------------------------
create table if not exists public.devices (
  id             uuid primary key default gen_random_uuid(),
  store_id       uuid not null references public.stores(id) on delete cascade,
  label          text,                          -- "강남점 PC" 같은 표시용 이름
  token_hash     text not null unique,           -- 평문 토큰은 발급 시 1회만 보여주고 해시만 저장
  platform       text not null default 'electron',
  last_seen_at   timestamptz,
  revoked_at     timestamptz,
  created_at     timestamptz not null default now()
);

create index if not exists devices_store_id_idx on public.devices(store_id);

-- ----------------------------------------------------------------------------
-- device_pairing_codes: PC가 QR로 띄우는 짧은 수명의 페어링 코드 (2a "모바일 앱으로 연결").
-- ----------------------------------------------------------------------------
create table if not exists public.device_pairing_codes (
  code            text primary key,              -- QR 페이로드
  status          text not null default 'pending'
                    check (status in ('pending', 'claimed', 'expired')),
  claimed_store_id uuid references public.stores(id) on delete set null,
  claimed_by_user_id uuid references auth.users(id) on delete set null,
  issued_device_token_hash text,
  created_at      timestamptz not null default now(),
  expires_at      timestamptz not null,
  claimed_at      timestamptz
);

-- ----------------------------------------------------------------------------
-- cameras: 카메라 마스터. videos.camera_id 는 앞으로 이 id 를 참조한다(마이그레이션 필요).
-- ----------------------------------------------------------------------------
create table if not exists public.cameras (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid not null references public.stores(id) on delete cascade,
  device_id     uuid references public.devices(id) on delete set null,
  name          text not null,                   -- "계산대" 등, 알림에 표시
  location_tag  text not null default 'other'
                  check (location_tag in ('checkout','entrance','shelf','dining','storage','other')),
  quality       text not null default 'standard' check (quality in ('standard', 'high')),
  sort_order    integer not null default 0,
  created_at    timestamptz not null default now(),
  deleted_at    timestamptz
);

create index if not exists cameras_store_id_idx on public.cameras(store_id);
-- 매장당 8대 제한은 DB 제약(트리거)보다 API 레이어에서 stores.camera_limit 와 비교해 검증 권장
-- (동시 등록 레이스는 드물고, 트리거로 강제하면 에러 메시지가 사용자 친화적이지 않음).

-- ----------------------------------------------------------------------------
-- camera_status_events: 연결/끊김 이력 — 위험 기록의 점선(끊김) 구간 계산용.
-- ----------------------------------------------------------------------------
create table if not exists public.camera_status_events (
  id           uuid primary key default gen_random_uuid(),
  camera_id    uuid not null references public.cameras(id) on delete cascade,
  status       text not null check (status in ('connected', 'reconnecting', 'disconnected')),
  occurred_at  timestamptz not null default now()
);

create index if not exists camera_status_events_camera_id_idx
  on public.camera_status_events(camera_id, occurred_at);

-- ----------------------------------------------------------------------------
-- store_alert_rules: 매장 × 위험종류 별 on/off + 민감도(2g).
-- ----------------------------------------------------------------------------
create table if not exists public.store_alert_rules (
  store_id     uuid not null references public.stores(id) on delete cascade,
  risk_type    text not null check (risk_type in
                 ('theft','violence','eating_no_pay','minor_alcohol','loitering','sleeping','fall')),
  enabled      boolean not null default true,
  sensitivity  text not null default 'medium' check (sensitivity in ('low','medium','high')),
  primary key (store_id, risk_type)
);
-- fall(쓰러짐)은 UX상 끌 수 없음 — enabled=false 를 API 레벨에서 거부하는 정책으로 처리
-- (DB 제약으로 막으면 "항상 최우선" 행 자체를 못 넣는 문제가 생겨서 API 검증 권장).

-- ----------------------------------------------------------------------------
-- push_tokens: 모바일 푸시 발송 대상.
-- ----------------------------------------------------------------------------
create table if not exists public.push_tokens (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  platform     text not null check (platform in ('ios', 'android')),
  token        text not null,
  created_at   timestamptz not null default now(),
  last_used_at timestamptz,
  unique (user_id, token)
);

-- ----------------------------------------------------------------------------
-- device_commands: 모바일 → PC 원격 명령(재시작 등).
-- ----------------------------------------------------------------------------
create table if not exists public.device_commands (
  id           uuid primary key default gen_random_uuid(),
  device_id    uuid not null references public.devices(id) on delete cascade,
  command      text not null check (command in ('restart')),
  status       text not null default 'pending' check (status in ('pending','acked','done','failed')),
  created_at   timestamptz not null default now(),
  acked_at     timestamptz
);

-- ----------------------------------------------------------------------------
-- anomaly_events 확장 (기존 테이블에 컬럼 추가 — ALTER, 새 테이블 아님).
-- ----------------------------------------------------------------------------
alter table public.anomaly_events
  add column if not exists status text not null default 'unconfirmed'
    check (status in ('unconfirmed', 'confirmed', 'false_positive')),
  add column if not exists risk_type text  -- nullable(분류 전/미분류); check 제약은 NULL을 항상 통과시킨다
    check (risk_type in
      ('theft','violence','eating_no_pay','minor_alcohol','loitering','sleeping','fall')),
  add column if not exists risk_level text check (risk_level in ('low', 'medium', 'high')),  -- nullable
  add column if not exists ai_description text,
  add column if not exists confirmed_by uuid references auth.users(id),
  add column if not exists confirmed_at timestamptz,
  add column if not exists note text,
  add column if not exists reported_to_police boolean not null default false,
  add column if not exists reminder_sent_at timestamptz;

create index if not exists anomaly_events_status_idx on public.anomaly_events(status);

-- ----------------------------------------------------------------------------
-- videos/cameras 연결 (기존 videos.camera_id 는 text 자유필드 — 정규화하려면 별도
-- 마이그레이션으로 기존 문자열을 cameras.id 로 매핑한 뒤 FK를 건다. 초안이라 컬럼 추가만
-- 표시하고 기존 camera_id(text)는 당장 건드리지 않음.)
-- ----------------------------------------------------------------------------
alter table public.videos
  add column if not exists camera_ref uuid references public.cameras(id);
-- 마이그레이션 완료 후: camera_id(text) 를 없애거나 camera_ref 로 완전히 대체.
```

RLS는 기존 패턴(“본인 소유만 select”)을 그대로 확장하면 된다 — `stores`는
`owner_user_id = auth.uid()`, 나머지(`devices`/`cameras`/`camera_status_events`/
`store_alert_rules`/`device_commands`)는 `store_id`가 본인 소유 `stores`에 속하는지로
검증(서브쿼리 또는 `store_id in (select id from stores where owner_user_id = auth.uid())`).
지금 `videos`/`anomaly_events`처럼 backend는 service role로 직접 필터링하고, RLS는
defense-in-depth로 병행하는 기존 원칙을 유지하면 된다.
