import { createHash } from 'node:crypto'
import { config } from './config.js'
import { supabase } from './supabaseClient.js'

export interface IngestDevice {
  readonly storeId: string
  readonly userId: string
  readonly label: string | null
}

interface DeviceTokenEntry {
  readonly token: string
  readonly storeId: string
  readonly userId: string
  readonly label?: string
}

/**
 * 디바이스 토큰은 두 경로로 인증된다 (먼저 온 순서대로 시도):
 *
 * 1) DEVICE_TOKENS 환경변수(JSON 배열) — 정적 설정, 재배포해야 바뀐다. Supabase 없이도
 *    동작해야 하는 게 장점이라(ingest-worker의 핵심 회복탄력성 — README 참고) 계속 남겨둔다.
 * 2) backend 가 발급한 동적 디바이스(Supabase `devices` 테이블) — 2a 로그인/매장연결·QR
 *    페어링 흐름(docs/ux-backend-design.md)으로 만들어진 토큰. 이건 Supabase 조회가
 *    필요해서 SUPABASE_URL/KEY 가 없으면 이 경로는 그냥 스킵된다(1번만 동작).
 *
 * userId 는 Supabase Auth 유저의 uuid다 (supabase/schema.sql 에서 videos/anomaly_events.user_id
 * 가 auth.users(id) FK) — 매장 운영자가 Supabase Auth 로 회원가입한 계정과 여기 값이 같아야
 * 나중에 대시보드 로그인 시 자기 매장(storeId) 영상이 backend 조회 API에 보인다.
 *
 * 형식: DEVICE_TOKENS='[{"token":"<deviceToken>","storeId":"store-gangnam-01","userId":"<auth.users uuid>","label":"강남점"}]'
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const parseDeviceTokens = (): ReadonlyMap<string, IngestDevice> => {
  let entries: DeviceTokenEntry[]
  try {
    entries = JSON.parse(config.deviceTokensJson)
  } catch {
    throw new Error('DEVICE_TOKENS 가 올바른 JSON 이 아닙니다')
  }
  if (!Array.isArray(entries)) throw new Error('DEVICE_TOKENS 는 배열이어야 합니다')

  const map = new Map<string, IngestDevice>()
  for (const entry of entries) {
    if (
      typeof entry?.token !== 'string' ||
      typeof entry?.storeId !== 'string' ||
      typeof entry?.userId !== 'string'
    ) {
      throw new Error('DEVICE_TOKENS 의 각 항목은 { token, storeId, userId } 를 가져야 합니다')
    }
    if (!UUID_RE.test(entry.userId)) {
      throw new Error(
        `DEVICE_TOKENS 의 userId(${entry.userId})는 Supabase Auth uuid여야 합니다 — ` +
          '임의 문자열이면 videos insert 시 auth.users FK 위반으로 실패합니다',
      )
    }
    map.set(entry.token, { storeId: entry.storeId, userId: entry.userId, label: entry.label ?? null })
  }
  return map
}

const deviceTokens = parseDeviceTokens()

/** backend(app/security.py)의 hash_device_token 과 반드시 같은 방식(SHA-256 hex)이어야 한다. */
const hashDeviceToken = (token: string): string => createHash('sha256').update(token, 'utf8').digest('hex')

const authenticateDynamicDevice = async (bearerToken: string): Promise<IngestDevice | null> => {
  if (!supabase) return null

  const { data, error } = await supabase
    .from('devices')
    .select('store_id, label, revoked_at, stores(owner_user_id)')
    .eq('token_hash', hashDeviceToken(bearerToken))
    .maybeSingle()

  if (error) {
    console.error('[ingest] dynamic device lookup failed:', error)
    return null
  }
  if (!data || data.revoked_at) return null

  // supabase-js 가 FK 임베드(stores)를 단일 객체가 아니라 배열로 돌려줄 수도 있어서 방어.
  const store = Array.isArray(data.stores) ? data.stores[0] : data.stores
  if (!store?.owner_user_id) return null

  return { storeId: data.store_id, userId: store.owner_user_id, label: data.label ?? null }
}

export const authenticateDevice = async (bearerToken: string): Promise<IngestDevice | null> => {
  const staticDevice = deviceTokens.get(bearerToken)
  if (staticDevice) return staticDevice
  return authenticateDynamicDevice(bearerToken)
}
