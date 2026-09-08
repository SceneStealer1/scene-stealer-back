/**
 * scene-stealer 백엔드 API. ingest-worker 가 받아서 ai-worker 가 분석해 Supabase 에
 * 쌓아둔 결과(videos, anomaly_events)를 프론트가 조회하는 창구다.
 * nginx가 "/" 이하 전부를 이 서비스로 라우팅한다 (nginx/nginx.conf 참고).
 */
import express, { type Request, type Response } from 'express'
import { config } from './config.js'
import { supabase } from './supabaseClient.js'
import { toClipDto } from './clips.js'
import type { AnomalyEventRow, VideoRow } from './types.js'

const app = express()
app.use(express.json())

const SUPABASE_UNAVAILABLE = { error: 'Supabase 가 설정되지 않았습니다' } as const

const clampLimit = (raw: unknown, fallback: number, max: number): number => {
  const n = Number(raw)
  if (!Number.isFinite(n) || n <= 0) return fallback
  return Math.min(Math.floor(n), max)
}

app.get('/healthz', (_req, res) => res.status(200).json({ ok: true }))

app.get('/', (_req: Request, res: Response) => {
  res.status(200).json({ service: 'scene-stealer-backend', status: 'ok' })
})

// 최근 영상 목록 + 영상별 이상행동 건수.
app.get('/videos', async (req: Request, res: Response) => {
  if (!supabase) return res.status(503).json(SUPABASE_UNAVAILABLE)

  const limit = clampLimit(req.query.limit, 20, 100)
  const status = typeof req.query.status === 'string' ? req.query.status : undefined

  let query = supabase
    .from('videos')
    .select('*')
    .order('recorded_started_at', { ascending: false })
    .limit(limit)
  if (status) query = query.eq('status', status)

  const { data: videos, error } = await query
  if (error) {
    console.error('[backend] /videos query failed:', error)
    return res.status(500).json({ error: '조회 실패' })
  }

  const videoIds = (videos ?? []).map((v) => v.id)
  const countByVideoId = new Map<string, number>()
  if (videoIds.length > 0) {
    const { data: events, error: countError } = await supabase
      .from('anomaly_events')
      .select('video_id')
      .in('video_id', videoIds)
    if (countError) {
      console.error('[backend] /videos anomaly count query failed:', countError)
    } else {
      for (const e of events ?? []) {
        countByVideoId.set(e.video_id, (countByVideoId.get(e.video_id) ?? 0) + 1)
      }
    }
  }

  res.status(200).json({
    videos: (videos ?? []).map((v: VideoRow) => ({
      id: v.id,
      userId: v.user_id,
      storeId: v.store_id,
      cameraLocation: v.camera_location,
      status: v.status,
      progress: v.progress,
      recordedStartedAt: v.recorded_started_at,
      recordedEndedAt: v.recorded_ended_at,
      durationSec: v.duration_sec,
      anomalyCount: countByVideoId.get(v.id) ?? 0,
    })),
  })
})

// 영상 하나 + 그 영상의 하이라이트 클립들(signed URL 포함).
app.get('/videos/:id', async (req: Request, res: Response) => {
  if (!supabase) return res.status(503).json(SUPABASE_UNAVAILABLE)

  const { data: video, error } = await supabase
    .from('videos')
    .select('*')
    .eq('id', req.params.id)
    .maybeSingle()
  if (error) {
    console.error('[backend] /videos/:id query failed:', error)
    return res.status(500).json({ error: '조회 실패' })
  }
  if (!video) return res.status(404).json({ error: '영상을 찾을 수 없습니다' })

  const { data: events, error: eventsError } = await supabase
    .from('anomaly_events')
    .select('*')
    .eq('video_id', video.id)
    .order('start_time_sec', { ascending: true })
  if (eventsError) {
    console.error('[backend] /videos/:id anomaly_events query failed:', eventsError)
    return res.status(500).json({ error: '조회 실패' })
  }

  const clips = await Promise.all(
    ((events ?? []) as AnomalyEventRow[]).map((event) =>
      toClipDto(event, {
        store_id: video.store_id,
        camera_location: video.camera_location,
        recorded_started_at: video.recorded_started_at,
      }),
    ),
  )

  res.status(200).json({
    video: {
      id: video.id,
      userId: video.user_id,
      storeId: video.store_id,
      cameraLocation: video.camera_location,
      status: video.status,
      progress: video.progress,
      recordedStartedAt: video.recorded_started_at,
      recordedEndedAt: video.recorded_ended_at,
      durationSec: video.duration_sec,
    },
    clips,
  })
})

// 전체 매장/카메라를 가로지르는 하이라이트 클립 피드 — 프론트 대시보드가 주로 쓸 API.
app.get('/clips', async (req: Request, res: Response) => {
  if (!supabase) return res.status(503).json(SUPABASE_UNAVAILABLE)

  const limit = clampLimit(req.query.limit, 20, 100)
  const userId = typeof req.query.userId === 'string' ? req.query.userId : undefined

  let query = supabase
    .from('anomaly_events')
    .select('*, videos!inner(store_id, camera_location, recorded_started_at, user_id)')
    .order('created_at', { ascending: false })
    .limit(limit)
  if (userId) query = query.eq('user_id', userId)

  const { data, error } = await query
  if (error) {
    console.error('[backend] /clips query failed:', error)
    return res.status(500).json({ error: '조회 실패' })
  }

  type Row = AnomalyEventRow & {
    videos: Pick<VideoRow, 'store_id' | 'camera_location' | 'recorded_started_at' | 'user_id'>
  }

  const clips = await Promise.all((data as Row[]).map((row) => toClipDto(row, row.videos)))
  res.status(200).json({ clips })
})

app.listen(config.port, () => {
  console.log(`[backend] listening on :${config.port}`)
})
