import 'dotenv/config'

const required = (name: string): string => {
  const v = process.env[name]
  if (!v) throw new Error(`missing required env: ${name}`)
  return v
}

export const config = {
  port: Number(process.env.PORT ?? 8080),
  /** 조각(mp4 + meta json)을 쌓아 두는 로컬 디렉터리. 스웜 배포 시 볼륨을 마운트한다. */
  storageDir: process.env.STORAGE_DIR ?? './data/segments',
  /** 메인스트림 5분 조각(약 150MB)이 여유 있게 통과하도록 기본값을 250MB 로 잡는다. */
  maxSegmentBytes: Number(process.env.MAX_SEGMENT_BYTES ?? 250 * 1024 * 1024),
  /** JSON.parse(required('DEVICE_TOKENS')) 로 늦게 파싱한다 — deviceAuth.ts 참고. */
  deviceTokensJson: required('DEVICE_TOKENS'),
} as const
