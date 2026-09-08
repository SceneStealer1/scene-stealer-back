/**
 * cctv-agent-electron 의 `src/shared/types.ts` 에 있는 SegmentMeta 의 미러.
 * 그쪽이 API 계약의 단일 출처이고, 이 파일은 수신 측에서 같은 모양을 검증하기 위한 사본이다.
 * 두 프로젝트는 서로 다른 저장소라 타입을 import 할 수 없으므로, 필드를 바꿀 때는
 * docs/protocol-flow.md 와 이 파일을 함께 맞춰야 한다.
 */

export type StreamProfileKind = 'main' | 'sub'
export type VideoCodec = 'h264' | 'h265'

export interface SegmentMeta {
  readonly segmentId: string
  readonly storeId: string
  readonly deviceId: string
  readonly camera: {
    readonly id: string
    readonly name: string
    readonly manufacturer: string | null
    readonly model: string | null
    readonly streamProfile: StreamProfileKind
  }
  readonly video: {
    readonly codec: VideoCodec
    readonly width: number
    readonly height: number
    readonly fps: number
    readonly durationMs: number
    readonly sizeBytes: number
    readonly container: 'mp4'
  }
  readonly startedAt: string
  readonly endedAt: string
  readonly sequence: number
  readonly agentVersion: string
}

const isNonEmptyString = (v: unknown): v is string => typeof v === 'string' && v.length > 0
const isFiniteNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isNullableString = (v: unknown): v is string | null => v === null || typeof v === 'string'

/** 명세(5.2절)에 정의된 필드와 타입을 검사한다. 하나라도 어긋나면 400 감이다. */
export const parseSegmentMeta = (raw: unknown): SegmentMeta | null => {
  if (typeof raw !== 'object' || raw === null) return null
  const m = raw as Record<string, unknown>

  if (!isNonEmptyString(m.segmentId)) return null
  if (!isNonEmptyString(m.storeId)) return null
  if (!isNonEmptyString(m.deviceId)) return null
  if (!isNonEmptyString(m.startedAt)) return null
  if (!isNonEmptyString(m.endedAt)) return null
  if (!isFiniteNumber(m.sequence)) return null
  if (!isNonEmptyString(m.agentVersion)) return null

  const camera = m.camera as Record<string, unknown> | undefined
  if (typeof camera !== 'object' || camera === null) return null
  if (!isNonEmptyString(camera.id)) return null
  if (!isNonEmptyString(camera.name)) return null
  if (!isNullableString(camera.manufacturer)) return null
  if (!isNullableString(camera.model)) return null
  if (camera.streamProfile !== 'main' && camera.streamProfile !== 'sub') return null

  const video = m.video as Record<string, unknown> | undefined
  if (typeof video !== 'object' || video === null) return null
  if (video.codec !== 'h264' && video.codec !== 'h265') return null
  if (!isFiniteNumber(video.width)) return null
  if (!isFiniteNumber(video.height)) return null
  if (!isFiniteNumber(video.fps)) return null
  if (!isFiniteNumber(video.durationMs)) return null
  if (!isFiniteNumber(video.sizeBytes)) return null
  if (video.container !== 'mp4') return null

  return {
    segmentId: m.segmentId,
    storeId: m.storeId,
    deviceId: m.deviceId,
    camera: {
      id: camera.id,
      name: camera.name,
      manufacturer: camera.manufacturer,
      model: camera.model,
      streamProfile: camera.streamProfile,
    },
    video: {
      codec: video.codec,
      width: video.width,
      height: video.height,
      fps: video.fps,
      durationMs: video.durationMs,
      sizeBytes: video.sizeBytes,
      container: 'mp4',
    },
    startedAt: m.startedAt,
    endedAt: m.endedAt,
    sequence: m.sequence,
    agentVersion: m.agentVersion,
  }
}
