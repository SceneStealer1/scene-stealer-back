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
  /**
   * Supabase 프로젝트가 아직 없을 수 있어서(둘 다 없으면 undefined) 선택값으로 둔다.
   * 없으면 로컬 저장까지만 하고 ai-worker 로의 핸드오프는 건너뛴다 — src/analysisHandoff.ts.
   */
  supabaseUrl: process.env.SUPABASE_URL,
  supabaseServiceKey: process.env.SUPABASE_SERVICE_KEY,
} as const
