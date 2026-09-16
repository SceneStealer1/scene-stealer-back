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
-- profiles: 매장 운영자(auth.users) 1명당 1행 — auth.users 는 이메일/비밀번호 같은
-- 인증 전용 데이터만 가지고 있어서, 매장명/담당자/연락처 같은 비즈니스 정보는 여기
-- 따로 둔다. auth.users 에 새 계정이 생기면(회원가입) 아래 트리거가 자동으로 빈 행을
-- 만든다 — signUp() 호출 시 넘긴 메타데이터(store_id 등)가 있으면 그걸로 채워지고,
-- 없으면 null로 만들어졌다가 나중에 본인이 채우면 된다(RLS로 본인 행만 수정 가능).
-- ----------------------------------------------------------------------------
create table if not exists public.profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  store_id      text,  -- DEVICE_TOKENS/videos.store_id 와 같은 값 — 대시보드에 매장명과 같이 보여줄 때 조인용
  store_name    text,
  contact_name  text,
  phone_number  text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists profiles_store_id_idx on public.profiles(store_id);

-- auth.users 에 새 행이 생길 때마다 profiles 에도 자동으로 1행을 만든다. security
-- definer 로 만들어서 RLS 를 우회해야 한다 — 이 트리거는 auth 스키마 쪽(로그인 처리
-- 주체)에서 실행되기 때문에 일반 authenticated 권한으로는 애초에 insert 가 막혀 있다.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, store_id, store_name, contact_name, phone_number)
  values (
    new.id,
    new.raw_user_meta_data ->> 'store_id',
    new.raw_user_meta_data ->> 'store_name',
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
