import { NextRequest } from 'next/server'
import { cached, cachePolicy, redis } from '@/lib/redis'

const backend = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
type ProxyResult = { body: string; status: number; contentType: string }

async function fetchUpstream(request: NextRequest, routePath: string): Promise<ProxyResult> {
  const upstreamPath = routePath === 'health' || routePath === 'openapi.json' ? routePath : `api/${routePath}`
  const target = `${backend}/${upstreamPath}${request.nextUrl.search}`
  const headers = new Headers()
  for (const name of ['content-type', 'accept', 'idempotency-key']) {
    const value = request.headers.get(name)
    if (value) headers.set(name, value)
  }
  const body = request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.text()
  try {
    const response = await fetch(target, { method: request.method, headers, body, cache: 'no-store' })
    return { body: await response.text(), status: response.status, contentType: response.headers.get('content-type') || 'application/json' }
  } catch (error) {
    console.error('[v0] FastAPI proxy unavailable:', error)
    return { body: JSON.stringify({ detail: 'The reconciliation backend is unavailable. Start FastAPI on http://localhost:8000 and try again.' }), status: 502, contentType: 'application/json' }
  }
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params
  const routePath = path.join('/')
  let result: ProxyResult
  if (request.method === 'GET' && routePath === 'results/latest') result = await cached(cachePolicy.latestResults.key, () => fetchUpstream(request, routePath), cachePolicy.latestResults.ttlSeconds)
  else if (request.method === 'GET' && routePath.startsWith('results/')) {
    const policy = cachePolicy.results(routePath.slice(8))
    result = await cached(policy.key, () => fetchUpstream(request, routePath), policy.ttlSeconds)
    if (result.status >= 200 && result.status < 300 && redis) {
      try {
        await redis.set(cachePolicy.latestResults.key, result, { ex: cachePolicy.latestResults.ttlSeconds })
      } catch (error) { console.warn('[v0] Latest-result cache refresh bypass:', error) }
    }
  } else if (request.method === 'GET' && routePath.startsWith('exceptions/')) { const policy = cachePolicy.exceptions(routePath.slice(10)); result = await cached(policy.key, () => fetchUpstream(request, routePath), policy.ttlSeconds) }
  else if (request.method === 'GET' && routePath.startsWith('reasoning-trace/')) { const policy = cachePolicy.trace(routePath.slice(16)); result = await cached(policy.key, () => fetchUpstream(request, routePath), policy.ttlSeconds) }
  else result = await fetchUpstream(request, routePath)
  if (request.method === 'POST' && routePath === 'run' && redis) {
    try { await redis.del(cachePolicy.latestResults.key) } catch (error) { console.warn('[v0] Latest-result cache invalidation bypass:', error) }
  }
  return new Response(result.body, { status: result.status, headers: { 'content-type': result.contentType } })
}

export const GET = proxy
export const POST = proxy
export const PUT = proxy
export const PATCH = proxy
export const DELETE = proxy
