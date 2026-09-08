import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import { config } from './config.js'

/** Supabase 미설정이면 null — 조회 라우트가 503 으로 응답한다 (server.ts 참고). */
export const supabase: SupabaseClient | null =
  config.supabaseUrl && config.supabaseServiceKey
    ? createClient(config.supabaseUrl, config.supabaseServiceKey, { auth: { persistSession: false } })
    : null

if (!supabase) {
  console.warn('[backend] SUPABASE_URL/SUPABASE_SERVICE_KEY 미설정 — 조회 API가 503을 반환합니다')
}
