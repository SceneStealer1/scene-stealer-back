-- ============================================================================
-- videos/anomaly_events.user_id 를 text -> uuid(auth.users FK) 로 안전하게
-- 바꾸는 마이그레이션.
--
-- 언제 필요한가: schema.sql 을 예전 버전(user_id 가 text 였던 시절)으로 이미
-- 실행해서 프로젝트/테이블이 존재하는 경우에만 필요하다. 완전히 새 Supabase
-- 프로젝트라면 이 파일은 필요 없고 최신 schema.sql 만 실행하면 된다(이미
-- user_id uuid 로 만들어짐).
--
-- 실행 순서: 이 파일을 먼저 실행 → 그 다음 schema.sql 을 (다시) 실행해서
-- 인덱스/RLS 정책/Storage 버킷까지 맞춘다. schema.sql 은 전부 "이미 있으면
-- 건너뛴다" 식이라 이 순서로 실행해도, 반대로 실행해도 안전하다.
--
-- 안전장치: 기존 user_id 값("owner-1" 같은 테스트 문자열 등)은 실제
-- auth.users.id 가 아닐 수 있어서 무작정 ::uuid 로 캐스팅하면 실패하거나,
-- 캐스팅은 성공해도 FK 제약에서 막힌다. 이 스크립트는 그런 값을 임의로 지우거나
-- 바꾸지 않는다 — 유효하지 않은 값이 하나라도 있으면 아무것도 바꾸지 않고
-- 에러로 멈춘다(트랜잭션 전체 롤백). 그 경우 아래 "문제 있는 행 확인" 쿼리로
-- 원인을 보고, 테스트 데이터면 지우거나(`delete from ...`), 실제 데이터면 해당
-- 유저의 진짜 auth.users.id 로 UPDATE 한 뒤 다시 실행할 것.
--
-- 이미 uuid 로 마이그레이션된 상태에서 다시 실행해도 안전하다(멱등) — 각 테이블
-- 마다 "이미 uuid 면 건너뛴다"로 시작한다.
-- ============================================================================

begin;

-- ----------------------------------------------------------------------------
-- videos.user_id
-- ----------------------------------------------------------------------------
do $$
declare
  bad_count integer;
begin
  if not exists (select 1 from information_schema.tables
                 where table_schema = 'public' and table_name = 'videos') then
    raise notice 'public.videos 테이블이 없습니다 — schema.sql 을 먼저 실행하세요. 건너뜁니다.';
    return;
  end if;

  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'videos'
               and column_name = 'user_id' and data_type = 'uuid') then
    raise notice 'videos.user_id 는 이미 uuid 입니다 — 건너뜁니다.';
    return;
  end if;

  -- uuid 형식이 아니거나, 형식은 맞아도 auth.users 에 없는 값이 있으면 여기서 막는다.
  select count(*) into bad_count
  from public.videos v
  where v.user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     or not exists (select 1 from auth.users u where u.id::text = v.user_id);

  if bad_count > 0 then
    raise exception
      'videos.user_id 중 % 개 행이 uuid 형식이 아니거나 auth.users 에 없는 값입니다. '
      '"문제 있는 행 확인" 쿼리(주석 참고)로 살펴본 뒤 정리하고 다시 실행하세요.', bad_count;
  end if;

  alter table public.videos
    alter column user_id type uuid using user_id::uuid;

  alter table public.videos
    add constraint videos_user_id_fkey
    foreign key (user_id) references auth.users(id) on delete cascade;

  raise notice 'videos.user_id 를 uuid(auth.users FK) 로 마이그레이션했습니다.';
end $$;

-- ----------------------------------------------------------------------------
-- anomaly_events.user_id
-- ----------------------------------------------------------------------------
do $$
declare
  bad_count integer;
begin
  if not exists (select 1 from information_schema.tables
                 where table_schema = 'public' and table_name = 'anomaly_events') then
    raise notice 'public.anomaly_events 테이블이 없습니다 — schema.sql 을 먼저 실행하세요. 건너뜁니다.';
    return;
  end if;

  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'anomaly_events'
               and column_name = 'user_id' and data_type = 'uuid') then
    raise notice 'anomaly_events.user_id 는 이미 uuid 입니다 — 건너뜁니다.';
    return;
  end if;

  select count(*) into bad_count
  from public.anomaly_events e
  where e.user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     or not exists (select 1 from auth.users u where u.id::text = e.user_id);

  if bad_count > 0 then
    raise exception
      'anomaly_events.user_id 중 % 개 행이 uuid 형식이 아니거나 auth.users 에 없는 값입니다. '
      '"문제 있는 행 확인" 쿼리(주석 참고)로 살펴본 뒤 정리하고 다시 실행하세요.', bad_count;
  end if;

  alter table public.anomaly_events
    alter column user_id type uuid using user_id::uuid;

  alter table public.anomaly_events
    add constraint anomaly_events_user_id_fkey
    foreign key (user_id) references auth.users(id) on delete cascade;

  raise notice 'anomaly_events.user_id 를 uuid(auth.users FK) 로 마이그레이션했습니다.';
end $$;

commit;

-- ----------------------------------------------------------------------------
-- 문제 있는 행 확인 (위에서 에러가 났을 때 원인 파악용 — 필요할 때만 따로 실행)
-- ----------------------------------------------------------------------------
-- select id, user_id from public.videos
-- where user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
--    or not exists (select 1 from auth.users u where u.id::text = videos.user_id);
--
-- select id, user_id from public.anomaly_events
-- where user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
--    or not exists (select 1 from auth.users u where u.id::text = anomaly_events.user_id);
--
-- 테스트 데이터라 그냥 지워도 되면(연쇄로 anomaly_events 도 같이 지워진다):
-- delete from public.videos
-- where user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
--    or not exists (select 1 from auth.users u where u.id::text = videos.user_id);
