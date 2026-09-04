# AI Finance Controller - Backend

FastAPI backend for the AI Finance Controller hackathon project.

## Setup

1. Create a Python virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env and add your Groq API key
   ```

4. Run the server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

The API will be available at `http://localhost:8000`

## Production run storage

Uploaded runs are isolated by `run_id`. Source files are stored under
`uploads/`, results under `results/{run_id}/`, and reasoning traces under
`logs/reasoning_trace/{run_id}/`. Set `REDIS_URL` in production to enable
shared run state, idempotency, session recovery, and the durable run queue:

```bash
REDIS_URL=redis://localhost:6379/0
```

Upstash REST is also supported:

```bash
UPSTASH_REDIS_REST_URL=https://your-instance.upstash.io
UPSTASH_REDIS_REST_TOKEN=your-server-only-token
```

Keep the token in the backend deployment environment. Do not expose it through
`NEXT_PUBLIC_*` variables or commit it to the repository.

Run one or more worker-capable API processes with the same Redis instance.
Without Redis, the service keeps local durable snapshots and a development
fallback, but background jobs remain process-local. Configure filesystem
retention or object storage lifecycle rules for `uploads/`, `results/`, and
`logs/reasoning_trace/` before handling sensitive financial data.

The upload endpoint is `POST /api/runs/upload` and expects `bank`, `ledger`,
and `gateway` multipart CSV fields. An optional `ground_truth` field enables
evaluation scoring; reconciliation itself does not require ground truth.

## Project Structure

- `app/data_generation/` - Synthetic data generation for bank statements, ledger, and gateway exports
- `app/matcher/` - Reconciliation matching logic (fast path + LLM escalation)
- `app/agent/` - LLM agent with tool-calling for ambiguous matches
- `app/api/` - REST API endpoints
- `app/eval/` - Evaluation and scoring utilities
