import { readFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { supabase } from './supabaseClient.js'
import type { IngestDevice } from './deviceAuth.js'
import type { SegmentMeta } from './segmentMeta.js'

/**
 * 로컬 디스크에 저장된 조각을 Supabase 로 올려서 ai-worker(scene-stealer-back/ai-worker)
 * 가 집어갈 수 있게 한다 — ai-worker 는 videos 테이블의 status='uploaded' 를 폴링한다.
 *
 * 응답은 이미 에이전트에게 보낸 뒤 fire-and-forget 으로 호출된다 (server.ts 참고) — 5분
 * 영상을 분석 전에 동기로 기다리면 에이전트의 순차 업로드 큐 전체가 막히기 때문이다.
 * 그래서 실패해도 이미 보낸 HTTP 응답을 바꿀 수 없고, 로그만 남긴다 — 로컬 디스크에는
 * 원본이 그대로 남아 있으니 데이터 유실은 아니다.
 */
export const handoffToAnalysis = async (
  meta: SegmentMeta,
  device: IngestDevice,
  localVideoPath: string,
): Promise<void> => {
  if (!supabase) return // 미설정 — 로컬 저장까지만 하고 조용히 스킵

  const filename = `${meta.segmentId}.mp4`
  const storagePath = `${device.userId}/${meta.segmentId}/${filename}`
  const videoBuffer = await readFile(localVideoPath)

  const { error: uploadError } = await supabase.storage
    .from('videos')
    .upload(storagePath, videoBuffer, { contentType: 'video/mp4' })
  if (uploadError) throw new Error(`storage upload failed: ${uploadError.message}`)

  const { error: insertError } = await supabase.from('videos').insert({
    id: randomUUID(),
    user_id: device.userId,
    store_id: meta.storeId,
    device_id: meta.deviceId,
    camera_id: meta.camera.id,
    camera_location: meta.camera.name,
    filename,
    storage_path: storagePath,
    status: 'uploaded',
    duration_sec: meta.video.durationMs / 1000,
    fps: meta.video.fps,
    frame_width: meta.video.width,
    frame_height: meta.video.height,
    recorded_started_at: meta.startedAt,
    recorded_ended_at: meta.endedAt,
    sequence: meta.sequence,
  })
  if (insertError) throw new Error(`videos insert failed: ${insertError.message}`)
}
