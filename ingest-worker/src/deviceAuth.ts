import { config } from './config.js'

export interface IngestDevice {
  readonly storeId: string
  readonly label: string | null
}

interface DeviceTokenEntry {
  readonly token: string
  readonly storeId: string
  readonly label?: string
}

/**
 * 백엔드(DB)가 아직 없으므로 디바이스 토큰을 DEVICE_TOKENS 환경변수(JSON 배열)로 관리한다.
 * 나중에 실제 발급/폐기 절차가 생기면 이 파일만 DB 조회로 바꾸면 된다 — 나머지 로직은
 * IngestDevice 모양만 보고 동작한다.
 *
 * 형식: DEVICE_TOKENS='[{"token":"<deviceToken>","storeId":"store-gangnam-01","label":"강남점"}]'
 */
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
    if (typeof entry?.token !== 'string' || typeof entry?.storeId !== 'string') {
      throw new Error('DEVICE_TOKENS 의 각 항목은 { token, storeId } 를 가져야 합니다')
    }
    map.set(entry.token, { storeId: entry.storeId, label: entry.label ?? null })
  }
  return map
}

const deviceTokens = parseDeviceTokens()

export const authenticateDevice = (bearerToken: string): IngestDevice | null =>
  deviceTokens.get(bearerToken) ?? null
