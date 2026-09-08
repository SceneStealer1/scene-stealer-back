export type VideoStatus = 'uploaded' | 'processing' | 'done' | 'failed'

export interface VideoRow {
  id: string
  user_id: string
  store_id: string
  device_id: string
  camera_id: string
  camera_location: string | null
  filename: string
  storage_path: string
  status: VideoStatus
  progress: number
  error_message: string | null
  duration_sec: number | null
  fps: number | null
  frame_width: number | null
  frame_height: number | null
  recorded_started_at: string
  recorded_ended_at: string
  sequence: number
  created_at: string
  processed_at: string | null
}

export interface AnomalyEventRow {
  id: string
  video_id: string
  user_id: string
  track_id: number | null
  start_frame: number | null
  end_frame: number | null
  start_time_sec: number
  end_time_sec: number
  anomaly_score: number
  threshold: number
  clip_storage_path: string | null
  thumbnail_storage_path: string | null
  created_at: string
}
