import { config } from './config.js'
import { supabase } from './supabaseClient.js'
import type { AnomalyEventRow, VideoRow } from './types.js'

/** videos.recorded_started_at + 상대 오프셋(초) -> 실제 촬영 절대시각. */
export const toAbsoluteTime = (recordedStartedAt: string, offsetSec: number): string =>
  new Date(new Date(recordedStartedAt).getTime() + offsetSec * 1000).toISOString()

export const signedUrl = async (bucket: string, path: string | null): Promise<string | null> => {
  if (!supabase || !path) return null
  const { data, error } = await supabase.storage.from(bucket).createSignedUrl(path, config.signedUrlTtlSec)
  if (error) {
    console.error(`[backend] signed url failed bucket=${bucket} path=${path}:`, error)
    return null
  }
  return data.signedUrl
}

export interface ClipDto {
  readonly id: string
  readonly videoId: string
  readonly userId: string
  readonly storeId: string | null
  readonly cameraLocation: string | null
  readonly startAt: string
  readonly endAt: string
  readonly startTimeSec: number
  readonly endTimeSec: number
  readonly anomalyScore: number
  readonly threshold: number
  readonly clipUrl: string | null
  readonly thumbnailUrl: string | null
  readonly createdAt: string
}

/** anomaly_events 행 하나를 프론트가 바로 쓸 수 있는 모양으로 변환한다 (signed URL 포함). */
export const toClipDto = async (
  event: AnomalyEventRow,
  video: Pick<VideoRow, 'store_id' | 'camera_location' | 'recorded_started_at'>,
): Promise<ClipDto> => {
  const [clipUrl, thumbnailUrl] = await Promise.all([
    signedUrl('clips', event.clip_storage_path),
    signedUrl('clips', event.thumbnail_storage_path),
  ])

  return {
    id: event.id,
    videoId: event.video_id,
    userId: event.user_id,
    storeId: video.store_id,
    cameraLocation: video.camera_location,
    startAt: toAbsoluteTime(video.recorded_started_at, event.start_time_sec),
    endAt: toAbsoluteTime(video.recorded_started_at, event.end_time_sec),
    startTimeSec: event.start_time_sec,
    endTimeSec: event.end_time_sec,
    anomalyScore: event.anomaly_score,
    threshold: event.threshold,
    clipUrl,
    thumbnailUrl,
    createdAt: event.created_at,
  }
}
