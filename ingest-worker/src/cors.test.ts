import assert from 'node:assert/strict'
import { once } from 'node:events'
import { request, type IncomingHttpHeaders } from 'node:http'
import type { AddressInfo } from 'node:net'
import { describe, it } from 'node:test'
import express from 'express'
import { createCors, isAllowedOrigin, normalizeDomain } from './cors.js'

interface Reply {
  readonly status: number
  readonly headers: IncomingHttpHeaders
}

/** 실제 Express 에 미들웨어를 물려 요청을 보낸다. 사전 확인은 인증까지 가지 않아야 한다. */
const withServer = async (domain: string | undefined, run: (send: (method: string, headers: Record<string, string>) => Promise<Reply>) => Promise<void>) => {
  const app = express()
  app.use(createCors(domain))
  app.post('/v1/segments', (_req, res) => {
    res.status(201).json({ received: true })
  })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const { port } = server.address() as AddressInfo

  const send = (method: string, headers: Record<string, string>): Promise<Reply> =>
    new Promise((resolve, reject) => {
      const req = request({ host: '127.0.0.1', port, path: '/v1/segments', method, headers }, (res) => {
        res.resume()
        res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers }))
      })
      req.on('error', reject)
      req.end()
    })

  try {
    await run(send)
  } finally {
    server.close()
  }
}

const preflightHeaders = (origin: string) => ({
  origin,
  'access-control-request-method': 'POST',
  'access-control-request-headers': 'authorization, idempotency-key',
})

describe('CORS — 같은 최상위 도메인의 https 만', () => {
  it('같은 도메인과 하위 도메인은 사전 확인을 통과하고, 실제 요청에도 허용 헤더가 붙는다', async () => {
    await withServer('example.com', async (send) => {
      for (const origin of ['https://example.com', 'https://demo.example.com']) {
        const preflight = await send('OPTIONS', preflightHeaders(origin))
        assert.equal(preflight.status, 204)
        assert.equal(preflight.headers['access-control-allow-origin'], origin)
        assert.match(String(preflight.headers['access-control-allow-headers']), /idempotency-key/)

        const upload = await send('POST', { origin })
        assert.equal(upload.status, 201)
        assert.equal(upload.headers['access-control-allow-origin'], origin)
      }
    })
  })

  it('다른 출처는 사전 확인에서 막는다 — 인증·업로드까지 가지 않는다', async () => {
    await withServer('example.com', async (send) => {
      for (const origin of ['https://cctv-agent-electron-pc.vercel.app', 'https://example.com.evil.io', 'http://demo.example.com']) {
        const preflight = await send('OPTIONS', preflightHeaders(origin))
        assert.equal(preflight.status, 403)
        assert.equal(preflight.headers['access-control-allow-origin'], undefined)
      }
    })
  })

  it('설정이 없으면 예전과 같다 — CORS 헤더를 붙이지 않는다', async () => {
    await withServer(undefined, async (send) => {
      const upload = await send('POST', { origin: 'https://demo.example.com' })
      assert.equal(upload.status, 201)
      assert.equal(upload.headers['access-control-allow-origin'], undefined)
    })
  })

  it('도메인 규칙', () => {
    assert.equal(isAllowedOrigin('https://a.b.example.com', 'example.com'), true)
    assert.equal(isAllowedOrigin('https://evilexample.com', 'example.com'), false)
    assert.equal(isAllowedOrigin('https://example.co', 'example.com'), false)
    assert.equal(isAllowedOrigin(undefined, 'example.com'), false)
    assert.equal(normalizeDomain(' Example.COM '), 'example.com')
    assert.equal(normalizeDomain(''), null)
    assert.throws(() => normalizeDomain('https://example.com'))
    assert.throws(() => normalizeDomain('*.example.com'))
  })
})
