import 'dotenv/config'

export const config = {
  port: Number(process.env.PORT ?? 8081),
} as const
