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
RESULTS_DIR = BASE_DIR / "results"


def _load_result(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(f"Result {path.name} is not a JSON object.")

    return payload


async def _attach_legacy_run_id(payload: dict) -> dict:
    """Compatibility only for an older file missing run_id.

    We attach an ID only when the current in-memory completed run has the
    exact same timestamp. Never infer run_id from a filename.
    """
    if payload.get("run_id"):
        return payload

    state = await run_store.find_latest_completed()
    if state is None or not state.results:
        return payload

    if state.results.get("timestamp") != payload.get("timestamp"):
        return payload

    enriched = dict(payload)
    enriched["run_id"] = state.run_id
    return enriched


@router.post("/run", response_model=RunStartResponse, status_code=202)
async def start_run(
    request: Request,
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
    ),
) -> RunStartResponse:
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
        raise HTTPException(
            status_code=404,
            detail=f"No run found with id '{run_id}'.",
        )

    payload = state.to_status_payload()
    payload["error"] = state.error
    return RunStatusResponse(**payload)


@router.get("/results/latest", response_model=RunResultsResponse)
async def get_latest_results() -> RunResultsResponse:
    """Return the newest persisted reconciliation result."""
    files = sorted(RESULTS_DIR.glob("eval_run_*.json"))

    if files:
        newest = files[-1]

        try:
            payload = _load_result(newest)
            payload = await _attach_legacy_run_id(payload)
            return RunResultsResponse(**payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Unable to read {newest.name}: {exc}",
            ) from exc

    state = await run_store.find_latest_completed()

    if state is None or state.results is None:
        raise HTTPException(
            status_code=404,
            detail="No completed runs found yet.",
        )

    payload = dict(state.results)
    payload["run_id"] = state.run_id
    return RunResultsResponse(**payload)


@router.get("/results/{run_id}", response_model=RunResultsResponse)
async def get_results(run_id: str) -> RunResultsResponse:
    """Return a result from live memory or durable persisted files."""
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

    # This is what makes the View Results link work for a previously
    # persisted run that is no longer present in the in-memory RunStore.
    for filepath in sorted(RESULTS_DIR.glob("eval_run_*.json"), reverse=True):
        try:
            payload = _load_result(filepath)
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
        with filepath.open("r", encoding="utf-8") as file:
            trace = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to read reasoning trace for '{record_id}': {exc}",
        ) from exc

    return ReasoningTraceResponse(**trace)


@router.get("/exceptions/{run_id}", response_model=RunExceptionsResponse)
async def get_exceptions(run_id: str) -> RunExceptionsResponse:
    state = await run_store.get_run(run_id)

    if state is None:
        # Completed runs survive a FastAPI restart in eval_run_*.json. Older
        # result files only carry the DLQ, so expose that durable evidence and
        # do not pretend an in-memory store is the source of truth.
        for filepath in sorted(RESULTS_DIR.glob("eval_run_*.json"), reverse=True):
            try:
                payload = _load_result(filepath)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if payload.get("run_id") == run_id:
                dlq = payload.get("dead_letter_queue") or []
                return RunExceptionsResponse(
                    run_id=run_id,
                    exceptions=[],
                    dead_letter_queue=[ExceptionRecord(**item) for item in dlq],
                )
        raise HTTPException(status_code=404, detail=f"No run found with id '{run_id}'.")

    return RunExceptionsResponse(
        run_id=run_id,
        exceptions=[ExceptionRecord(**item) for item in state.exceptions],
        dead_letter_queue=[
            ExceptionRecord(**item)
            for item in state.dead_letter_queue
        ],
    )
