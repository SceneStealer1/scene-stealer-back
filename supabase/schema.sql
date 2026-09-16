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
-- profiles: 매장 운영자(auth.users) 1명당 1행 — 계정 개인정보(담당자명/연락처)만
-- 담는다. 매장 정보(이름/주소 등)는 유저 1명이 매장을 여러 개 가질 수 있어서
-- profiles가 아니라 아래 stores 테이블(owner_user_id FK)에 둔다 — 처음엔 매장명도
-- 여기 있었는데, 다매장을 지원하면서 stores로 옮겼다(2026-09-16).
--
-- auth.users 에 새 계정이 생기면(회원가입) 아래 트리거가 자동으로 빈 행을 만든다 —
-- signUp() 호출 시 넘긴 메타데이터(contact_name 등)가 있으면 그걸로 채워지고, 없으면
-- null로 만들어졌다가 나중에 본인이 채우면 된다(RLS로 본인 행만 수정 가능).
-- ----------------------------------------------------------------------------
create table if not exists public.profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  contact_name  text,
  phone_number  text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- 예전 스키마(다매장 지원 전)로 이미 만들어진 프로젝트면 이 컬럼들이 남아있을 수
-- 있다 — 이제 stores가 담당하므로 걷어낸다. 데이터가 있었다면 이 시점에 유실되니,
-- 필요하면 실행 전에 `select id, store_id, store_name from public.profiles`로 미리
-- 백업해서 각 유저의 store_id/store_name으로 stores 행을 만들어줄 것.
alter table public.profiles drop column if exists store_id;
alter table public.profiles drop column if exists store_name;

-- auth.users 에 새 행이 생길 때마다 profiles 에도 자동으로 1행을 만든다. security
-- definer 로 만들어서 RLS 를 우회해야 한다 — 이 트리거는 auth 스키마 쪽(로그인 처리
-- 주체)에서 실행되기 때문에 일반 authenticated 권한으로는 애초에 insert 가 막혀 있다.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, contact_name, phone_number)
  values (
    new.id,
    new.raw_user_meta_data ->> 'contact_name',
    new.raw_user_meta_data ->> 'phone_number'
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- 트리거가 생기기 전에 이미 가입해 있던 계정(예: 대시보드에서 수동으로 Add user 한
-- 매장 운영자)은 자동으로 안 만들어지므로, 빠진 행을 채워 넣는다. 여러 번 실행해도
-- 안전하다(on conflict do nothing).
insert into public.profiles (id)
select u.id from auth.users u
on conflict (id) do nothing;

alter table public.profiles enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles
  for select
  to authenticated
  using (auth.uid() = id);

drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own" on public.profiles
  for update
  to authenticated
  using (auth.uid() = id)
  with check (auth.uid() = id);

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

-- auth.uid() 는 uuid 를 반환하므로, user_id 가 아직 uuid 로 마이그레이션되기 전(예전
-- 스키마로 이미 만들어둔 프로젝트에 이 파일을 그대로 다시 실행한 경우)이면 정책
-- 생성 시점에 "operator does not exist: uuid = text" 로 실패한다. 그 cryptic 에러
-- 대신 원인과 해결법을 바로 알려준다.
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'videos'
      and column_name = 'user_id' and data_type <> 'uuid'
  ) then
    raise exception
      'public.videos.user_id 가 아직 uuid 가 아닙니다(예전 스키마로 이미 만들어진 프로젝트로 '
      '보입니다). supabase/migrate_user_id_to_auth_uuid.sql 을 먼저 실행한 뒤 이 schema.sql 을 '
      '다시 실행하세요.';
  end if;

  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'anomaly_events'
      and column_name = 'user_id' and data_type <> 'uuid'
  ) then
    raise exception
      'public.anomaly_events.user_id 가 아직 uuid 가 아닙니다(예전 스키마로 이미 만들어진 '
      '프로젝트로 보입니다). supabase/migrate_user_id_to_auth_uuid.sql 을 먼저 실행한 뒤 이 '
      'schema.sql 을 다시 실행하세요.';
  end if;
end $$;

-- create policy 는 if not exists 를 지원하지 않아서, 이 파일을 다시 실행해도(재배포 등)
-- 안전하도록 먼저 지우고 다시 만든다.
drop policy if exists "videos_select_own" on public.videos;
create policy "videos_select_own" on public.videos
  for select
  to authenticated
  using (auth.uid() = user_id);

drop policy if exists "anomaly_events_select_own" on public.anomaly_events;
create policy "anomaly_events_select_own" on public.anomaly_events
  for select
  to authenticated
  using (auth.uid() = user_id);

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
-- 멀티매장/디바이스/카메라 확장 (docs/ux-backend-design.md 기반, 2026-09-16).
--
-- 범위에서 뺀 것 — 설계 문서 5장 질문 중 아직 답이 없는 부분:
--   - risk_type(위험 종류: 절도/폭력/배회 등) 분류 컬럼/설정 테이블. AI가 아직 종류를
--     구분 못 해서(질문 1) 스키마에 넣어봐야 채울 수가 없다. 나중에 분류 방식이
--     정해지면 anomaly_events.risk_type + store_alert_rules 를 추가한다.
--   - anomaly_events.ai_description(자연어 설명, 질문 2) — 같은 이유로 보류.
-- 넣은 것: risk_level(점수/threshold 비율로 계산하는 간이 심각도 — 분류가 아니라 지금
-- 있는 anomaly_score 그대로 활용하는 것이라 질문 1과 무관해서 포함).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- stores: 매장 마스터. "유저 1명 = 매장 N개"를 반영한다 — profiles 에 있던
-- store_id/store_name 은 이 테이블로 대체됐다(위 profiles 섹션에서 drop column).
-- ----------------------------------------------------------------------------
create table if not exists public.stores (
  id                      uuid primary key default gen_random_uuid(),
  owner_user_id           uuid not null references auth.users(id) on delete cascade,
  name                    text not null,
  address                 text,
  operating_hours_start   time,
  operating_hours_end     time,
  quiet_hours_start       time,
  quiet_hours_end         time,
  monitoring_paused       boolean not null default false,
  segment_interval_sec    integer not null default 60
                            check (segment_interval_sec in (30, 60, 300)),  -- "알림 빠르기"
  clip_retention_days     integer not null default 30,
  segment_retention_days  integer not null default 7,
  camera_limit            integer not null default 8,
  pc_popup_enabled        boolean not null default true,
  mobile_push_enabled     boolean not null default true,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);

create index if not exists stores_owner_user_id_idx on public.stores(owner_user_id);

alter table public.stores enable row level security;

drop policy if exists "stores_select_own" on public.stores;
create policy "stores_select_own" on public.stores
  for select to authenticated using (auth.uid() = owner_user_id);

drop policy if exists "stores_insert_own" on public.stores;
create policy "stores_insert_own" on public.stores
  for insert to authenticated with check (auth.uid() = owner_user_id);

drop policy if exists "stores_update_own" on public.stores;
create policy "stores_update_own" on public.stores
  for update to authenticated using (auth.uid() = owner_user_id) with check (auth.uid() = owner_user_id);

-- ----------------------------------------------------------------------------
-- devices: PC 앱 설치 1건 = 1행. 지금 .env의 DEVICE_TOKENS(정적)를 대체하는 동적
-- 버전 — 토큰은 평문을 저장하지 않고 해시만 저장한다(발급 시 1회만 평문을 보여줌).
-- 민감한 token_hash가 섞여 있어서 authenticated select 정책은 일부러 안 만든다
-- (backend가 service role로 조회해서 token_hash를 뺀 모양으로만 응답한다).
-- ----------------------------------------------------------------------------
create table if not exists public.devices (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid not null references public.stores(id) on delete cascade,
  label         text,
  token_hash    text not null unique,
  platform      text not null default 'electron',
  last_seen_at  timestamptz,
  revoked_at    timestamptz,
  created_at    timestamptz not null default now()
);

create index if not exists devices_store_id_idx on public.devices(store_id);

alter table public.devices enable row level security;

-- ----------------------------------------------------------------------------
-- device_pairing_codes: PC가 QR로 띄우는 짧은 수명의 페어링 코드(모바일이 스캔해서
-- claim). 코드를 아는 사람만 진행 가능한 "능력 기반" 흐름이라 RLS 정책 없이 service
-- role 전용으로 둔다(백엔드가 코드 자체를 무작위/짧은 TTL로 관리해서 방어).
-- ----------------------------------------------------------------------------
create table if not exists public.device_pairing_codes (
  code                text primary key,
  status              text not null default 'pending'
                        check (status in ('pending', 'claimed', 'issued', 'expired')),
  claimed_store_id    uuid references public.stores(id) on delete set null,
  claimed_by_user_id  uuid references auth.users(id) on delete set null,
  issued_device_id    uuid references public.devices(id) on delete set null,
  created_at          timestamptz not null default now(),
  expires_at          timestamptz not null,
  claimed_at          timestamptz,
  issued_at           timestamptz
);

alter table public.device_pairing_codes enable row level security;

-- ----------------------------------------------------------------------------
-- cameras: 카메라 마스터(2b 위저드로 등록). RTSP 주소/비밀번호는 PC 로컬에만
-- 남고 여기엔 올라오지 않는다는 전제 — docs/ux-backend-design.md 5장 질문 4 참고,
-- 확정되면 이 주석도 갱신할 것.
-- ----------------------------------------------------------------------------
create table if not exists public.cameras (
  id            uuid primary key default gen_random_uuid(),
  store_id      uuid not null references public.stores(id) on delete cascade,
  device_id     uuid references public.devices(id) on delete set null,
  name          text not null,
  location_tag  text not null default 'other'
                  check (location_tag in ('checkout', 'entrance', 'shelf', 'dining', 'storage', 'other')),
  quality       text not null default 'standard' check (quality in ('standard', 'high')),
  sort_order    integer not null default 0,
  last_status   text check (last_status in ('connected', 'reconnecting', 'disconnected')),
  last_seen_at  timestamptz,
  created_at    timestamptz not null default now(),
  deleted_at    timestamptz
);

create index if not exists cameras_store_id_idx on public.cameras(store_id);

alter table public.cameras enable row level security;

drop policy if exists "cameras_select_own" on public.cameras;
create policy "cameras_select_own" on public.cameras
  for select to authenticated
  using (store_id in (select id from public.stores where owner_user_id = auth.uid()));

-- ----------------------------------------------------------------------------
-- camera_status_events: 연결/끊김 이력 — 위험 기록 화면의 "카메라 끊김(점선)" 구간을
-- 계산하는 데 쓴다. cameras.last_status/last_seen_at은 최신 상태 캐시(빠른 조회용).
-- ----------------------------------------------------------------------------
create table if not exists public.camera_status_events (
  id           uuid primary key default gen_random_uuid(),
  camera_id    uuid not null references public.cameras(id) on delete cascade,
  status       text not null check (status in ('connected', 'reconnecting', 'disconnected')),
  occurred_at  timestamptz not null default now()
);

create index if not exists camera_status_events_camera_id_idx
  on public.camera_status_events(camera_id, occurred_at);

alter table public.camera_status_events enable row level security;

drop policy if exists "camera_status_events_select_own" on public.camera_status_events;
create policy "camera_status_events_select_own" on public.camera_status_events
  for select to authenticated
  using (camera_id in (
    select c.id from public.cameras c
    join public.stores s on s.id = c.store_id
    where s.owner_user_id = auth.uid()
  ));

-- ----------------------------------------------------------------------------
-- push_tokens: 모바일 푸시 발송 대상(토큰 저장까지만 — 실제 FCM/APNs 발송은 아직
-- 미구현, docs/ux-backend-design.md 5장 질문 6 참고). profiles처럼 본인 소유 데이터라
-- select/insert/delete 정책을 직접 건다.
-- ----------------------------------------------------------------------------
create table if not exists public.push_tokens (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  platform      text not null check (platform in ('ios', 'android')),
  token         text not null,
  created_at    timestamptz not null default now(),
  last_used_at  timestamptz,
  unique (user_id, token)
);

alter table public.push_tokens enable row level security;

drop policy if exists "push_tokens_select_own" on public.push_tokens;
create policy "push_tokens_select_own" on public.push_tokens
  for select to authenticated using (auth.uid() = user_id);

drop policy if exists "push_tokens_insert_own" on public.push_tokens;
create policy "push_tokens_insert_own" on public.push_tokens
  for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists "push_tokens_delete_own" on public.push_tokens;
create policy "push_tokens_delete_own" on public.push_tokens
  for delete to authenticated using (auth.uid() = user_id);

-- ----------------------------------------------------------------------------
-- device_commands: 모바일 → PC 원격 명령(예: 2m "PC 앱 원격 재시작"). PC는 자기
-- device_id로 pending 목록을 device 토큰으로 조회해서 소비한다(backend API, 6장).
-- ----------------------------------------------------------------------------
create table if not exists public.device_commands (
  id           uuid primary key default gen_random_uuid(),
  device_id    uuid not null references public.devices(id) on delete cascade,
  command      text not null check (command in ('restart')),
  status       text not null default 'pending' check (status in ('pending', 'acked')),
  created_at   timestamptz not null default now(),
  acked_at     timestamptz
);

alter table public.device_commands enable row level security;

drop policy if exists "device_commands_select_own" on public.device_commands;
create policy "device_commands_select_own" on public.device_commands
  for select to authenticated
  using (device_id in (
    select d.id from public.devices d
    join public.stores s on s.id = d.store_id
    where s.owner_user_id = auth.uid()
  ));

-- ----------------------------------------------------------------------------
-- anomaly_events 확장: 확인/오탐 상태, 메모, 신고 여부, 간이 심각도. risk_type(위험
-- 종류 분류)과 ai_description(자연어 설명)은 위 안내대로 이번엔 뺐다.
-- ----------------------------------------------------------------------------
alter table public.anomaly_events
  add column if not exists status text not null default 'unconfirmed'
    check (status in ('unconfirmed', 'confirmed', 'false_positive')),
  add column if not exists risk_level text check (risk_level in ('low', 'medium', 'high')),
  add column if not exists confirmed_by uuid references auth.users(id),
  add column if not exists confirmed_at timestamptz,
  add column if not exists note text,
  add column if not exists reported_to_police boolean not null default false,
  add column if not exists reminder_sent_at timestamptz;

create index if not exists anomaly_events_status_idx on public.anomaly_events(status);
