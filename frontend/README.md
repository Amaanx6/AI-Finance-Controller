# Arbiter

Arbiter is a frontend for an evidence-first bank reconciliation system. A deterministic fast path resolves obvious matches; ambiguous records move through a Proposer/Verifier loop, where the Verifier challenges unsupported guesses and either requests another proposal or records an exception.

## Implemented

- Editorial landing page at `/` with hero investigation, pipeline narrative, proof metrics, adversarial dataset, and CTA.
- Run control room at `/runs` and URL-persistent run detail at `/runs/[runId]`.
- Real same-origin API boundary through `app/api/[...path]/route.ts`.
- Typed contract/view-model helpers in `lib/api-types.ts` and `lib/api.ts`.
- TanStack Query status polling and immutable result, exception, and trace queries.
- Defensive completed-results, highlighted exception, and reasoning trace presentation.
- Accessible animated Trace Drawer with desktop right-sheet behavior, mobile CSS recomposition, Escape, focus trapping/restoration, deep-link query state, hover/focus prefetch, and retry.
- Selective LiquidGlass focal surfaces and Motion choreography using `motion/react` with reduced-motion handling.
- Redis cache-aside behavior using the connected Upstash/KV environment variables.

## Routes and API architecture

Browser components call only same-origin routes:

- `POST /api/run` → FastAPI `POST /api/run`
- `GET /api/status/{run_id}` → FastAPI status
- `GET /api/results/latest` → FastAPI latest results
- `GET /api/results/{run_id}` → FastAPI immutable results
- `GET /api/exceptions/{run_id}` → FastAPI immutable exceptions
- `GET /api/reasoning-trace/{record_id}` → FastAPI immutable trace
- `GET /api/health` and `/api/openapi.json` → FastAPI infrastructure endpoints

The server proxy uses `NEXT_PUBLIC_API_BASE_URL`, defaulting to `http://localhost:8000`, forwards request bodies and `Idempotency-Key`, disables upstream fetch caching, and returns an explicit 502 when the developer backend is unreachable. It does not invent response fields or provide production fixtures.

## Backend contract and generation

The authoritative contract is the developer's FastAPI `/openapi.json`. The repository includes contract interfaces matching the supplied schema in `lib/api-types.ts`; these are deliberately defensive for nested fields whose exact schemas are supplied by the running backend. When FastAPI is reachable, generate the machine output with:

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm generate:api
```

This writes `lib/generated-api-types.ts` and `lib/api-types.ts` imports its generated schema type. The generator is cross-platform and fetches the configured local FastAPI endpoint.

## Redis policy

Redis is a cache/availability layer only; FastAPI remains the source of truth. Keys and TTLs:

- `reconcile:results:latest` — 15 seconds
- `reconcile:results:{run_id}` — 86400 seconds
- `reconcile:exceptions:{run_id}` — 86400 seconds
- `reconcile:trace:{record_id}` — 86400 seconds
- status is never Redis-cached

Redis failures bypass cleanly to FastAPI. Error responses are never cached. Connected variables are `KV_REST_API_URL`, `KV_REST_API_TOKEN` (or the equivalent `UPSTASH_REDIS_REST_URL` and token names).

## Run persistence and loading UX

`run_id` lives in the URL, not browser storage. Mounting `/runs/[runId]` starts both `['status', runId]` and `['results', runId]` at the page level. Pending/running status polls every 2000ms; completed/failed/404 stop polling. A transient status 404 then checks the durable `/results/{run_id}` record: a 200 renders Results, while a second 404 renders Not Found. This makes completed runs viewable after a FastAPI restart without starting a new run.

Completed views query immutable results and exceptions separately. They show a Bklit gauge, outcome donut, baseline/full comparison, pattern analysis, latency, and provider distribution from the actual result payload. Highlighted cases appear before aggregate data. Trace IDs are backend-provided; the drawer recursively renders nested proposer, verifier, decision, identity, and timing evidence without a raw JSON viewer.

## Visual system

The landing uses the supplied near-black, Fora-inspired editorial composition: asymmetric grids, deliberate whitespace, restrained glass hierarchy, immersive investigation visual, and a mint evidence accent. LiquidGlass is used selectively for focal surfaces rather than every card. Motion uses `motion/react`; `framer-motion` is not used. Reduced motion disables movement-heavy transitions while preserving content and state changes.

The chart layer uses the locally installed Bklit source components under `components/charts/`: `Gauge`, `PieChart`, `PieSlice`, `BarChart`, `Bar`, `Grid`, `BarXAxis`, and `ChartTooltip`. It does not import a nonexistent `@bklitui/ui` package.

## Accessibility and responsive behavior

The run status has live announcements, visible status text plus icon, labeled progress, keyboard-accessible trace controls, dialog semantics, Escape handling, focus containment/restoration, retry feedback, and defensive empty/error states. The same composition recomposes at 1440px, 1024px, 768px, and 375px: desktop keeps asymmetric columns and a right sheet; tablet collapses columns; mobile stacks evidence and uses the drawer as a bottom sheet.

## Local setup

```bash
pnpm install
pnpm dev
```

Set `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` in `.env.local` (the code already defaults to this value). Start the developer's local FastAPI service, then verify `http://localhost:8000/health`, `/openapi.json`, `/api/results/latest`, and the corresponding Next.js `/api/*` routes. Live FastAPI verification requires the developer's local backend runtime; this environment cannot claim those responses when that machine is not reachable.

## Final verification boundary

Validated with the local FastAPI service: OpenAPI generation, a durable run where `GET /status/{run_id}` returned 404 and `GET /results/{run_id}` returned 200, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, and the rendered persisted-results browser route. A full newly-started agent run and Redis hit/miss behavior depend on the configured provider and Redis credentials.
