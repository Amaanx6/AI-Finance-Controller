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
    """Scans the results/ directory for the newest eval_run_*.json rather
    than requiring a run_id, per spec. Falls back to the newest completed
    in-memory run's payload if the file listing is empty."""
    results_dir = BASE_DIR / "results"
    files = sorted(results_dir.glob("eval_run_*.json"))
    if files:
        newest = files[-1]
        with open(newest, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return RunResultsResponse(**payload)

    state = await run_store.find_latest_completed()
    if state is None or state.results is None:
        raise HTTPException(status_code=404, detail="No completed runs found yet.")
    return RunResultsResponse(**state.results)


@router.get("/results/{run_id}", response_model=RunResultsResponse)
async def get_results(run_id: str) -> RunResultsResponse:
    state = await run_store.get_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"No run found with id '{run_id}'.")

    if state.status == RunStatus.FAILED:
        raise HTTPException(status_code=400, detail=f"Run '{run_id}' failed: {state.error}")
    if state.status != RunStatus.COMPLETED or state.results is None:
        raise HTTPException(
            status_code=425,
            detail=f"Run '{run_id}' is still {state.status.value}; results are not ready yet.",
        )

    return RunResultsResponse(**state.results)


@router.get("/reasoning-trace/{record_id}", response_model=ReasoningTraceResponse)
async def get_reasoning_trace(record_id: str) -> ReasoningTraceResponse:
    filepath = TRACE_DIR / f"{record_id}.json"
    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No reasoning trace found for record '{record_id}'.",
        )
    with open(filepath, "r", encoding="utf-8") as f:
        trace = json.load(f)
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
