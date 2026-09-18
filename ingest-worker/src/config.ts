import 'dotenv/config'

export const config = {
  port: Number(process.env.PORT ?? 8080),
  /** 조각(mp4 + meta json)을 쌓아 두는 로컬 디렉터리. 스웜 배포 시 볼륨을 마운트한다. */
  storageDir: process.env.STORAGE_DIR ?? './data/segments',
  /** 메인스트림 5분 조각(약 150MB)이 여유 있게 통과하도록 기본값을 250MB 로 잡는다. */
  maxSegmentBytes: Number(process.env.MAX_SEGMENT_BYTES ?? 250 * 1024 * 1024),
  /**
   * 기기 토큰은 이제 devices 테이블이 단일 출처라 선택값이다 — deviceAuth.ts 참고.
   * 이미 배포된 에이전트를 위한 이행 기간 폴백으로만 남아 있고, 전환이 끝나면
   * 이 값과 deviceAuth 의 폴백 경로를 같이 지운다.
   */
  deviceTokensJson: process.env.DEVICE_TOKENS ?? '',
  /** backend 의 /internal/* 를 부를 때 쓰는 공유 비밀값. 없으면 실시간 알림을 건너뛴다. */
  internalApiToken: process.env.INTERNAL_API_TOKEN,
  /** 스택 내부 주소. nginx 를 거치지 않는다. */
  backendInternalUrl: process.env.BACKEND_INTERNAL_URL ?? 'http://backend:8081',
  /**
   * Supabase 프로젝트가 아직 없을 수 있어서(둘 다 없으면 undefined) 선택값으로 둔다.
   * 없으면 로컬 저장까지만 하고 ai-worker 로의 핸드오프는 건너뛴다 — src/analysisHandoff.ts.
   */
  supabaseUrl: process.env.SUPABASE_URL,
  supabaseServiceKey: process.env.SUPABASE_SERVICE_KEY,
  /**
   * 웹 체험판이 브라우저에서 조각을 올릴 수 있는 도메인. 이 도메인과 하위 도메인의 https 만
   * 받는다 (src/cors.ts). 비우면 CORS 를 열지 않는다.
   */
  corsAllowedDomain: process.env.CORS_ALLOWED_DOMAIN,
} as const
