/**
 * cctv-agent-electron 의 업로더(src/main/services/uploader.ts)가 보내는
 * POST /v1/segments 를 받는다. 계약은 cctv-agent-electron/docs/protocol-flow.md 5절 참고.
 *
 * 조각은 항상 로컬 디스크(STORAGE_DIR)에 mp4 + meta json 으로 먼저 쌓인다 — src/segmentStore.ts.
 * 응답을 보낸 뒤에는 Supabase 가 설정돼 있으면 ai-worker 가 집어갈 수 있게 백그라운드로
 * 핸드오프한다 — src/analysisHandoff.ts. 분석은 5분 영상 기준 수 분이 걸릴 수 있어서
 * 응답 전에 기다리면 에이전트의 순차 업로드 큐 전체가 막힌다 (agent 는 카메라를 돌아가며
 * 한 번에 하나씩만 업로드한다 — protocol-flow.md 6절).
 *
 * meta 파트는 undici FormData 가 filename 없는 Blob 에 기본값 "blob" 을 붙이는 스펙 동작 때문에
 * multer 관점에서는 "video" 와 동일하게 파일 파트로 도착한다 — fields() 로 둘 다 받는다.
 */
import express, { type NextFunction, type Request, type Response } from 'express'
import multer, { MulterError } from 'multer'
import { config } from './config.js'
import { authenticateDevice } from './deviceAuth.js'
import { parseSegmentMeta } from './segmentMeta.js'
import { saveSegment } from './segmentStore.js'
import { handoffToAnalysis } from './analysisHandoff.js'

const app = express()

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: config.maxSegmentBytes },
})

app.get('/healthz', (_req, res) => res.status(200).json({ ok: true }))

const jsonError = (res: Response, status: number, message: string): Response =>
  res.status(status).json({ error: message })

app.post(
  '/v1/segments',
  (req: Request, res: Response, next: NextFunction) => {
    upload.fields([
      { name: 'meta', maxCount: 1 },
      { name: 'video', maxCount: 1 },
    ])(req, res, (err: unknown) => {
      if (err instanceof MulterError && err.code === 'LIMIT_FILE_SIZE') {
        return jsonError(res, 413, '조각이 너무 큽니다')
      }
      if (err) return next(err)
      next()
    })
  },
  async (req: Request, res: Response) => {
    const authHeader = req.headers.authorization
    const bearerToken = authHeader?.startsWith('Bearer ') ? authHeader.slice('Bearer '.length) : null
    if (!bearerToken) return jsonError(res, 401, '토큰이 없습니다')

    const device = await authenticateDevice(bearerToken)
    if (!device) return jsonError(res, 401, '유효하지 않은 토큰입니다')

    const files = req.files as Record<string, Express.Multer.File[]> | undefined
    const metaFile = files?.meta?.[0]
    const videoFile = files?.video?.[0]
    if (!metaFile) return jsonError(res, 400, 'meta 파트가 없습니다')
    if (!videoFile) return jsonError(res, 400, 'video 파트가 없습니다')

    let metaJson: unknown
    try {
      metaJson = JSON.parse(metaFile.buffer.toString('utf-8'))
    } catch {
      return jsonError(res, 400, 'meta 가 올바른 JSON 이 아닙니다')
    }

    const meta = parseSegmentMeta(metaJson)
    if (!meta) return jsonError(res, 400, 'meta 스키마가 명세와 다릅니다')

    const idempotencyKey = req.headers['idempotency-key']
    if (idempotencyKey !== meta.segmentId) {
      return jsonError(res, 400, 'Idempotency-Key 가 meta.segmentId 와 다릅니다')
    }

    if (meta.storeId !== device.storeId) {
      return jsonError(res, 403, 'storeId 가 토큰에 등록된 매장과 다릅니다')
    }

    try {
      const result = await saveSegment(meta, videoFile.buffer)
      if (result.kind === 'duplicate') {
        return res.status(409).json({ segmentId: meta.segmentId, received: true })
      }
      console.log(
        `[ingest] stored segmentId=${meta.segmentId} storeId=${meta.storeId} camera=${meta.camera.name} sequence=${meta.sequence} -> ${result.videoPath}`,
      )

      // 응답은 여기서 바로 나간다 — 에이전트는 이 body 를 읽지 않는다(상태 코드만 본다).
      // analysis 필드는 curl/로그로 확인할 사람을 위한 것이고, 실제 결과는 backend 조회
      // API가 Supabase 에서 읽어서 프론트에 보여준다.
      res.status(201).json({ segmentId: meta.segmentId, received: true, analysis: 'pending' })

      handoffToAnalysis(meta, device, result.videoPath).catch((err: unknown) => {
        console.error(`[ingest] analysis handoff failed segmentId=${meta.segmentId}:`, err)
      })
      return
    } catch (err) {
      console.error(`[ingest] save failed segmentId=${meta.segmentId}:`, err)
      return jsonError(res, 500, '저장에 실패했습니다')
    }
  },
)

app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  console.error('[ingest] unhandled error:', err)
  jsonError(res, 500, '내부 오류')
})

app.listen(config.port, () => {
  console.log(`[ingest] listening on :${config.port}, storageDir=${config.storageDir}`)
})
