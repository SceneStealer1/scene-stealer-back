import { createHash } from 'node:crypto'
import { config } from './config.js'
import { supabase } from './supabaseClient.js'

export interface IngestDevice {
  /** stores.id (uuid). meta.storeId 와 대조하는 값이다. */
  readonly storeId: string
  /** auth.users.id (uuid). videos.user_id 에 들어간다 — FK 라 임의 문자열이면 insert 가 깨진다. */
  readonly userId: string
  readonly label: string | null
  /** devices.id. 하트비트가 갱신할 행. 환경변수 폴백으로 인증된 경우 null. */
  readonly deviceRowId: string | null
}

interface DeviceTokenEntry {
  readonly token: string
  readonly storeId: string
  readonly userId: string
  readonly label?: string
}

/**
 * 기기 토큰 검증.
 *
 * 원래는 DEVICE_TOKENS 환경변수에 박아 두었는데, 그러면 런타임에 토큰을 발급하거나
 * 교체할 수 없어서 요구사항 1.4(PC 등록·교체)와 1.5(QR 페어링)가 원리적으로 막힌다.
 * 그래서 devices 테이블 조회를 1순위로 두고, 환경변수는 이행 기간 폴백으로 남긴다 —
 * 이미 배포된 에이전트를 한 번에 끊지 않기 위해서다. 전환이 끝나면 envDeviceTokens
 * 경로를 지우면 된다.
 *
 * 평문 토큰은 서버에 없다. backend 가 발급할 때 sha256 해시만 저장했고
 * (backend/app/device_token.py), 여기서도 같은 방식으로 해싱해 대조한다.
 * 해시 방식을 바꾸면 양쪽을 같이 고쳐야 한다.
 */
const hashToken = (token: string): string =>
  createHash('sha256').update(token, 'utf-8').digest('hex')

const parseEnvDeviceTokens = (): ReadonlyMap<string, IngestDevice> => {
  if (!config.deviceTokensJson) return new Map()

  let entries: DeviceTokenEntry[]
  try {
    entries = JSON.parse(config.deviceTokensJson)
  } catch {
    throw new Error('DEVICE_TOKENS 가 올바른 JSON 이 아닙니다')
  }
  if (!Array.isArray(entries)) throw new Error('DEVICE_TOKENS 는 배열이어야 합니다')

  return new Map(
    entries.map((entry) => {
      if (
        typeof entry?.token !== 'string' ||
        typeof entry?.storeId !== 'string' ||
        typeof entry?.userId !== 'string'
      ) {
        throw new Error('DEVICE_TOKENS 의 각 항목은 { token, storeId, userId } 를 가져야 합니다')
      }
      return [
        entry.token,
        {
          storeId: entry.storeId,
          userId: entry.userId,
          label: entry.label ?? null,
          deviceRowId: null,
        },
      ] as const
    }),
  )
}

const envDeviceTokens = parseEnvDeviceTokens()

/**
 * 조회 결과 캐시. 조각 업로드는 카메라당 분당 1회지만 하트비트는 30초마다 온다 —
 * 매번 DB 를 두 번씩 때릴 이유가 없다. 폐기(revoke)가 이 시간만큼 늦게 반영되는
 * 것은 감수한다.
 */
const CACHE_TTL_MS = 30_000
const cache = new Map<string, { readonly device: IngestDevice | null; readonly at: number }>()

const lookupInDatabase = async (tokenHash: string): Promise<IngestDevice | null> => {
  if (!supabase) return null

  const { data: devices, error } = await supabase
    .from('devices')
    .select('id, store_id, label')
    .eq('token_hash', tokenHash)
    .is('revoked_at', null)
    .limit(1)
  if (error) {
    console.error('[ingest] devices 조회 실패:', error.message)
    return null
  }
  const device = devices?.[0]
  if (!device) return null

  // videos.user_id 는 auth.users FK 다. 매장 주인('owner' 가 'staff' 보다 먼저 정렬된다)
  // 을 소유자로 쓴다.
  const { data: members } = await supabase
    .from('store_members')
    .select('user_id, role')
    .eq('store_id', device.store_id)
    .order('role')
    .limit(1)
  const userId = members?.[0]?.user_id
  if (!userId) {
    console.error(`[ingest] 매장에 구성원이 없습니다 store_id=${device.store_id}`)
    return null
  }

  return { storeId: device.store_id, userId, label: device.label ?? null, deviceRowId: device.id }
}

export const authenticateDevice = async (bearerToken: string): Promise<IngestDevice | null> => {
  const cached = cache.get(bearerToken)
  if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.device

  const fromDatabase = await lookupInDatabase(hashToken(bearerToken))
  // 이행 기간: 테이블에 없으면 환경변수를 본다. 전환이 끝나면 이 줄을 지운다.
  const device = fromDatabase ?? envDeviceTokens.get(bearerToken) ?? null

  cache.set(bearerToken, { device, at: Date.now() })
  return device
}
