import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import { config } from './config.js'

/**
 * Supabase 프로젝트가 아직 준비되지 않았을 수 있어서 null 을 허용한다 — 그 경우
 * analysisHandoff.ts 가 업로드를 건너뛰고 로그만 남긴다. 로컬 저장(segmentStore.ts)은
 * 이것과 무관하게 항상 동작한다.
 */
export const supabase: SupabaseClient | null =
  config.supabaseUrl && config.supabaseServiceKey
    ? createClient(config.supabaseUrl, config.supabaseServiceKey, { auth: { persistSession: false } })
    : null

if (!supabase) {
  console.warn('[ingest] SUPABASE_URL/SUPABASE_SERVICE_KEY 미설정 — 분석 파이프라인 핸드오프를 건너뜁니다')
}
