"""Async job orchestration for POST /api/run."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from backend.app.api.circuit_breaker import CircuitBreaker
from backend.app.api.logging_utils import log_event
from backend.app.api.store import RunState, RunStatus, run_store
from backend.app.eval import evaluate

IDEMPOTENCY_WINDOW_SECONDS = float(
    os.environ.get("IDEMPOTENCY_WINDOW_SECONDS", 300)
)


def _hash_input_files() -> str:
    hasher = hashlib.sha256()

    for path in (evaluate.BANK_CSV, evaluate.LEDGER_CSV, evaluate.GATEWAY_CSV):
        try:
            with open(path, "rb") as file:
                hasher.update(file.read())
        except FileNotFoundError:
            hasher.update(str(path).encode())

    return hasher.hexdigest()


async def get_or_create_run(
    idempotency_key_header: Optional[str],
) -> tuple[RunState, bool]:
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
    """Add the authoritative run_id to the newly written result file."""
    results_dir = Path(evaluate.RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(results_dir.glob("eval_run_*.json"))
    if not files:
        log_event(
            "result_metadata_missing",
            run_id,
            level="warning",
            message="No eval_run_*.json found after evaluation.",
        )
        return

    newest = files[-1]

    try:
        with newest.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        if not isinstance(payload, dict):
            raise ValueError("Persisted result is not a JSON object.")

        existing = payload.get("run_id")
        if existing and existing != run_id:
            raise ValueError(
                f"Newest result already belongs to another run: {existing}"
            )

        payload["run_id"] = run_id

        temporary = newest.with_suffix(newest.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)

        temporary.replace(newest)

        log_event(
            "result_run_id_persisted",
            run_id,
            file=newest.name,
        )

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log_event(
            "result_run_id_persist_failed",
            run_id,
            level="warning",
            error=str(exc),
            file=newest.name,
        )


async def execute_run(run_id: str) -> None:
    state = await run_store.get_run(run_id)
    if state is None or state.status == RunStatus.CANCELLED:
        return

    await run_store.update_run(run_id, status=RunStatus.RUNNING)

    async def is_cancelled() -> bool:
        current = await run_store.get_run(run_id)
        return current is None or current.status == RunStatus.CANCELLED

    def discard_cancelled_artifacts() -> None:
        for path in (
            Path(evaluate.RESULTS_DIR) / run_id,
            Path(evaluate.BASE_DIR) / "logs" / "reasoning_trace" / run_id,
        ):
            try:
                if path.exists():
                    shutil.rmtree(path)
            except OSError as exc:
                log_event(
                    "cancel_cleanup_failed",
                    run_id,
                    level="warning",
                    path=str(path),
                    error=str(exc),
                )

    input_dir = Path(state.input_dir) if state and state.input_dir else None
    bank_path = input_dir / "bank.csv" if input_dir else evaluate.BANK_CSV
    ledger_path = input_dir / "ledger.csv" if input_dir else evaluate.LEDGER_CSV
    gateway_path = input_dir / "gateway.csv" if input_dir else evaluate.GATEWAY_CSV
    ground_truth_path = input_dir / "ground_truth.csv" if input_dir and (input_dir / "ground_truth.csv").exists() else evaluate.GROUND_TRUTH_CSV
    results_dir = Path(evaluate.RESULTS_DIR) / run_id
    trace_dir = Path(evaluate.BASE_DIR) / "logs" / "reasoning_trace" / run_id

    # Set the denominator before the first poll.
    try:
        total_records = len(evaluate.load_csv(bank_path))
    except Exception as exc:
        await run_store.update_run(
            run_id,
            status=RunStatus.FAILED,
            error=f"Unable to load reconciliation input: {exc}",
        )
        log_event(
            "run_finished",
            run_id,
            status="failed",
            level="error",
            error=str(exc),
        )
        return

    await run_store.update_run(
        run_id,
        total_records=total_records,
        records_processed=0,
        fast_path_resolved_so_far=0,
        agent_resolved_so_far=0,
    )

    circuit_breaker = CircuitBreaker(
        failure_threshold=3,
        cooldown_seconds=30.0,
    )

    def on_fast_progress(
        _completed: int,
        total: int,
        result: Optional[Any] = None,
    ) -> None:
        asyncio.create_task(
            run_store.record_fast_path_progress(
                run_id,
                total,
                result,
            )
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
            bank_path=bank_path,
            ledger_path=ledger_path,
            gateway_path=gateway_path,
            ground_truth_path=ground_truth_path,
            results_dir=results_dir,
            trace_dir=trace_dir,
            fast_progress_cb=on_fast_progress,
            agent_progress_cb=on_agent_progress,
            exception_cb=on_exception,
            dlq_cb=on_dlq,
            circuit_breaker=circuit_breaker,
            cancel_check=is_cancelled,
        )

        current_state = await run_store.get_run(run_id)
        if current_state is None or current_state.status == RunStatus.CANCELLED:
            discard_cancelled_artifacts()
            return

        results_payload = dict(results_payload)
        results_payload["run_id"] = run_id
        results_payload["dataset_manifest"] = state.dataset_manifest if state else {}

        # run_evaluation writes the authoritative run id into its original
        # durable file. Keep the defensive helper only for compatibility with
        # an older evaluator implementation that omitted it.
        if results_payload.get("run_id") != run_id:
            _persist_run_id(run_id)

        breakdown = results_payload.get("breakdown") or {}

        final_fast = int(breakdown.get("fast_path_confirmed", 0) or 0)
        final_flagged = int(breakdown.get("fast_path_flagged", 0) or 0)

        # The agent progress counter means "records handled by the agent",
        # regardless of whether the final decision was confirmed or exceptional.
        full_scores = results_payload.get("full_pipeline_scores") or {}
        detail_rows = full_scores.get("detail_rows") or []

        agent_record_count = max(
            0,
            int(results_payload.get("total_records", total_records) or total_records)
            - final_fast
            - final_flagged,
        )

        # detail_rows describes the scored bank population and therefore is not
        # a reliable substitute for agent count when DLQ rows are excluded.
        # Prefer the actual persisted breakdown when present; otherwise the
        # escalated population is total - fast terminal records.
        final_agent = agent_record_count

        final_processed = min(
            total_records,
            final_fast + final_flagged + final_agent,
        )

        await run_store.update_run(
            run_id,
            status=RunStatus.COMPLETED,
            results=results_payload,
            provider_mode=results_payload.get("provider_mode", "auto"),
            total_records=int(
                results_payload.get("total_records", total_records) or total_records
            ),
            records_processed=final_processed,
            fast_path_resolved_so_far=final_fast,
            agent_resolved_so_far=final_agent,
            error=None,
        )

        log_event("run_finished", run_id, status="completed")

    except asyncio.CancelledError:
        discard_cancelled_artifacts()
        await run_store.update_run(
            run_id,
            status=RunStatus.CANCELLED,
            error="Run cancelled by the user.",
        )
        log_event(
            "run_finished",
            run_id,
            status="cancelled",
        )
    except Exception as exc:  # noqa: BLE001
        current_state = await run_store.get_run(run_id)
        if current_state is not None and current_state.status == RunStatus.CANCELLED:
            discard_cancelled_artifacts()
            return

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
    finally:
        await run_store.remove_task(run_id)
