"""Async job orchestration for POST /api/run.

Owns:
  - idempotency: hashing the input CSVs (or honoring a client-supplied
    Idempotency-Key header) so a duplicate request within the time window
    returns the existing run instead of re-processing the batch.
  - kicking off the pipeline as a background asyncio task so the HTTP
    request returns immediately.
  - wiring evaluate.run_evaluation()'s progress/exception/DLQ callbacks
    into the RunStore, which is what makes GET /api/status/{run_id} show
    live, mid-run progress.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Optional

from backend.app.api.circuit_breaker import CircuitBreaker
from backend.app.api.logging_utils import log_event
from backend.app.api.store import RunState, RunStatus, run_store
from backend.app.eval import evaluate

IDEMPOTENCY_WINDOW_SECONDS = float(os.environ.get("IDEMPOTENCY_WINDOW_SECONDS", 300))


def _hash_input_files() -> str:
    """Cryptographic hash of the pipeline's input CSVs.

    Used as the idempotency key when the client doesn't supply an
    Idempotency-Key header, so two requests for the same input data within
    the time window are recognized as the same logical run.
    """
    hasher = hashlib.sha256()
    for path in (evaluate.BANK_CSV, evaluate.LEDGER_CSV, evaluate.GATEWAY_CSV):
        try:
            with open(path, "rb") as f:
                hasher.update(f.read())
        except FileNotFoundError:
            # Let the missing-file error surface naturally once the run
            # actually starts (run_evaluation raises a clear message).
            hasher.update(str(path).encode())
    return hasher.hexdigest()


async def get_or_create_run(idempotency_key_header: Optional[str]) -> tuple[RunState, bool]:
    """Returns (run_state, created). created=False means an existing,
    in-window run was returned instead of starting a new one."""
    idempotency_key = idempotency_key_header or _hash_input_files()

    existing = await run_store.find_by_idempotency_key(idempotency_key, IDEMPOTENCY_WINDOW_SECONDS)
    if existing is not None:
        log_event("idempotent_hit", existing.run_id, idempotency_key=idempotency_key[:12])
        return existing, False

    configured_provider = (os.environ.get("PROVIDER") or "auto").lower()
    state = await run_store.create_run(provider_mode=configured_provider, idempotency_key=idempotency_key)
    log_event("run_created", state.run_id, idempotency_key=idempotency_key[:12])
    return state, True


async def execute_run(run_id: str) -> None:
    """The background task body. Never raises — failures are captured onto
    the run's state so GET /api/status/{run_id} reports status='failed'
    with an error message instead of leaving the run stuck."""
    await run_store.update_run(run_id, status=RunStatus.RUNNING)
    circuit_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)

    def on_fast_progress(completed: int, total: int) -> None:
        asyncio.create_task(run_store.bump_fast_progress(run_id, total))

    def on_agent_progress(_result: dict) -> None:
        asyncio.create_task(run_store.bump_agent_progress(run_id))

    def on_exception(exc_record: dict) -> None:
        asyncio.create_task(run_store.append_exception(run_id, exc_record))

    def on_dlq(dlq_record: dict) -> None:
        asyncio.create_task(run_store.append_dlq(run_id, dlq_record))

    try:
        results_payload = await evaluate.run_evaluation(
            run_id=run_id,
            fast_progress_cb=on_fast_progress,
            agent_progress_cb=on_agent_progress,
            exception_cb=on_exception,
            dlq_cb=on_dlq,
            circuit_breaker=circuit_breaker,
        )
        await run_store.update_run(
            run_id,
            status=RunStatus.COMPLETED,
            results=results_payload,
            provider_mode=results_payload.get("provider_mode", "auto"),
        )
        log_event("run_finished", run_id, status="completed")
    except Exception as exc:  # noqa: BLE001 - top-level job guard
        log_event("run_finished", run_id, status="failed", level="error", error=str(exc))
        await run_store.update_run(run_id, status=RunStatus.FAILED, error=str(exc))
