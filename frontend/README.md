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

This writes `lib/generated-api-types.ts`. If the backend is running on another machine, do not expose it publicly; place a local copy of the authoritative `openapi.json` in the project only if you want offline generation, then run `openapi-typescript ./openapi.json -o lib/generated-api-types.ts`.

## Redis policy

Redis is a cache/availability layer only; FastAPI remains the source of truth. Keys and TTLs:

- `arbiter:results:latest` — 15 seconds
- `arbiter:results:{run_id}` — 86400 seconds
- `arbiter:exceptions:{run_id}` — 86400 seconds
- `arbiter:trace:{record_id}` — 86400 seconds
- status is never Redis-cached

Redis failures bypass cleanly to FastAPI. Error responses are never cached. Connected variables are `KV_REST_API_URL`, `KV_REST_API_TOKEN` (or the equivalent `UPSTASH_REDIS_REST_URL` and token names).

## Run persistence and loading UX

`run_id` lives in the URL, not browser storage. Mounting `/runs/[runId]` reads that ID, queries `['status', runId]`, and polls every 2000ms until normalized status is exactly `completed` or `failed`; unknown states remain visible and continue polling. Progress, processed counts, fast-path counts, agent counts, and activity text are shown only when returned by FastAPI.

Completed views query results and exceptions separately. Highlighted cases appear before aggregate data so the judge sees where verification changed confidence first. Trace IDs are backend-provided; the drawer renders history, final decision, status, provider, and timing defensively.

## Visual system

The landing uses the supplied near-black, Fora-inspired editorial composition: asymmetric grids, deliberate whitespace, restrained glass hierarchy, immersive investigation visual, and a mint evidence accent. LiquidGlass is used selectively for focal surfaces rather than every card. Motion uses `motion/react`; `framer-motion` is not used. Reduced motion disables movement-heavy transitions while preserving content and state changes.

No Bklit package is present in the supplied project dependencies or contract. Charts are therefore represented with accessible semantic data sections rather than falsely describing custom markup as Bklit. If a project-specific Bklit package is provided, it can replace those presentation primitives without changing the API view models.

## Accessibility and responsive behavior

The run status has live announcements, visible status text plus icon, labeled progress, keyboard-accessible trace controls, dialog semantics, Escape handling, focus containment/restoration, retry feedback, and defensive empty/error states. The same composition recomposes at 1440px, 1024px, 768px, and 375px: desktop keeps asymmetric columns and a right sheet; tablet collapses columns; mobile stacks evidence and uses the drawer as a bottom sheet.

## Local setup

```bash
pnpm install
pnpm dev
```

Set `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` in `.env.local` (the code already defaults to this value). Start the developer's local FastAPI service, then verify `http://localhost:8000/health`, `/openapi.json`, `/api/results/latest`, and the corresponding Next.js `/api/*` routes. Live FastAPI verification requires the developer's local backend runtime; this environment cannot claim those responses when that machine is not reachable.

## Final verification boundary

Verified from frontend code: route mapping, same-origin requests, idempotency forwarding, typed contract architecture, TanStack Query keys/polling, Redis key policy, URL run persistence, defensive rendering, Motion/LiquidGlass usage, and accessible drawer behavior. Requires the developer backend runtime: OpenAPI generation output, real run transitions, real results/exceptions/traces, Redis hit/miss behavior, and browser verification with actual backend payloads.
