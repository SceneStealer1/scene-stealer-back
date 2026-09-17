-- ============================================================================
-- scene-stealer-back 스키마. Supabase 프로젝트 SQL Editor에 이 파일 전체를
-- 붙여넣고 실행한다. (CLI: supabase db push 또는 psql -f supabase/schema.sql)
--
-- user_id 는 Supabase Auth 의 auth.users(id) 를 가리키는 FK다 — 매장 운영자가
-- Supabase Auth 로 회원가입한 계정의 uuid. ingest-worker 의 DEVICE_TOKENS 에 박아두는
-- userId 값도 이제 그 uuid 여야 한다(문자열이면 FK 위반으로 insert 가 실패한다).
-- ============================================================================

create extension if not exists "pgcrypto";

-- ----------------------------------------------------------------------------
-- videos: 원본 5분 조각 1개 = 행 1개.
-- ingest-worker 가 Storage 'videos' 버킷에 올리고 status='uploaded' 로 이 행을
-- 만들면, ai-worker 가 그걸 집어가서 분석하고 status 를 갱신한다.
-- ----------------------------------------------------------------------------
create table if not exists public.videos (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null references auth.users(id) on delete cascade,
  store_id             text not null,
  device_id            text not null,
  camera_id            text not null,
  camera_location      text,
  filename             text not null,
  storage_path         text not null,   -- Storage 버킷 'videos' 내 경로: {user_id}/{video_id}/{filename}
  status               text not null default 'uploaded'
                         check (status in ('uploaded', 'processing', 'done', 'failed')),
  progress             integer not null default 0 check (progress between 0 and 100),
  error_message        text,
  duration_sec         double precision,
  fps                  double precision,
  frame_width          integer,
  frame_height         integer,
  recorded_started_at  timestamptz not null,  -- 영상이 실제로 찍힌 시작 시각 (SegmentMeta.startedAt)
  recorded_ended_at    timestamptz not null,  -- SegmentMeta.endedAt
  sequence             integer not null,      -- (device_id, camera_id) 별 생성 순서
  created_at           timestamptz not null default now(),
  processed_at         timestamptz
);

create index if not exists videos_user_id_idx on public.videos(user_id);
create index if not exists videos_status_idx on public.videos(status);
create index if not exists videos_device_camera_idx on public.videos(device_id, camera_id, sequence);

-- ----------------------------------------------------------------------------
-- anomaly_events: 이상행동으로 판정된 구간의 하이라이트 클립.
-- start_time_sec/end_time_sec 는 원본 5분 조각 기준 상대 시간이고, 실제 촬영
-- 시각은 videos.recorded_started_at 에 더해서 계산한다 (backend 조회 API가 함).
-- 클립 자체는 이상 구간 앞뒤로 5초씩 패딩해서 뽑는다 (ai-worker/pipeline 이 할 일).
-- ----------------------------------------------------------------------------
create table if not exists public.anomaly_events (
  id                      uuid primary key default gen_random_uuid(),
  video_id                uuid not null references public.videos(id) on delete cascade,
  user_id                 uuid not null references auth.users(id) on delete cascade,  -- 조회 편의를 위한 비정규화 컬럼 (videos.user_id 와 동일)
  track_id                integer,
  start_frame             integer,
  end_frame               integer,
  start_time_sec          double precision not null,
  end_time_sec            double precision not null,
  anomaly_score           double precision not null,
  threshold               double precision not null,
  clip_storage_path       text,  -- Storage 버킷 'clips' 내 경로
  thumbnail_storage_path  text,
  created_at              timestamptz not null default now()
);

create index if not exists anomaly_events_video_id_idx on public.anomaly_events(video_id);
create index if not exists anomaly_events_user_id_idx on public.anomaly_events(user_id);

-- ----------------------------------------------------------------------------
-- Row Level Security.
-- ingest-worker/ai-worker/backend 는 service role 키를 쓰므로 RLS 를 우회해서
-- 항상 전체 접근이 가능하다(쓰기는 이 셋만 함 — 아래 정책에 insert/update 는 없다).
-- 아래 select 정책은 "로그인한 본인 데이터만" 보게 하는 것으로, backend 가 이미
-- service role 키 + 쿼리에서 직접 user_id 필터링을 하고 있어 지금 당장 쓰이지는
-- 않지만, 나중에 프론트가 anon 키 + 로그인 세션으로 Supabase 를 직접 조회하는
-- 경로가 생기면 그때 바로 안전망 역할을 한다(defense in depth). anon(비로그인)은
-- 정책이 없으므로 여전히 전면 차단이다.
-- ----------------------------------------------------------------------------
alter table public.videos enable row level security;
alter table public.anomaly_events enable row level security;

-- create policy 에는 if not exists 가 없어서 이 파일을 다시 실행하면
-- "policy already exists" 로 죽는다 — 파일 전체를 재실행하는 게 전제이므로
-- (맨 위 주석) 나머지처럼 멱등하게 감싼다.
do $$ begin
  create policy "videos_select_own" on public.videos
    for select
    to authenticated
    using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "anomaly_events_select_own" on public.anomaly_events
    for select
    to authenticated
    using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

-- ----------------------------------------------------------------------------
-- Storage 버킷: videos(원본, private) / clips(하이라이트 클립+썸네일, private)
-- ----------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('videos', 'videos', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('clips', 'clips', false)
on conflict (id) do nothing;

-- 버킷 접근도 서비스 롤 키로만 한다. storage.objects 는 모든 Supabase 프로젝트에서
-- 기본적으로 이미 RLS 가 켜져 있고 소유자가 supabase_storage_admin 이라, SQL Editor
-- 의 postgres 롤로 `alter table storage.objects enable row level security` 를 실행하면
-- "must be owner of table objects" permission denied 가 난다 — 애초에 필요도 없어서
-- (정책을 하나도 안 만든 지금 상태 = anon/authenticated 전면 차단, service role 은 항상
-- RLS 를 우회) 이 파일에서는 아예 건드리지 않는다.


-- ============================================================================
-- 도메인 모델 (2026-09-16 추가)
--
-- 위쪽 videos/anomaly_events 는 AI 파이프라인이 쓰는 저수준 기록이고, 아래가
-- 사용자에게 보이는 도메인이다. 계약은 docs/api-contract.md 2절.
--
-- 이 파일은 통째로 다시 실행해도 안전하다 (if not exists / do 블록).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- enum. create type 에는 if not exists 가 없어서 do 블록으로 감싼다.
-- ----------------------------------------------------------------------------
do $$ begin
  create type public.risk_level as enum ('high','medium','low');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.event_state as enum ('unconfirmed','confirmed','false_positive');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.camera_runtime_state as enum
    ('unknown','connected','reconnecting','disconnected','auth_failed');
exception when duplicate_object then null; end $$;

-- ----------------------------------------------------------------------------
-- stores: 매장. address 는 112 신고 안내문(요구사항 5.4)에 들어가고,
-- opens_at/closes_at 은 조용한 구간(6.3) 판정에 쓴다.
-- segment_seconds 는 조각 길이의 단일 출처다 — PC 는 하트비트 응답으로 이 값을
-- 받아 따라간다 (docs/api-contract.md 4.1). UI 문구는 '알림 빠르기'.
-- ----------------------------------------------------------------------------
create table if not exists public.stores (
  id                  uuid primary key default gen_random_uuid(),
  name                text not null,
  address             text,
  opens_at            time,
  closes_at           time,
  segment_seconds     integer not null default 60 check (segment_seconds in (30, 60, 300)),
  clip_retention_days integer not null default 30 check (clip_retention_days between 1 and 365),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- ----------------------------------------------------------------------------
-- store_members: 계정 1 : 매장 N. 기존에는 videos.user_id 가 소유자를 직접 들고
-- 있어서 매장을 여러 개 갖거나 직원 계정을 두는 걸 표현할 수 없었다.
-- 모든 권한 판정은 이제 이 테이블을 통한다.
-- ----------------------------------------------------------------------------
create table if not exists public.store_members (
  store_id   uuid not null references public.stores(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  role       text not null default 'owner' check (role in ('owner','staff')),
  created_at timestamptz not null default now(),
  primary key (store_id, user_id)
);

create index if not exists store_members_user_idx on public.store_members(user_id);

-- ----------------------------------------------------------------------------
-- devices: 매장 PC 수집기. 매장당 1대가 전제라, 새 PC 를 등록하면 기존 활성
-- 기기를 revoked_at 으로 막는다 (docs/api-contract.md 3.3).
--
-- token_hash: 평문 토큰은 발급 응답에서 한 번만 돌려주고 저장하지 않는다.
-- 기존 DEVICE_TOKENS 환경변수 방식은 런타임 발급/교체가 불가능해서 요구사항
-- 1.4(PC 등록·교체)와 1.5(QR 페어링)가 원리적으로 막혀 있었다.
-- ----------------------------------------------------------------------------
create table if not exists public.devices (
  id                   uuid primary key default gen_random_uuid(),
  store_id             uuid not null references public.stores(id) on delete cascade,
  device_id            text not null,   -- PC 가 최초 실행 시 만든 값 (AgentConfig.deviceId)
  token_hash           text not null,   -- sha256(평문 토큰)
  label                text,
  agent_version        text,
  last_heartbeat_at    timestamptz,
  spool_bytes          bigint,
  uploaded_bytes_today bigint,
  revoked_at           timestamptz,
  created_at           timestamptz not null default now(),
  unique (store_id, device_id)
);

create index if not exists devices_token_hash_idx on public.devices(token_hash) where revoked_at is null;
create index if not exists devices_store_idx on public.devices(store_id);

-- ----------------------------------------------------------------------------
-- cameras: 카메라. RTSP URL 과 자격증명은 서버에 올리지 않는다 — PC 로컬에만
-- 둔다 (요구사항 2.1). location_tag 는 화면에 보이는 위치 구분이다 — 지금 AI 는
-- 이 값을 쓰지 않는다.
--
-- deleted_at 으로 soft delete 한다 — 지난 이벤트가 이 행을 참조하고 있어서
-- 물리 삭제하면 기록이 끊긴다.
-- ----------------------------------------------------------------------------
create table if not exists public.cameras (
  id               uuid primary key default gen_random_uuid(),
  store_id         uuid not null references public.stores(id) on delete cascade,
  agent_camera_id  text not null,   -- PC 의 ONVIF XAddr 기반 안정 id = SegmentMeta.camera.id
  name             text not null,
  location_tag     text check (location_tag in
                     ('checkout','entrance','shelf','dining','storage','other')),
  sort_order       integer not null default 0,
  stream_profile   text check (stream_profile in ('main','sub')),
  runtime_state    public.camera_runtime_state not null default 'unknown',
  last_frame_at    timestamptz,
  last_segment_at  timestamptz,
  state_updated_at timestamptz,
  deleted_at       timestamptz,
  created_at       timestamptz not null default now(),
  unique (store_id, agent_camera_id)
);

create index if not exists cameras_store_idx on public.cameras(store_id) where deleted_at is null;

-- ----------------------------------------------------------------------------
-- events: 사장님이 보는 위험 이벤트. anomaly_events(ai-worker 가 남기는 "평소와
-- 다른 움직임" 점수 구간) 한 건이 이벤트 한 건이 된다.
--
-- 위험 종류(절도·배회 …)는 나누지 않는다. AI 는 지금 파이프라인 그대로 구간과
-- 점수만 주고, backend 가 그걸 읽어 이 행을 만든다 (backend/app/anomaly_ingest.py).
-- risk 는 점수 ÷ 임계값 비율로 정한다 (backend/app/domain/risk.py).
-- ----------------------------------------------------------------------------
create table if not exists public.events (
  id                     uuid primary key default gen_random_uuid(),
  store_id               uuid not null references public.stores(id) on delete cascade,
  camera_id              uuid not null references public.cameras(id) on delete cascade,

  -- 근거. anomaly_events 는 원본 조각(videos)이 지워질 때 같이 지워지므로
  -- 점수·임계값은 여기에도 옮겨 둔다 — 위험도 경계를 나중에 조정할 때 쓴다.
  anomaly_event_id       uuid references public.anomaly_events(id) on delete set null,
  video_id               uuid references public.videos(id) on delete set null,
  started_at             timestamptz not null,
  ended_at               timestamptz not null,
  anomaly_score          double precision,
  anomaly_threshold      double precision,
  risk                   public.risk_level not null,

  -- 사용자 상태
  state                  public.event_state not null default 'unconfirmed',
  state_changed_at       timestamptz,
  state_changed_by       uuid references auth.users(id) on delete set null,
  false_positive_reason  text,
  memo                   text,   -- 112 접수번호, 피해액 등 (요구사항 4.7)

  -- 클립
  clip_storage_path      text,
  thumbnail_storage_path text,
  clip_expires_at        timestamptz,

  created_at             timestamptz not null default now()
);

-- 목록 정렬 키 (계약 5.1 "미확인 먼저, 나머지는 상태와 무관하게 최신순").
-- state 로 바로 정렬하면 enum 선언 순서를 따라 오탐이 확인됨 뒤로 몰리는데, 디자인 2e 는
-- 확인됨·오탐을 시각순으로 섞는다. PostgREST 는 식으로 정렬할 수 없어 생성 컬럼으로 둔다.
-- create table 밖에 두는 건 이 파일을 이미 한 번 돌린 DB 에도 붙게 하려는 것.
alter table public.events
  add column if not exists needs_review boolean generated always as (state = 'unconfirmed') stored;

create index if not exists events_store_started_idx on public.events(store_id, started_at desc);
create index if not exists events_store_review_idx  on public.events(store_id, needs_review desc, started_at desc);
create index if not exists events_store_state_idx   on public.events(store_id, state);
create index if not exists events_camera_started_idx on public.events(camera_id, started_at desc);
-- 이상 구간 하나 = 이벤트 하나. backend 가 겹쳐 읽어도(재시작·폴링 겹침) 두 번
-- 만들지 않게 막는 마지막 장치다. null 은 여러 개여도 된다.
create unique index if not exists events_anomaly_event_uniq on public.events(anomaly_event_id);

-- ----------------------------------------------------------------------------
-- event_state_changes: 처리 이력 (누가/언제/어디서 확인했는지 — 요구사항 4.5).
-- to_state='false_positive' 인 행이 곧 AI 재학습 큐의 입력이다 (4.11).
-- ----------------------------------------------------------------------------
create table if not exists public.event_state_changes (
  id         uuid primary key default gen_random_uuid(),
  event_id   uuid not null references public.events(id) on delete cascade,
  from_state public.event_state,
  to_state   public.event_state not null,
  changed_by uuid references auth.users(id) on delete set null,
  source     text not null default 'pc' check (source in ('pc','mobile','system')),
  reason     text,
  created_at timestamptz not null default now()
);

create index if not exists event_state_changes_event_idx on public.event_state_changes(event_id, created_at);
create index if not exists event_state_changes_fp_idx on public.event_state_changes(to_state, created_at)
  where to_state = 'false_positive';

-- ----------------------------------------------------------------------------
-- push_devices: FCM/APNs 토큰 (요구사항 6.1). 매장이 아니라 계정에 붙는다 —
-- 한 사장님이 매장 여러 개를 보고, 기기 하나로 전부 받는다.
-- ----------------------------------------------------------------------------
create table if not exists public.push_devices (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  platform     text not null check (platform in ('ios','android')),
  token        text not null,
  last_seen_at timestamptz not null default now(),
  created_at   timestamptz not null default now(),
  unique (user_id, token)
);

create index if not exists push_devices_user_idx on public.push_devices(user_id);

-- ----------------------------------------------------------------------------
-- notification_settings: 매장 × 사용자 알림 설정 (요구사항 6.2 · 6.3).
--
-- 위험 종류가 없어서 "무엇을 알릴지"는 위험도 기준 하나다 (min_risk 이상만).
-- sleep_start > sleep_end 면 자정을 넘는 구간이다 (23:00~07:00).
-- ----------------------------------------------------------------------------
create table if not exists public.notification_settings (
  store_id                 uuid not null references public.stores(id) on delete cascade,
  user_id                  uuid not null references auth.users(id) on delete cascade,
  min_risk                 public.risk_level not null default 'low',  -- 이 위험도 이상만 알림
  business_hours_high_only boolean not null default true,  -- 영업시간엔 '높음'만
  sleep_start              time,
  sleep_end                time,
  sleep_high_only          boolean not null default true,  -- 수면시간엔 높음만 소리
  override_dnd_for_high    boolean not null default true,  -- 높음은 방해금지 무시
  updated_at               timestamptz not null default now(),
  primary key (store_id, user_id)
);

-- ----------------------------------------------------------------------------
-- videos 연결. 기존 store_id/camera_id 는 text 라 조인할 수 없었다.
-- 이미 들어간 행이 있으므로 기존 컬럼은 남겨 두고 uuid 컬럼을 추가한다 —
-- 신규 insert 는 둘 다 채운다 (ingest-worker/src/analysisHandoff.ts).
-- ----------------------------------------------------------------------------
alter table public.videos add column if not exists store_uuid  uuid references public.stores(id) on delete set null;
alter table public.videos add column if not exists camera_uuid uuid references public.cameras(id) on delete set null;

create index if not exists videos_store_uuid_idx  on public.videos(store_uuid, recorded_started_at desc);
create index if not exists videos_camera_uuid_idx on public.videos(camera_uuid, recorded_started_at desc);

-- ----------------------------------------------------------------------------
-- Row Level Security.
--
-- 위 videos/anomaly_events 와 같은 정책이다: 서비스 롤(ingest-worker/ai-worker/
-- backend)은 RLS 를 우회하고, 아래 select 정책은 프론트가 나중에 anon 키로 직접
-- 조회하는 경로가 생겼을 때의 안전망이다. 소유 판정은 이제 user_id 직접 비교가
-- 아니라 store_members 경유다.
-- ----------------------------------------------------------------------------
alter table public.stores                    enable row level security;
alter table public.store_members             enable row level security;
alter table public.devices                   enable row level security;
alter table public.cameras                   enable row level security;
alter table public.events                    enable row level security;
alter table public.event_state_changes       enable row level security;
alter table public.push_devices              enable row level security;
alter table public.notification_settings     enable row level security;

-- 내가 속한 매장인지. security definer 가 아니어도 store_members 자체 정책과
-- 맞물려 동작한다 (본인 행은 항상 보이므로).
create or replace function public.is_store_member(target_store_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.store_members
    where store_id = target_store_id and user_id = auth.uid()
  );
$$;

do $$ begin
  create policy "stores_select_member" on public.stores
    for select to authenticated using (public.is_store_member(id));
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "store_members_select_own" on public.store_members
    for select to authenticated using (user_id = auth.uid());
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "devices_select_member" on public.devices
    for select to authenticated using (public.is_store_member(store_id));
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "cameras_select_member" on public.cameras
    for select to authenticated using (public.is_store_member(store_id));
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "events_select_member" on public.events
    for select to authenticated using (public.is_store_member(store_id));
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "event_state_changes_select_member" on public.event_state_changes
    for select to authenticated using (exists (
      select 1 from public.events e
      where e.id = event_id and public.is_store_member(e.store_id)
    ));
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "push_devices_select_own" on public.push_devices
    for select to authenticated using (user_id = auth.uid());
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "notification_settings_select_own" on public.notification_settings
    for select to authenticated using (user_id = auth.uid());
exception when duplicate_object then null; end $$;
