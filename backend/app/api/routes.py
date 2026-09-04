from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Cookie, File, Header, HTTPException, Request, Response, UploadFile

from backend.app.api import job
from backend.app.api.schemas import (
    ExceptionRecord,
    ReasoningTraceResponse,
    RunExceptionsResponse,
    RunResultsResponse,
    RunResultsSummary,
    RunStartResponse,
    RunStatusResponse,
)
from backend.app.api.store import RunStatus, run_store
from backend.app.eval.evaluate import BASE_DIR

router = APIRouter(prefix="/api")

TRACE_DIR = BASE_DIR / "logs" / "reasoning_trace"
RESULTS_DIR = BASE_DIR / "results"
UPLOADS_DIR = BASE_DIR / "uploads"
RUN_STATE_DIR = BASE_DIR / "run_state"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
REQUIRED_COLUMNS = {"record_id", "date", "amount", "description", "reference_number"}


async def _dispatch_run(run_id: str) -> None:
    if os.environ.get("RUN_QUEUE_ONLY", "false").lower() == "true":
        if await run_store.enqueue(run_id):
            return
    task = asyncio.create_task(job.execute_run(run_id))
    await run_store.register_task(run_id, task)


def _result_files() -> List[Path]:
    return sorted(
        (path for path in RESULTS_DIR.rglob("eval_run_*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _load_result(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(f"Result {path.name} is not a JSON object.")

    return payload


async def _assert_session_access(run_id: str, session_id: Optional[str]) -> None:
    if not session_id:
        return
    owned_runs = await run_store.session_run_ids(session_id)
    if owned_runs and run_id not in owned_runs:
        raise HTTPException(status_code=403, detail="This run is not part of the current session.")


async def _save_csv_upload(upload: UploadFile, destination: Path, label: str) -> dict:
    filename = Path(upload.filename or "").name
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail=f"{label} must be a CSV file.")

    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{label} exceeds the 25 MB upload limit.")

    try:
        text = data.decode("utf-8-sig")
        rows = list(csv.DictReader(text.splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(status_code=400, detail=f"{label} is not a readable UTF-8 CSV: {exc}") from exc

    if not rows and not text.strip():
        raise HTTPException(status_code=400, detail=f"{label} is empty.")
    reader = csv.DictReader(text.splitlines())
    headers = set(reader.fieldnames or [])
    missing = sorted(REQUIRED_COLUMNS - headers)
    if missing:
        raise HTTPException(status_code=400, detail=f"{label} is missing columns: {', '.join(missing)}.")

    ids = [str(row.get("record_id") or "").strip() for row in rows]
    if any(not record_id for record_id in ids):
        raise HTTPException(status_code=400, detail=f"{label} contains a blank record_id.")
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail=f"{label} contains duplicate record_id values.")

    destination.write_bytes(data)
    return {
        "filename": filename,
        "stored_as": destination.name,
        "bytes": len(data),
        "rows": len(rows),
        "sha256": hashlib.sha256(data).hexdigest(),
        "columns": sorted(headers),
    }


def _exception_records_from_result(payload: dict) -> List[dict]:
    scores = payload.get("full_pipeline_scores") or {}
    detail_rows = scores.get("detail_rows") or []
    records = []

    for row in detail_rows:
        if not isinstance(row, dict):
            continue

        status = str(row.get("predicted_status") or "").lower()
        record_id = row.get("bank_record_id")
        if status not in {"exception", "dlq"} or not record_id:
            continue

        records.append(
            {
                "record_id": str(record_id),
                "stage": "agent_resolution",
                "reason": "This record was not automatically resolved.",
                "provider": payload.get("provider_mode"),
                "detail": "Persisted evaluation marked this record for review.",
            }
        )

    return records


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
    response: Response,
    session_id: Optional[str] = Cookie(default=None, alias="arbiter_session"),
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
    ),
) -> RunStartResponse:
    state, created = await job.get_or_create_run(idempotency_key)

    if created:
        await _dispatch_run(state.run_id)
    session_id = session_id or uuid.uuid4().hex
    response.set_cookie("arbiter_session", session_id, max_age=2592000, httponly=True, samesite="lax")
    await run_store.attach_session(session_id, state.run_id)

    return RunStartResponse(
        run_id=state.run_id,
        status=state.status.value,
        provider_mode=state.provider_mode,
    )


@router.post("/runs/upload", status_code=202)
async def upload_run(
    response: Response,
    session_id: Optional[str] = Cookie(default=None, alias="arbiter_session"),
    bank: UploadFile = File(...),
    ledger: UploadFile = File(...),
    gateway: UploadFile = File(...),
    ground_truth: Optional[UploadFile] = File(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Validate uploaded source files and start an isolated reconciliation."""
    upload_id = uuid.uuid4().hex
    input_dir = UPLOADS_DIR / upload_id
    input_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest = {
            "bank": await _save_csv_upload(bank, input_dir / "bank.csv", "Bank statement"),
            "ledger": await _save_csv_upload(ledger, input_dir / "ledger.csv", "Internal ledger"),
            "gateway": await _save_csv_upload(gateway, input_dir / "gateway.csv", "Gateway export"),
        }
        if ground_truth is not None and ground_truth.filename:
            manifest["ground_truth"] = await _save_csv_upload(
                ground_truth,
                input_dir / "ground_truth.csv",
                "Ground truth",
            )
    except HTTPException:
        shutil.rmtree(input_dir, ignore_errors=True)
        raise

    key = idempotency_key or hashlib.sha256(
        "|".join(item["sha256"] for item in manifest.values()).encode()
    ).hexdigest()
    existing = await run_store.find_by_idempotency_key(key, job.IDEMPOTENCY_WINDOW_SECONDS)
    if existing is not None:
        shutil.rmtree(input_dir, ignore_errors=True)
        session_id = session_id or uuid.uuid4().hex
        response.set_cookie("arbiter_session", session_id, max_age=2592000, httponly=True, samesite="lax")
        await run_store.attach_session(session_id, existing.run_id)
        return {"run_id": existing.run_id, "status": existing.status.value, "manifest": existing.dataset_manifest}

    provider = (os.environ.get("PROVIDER") or "auto").lower()
    state = await run_store.create_run(
        provider_mode=provider,
        idempotency_key=key,
        input_dir=str(input_dir),
        dataset_manifest=manifest,
    )
    await _dispatch_run(state.run_id)
    session_id = session_id or uuid.uuid4().hex
    response.set_cookie("arbiter_session", session_id, max_age=2592000, httponly=True, samesite="lax")
    await run_store.attach_session(session_id, state.run_id)
    return {"run_id": state.run_id, "status": state.status.value, "manifest": manifest}


@router.get("/session/runs")
async def get_session_runs(session_id: Optional[str] = Cookie(default=None, alias="arbiter_session")) -> dict:
    summaries = []
    session_run_ids = await run_store.session_run_ids(session_id or "")
    if not session_run_ids:
        session_run_ids = [
            path.stem
            for path in sorted(RUN_STATE_DIR.glob("run_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:20]
        ]
        for path in _result_files()[:20]:
            try:
                payload = _load_result(path)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            result_run_id = payload.get("run_id")
            if result_run_id and result_run_id not in session_run_ids:
                session_run_ids.append(result_run_id)

    for run_id in session_run_ids:
        state = await run_store.get_run(run_id)
        snapshot_payload = None
        if state is None:
            snapshot = RUN_STATE_DIR / f"{run_id}.json"
            if snapshot.exists():
                try:
                    snapshot_payload = _load_result(snapshot)
                except (OSError, json.JSONDecodeError, ValueError):
                    snapshot_payload = None
        payload = state.results if state and state.results else None
        if payload is None:
            candidates = [path for path in _result_files() if run_id in path.parts or path.name == f"eval_run_{run_id}.json"]
            if candidates:
                try:
                    payload = _load_result(candidates[0])
                except (OSError, json.JSONDecodeError, ValueError):
                    payload = None
        if payload:
            summaries.append({
                "run_id": run_id,
                "run_started_at": payload.get("run_started_at"),
                "timestamp": payload.get("timestamp"),
                "provider_mode": payload.get("provider_mode"),
                "total_records": payload.get("total_records"),
                "overall_match_rate": payload.get("overall_match_rate"),
                "breakdown": payload.get("breakdown") or {},
            })
        elif state or snapshot_payload:
            active = snapshot_payload or {}
            summaries.append({
                "run_id": run_id,
                "run_started_at": datetime.fromtimestamp(
                    state.created_at if state else float(active.get("created_at", 0)),
                    tz=timezone.utc,
                ).isoformat(),
                "timestamp": None,
                "provider_mode": state.provider_mode if state else active.get("provider_mode"),
                "total_records": state.total_records if state else active.get("total_records", 0),
                "overall_match_rate": None,
                "breakdown": {},
                "status": state.status.value if state else active.get("status", "pending"),
            })
    active_indexes = [
        index for index, item in enumerate(summaries)
        if item.get("status") in {"pending", "running"}
    ]
    if len(active_indexes) > 1:
        newest_active = active_indexes[0]
        summaries = [
            item for index, item in enumerate(summaries)
            if item.get("status") not in {"pending", "running"} or index == newest_active
        ]
    return {"run_ids": await run_store.session_run_ids(session_id or ""), "runs": summaries}


@router.get("/status/{run_id}", response_model=RunStatusResponse)
async def get_status(run_id: str, session_id: Optional[str] = Cookie(default=None, alias="arbiter_session")) -> RunStatusResponse:
    await _assert_session_access(run_id, session_id)
    snapshot = RUN_STATE_DIR / f"{run_id}.json"
    if snapshot.exists():
        try:
            snapshot_payload = _load_result(snapshot)
            return RunStatusResponse(
                status=snapshot_payload.get("status", "unknown"),
                records_processed=snapshot_payload.get("records_processed", 0),
                total_records=snapshot_payload.get("total_records", 0),
                fast_path_resolved_so_far=snapshot_payload.get("fast_path_resolved_so_far", 0),
                agent_resolved_so_far=snapshot_payload.get("agent_resolved_so_far", 0),
                error=snapshot_payload.get("error"),
            )
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass

    state = await run_store.get_run(run_id)

    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No run found with id '{run_id}'.",
        )

    payload = state.to_status_payload()
    payload["error"] = state.error
    return RunStatusResponse(**payload)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    session_id: Optional[str] = Cookie(default=None, alias="arbiter_session"),
) -> dict:
    await _assert_session_access(run_id, session_id)

    state = await run_store.get_run(run_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No run found with id '{run_id}'.",
        )

    if state.status == RunStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Run has already completed.")

    if state.status == RunStatus.FAILED:
        raise HTTPException(status_code=409, detail="Run has already failed.")

    state = await run_store.cancel_run(run_id)

    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No run found with id '{run_id}'.",
        )

    return {
        "run_id": run_id,
        "status": state.status.value,
        "message": state.error,
    }


@router.get("/results/latest", response_model=RunResultsResponse)
async def get_latest_results() -> RunResultsResponse:
    """Return the newest non-cancelled persisted reconciliation result."""
    files = _result_files()

    # _result_files() is already ordered newest-first by file modification time.
    # Never reverse it: doing so selects the oldest result.
    for filepath in files:
        try:
            payload = _load_result(filepath)
            payload = await _attach_legacy_run_id(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Unable to read {filepath.name}: {exc}",
            ) from exc

        result_run_id = payload.get("run_id")
        if result_run_id:
            live_state = await run_store.get_run(str(result_run_id))
            if live_state is not None and live_state.status == RunStatus.CANCELLED:
                continue

        return RunResultsResponse(**payload)

    state = await run_store.find_latest_completed()

    if state is None or state.results is None:
        raise HTTPException(
            status_code=404,
            detail="No completed runs found yet.",
        )

    payload = dict(state.results)
    payload["run_id"] = state.run_id
    return RunResultsResponse(**payload)


@router.get("/results", response_model=List[RunResultsSummary])
async def list_results() -> List[RunResultsSummary]:
    """Return compact summaries for every durable run, newest first."""
    summaries: List[RunResultsSummary] = []
    for filepath in _result_files():
        try:
            payload = _load_result(filepath)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        summaries.append(RunResultsSummary(
            run_id=payload.get("run_id"),
            run_started_at=payload.get("run_started_at"),
            timestamp=payload.get("timestamp"),
            provider_mode=payload.get("provider_mode"),
            total_records=payload.get("total_records"),
            overall_match_rate=payload.get("overall_match_rate"),
            breakdown=payload.get("breakdown") or {},
        ))
    return summaries


@router.get("/results/by-timestamp/{timestamp}", response_model=RunResultsResponse)
async def get_result_by_timestamp(timestamp: str) -> RunResultsResponse:
    """Read a legacy persisted result whose file predates run_id metadata."""
    for filepath in _result_files():
        try:
            payload = _load_result(filepath)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if payload.get("timestamp") == timestamp:
            return RunResultsResponse(**payload)
    raise HTTPException(status_code=404, detail=f"No persisted result found for timestamp '{timestamp}'.")


@router.get("/results/{run_id}", response_model=RunResultsResponse)
async def get_results(run_id: str, session_id: Optional[str] = Cookie(default=None, alias="arbiter_session")) -> RunResultsResponse:
    """Return a result from live memory or durable persisted files."""
    await _assert_session_access(run_id, session_id)
    state = await run_store.get_run(run_id)

    if state is not None:
        if state.status == RunStatus.CANCELLED:
            raise HTTPException(
                status_code=410,
                detail=f"Run '{run_id}' was cancelled.",
            )

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

    scoped_result = RESULTS_DIR / run_id / f"eval_run_{run_id}.json"
    if scoped_result.exists():
        try:
            return RunResultsResponse(**_load_result(scoped_result))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"Unable to read {scoped_result.name}: {exc}") from exc

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


@router.get("/reasoning-trace/{run_id}/{record_id}", response_model=ReasoningTraceResponse)
async def get_run_reasoning_trace(run_id: str, record_id: str, session_id: Optional[str] = Cookie(default=None, alias="arbiter_session")) -> ReasoningTraceResponse:
    await _assert_session_access(run_id, session_id)
    filepath = TRACE_DIR / run_id / f"{record_id}.json"

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


@router.get("/reasoning-trace/{record_id}", response_model=ReasoningTraceResponse)
async def get_reasoning_trace(record_id: str) -> ReasoningTraceResponse: # pyright: ignore[reportReturnType]
    """Legacy trace lookup retained for older persisted runs."""
    filepath = TRACE_DIR / f"{record_id}.json"


@router.get("/exceptions/{run_id}", response_model=RunExceptionsResponse)
async def get_exceptions(run_id: str, session_id: Optional[str] = Cookie(default=None, alias="arbiter_session")) -> RunExceptionsResponse:
    await _assert_session_access(run_id, session_id)
    state = await run_store.get_run(run_id)

    if state is None:
        # Completed runs survive a FastAPI restart in eval_run_*.json. Older
        # result files only carry the DLQ, so expose that durable evidence and
        # do not pretend an in-memory store is the source of truth.
        for filepath in _result_files():
            try:
                payload = _load_result(filepath)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if payload.get("run_id") == run_id:
                dlq = payload.get("dead_letter_queue") or []
                exceptions = _exception_records_from_result(payload)
                return RunExceptionsResponse(
                    run_id=run_id,
                    exceptions=[ExceptionRecord(**item) for item in exceptions],
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
