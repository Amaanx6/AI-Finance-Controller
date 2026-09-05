import { Redis } from '@upstash/redis'

const url = process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL
const token = process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN
const hasValidConfig = Boolean(url?.startsWith('https://') && token && !token.includes('<'))

// Redis is an optional acceleration layer: malformed or missing credentials must
// never prevent the same request from reaching the FastAPI source of truth.
export const redis = hasValidConfig ? new Redis({ url: url as string, token: token as string }) : null

export async function cached<T extends { status?: number }>(key: string, loader: () => Promise<T>, ttlSeconds: number): Promise<T> {
  if (!redis) return loader()
  try {
    const hit = await redis.get<T>(key)
    if (hit !== null && hit !== undefined) return hit
  } catch (error) { console.warn('[v0] Redis read bypass:', error) }
  const value = await loader()
  if (typeof value.status === 'number' && value.status >= 400) return value
  try { await redis.set(key, value, { ex: ttlSeconds }) } catch (error) { console.warn('[v0] Redis write bypass:', error) }
  return value
}

export const cachePolicy = {
  latestResults: { key: 'reconcile:results:latest', ttlSeconds: 15 },
  results: (runId: string) => ({ key: `reconcile:results:${runId}`, ttlSeconds: 86400 }),
  exceptions: (runId: string) => ({ key: `reconcile:exceptions:${runId}`, ttlSeconds: 86400 }),
  trace: (recordId: string) => ({ key: `reconcile:trace:${recordId}`, ttlSeconds: 86400 }),
}
