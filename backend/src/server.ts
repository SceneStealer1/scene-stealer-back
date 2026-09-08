/**
 * scene-stealer 백엔드 API 스켈레톤. 지금은 헬스체크뿐이고, 대시보드/분석 파이프라인이
 * 필요로 하는 엔드포인트(예: 이상행동 이벤트 조회, 리포트 생성 트리거)를 여기에 얹는다.
 * nginx가 "/" 이하 전부를 이 서비스로 라우팅한다 (nginx/nginx.conf 참고).
 */
import express, { type Request, type Response } from 'express'
import { config } from './config.js'

const app = express()
app.use(express.json())

app.get('/healthz', (_req, res) => res.status(200).json({ ok: true }))

app.get('/', (_req: Request, res: Response) => {
  res.status(200).json({ service: 'scene-stealer-backend', status: 'ok' })
})

app.listen(config.port, () => {
  console.log(`[backend] listening on :${config.port}`)
})
