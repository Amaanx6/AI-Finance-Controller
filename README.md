# AI Finance Controller

An agentic financial reconciliation system that reconciles messy **bank statements, internal ledgers, and payment-gateway exports**.

The core idea is simple:

> **Use deterministic logic for straightforward matches. Use AI only for genuine ambiguity, then independently verify the AI's decision.**

This avoids wasting LLM calls on easy records while reducing the risk of confidently accepting a wrong match.

---

## What It Solves

Financial reconciliation becomes difficult when the same transaction appears differently across systems.

Examples include:

- One bank settlement representing multiple ledger entries after a gateway fee.
- The same transaction having completely different descriptions across systems.
- Multiple candidates having similar amounts and dates, including deliberately planted decoys.
- Genuine anomalies that should remain unresolved instead of being guessed.

The system is designed to handle all four cases and produce an **auditable result with an honest exception list**.

---

## Architecture

<img width="1536" height="1024" alt="ChatGPT Image Sep 4, 2026, 09_56_24 PM (1)" src="https://github.com/user-attachments/assets/de7ead96-95df-4d8c-8d04-3d2499f90cb4" />

## Deterministic Fast Path

The fast path handles the majority of records without an LLM.

### 1. Reference match

Records are matched using reference numbers when available.

A reference match is also checked for **amount consistency**. A suspicious amount mismatch is flagged rather than blindly confirmed.

### 2. Tolerance match

For records without a reliable reference match, the system considers approximately:

- **Amount:** ±1%
- **Date:** ±3 days

Exactly one candidate can be confirmed.

Multiple candidates are treated as ambiguous.

No candidates are treated as unresolved.

Ambiguous and unresolved cases move to the agent layer instead of being guessed.

---

## Proposer–Verifier Design

<img width="1536" height="1024" alt="ChatGPT Image Sep 4, 2026, 09_56_24 PM (2)" src="https://github.com/user-attachments/assets/b4dac62e-7f59-4984-be92-17534aad04c7" />


The agent path uses two separate roles.

### Proposer

The Proposer investigates one unresolved or ambiguous record and suggests a match using the available evidence and tools.

### Verifier

The Verifier independently challenges that proposal.

It is specifically instructed to look for:

- incorrect candidates
- decoy records
- weak evidence
- amount/date inconsistencies
- incorrect settlement combinations

The Verifier is not there to rubber-stamp the Proposer.

```text
Proposer
   ↓
Verifier
   ├── Agree → Confirmed
   └── Disagree → Retry with objection
                      ↓
                 Still unclear
                      ↓
                  Exception
```

This makes the AI component about **judgment under ambiguity**, not simply adding an LLM to a matching script.

---

## Agent Tools

### `sum_check`

Used for many-to-one settlements.

It searches candidate combinations and checks whether their total explains the target amount within the configured fee tolerance.

A real bug was found here: a wrong cross-batch combination could be numerically closer than the correct settlement.

The tool was changed to rank candidates using **settlement consistency first, numerical closeness second**.

### `description_similarity`

Used as supporting semantic evidence when descriptions differ across systems.

Low similarity is not treated as an automatic rejection because legitimate transactions can have very different descriptions.

---

## Synthetic Data

The dataset is deliberately messy and contains several transaction patterns:

| Pattern | Approx. Share | Purpose |
|---|---:|---|
| Clean 1:1 | ~40% | Straightforward deterministic matches |
| Many-to-one | ~20% | Settlement batching and fee differences |
| Description mismatch + decoys | ~15% | Tests semantic reasoning and false positives |
| Near-miss noise | ~15% | Small amount/date deviations |
| Genuine anomalies | ~10% | Cases that should remain unresolved |

The ground-truth mapping is isolated from the matching pipeline and is used only for evaluation.

---

## Proof Cases

### `BANK_0042`

A description-mismatch case with an unlabeled decoy.

The correct ledger record matches the amount exactly even though description similarity is very low.

The final logic correctly avoids treating low text similarity as an automatic veto.

### `BANK_0028`

A many-to-one settlement where four ledger records form the correct settlement.

A deliberately wrong cross-batch combination is numerically closer, which exposed the need for consistency-aware subset ranking.

---

## Multiple LLM Providers

The agent layer supports:

```text
local
Groq
gemini
auto
```

Local inference uses **Ollama + qwen2.5:7b**.

Cloud providers use OpenAI-compatible tool-calling interfaces so the same agent logic can work across providers.

The `auto` mode allows provider-aware routing and helps distribute work when cloud rate limits become the bottleneck.

---

## Async Processing

Agent resolution is significantly slower than deterministic matching, so the backend runs reconciliation as an asynchronous job instead of blocking one HTTP request until the entire batch finishes.

The frontend polls backend status and displays live progress using real runtime state.

This also allows multiple agent records to be processed concurrently with bounded concurrency.

---

## Backend and Frontend

### Backend

- Python
- FastAPI
- Async job execution
- CSV input data
- JSON result persistence
- Provider-aware LLM routing

### Frontend

- Next.js
- React
- TypeScript
- TanStack Query
- Tailwind CSS

The frontend is driven by backend API data rather than browser-side fake progress or hardcoded results.

---

## API

```text
POST /api/run
GET  /api/status/{run_id}
GET  /api/results/{run_id}
GET  /api/results/latest
GET  /api/reasoning-trace/{record_id}
GET  /api/exceptions/{run_id}
```

`/api/results/latest` is used by the Runs control room to load the newest completed persisted result.

Completed results are stored under `results/` as JSON files.

---

## Quick Start

### Backend

```bash
cd backend
python -m venv venv
```

Windows:

```powershell
venv\Scripts\activate
```

Linux/macOS:

```bash
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your environment file:

```text
backend/.env
```

using:

```text
backend/.env.example
```

Then run:

```bash
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
pnpm install
pnpm run dev
```

Frontend:

```text
http://localhost:3000
```

Backend:

```text
http://localhost:8000
```

### Run Both From the Root

The repository also includes a root development command:

```bash
pnpm run dev
```

This starts the Next.js frontend and FastAPI backend together.

---

## Testing

Backend tests:

```bash
cd backend
python -m pytest tests/ -v
```

The test suite covers the matching and agent-tool foundations, including reference matching, tolerance matching, amount sanity checks, subset matching, and description similarity.

---

## Evaluation

The evaluation layer compares reconciliation output against the held-out ground truth and measures both **coverage** and **correctness**.

Important metrics include:

- overall match rate
- precision
- recall
- fast-path vs agent resolution
- accuracy by transaction pattern
- single-agent vs proposer-verifier performance
- latency and throughput

An earlier full benchmark reached approximately **95.5% precision** with approximately **3.85× sequential speedup** under the tested configuration.

A later completed run also verified the durable-results path with a 64-record batch and a **71.88% overall match rate**. These values come from different runs and should not be treated as the same benchmark.

---

## Real Engineering Problems We Found

This project was built by testing failure cases, not just the happy path.

### Decoy leakage

Early decoys had explicit labels that revealed the answer.

**Fix:** regenerated realistic, unlabeled decoys.

### Cross-batch subset bug

A wrong settlement combination could beat the correct one on numerical closeness.

**Fix:** consistency-aware ranking.

### Empty agent decisions

An empty model response could previously be treated as a valid decision.

**Fix:** explicit retry/failure handling.

### LLM API/tool-call failures

Provider-specific response behavior caused tool-call and structured-output failures.

**Fix:** explicit terminal decision tools, bounded retries, and provider-specific recovery.

### Token and rate-limit pressure

Large candidate payloads and growing multi-turn history caused unnecessary token usage.

**Fix:** smaller payloads, tighter turn limits, throttling, and concurrent provider-aware execution.

### Live progress blocking

Synchronous matching work could block the FastAPI event loop.

**Fix:** move blocking work off the event loop so status polling continues during a run.

### Stale latest result

The latest-results endpoint could return an older persisted file because result files were traversed in the wrong order.

**Fix:** newest-first result selection.

---

## Why the Architecture Matters

The project is not trying to maximize the number of records that receive an answer.

It is trying to maximize the number of records that receive a **defensible answer**.

That leads to three deliberate behaviors:

```text
Easy case
   → deterministic confirmation

Ambiguous case
   → AI investigation + independent verification

Unsafe case
   → explicit exception
```

That is the core design principle of the system.

---

## Project Structure

```text
RazorPay/
├── backend/
│   ├── app/
│   │   ├── data_generation/
│   │   ├── matcher/
│   │   ├── agent/
│   │   ├── api/
│   │   └── eval/
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── lib/
│   ├── scripts/
│   └── package.json
│
├── docs/
│   ├── architecture.png
│   └── proposer-verifier.png
│
├── results/
├── logs/
├── run_state/
├── uploads/
├── package.json
└── README.md
```

---

## Core Principle

> **Deterministic when possible. Agentic when necessary. Verified before trusted. Honest when uncertain.**

