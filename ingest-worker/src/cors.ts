/**
 * CORS — 웹 체험판(/wanted-test)은 브라우저가 매장 PC 수집기 역할을 해서, 조각 업로드
 * (POST /v1/segments)와 하트비트를 다른 주소의 페이지에서 직접 보낸다.
 *
 * 허용하는 출처는 **같은 최상위 도메인**뿐이다. 도메인이 scene-stealer.site(기본값, config.ts)면
 * https://scene-stealer.site 와 https://<하위>.scene-stealer.site 만 받는다. 도메인을 주지 않으면
 * CORS 를 열지 않는다. 매장 PC 에이전트는 브라우저가 아니라 CORS 를 타지 않는다.
 *
 * backend/app/cors.py 와 같은 규칙이다. 바꾸면 양쪽을 같이 고친다.
 */
import type { NextFunction, Request, Response } from 'express'

const ALLOWED_HEADERS = 'authorization, content-type, idempotency-key'
const ALLOWED_METHODS = 'GET, POST, OPTIONS'
/** 브라우저가 사전 확인(OPTIONS) 결과를 기억하는 시간. 조각마다 한 번 더 왕복하지 않게. */
const PREFLIGHT_MAX_AGE_SEC = 600

/** 점으로 나뉜 라벨 두 개 이상. 한글 도메인은 퓨니코드(xn--…)로 적는다. */
const HOSTNAME = /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9-]{2,}$/

/**
 * ' Example.COM ' → 'example.com'. 비었으면 null, 도메인 모양이 아니면 던진다 —
 * 잘못 적은 값을 조용히 넘기면 '왜 업로드가 막히지'를 추적할 수 없다.
 */
export const normalizeDomain = (domain: string | undefined): string | null => {
  const value = (domain ?? '').trim().toLowerCase().replace(/^\.+|\.+$/g, '')
  if (!value) return null
  if (!HOSTNAME.test(value)) {
    throw new Error(`CORS_ALLOWED_DOMAIN 은 'example.com' 같은 도메인이어야 합니다 (받은 값: ${JSON.stringify(domain)})`)
  }
  return value
}

const escapeRegExp = (value: string): string => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** 그 도메인과 하위 도메인의 https 출처. 'example.com.evil.io' · 'evilexample.com' 은 걸리지 않는다. */
export const isAllowedOrigin = (origin: string | undefined, domain: string | null): boolean =>
  Boolean(domain && origin && new RegExp(`^https://(?:[a-z0-9-]+\\.)*${escapeRegExp(domain)}$`).test(origin))

export const createCors = (configuredDomain: string | undefined) => {
  const domain = normalizeDomain(configuredDomain)

  return (req: Request, res: Response, next: NextFunction): void => {
    // 설정이 없으면 예전과 똑같이 둔다.
    if (!domain) return next()

    const origin = req.headers.origin
    const allowed = isAllowedOrigin(origin, domain)
    // 응답이 Origin 에 따라 달라진다는 표시 — 중간 캐시가 다른 출처에 같은 응답을 주지 않게.
    res.append('Vary', 'Origin')
    if (allowed && origin) res.setHeader('Access-Control-Allow-Origin', origin)

    const preflight = req.method === 'OPTIONS' && req.headers['access-control-request-method'] !== undefined
    if (!preflight) return next()

    if (!allowed) {
      res.status(403).json({ error: '허용되지 않은 출처입니다' })
      return
    }
    res.setHeader('Access-Control-Allow-Methods', ALLOWED_METHODS)
    res.setHeader('Access-Control-Allow-Headers', ALLOWED_HEADERS)
    res.setHeader('Access-Control-Max-Age', String(PREFLIGHT_MAX_AGE_SEC))
    res.status(204).end()
  }
}
