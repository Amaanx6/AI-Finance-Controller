"""Async job orchestration for POST /api/run.

Owns:
  - idempotency: hashing input CSVs or honoring Idempotency-Key.
  - starting the pipeline as a background task.
  - wiring evaluation progress/exception/DLQ callbacks into RunStore.
  - persisting the authoritative run_id alongside the completed result file.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from backend.app.api.circuit_breaker import CircuitBreaker
from backend.app.api.logging_utils import log_event
from backend.app.api.store import RunState, RunStatus, run_store
from backend.app.eval import evaluate

IDEMPOTENCY_WINDOW_SECONDS = float(os.environ.get("IDEMPOTENCY_WINDOW_SECONDS", 300))


def _hash_input_files() -> str:
    """Cryptographic hash of the pipeline's input CSVs."""
    hasher = hashlib.sha256()

    for path in (evaluate.BANK_CSV, evaluate.LEDGER_CSV, evaluate.GATEWAY_CSV):
        try:
            with open(path, "rb") as f:
                hasher.update(f.read())
        except FileNotFoundError:
            hasher.update(str(path).encode())

    return hasher.hexdigest()


async def get_or_create_run(
    idempotency_key_header: Optional[str],
) -> tuple[RunState, bool]:
    """Return (run_state, created)."""
    idempotency_key = idempotency_key_header or _hash_input_files()

    existing = await run_store.find_by_idempotency_key(
        idempotency_key,
        IDEMPOTENCY_WINDOW_SECONDS,
    )

    if existing is not None:
        log_event(
            "idempotent_hit",
            existing.run_id,
            idempotency_key=idempotency_key[:12],
        )
        return existing, False

    configured_provider = (os.environ.get("PROVIDER") or "auto").lower()

    state = await run_store.create_run(
        provider_mode=configured_provider,
        idempotency_key=idempotency_key,
    )

    log_event(
        "run_created",
        state.run_id,
        idempotency_key=idempotency_key[:12],
    )
    return state, True


def _persist_run_id(run_id: str) -> None:
    """Add the authoritative run_id to the newest persisted result.

    The evaluator remains responsible for creating eval_run_*.json.
    This function only adds the run identity after evaluation succeeds.
    """
    results_dir = Path(evaluate.BASE_DIR) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(results_dir.glob("eval_run_*.json"))

    if not files:
        log_event(
            "result_metadata_missing",
            run_id,
            level="warning",
            message="Evaluation completed but no eval_run_*.json was found.",
        )
        return

    newest = files[-1]

    try:
        with newest.open("r", encoding="utf-8") as f:
            persisted = json.load(f)

        if not isinstance(persisted, dict):
            raise ValueError("persisted result is not a JSON object")

        # Do not silently overwrite a different existing run_id.
        existing_run_id = persisted.get("run_id")
        if existing_run_id and existing_run_id != run_id:
            raise ValueError(
                f"latest result already belongs to run_id={existing_run_id!r}"
            )

        persisted["run_id"] = run_id

        temp_path = newest.with_suffix(newest.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(persisted, f, indent=2)

        temp_path.replace(newest)

        log_event(
            "result_run_id_persisted",
            run_id,
            file=newest.name,
        )

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        # Metadata failure must not turn an otherwise successful reconciliation
        # into a failed run. RunState still has the authoritative run_id.
        log_event(
            "result_run_id_persist_failed",
            run_id,
            level="warning",
            error=str(exc),
            file=newest.name,
        )


async def execute_run(run_id: str) -> None:
    """Run the reconciliation in the background and capture failures."""
    await run_store.update_run(run_id, status=RunStatus.RUNNING)

    circuit_breaker = CircuitBreaker(
        failure_threshold=3,
        cooldown_seconds=30.0,
    )

    def on_fast_progress(
        completed: int,
        total: int,
        result: Optional[Any] = None,
    ) -> None:
        if result is not None and getattr(result, "status", None) in (
            "confirmed",
            "flagged",
        ):
            asyncio.create_task(
                run_store.bump_fast_progress(run_id, 1)
            )

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

        # Keep run_id authoritative in the in-memory result.
        results_payload = dict(results_payload)
        results_payload["run_id"] = run_id

        # The evaluator created the JSON file. Persist the same authoritative ID.
        _persist_run_id(run_id)

        await run_store.update_run(
            run_id,
            status=RunStatus.COMPLETED,
            results=results_payload,
            provider_mode=results_payload.get("provider_mode", "auto"),
        )

        log_event("run_finished", run_id, status="completed")

    except Exception as exc:  # noqa: BLE001
        log_event(
            "run_finished",
            run_id,
            status="failed",
            level="error",
            error=str(exc),
        )
        await run_store.update_run(
            run_id,
            status=RunStatus.FAILED,
            error=str(exc),
        )
