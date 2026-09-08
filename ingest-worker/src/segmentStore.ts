import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { config } from './config.js'
import type { SegmentMeta } from './segmentMeta.js'

export type SaveResult = { readonly kind: 'saved'; readonly videoPath: string } | { readonly kind: 'duplicate' }

const segmentPaths = (meta: SegmentMeta): { readonly dir: string; readonly videoPath: string; readonly metaPath: string } => {
  // storeId/cameraId 로 사람이 훑어보기 쉽게 나누고, 파일명은 전역 고유한 segmentId 로 둔다.
  const dir = path.join(config.storageDir, meta.storeId, meta.camera.id)
  return {
    dir,
    videoPath: path.join(dir, `${meta.segmentId}.mp4`),
    metaPath: path.join(dir, `${meta.segmentId}.json`),
  }
}

/**
 * 조각을 로컬 디스크에 저장한다. 멱등 처리는 mp4 파일의 배타적 생성(`wx`)에 기댄다 —
 * 이미 존재하면 EEXIST 로 실패하고 그걸 'duplicate' 로 번역한다. 에이전트가 한 번에
 * 하나씩만 순차 업로드하므로(명세 5.5절) 동시 쓰기 경합은 사실상 없다.
 */
export const saveSegment = async (meta: SegmentMeta, video: Buffer): Promise<SaveResult> => {
  const { dir, videoPath, metaPath } = segmentPaths(meta)
  await mkdir(dir, { recursive: true })

  try {
    await writeFile(videoPath, video, { flag: 'wx' })
  } catch (err) {
    if (isNodeError(err) && err.code === 'EEXIST') return { kind: 'duplicate' }
    throw err
  }

  // meta 는 사람이 훑어보기 위한 부가 기록이라 mp4 저장 성공 여부만큼 엄격하게 다루지 않는다.
  await writeFile(metaPath, JSON.stringify(meta, null, 2))

  return { kind: 'saved', videoPath }
}

const isNodeError = (err: unknown): err is NodeJS.ErrnoException =>
  typeof err === 'object' && err !== null && 'code' in err
