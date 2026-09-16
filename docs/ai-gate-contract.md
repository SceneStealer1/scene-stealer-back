# AI 게이트 계약 — 붙일 사람이 읽는 문서

**이 문서는 "위험 종류를 판정하는 AI 게이트"를 만드는 작업자를 위한 것이다.**
게이트 쪽 구현은 이 레포 밖이고, 여기에는 **어디에 · 무엇을 · 어떤 모양으로** 붙이면 되는지만 적는다.

---

## 1. 왜 게이트가 필요한가

지금 `ai-worker`는 **스켈레톤 오토인코더로 "평소와 다르다"는 점수만** 낸다.

`ai-worker/pipeline/anomaly_detection.py:1-20` 요약:
- YOLO11-pose로 사람 keypoint를 뽑고, 한 영상 안에서만 오토인코더를 학습시킨다
- 재구성 오차가 큰 구간을 "이상"으로 플래그한다
- 출력은 `(track_id, 프레임 범위, 시각 범위, anomaly_score, threshold)` 뿐이다

즉 **"무엇이" 일어났는지는 모른다.** 절도인지 쓰러짐인지 그냥 청소부가 이상하게 움직인 건지 구분하지 못한다.
그 파일 주석도 "콜드스타트 데모에 적합한 근사치"라고 적어 두었다.

그런데 제품의 **모든 화면**이 종류·위험도를 키로 쓴다:

| 쓰는 곳 | 필요한 것 |
|---|---|
| 2c 실시간 피드 | 종류 태그, 위험도 색(높음=error/600 테두리) |
| 2d 위험 팝업 | 종류, 위험도, AI 한글 설명, 인상착의, 바운딩박스 |
| 2e 위험 기록 | 종류·위험도 필터, 타임라인 색 |
| 2j 모바일 상세 | 종류 태그 + AI 설명(인상착의 포함) |
| 6.2 알림 설정 | **종류별 on/off** — 종류가 없으면 설정 자체가 성립 안 함 |
| 5.4 112 안내문 | 종류 + 인상착의 |

**그래서 게이트가 채워야 하는 건 4개다:** `kind`, `risk`, `description`, `appearance`.

---

## 2. 붙일 위치 — 정확히 한 곳

`ai-worker/worker.py`의 `process_one()` 안, **`anomaly_events` insert 직후**다.

현재 코드 (`ai-worker/worker.py:125-150` 부근):

```python
for seg, clip_path, thumb_path in results:
    clip_storage_path  = f"{user_id}/{video_id}/{clip_path.name}"
    thumb_storage_path = f"{user_id}/{video_id}/{thumb_path.name}"

    sb.storage.from_("clips").upload(clip_storage_path, str(clip_path), {...})
    sb.storage.from_("clips").upload(thumb_storage_path, str(thumb_path), {...})

    sb.table("anomaly_events").insert({ ... }).execute()
    # ◀◀◀ 여기. 이 아래에 게이트 호출 + events insert 가 들어간다
```

바뀐 뒤 모양:

```python
    anomaly_row = sb.table("anomaly_events").insert({ ... }).execute().data[0]

    # ── AI 게이트 ───────────────────────────────────────────────
    verdict = risk_gate.classify(RiskGateRequest(
        clip_path       = clip_path,          # 로컬 mp4 (±5초 패딩된 하이라이트)
        thumbnail_path  = thumb_path,
        started_at      = absolute_start,     # videos.recorded_started_at + seg.start_time_sec
        ended_at        = absolute_end,
        camera_name     = camera["name"],
        location_tag    = camera["location_tag"],   # 'checkout' 등 — AI 컨텍스트
        anomaly_score   = seg.score,
        threshold       = seg.threshold,
    ))
    # ───────────────────────────────────────────────────────────

    sb.table("events").insert({
        "store_id": store_uuid, "camera_id": camera_uuid,
        "anomaly_event_id": anomaly_row["id"], "video_id": video_id,
        "started_at": absolute_start, "ended_at": absolute_end,
        "anomaly_score": seg.score,
        "clip_storage_path": clip_storage_path,
        "thumbnail_storage_path": thumb_storage_path,
        **verdict.to_event_columns(),          # kind / risk / description / appearance /
                                               # bounding_boxes / ai_gate_status / ai_gate_error
    }).execute()
```

**게이트 작업자가 구현할 파일: `ai-worker/pipeline/risk_gate.py`**
이 레포 쪽에서는 그 모듈을 **호출만** 하고, 기본 구현은 §5의 폴백(`skipped`)으로 채워 둔다.

---

## 3. 인터페이스

### 3.1 요청 — `RiskGateRequest`

```python
@dataclass(frozen=True)
class RiskGateRequest:
    clip_path: Path          # 로컬 mp4. 이상 구간 ±5초 패딩됨. 보통 10~40초
    thumbnail_path: Path     # 로컬 jpg. 구간 중앙 프레임
    started_at: str          # ISO8601 UTC — 이상 구간 시작 절대시각
    ended_at: str
    camera_name: str         # "계산대"
    location_tag: str        # checkout|entrance|shelf|dining|storage|other
    anomaly_score: float     # 오토인코더 재구성 오차
    threshold: float         # 그 영상의 임계값
```

`location_tag`를 **반드시 프롬프트/모델에 넣어라.** 같은 동작도 계산대냐 창고냐에 따라 판정이 달라진다
(진열대 앞에서 물건을 가방에 넣음 = 절도 의심 / 창고에서 = 정상 업무).

### 3.2 응답 — `RiskGateVerdict`

```python
@dataclass(frozen=True)
class RiskGateVerdict:
    kind: RiskKind                    # §3.3
    risk: RiskLevel                   # 'high' | 'medium' | 'low'
    description: str | None           # 한글 한두 문장. 사장님이 읽는 문장이다
    appearance: str | None            # 인상착의
    bounding_boxes: list[dict] | None # [{ "t": 1.2, "x": .., "y": .., "w": .., "h": .. }] 0~1 정규화
    status: Literal['done','failed','skipped']
    error: str | None = None
```

### 3.3 `kind` — 7종 + `unknown` (이 문자열 그대로)

| 값 | 한글 | 비고 |
|---|---|---|
| `theft` | 절도(미결제 반출) | |
| `vandalism` | 기물파손/폭력 | |
| `dine_and_dash` | 취식 후 미결제 | |
| `underage_purchase` | 미성년자 주류/담배 | |
| `loitering` | 장시간 배회 | |
| `sleeping` | 노숙/취침 | |
| `collapse` | 쓰러짐 | **응급. 알림을 끌 수 없는 유일한 종류** |
| `unknown` | — | 판정 실패/미연결. 프론트는 "분석 중"으로 표시 |

DB에서 `risk_kind` enum이다. **여기 없는 값을 넣으면 insert가 깨진다.**
새 종류가 필요하면 마이그레이션(`supabase/schema.sql`)부터 고쳐야 한다.

### 3.4 `description` 작성 규칙

사장님이 **푸시 알림과 팝업에서 그대로 읽는 문장**이다. 모델 출력을 그대로 흘리지 말고 이 규칙을 지켜라:

- 한글 평서문 1~2문장, 120자 이내
- **단정하지 말 것.** "훔쳤습니다"(X) → "결제 없이 나갔습니다"(O), "~로 보입니다"(O)
- 위치 + 인원 + 행동 순서. 예: `계산대 앞에서 한 명이 물건을 가방에 넣고 결제 없이 나갔습니다.`
- 시각·카메라명은 **넣지 마라.** UI가 따로 표시한다 (중복된다)

`appearance` 예: `검은 후드티, 흰 운동화, 20대 남성 추정` — 추정임이 드러나게.

### 3.5 위험도 기본값

종류별 기본값이 있고 매장 설정으로 덮어쓴다 (요구사항 0절). 게이트는 **기본값을 반환**하면 된다:

| kind | 기본 risk |
|---|---|
| `collapse` | `high` |
| `vandalism` | `high` |
| `theft` | `high` |
| `underage_purchase` | `medium` |
| `dine_and_dash` | `medium` |
| `loitering` | `low` |
| `sleeping` | `low` |

확신이 낮으면 한 단계 내려라 — **오탐 알림이 미탐보다 신뢰를 빨리 깎는다.**

---

## 4. 호출 방식 — 둘 중 아무거나

`risk_gate.classify()`의 **시그니처만 지키면** 안은 자유다.

**(a) 프로세스 내 호출** — 게이트가 파이썬 라이브러리/로컬 모델인 경우. 그냥 함수로 구현.

**(b) HTTP 호출** — 게이트가 별도 서비스인 경우. 권장 모양:

```
POST <RISK_GATE_URL>/classify
Authorization: Bearer <RISK_GATE_TOKEN>
multipart: clip(mp4) + meta(json = RiskGateRequest 중 파일 제외 필드)
→ 200 { kind, risk, description, appearance, boundingBoxes }
```

`docker-compose.yml`에 서비스를 추가하고 `ai-worker` 환경변수에 `RISK_GATE_URL`을 넣는다.
스택 내부 네트워크에서만 보이게 두고 호스트 포트는 열지 마라 (다른 워커들과 같은 정책).

---

## 5. 게이트가 없을 때 — 파이프라인은 끝까지 돈다

**이게 이 설계의 핵심이다.** 게이트가 아직 없어도 영상 수집 → 분석 → 이벤트 → 화면 표시까지 전부 동작한다.

`risk_gate.classify()`의 기본 구현(게이트 미설정 시):

```python
ratio = anomaly_score / threshold if threshold else 1.0
risk  = 'high' if ratio >= 1.5 else 'medium' if ratio >= 1.2 else 'low'
return RiskGateVerdict(kind='unknown', risk=risk,
                       description=None, appearance=None, bounding_boxes=None,
                       status='skipped')
```

프론트 동작:
- `kind === 'unknown'` → 종류 태그 자리에 **"분석 중"**
- `aiGateStatus`가 나중에 `done`으로 바뀌면 SSE `event.updated`로 받아 교체

### 실패 처리

| 상황 | status | 그 밖의 동작 |
|---|---|---|
| 게이트 미설정 | `skipped` | 위 폴백 |
| 호출 타임아웃/5xx | `failed` | `error`에 사유. **이벤트는 그래도 만든다** (`kind='unknown'`) |
| 응답이 enum 밖 | `failed` | `kind='unknown'`으로 강등, `error`에 원본 값 기록 |

**게이트 실패로 이벤트를 버리지 마라.** 클립은 이미 있고, 사장님은 종류를 몰라도 영상은 봐야 한다.
타임아웃은 30초를 넘기지 마라 — 5분 조각 하나에 이벤트가 여러 개 나올 수 있고,
`ai-worker`는 한 번에 영상 하나씩 처리하는 직렬 루프다 (`worker.py:main`).

---

## 6. 재분석 (나중에)

게이트가 나중에 붙으면 `ai_gate_status IN ('skipped','failed')`인 과거 이벤트를 다시 태울 수 있다.
클립이 Storage `clips` 버킷에 남아 있으므로 (`events.clip_storage_path`) 원본 5분 조각 없이도 가능하다.
단 원본 조각은 7일 후 삭제되고 클립은 30일 보관이므로, **재분석 창은 30일**이다.

---

## 7. 오탐 피드백 (4.11)

사용자가 이벤트를 `false_positive`로 바꾸면 (`PATCH /events/:id/state`)
`event_state_changes`에 이력이 남는다. 이게 게이트 재학습의 입력이다.

```sql
select e.id, e.kind, e.risk, e.clip_storage_path, c.reason
from events e
join event_state_changes c on c.event_id = e.id
where c.to_state = 'false_positive';
```

지금은 **쌓기만** 한다. 재학습 파이프라인은 게이트 작업자 몫이다.

---

## 8. 요약 — 붙일 사람 체크리스트

- [ ] `ai-worker/pipeline/risk_gate.py`에 `classify(RiskGateRequest) -> RiskGateVerdict` 구현
- [ ] `kind`는 §3.3의 8개 문자열만 반환 (enum 밖 = insert 깨짐)
- [ ] `description`은 §3.4 규칙 (단정 금지, 120자, 시각·카메라명 제외)
- [ ] `location_tag`를 컨텍스트로 사용
- [ ] 타임아웃 30초 이내, 실패해도 예외를 던지지 말고 `status='failed'` 반환
- [ ] HTTP 서비스라면 `docker-compose.yml` + `RISK_GATE_URL` 환경변수 추가, 호스트 포트 열지 않기
- [ ] 붙인 뒤 `ai_gate_status='skipped'`인 과거 이벤트 재분석 여부 결정 (§6)
