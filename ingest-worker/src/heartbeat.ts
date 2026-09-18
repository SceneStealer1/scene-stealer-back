/**
 * PC 하트비트 + 카메라 런타임 상태 보고 (요구사항 7.1 · 2.4).
 *
 * PC 가 30초마다 한 번에 보낸다. 두 가지를 한 요청으로 묶은 이유는 PC 가 이미
 * 카메라 상태를 들고 있어서고(카메라마다 독립적으로 끊기고 복구된다),
 * 따로 받으면 두 값의 시점이 어긋나 "PC 는 켜져 있는데 카메라 상태는 20분 전"
 * 같은 화면이 나온다.
 *
 * 응답으로 매장의 segment_seconds 를 돌려준다 — 조각 길이의 단일 출처는 서버이고
 * PC 가 그걸 따라간다 (docs/api-contract.md 4.1).
 */
import { config } from './config.js'
import type { IngestDevice } from './deviceAuth.js'
import { supabase } from './supabaseClient.js'

export type CameraRuntimeState =
  | 'connected'
  | 'reconnecting'
  | 'disconnected'
  | 'auth_failed'
  | 'unknown'

const CAMERA_STATES: readonly CameraRuntimeState[] = [
  'connected',
  'reconnecting',
  'disconnected',
  'auth_failed',
  'unknown',
]

export interface CameraReport {
  readonly agentCameraId: string
  readonly state: CameraRuntimeState
  readonly lastFrameAt: string | null
}

export interface HeartbeatBody {
  readonly agentVersion?: string
  readonly spoolBytes?: number
  readonly uploadedBytesToday?: number
  readonly cameras?: readonly CameraReport[]
}

const isCameraReport = (value: unknown): value is CameraReport => {
  if (typeof value !== 'object' || value === null) return false
  const report = value as Record<string, unknown>
  return (
    typeof report.agentCameraId === 'string' &&
    report.agentCameraId.length > 0 &&
    CAMERA_STATES.includes(report.state as CameraRuntimeState) &&
    (report.lastFrameAt === null ||
      report.lastFrameAt === undefined ||
      typeof report.lastFrameAt === 'string')
  )
}

export const parseHeartbeat = (raw: unknown): HeartbeatBody | null => {
  if (typeof raw !== 'object' || raw === null) return null
  const body = raw as Record<string, unknown>

  if (body.agentVersion !== undefined && typeof body.agentVersion !== 'string') return null
  if (body.spoolBytes !== undefined && !Number.isFinite(body.spoolBytes)) return null
  if (body.uploadedBytesToday !== undefined && !Number.isFinite(body.uploadedBytesToday)) return null

  const cameras = body.cameras
  if (cameras !== undefined) {
    if (!Array.isArray(cameras)) return null
    if (!cameras.every(isCameraReport)) return null
  }

  return {
    agentVersion: body.agentVersion as string | undefined,
    spoolBytes: body.spoolBytes as number | undefined,
    uploadedBytesToday: body.uploadedBytesToday as number | undefined,
    cameras: (cameras as CameraReport[] | undefined)?.map((c) => ({
      agentCameraId: c.agentCameraId,
      state: c.state,
      lastFrameAt: c.lastFrameAt ?? null,
    })),
  }
}

/** 상태가 실제로 바뀐 카메라만 SSE 로 알린다 — 30초마다 전부 밀면 화면이 깜빡인다. */
const notifyStateChange = async (
  storeId: string,
  cameraId: string,
  state: CameraRuntimeState,
  lastFrameAt: string | null,
): Promise<void> => {
  if (!config.internalApiToken) return
  try {
    await fetch(`${config.backendInternalUrl.replace(/\/$/, '')}/internal/cameras/state`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Internal-Token': config.internalApiToken,
      },
      body: JSON.stringify({ storeId, cameraId, state, lastFrameAt }),
    })
  } catch (err) {
    // 알림이 못 가도 DB 는 갱신됐다. 화면은 다음 조회에서 따라잡는다.
    console.error('[ingest] 카메라 상태 알림 실패:', err)
  }
}

export const applyHeartbeat = async (
  device: IngestDevice,
  body: HeartbeatBody,
): Promise<{ readonly segmentSeconds: number }> => {
  const now = new Date().toISOString()

  if (!supabase) return { segmentSeconds: 60 }

  if (device.deviceRowId) {
    await supabase
      .from('devices')
      .update({
        last_heartbeat_at: now,
        agent_version: body.agentVersion ?? null,
        spool_bytes: body.spoolBytes ?? null,
        uploaded_bytes_today: body.uploadedBytesToday ?? null,
      })
      .eq('id', device.deviceRowId)
  }

  for (const report of body.cameras ?? []) {
    const { data: rows } = await supabase
      .from('cameras')
      .select('id, runtime_state')
      .eq('store_id', device.storeId)
      .eq('agent_camera_id', report.agentCameraId)
      .limit(1)
    const camera = rows?.[0]
    if (!camera) continue // 아직 등록되지 않은 카메라. 등록되면 다음 하트비트에 잡힌다.

    const changed = camera.runtime_state !== report.state
    await supabase
      .from('cameras')
      .update({
        runtime_state: report.state,
        last_frame_at: report.lastFrameAt,
        // 상태가 그대로면 시각을 갱신하지 않는다 — "얼마나 끊겼는지"(모바일의
        // '창고 끊김 13분')를 이 값으로 재기 때문이다.
        ...(changed ? { state_updated_at: now } : {}),
      })
      .eq('id', camera.id)

    if (changed) {
      await notifyStateChange(device.storeId, camera.id, report.state, report.lastFrameAt)
    }
  }

  const { data: stores } = await supabase
    .from('stores')
    .select('segment_seconds')
    .eq('id', device.storeId)
    .limit(1)

  return { segmentSeconds: stores?.[0]?.segment_seconds ?? 60 }
}
