import { config } from './config.js'

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
 * 백엔드(DB)가 아직 없으므로 디바이스 토큰을 DEVICE_TOKENS 환경변수(JSON 배열)로 관리한다.
 * 나중에 실제 발급/폐기 절차가 생기면 이 파일만 DB 조회로 바꾸면 된다 — 나머지 로직은
 * IngestDevice 모양만 보고 동작한다.
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

export const authenticateDevice = (bearerToken: string): IngestDevice | null =>
  deviceTokens.get(bearerToken) ?? null
