import 'dotenv/config'

export const config = {
  port: Number(process.env.PORT ?? 8081),
  /** 아직 Supabase 프로젝트가 없을 수 있어서 선택값 — supabaseClient.ts 참고. */
  supabaseUrl: process.env.SUPABASE_URL,
  supabaseServiceKey: process.env.SUPABASE_SERVICE_KEY,
  /** 클립/썸네일 signed URL 유효시간 (초). */
  signedUrlTtlSec: Number(process.env.SIGNED_URL_TTL_SEC ?? 3600),
} as const
