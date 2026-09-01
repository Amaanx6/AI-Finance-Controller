from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request

from backend.app.api import job
from backend.app.api.schemas import (
    ExceptionRecord,
    ReasoningTraceResponse,
    RunExceptionsResponse,
    RunResultsResponse,
    RunStartResponse,
    RunStatusResponse,
)
from backend.app.api.store import RunStatus, run_store
from backend.app.eval.evaluate import BASE_DIR

router = APIRouter(prefix="/api")

TRACE_DIR = BASE_DIR / "logs" / "reasoning_trace"


def _load_persisted_result(filepath: Path) -> dict:
    with filepath.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        raise ValueError(f"Persisted result is not a JSON object: {filepath.name}")

    return payload


async def _attach_runtime_run_id(payload: dict) -> dict:
    """Compatibility for older result files without run_id.

    If the persisted result belongs to the latest completed in-memory run,
    use the authoritative run_id from RunStore. No ID is inferred.
    """
    if payload.get("run_id"):
        return payload

    state = await run_store.find_latest_completed()
    if state is None or state.results is None:
        return payload

    state_timestamp = state.results.get("timestamp")
    if state_timestamp and state_timestamp == payload.get("timestamp"):
        enriched = dict(payload)
        enriched["run_id"] = state.run_id
        return enriched

    return payload


@router.post("/run", response_model=RunStartResponse, status_code=202)
async def start_run(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> RunStartResponse:
    """Kick off the reconciliation pipeline as a background task.

    Idempotent: a request with the same Idempotency-Key header (or, if none
    is supplied, the same input-file hash) within the idempotency window
    returns the existing run's id/status instead of starting a new batch.
    """
    state, created = await job.get_or_create_run(idempotency_key)

    if created:
        asyncio.create_task(job.execute_run(state.run_id))

    return RunStartResponse(
        run_id=state.run_id,
        status=state.status.value,
        provider_mode=state.provider_mode,
    )


@router.get("/status/{run_id}", response_model=RunStatusResponse)
async def get_status(run_id: str) -> RunStatusResponse:
    state = await run_store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"No run found with id '{run_id}'.")

    payload = state.to_status_payload()
    payload["error"] = state.error
    return RunStatusResponse(**payload)


@router.get("/results/latest", response_model=RunResultsResponse)
async def get_latest_results() -> RunResultsResponse:
    """Return the newest persisted result.

    The results directory is authoritative for persisted completed runs.
    Older result files may lack run_id; when possible, the matching latest
    in-memory run supplies its real run_id without inventing one.
    """
    results_dir = BASE_DIR / "results"
    files = sorted(results_dir.glob("eval_run_*.json"))

    if files:
        newest = files[-1]

        try:
            payload = _load_persisted_result(newest)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Unable to read persisted result '{newest.name}': {exc}",
            ) from exc

        payload = await _attach_runtime_run_id(payload)
        return RunResultsResponse(**payload)

    state = await run_store.find_latest_completed()
    if state is None or state.results is None:
        raise HTTPException(status_code=404, detail="No completed runs found yet.")

    payload = dict(state.results)
    payload["run_id"] = state.run_id
    return RunResultsResponse(**payload)


@router.get("/results/{run_id}", response_model=RunResultsResponse)
async def get_results(run_id: str) -> RunResultsResponse:
    """Return completed results from memory or persisted files.

    Persisted fallback makes completed results readable after a backend
    restart, provided the result file contains its authoritative run_id.
    """
    state = await run_store.get_run(run_id)

    if state is not None:
        if state.status == RunStatus.FAILED:
            raise HTTPException(
                status_code=400,
                detail=f"Run '{run_id}' failed: {state.error}",
            )

        if state.status != RunStatus.COMPLETED or state.results is None:
            raise HTTPException(
                status_code=425,
                detail=(
                    f"Run '{run_id}' is still {state.status.value}; "
                    "results are not ready yet."
                ),
            )

        payload = dict(state.results)
        payload["run_id"] = run_id
        return RunResultsResponse(**payload)

    results_dir = BASE_DIR / "results"

    # Never derive run_id from the filename.
    for filepath in sorted(results_dir.glob("eval_run_*.json"), reverse=True):
        try:
            payload = _load_persisted_result(filepath)
        except (OSError, json.JSONDecodeError, ValueError):
            continue

        if payload.get("run_id") == run_id:
            return RunResultsResponse(**payload)

    raise HTTPException(
        status_code=404,
        detail=f"No persisted results found for run '{run_id}'.",
    )


@router.get("/reasoning-trace/{record_id}", response_model=ReasoningTraceResponse)
async def get_reasoning_trace(record_id: str) -> ReasoningTraceResponse:
    filepath = TRACE_DIR / f"{record_id}.json"

    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No reasoning trace found for record '{record_id}'.",
        )

    try:
        with filepath.open("r", encoding="utf-8") as f:
            trace = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to read reasoning trace for record '{record_id}': {exc}",
        ) from exc

    return ReasoningTraceResponse(**trace)


@router.get("/exceptions/{run_id}", response_model=RunExceptionsResponse)
async def get_exceptions(run_id: str) -> RunExceptionsResponse:
    state = await run_store.get_run(run_id)

    if state is None:
        raise HTTPException(status_code=404, detail=f"No run found with id '{run_id}'.")

    return RunExceptionsResponse(
        run_id=run_id,
        exceptions=[ExceptionRecord(**e) for e in state.exceptions],
        dead_letter_queue=[ExceptionRecord(**d) for d in state.dead_letter_queue],
    )
