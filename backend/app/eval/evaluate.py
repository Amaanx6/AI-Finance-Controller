from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import sys
import time
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from backend.app.matcher.fast_matcher import load_csv, fast_match_bank_records, MatchResult
from backend.app.matcher.reconciler import (
    resolve_batch,
    resolve_record,
    start_wait_tracking,
    stop_wait_tracking,
)
from backend.app.agent.proposer import run_proposer, get_candidate_pool, KEY_STATES, get_least_loaded_key
from backend.app.agent import config
from backend.app.api.circuit_breaker import CircuitBreaker, is_transient_error
from backend.app.api.logging_utils import log_event

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

SAMPLES_DIR = BASE_DIR / "backend" / "app" / "data_generation" / "samples"
BANK_CSV = SAMPLES_DIR / "bank_statement.csv"
LEDGER_CSV = SAMPLES_DIR / "internal_ledger.csv"
GATEWAY_CSV = SAMPLES_DIR / "gateway_export.csv"

GROUND_TRUTH_CSV = Path(
    os.environ.get("GROUND_TRUTH_PATH")
    or (BASE_DIR / "backend" / "app" / "data_generation" / "ground_truth" / "mapping.csv")
)

RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

PATTERN_ORDER = [
    "clean_1to1_match",
    "many_to_one_settlement",
    "description_mismatch",
    "near_miss_amount_date",
    "genuine_anomaly",
]

# Callback types used to wire this pipeline into the FastAPI job runner
# (see backend/app/api/job.py). All are optional and default to no-ops so
# `python -m backend.app.scripts.evaluate` behaves exactly as before.
FastProgressCB = Callable[[int, int, Optional[MatchResult]], None]
AgentProgressCB = Callable[[Dict[str, Any]], None]
ExceptionCB = Callable[[Dict[str, Any]], None]
DLQCB = Callable[[Dict[str, Any]], None]


class _TeeCapture(io.StringIO):
    def __init__(self, real_stdout):
        super().__init__()
        self._real = real_stdout

    def write(self, s: str) -> int:
        self._real.write(s)
        self._real.flush()
        return super().write(s)

_GROQ_WAIT_PATTERNS = [
    re.compile(r"Groq rate limited \(429\)"),
    re.compile(r"Proactive Pacing"),
    re.compile(r"rate limited \(transient\)"),
    re.compile(r"EXHAUSTED"),
]
_GEMINI_WAIT_PATTERNS = [
    re.compile(r"Gemini: at RPM budget"),
    re.compile(r"Gemini rate limited"),
]

def _count_provider_waits(captured_text: str) -> Dict[str, int]:
    groq_waits = sum(len(p.findall(captured_text)) for p in _GROQ_WAIT_PATTERNS)
    gemini_waits = sum(len(p.findall(captured_text)) for p in _GEMINI_WAIT_PATTERNS)
    return {"groq": groq_waits, "gemini": gemini_waits}

def _split_ids(field: str) -> Set[str]:
    if not field:
        return set()
    return {x.strip() for x in field.split(",") if x.strip()}

def load_ground_truth(path: Path) -> Dict[str, Dict[str, Any]]:
    by_bank: Dict[str, Dict[str, Any]] = {}
    orphans: List[Dict[str, Any]] = []

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bank_id = (row.get("bank_record_id") or "").strip()
            entry = {
                "transaction_id": row.get("transaction_id"),
                "ledger_ids": _split_ids(row.get("ledger_record_ids", "")),
                "gateway_ids": _split_ids(row.get("gateway_record_ids", "")),
                "pattern": row.get("pattern"),
            }
            if bank_id:
                by_bank[bank_id] = entry
            else:
                orphans.append(entry)

    by_bank["__orphans__"] = orphans  # type: ignore[assignment]
    return by_bank


# ---------------------------------------------------------------------------
# Circuit-breaker-aware, progress-reporting record resolution
# ---------------------------------------------------------------------------
# These two functions mirror resolve_batch()'s / run_single_agent_baseline()'s
# existing concurrency pattern (same semaphore sizing, same
# get_least_loaded_key() load balancing) but add three things reconciler.py
# and the original baseline runner don't have:
#   1. A progress callback fired the instant each individual record finishes
#      (this is what powers GET /api/status/{run_id}'s live progress bar).
#   2. A circuit breaker check before each LLM call, so a provider that's
#      throwing consecutive connection/timeout errors gets failed-fast
#      instead of hammered further.
#   3. Per-record exception capture into a Dead-Letter Queue instead of
#      letting one bad record crash the whole asyncio.gather().
# reconciler.py and evaluate.py's original run_single_agent_baseline are left
# untouched; resolve_record and _baseline_resolve_one (the actual LLM-calling
# units of work) are reused as-is.

async def _resolve_batch_with_progress(
    bank_records: List[Dict[str, Any]],
    all_ledger: List[Dict[str, Any]],
    all_gateway: List[Dict[str, Any]],
    *,
    run_id: str = "cli",
    progress_cb: Optional[AgentProgressCB] = None,
    exception_cb: Optional[ExceptionCB] = None,
    dlq_cb: Optional[DLQCB] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> List[Dict[str, Any]]:
    start_time = time.time()
    concurrency = len(KEY_STATES) if KEY_STATES else 1
    sem = asyncio.Semaphore(max(4, concurrency))
    results: List[Dict[str, Any]] = []

    async def process(record: Dict[str, Any]) -> None:
        record_id = record.get("record_id", "UNKNOWN")
        async with sem:
            key = get_least_loaded_key()

            if circuit_breaker is not None and circuit_breaker.is_open(key.provider):
                dlq_entry = {
                    "record_id": record_id,
                    "stage": "agent_resolution",
                    "reason": "circuit_breaker_open",
                    "provider": key.provider,
                    "detail": f"Circuit open for provider '{key.provider}' after repeated "
                              f"connection/timeout failures; skipped to avoid hammering a dead endpoint.",
                }
                log_event("circuit_breaker_skip", run_id, record_id=record_id, provider=key.provider)
                if dlq_cb:
                    dlq_cb(dlq_entry)
                result = {
                    "record_id": record_id,
                    "provider": key.provider,
                    "handled_by_key": key.key_id,
                    "final_status": "dlq",
                    "final_decision": {"status": "dlq", "reason": "circuit_breaker_open"},
                    "wall_clock_time_sec": 0.0,
                }
                results.append(result)
                if progress_cb:
                    progress_cb(result)
                return

            call_start = time.time()
            try:
                result = await resolve_record(record, all_ledger, all_gateway, key)
                if circuit_breaker is not None:
                    circuit_breaker.record_success(key.provider)
                log_event(
                    "record_resolved", run_id, record_id=record_id, provider=key.provider,
                    latency_sec=time.time() - call_start, final_status=result.get("final_status"),
                )
                if result.get("final_status") != "confirmed" and exception_cb:
                    exception_cb({
                        "record_id": record_id,
                        "stage": "agent_resolution",
                        "reason": result.get("final_status", "exception"),
                        "provider": key.provider,
                        "detail": (result.get("final_decision") or {}).get("reasoning"),
                    })
            except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure -> DLQ
                if circuit_breaker is not None:
                    circuit_breaker.record_failure(key.provider, exc)
                log_event(
                    "record_failed", run_id, record_id=record_id, provider=key.provider,
                    latency_sec=time.time() - call_start, level="error",
                    error_type=type(exc).__name__, error=str(exc),
                )
                dlq_entry = {
                    "record_id": record_id,
                    "stage": "agent_resolution",
                    "reason": "unresolvable_model_exception" if not is_transient_error(exc) else "connection_or_timeout_error",
                    "provider": key.provider,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
                if dlq_cb:
                    dlq_cb(dlq_entry)
                result = {
                    "record_id": record_id,
                    "provider": key.provider,
                    "handled_by_key": key.key_id,
                    "final_status": "dlq",
                    "final_decision": {"status": "dlq", "error": str(exc)},
                    "wall_clock_time_sec": round(time.time() - call_start, 2),
                }

            results.append(result)
            if progress_cb:
                progress_cb(result)

    log_event("agent_stage_start", run_id, total_records=len(bank_records))
    await asyncio.gather(*(process(r) for r in bank_records))

    total_time = time.time() - start_time
    avg_time = total_time / len(bank_records) if bank_records else 0.0
    est_seq_time = sum(r.get("wall_clock_time_sec", 0.0) for r in results)
    speedup = (est_seq_time / total_time) if total_time > 0 else 1.0

    print("\n--- BATCH RESOLUTION COMPLETE ---")
    print(f"Total Wall-Clock Time:    {total_time:.2f}s")
    print(f"Est. Sequential Time:     {est_seq_time:.2f}s (Speedup: {speedup:.2f}x)")
    print(f"Avg Time / Record:        {avg_time:.2f}s\n")
    log_event("agent_stage_complete", run_id, total_time_sec=total_time, speedup=speedup)

    return results


async def _baseline_resolve_one(
    bank_record: Dict[str, Any],
    all_ledger: List[Dict[str, Any]],
    all_gateway: List[Dict[str, Any]],
    assigned_key: Any,
) -> Dict[str, Any]:
    
    start = time.time()
    record_id = bank_record.get("record_id", "UNKNOWN")
    wait_token = start_wait_tracking()
    
    try:
        candidates = get_candidate_pool(bank_record, all_ledger, all_gateway)
        prop_res, _ = await run_proposer(bank_record, candidates, temperature=0.1, assigned_key=assigned_key)

        status = prop_res.get("status")
        matched_ledger_ids = prop_res.get("matched_ledger_ids") or []
        if status in ("no_match", "flagged") or not matched_ledger_ids:
            final_status = "exception"
        else:
            final_status = "confirmed"
    finally:
        wait_breakdown = stop_wait_tracking(wait_token)

    reactive = wait_breakdown["reactive"]
    self_paced = wait_breakdown["self_paced"]
    other = wait_breakdown["other"]
    total_wait = reactive + self_paced + other
    elapsed = time.time() - start
    active_time = max(0.0, elapsed - total_wait)
    return {
        "record_id": record_id,
        "provider": assigned_key.provider,
        "handled_by_key": assigned_key.key_id,
        "final_status": final_status,
        "final_decision": prop_res,
        "wall_clock_time_sec": round(elapsed, 2),
        "active_processing_time_sec": round(active_time, 2),
        "reactive_throttle_wait_sec": round(reactive, 2),
        "self_paced_wait_sec": round(self_paced, 2),
        "other_pacing_wait_sec": round(other, 2),
    }

async def run_single_agent_baseline(
    bank_records: List[Dict[str, Any]],
    all_ledger: List[Dict[str, Any]],
    all_gateway: List[Dict[str, Any]],
    *,
    run_id: str = "cli",
    circuit_breaker: Optional[CircuitBreaker] = None,
    dlq_cb: Optional[DLQCB] = None,
) -> List[Dict[str, Any]]:
    concurrency = len(KEY_STATES) if KEY_STATES else 1
    baseline_sem = asyncio.Semaphore(max(4, concurrency))
    
    tasks = []
    
    async def process(rec):
        async with baseline_sem:
            key = get_least_loaded_key()

            if circuit_breaker is not None and circuit_breaker.is_open(key.provider):
                if dlq_cb:
                    dlq_cb({
                        "record_id": rec.get("record_id", "UNKNOWN"),
                        "stage": "baseline_resolution",
                        "reason": "circuit_breaker_open",
                        "provider": key.provider,
                        "detail": f"Circuit open for provider '{key.provider}'; baseline call skipped.",
                    })
                return {
                    "record_id": rec.get("record_id", "UNKNOWN"),
                    "provider": key.provider,
                    "handled_by_key": key.key_id,
                    "final_status": "dlq",
                    "final_decision": {"status": "dlq", "reason": "circuit_breaker_open"},
                    "wall_clock_time_sec": 0.0,
                }

            try:
                result = await _baseline_resolve_one(rec, all_ledger, all_gateway, key)
                if circuit_breaker is not None:
                    circuit_breaker.record_success(key.provider)
                return result
            except Exception as exc:  # noqa: BLE001
                if circuit_breaker is not None:
                    circuit_breaker.record_failure(key.provider, exc)
                if dlq_cb:
                    dlq_cb({
                        "record_id": rec.get("record_id", "UNKNOWN"),
                        "stage": "baseline_resolution",
                        "reason": "unresolvable_model_exception" if not is_transient_error(exc) else "connection_or_timeout_error",
                        "provider": key.provider,
                        "detail": f"{type(exc).__name__}: {exc}",
                    })
                return {
                    "record_id": rec.get("record_id", "UNKNOWN"),
                    "provider": key.provider,
                    "handled_by_key": key.key_id,
                    "final_status": "dlq",
                    "final_decision": {"status": "dlq", "error": str(exc)},
                    "wall_clock_time_sec": 0.0,
                }

    for record in bank_records:
        tasks.append(process(record))

    print(f"\n[Baseline] Resolving {len(bank_records)} records (proposer-only, no verifier).")
    return await asyncio.gather(*tasks)

def _score_predictions(
    predictions: Dict[str, Dict[str, Any]],
    ground_truth: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    bank_ids = [k for k in ground_truth.keys() if k != "__orphans__"]

    per_pattern: Dict[str, Dict[str, int]] = {
        p: {"total": 0, "correct": 0, "predicted_positive": 0, "true_positive_universe": 0, "true_positives_found": 0}
        for p in PATTERN_ORDER
    }

    total_correct = 0
    total_predicted_positive = 0
    total_true_positive_universe = 0
    total_true_positives_found = 0

    detail_rows: List[Dict[str, Any]] = []

    for bank_id in bank_ids:
        gt = ground_truth[bank_id]
        pattern = gt["pattern"]
        if pattern not in per_pattern:
            per_pattern[pattern] = {"total": 0, "correct": 0, "predicted_positive": 0, "true_positive_universe": 0, "true_positives_found": 0}

        gt_has_true_match = bool(gt["ledger_ids"]) or bool(gt["gateway_ids"])

        pred = predictions.get(bank_id)
        pred_has_match = pred is not None and pred.get("status") in ("confirmed",)
        pred_ledger = pred.get("ledger_ids", set()) if pred else set()
        pred_gateway = pred.get("gateway_ids", set()) if pred else set()

        is_correct: bool
        if gt_has_true_match:
            is_correct = pred_has_match and pred_ledger == gt["ledger_ids"] and pred_gateway == gt["gateway_ids"]
        else:
            is_correct = not pred_has_match

        per_pattern[pattern]["total"] += 1
        if is_correct:
            per_pattern[pattern]["correct"] += 1
            total_correct += 1

        if pred_has_match:
            per_pattern[pattern]["predicted_positive"] += 1
            total_predicted_positive += 1

        if gt_has_true_match:
            per_pattern[pattern]["true_positive_universe"] += 1
            total_true_positive_universe += 1
            if pred_has_match and pred_ledger == gt["ledger_ids"] and pred_gateway == gt["gateway_ids"]:
                per_pattern[pattern]["true_positives_found"] += 1
                total_true_positives_found += 1

        detail_rows.append({
            "bank_record_id": bank_id,
            "pattern": pattern,
            "ground_truth_ledger_ids": sorted(gt["ledger_ids"]),
            "ground_truth_gateway_ids": sorted(gt["gateway_ids"]),
            "predicted_status": pred.get("status") if pred else "no_prediction",
            "predicted_ledger_ids": sorted(pred_ledger),
            "predicted_gateway_ids": sorted(pred_gateway),
            "correct": is_correct,
        })

    def pct(n, d):
        return round(n / d, 4) if d else None

    overall = {
        "total_scored_records": len(bank_ids),
        "accuracy": pct(total_correct, len(bank_ids)),
        "precision": pct(total_true_positives_found, total_predicted_positive),
        "recall": pct(total_true_positives_found, total_true_positive_universe),
    }

    by_pattern = {}
    for p in PATTERN_ORDER:
        d = per_pattern.get(p, {"total": 0, "correct": 0, "predicted_positive": 0, "true_positive_universe": 0, "true_positives_found": 0})
        by_pattern[p] = {
            "total_records": d["total"],
            "accuracy": pct(d["correct"], d["total"]),
            "precision": pct(d["true_positives_found"], d["predicted_positive"]),
            "recall": pct(d["true_positives_found"], d["true_positive_universe"]),
        }

    return {"overall": overall, "by_pattern": by_pattern, "detail_rows": detail_rows}

def _extract_prediction_from_fast(mr: MatchResult) -> Optional[Dict[str, Any]]:
    if mr.status == "confirmed":
        return {
            "status": "confirmed",
            "ledger_ids": {mr.matched_ledger_id} if mr.matched_ledger_id else set(),
            "gateway_ids": {mr.matched_gateway_id} if mr.matched_gateway_id else set(),
        }
    if mr.status == "flagged":
        return {"status": "flagged", "ledger_ids": set(), "gateway_ids": set()}
    return None 

def _extract_prediction_from_agent(trace: Dict[str, Any]) -> Dict[str, Any]:
    decision = trace.get("final_decision") or {}
    ledger_ids = set(decision.get("matched_ledger_ids") or [])
    gateway_ids = set(decision.get("matched_gateway_ids") or [])
    status = "confirmed" if trace.get("final_status") == "confirmed" else "exception"
    return {"status": status, "ledger_ids": ledger_ids, "gateway_ids": gateway_ids}


async def run_evaluation(
    run_id: str = "cli",
    fast_progress_cb: Optional[FastProgressCB] = None,
    agent_progress_cb: Optional[AgentProgressCB] = None,
    exception_cb: Optional[ExceptionCB] = None,
    dlq_cb: Optional[DLQCB] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> Dict[str, Any]:
    """Run the full fast-path + agent + baseline evaluation pipeline.

    This is the body of the original `main()`, extracted so both the CLI
    entry point below and the FastAPI job runner (api/job.py) share one
    implementation. All callback/circuit-breaker arguments are optional;
    calling this with no arguments reproduces the original script's
    behavior exactly (plus resilience: a single bad record no longer
    crashes the whole run, it's routed to the DLQ instead).
    """
    run_started_at = datetime.now()
    timestamp = run_started_at.strftime("%Y%m%d_%H%M%S")

    print("=" * 78)
    print("RECONCILIATION SYSTEM -- FULL EVALUATION RUN")
    print(f"Started: {run_started_at.isoformat()}")
    print("=" * 78)
    log_event("run_start", run_id, provider=os.environ.get("PROVIDER"))

    configured_provider = (os.environ.get("PROVIDER") or getattr(config, "PROVIDER", "") or "").lower()
    if configured_provider not in ["auto", "groq", "local"]:
        raise RuntimeError(
            f"PROVIDER is configured as '{configured_provider or '<unset>'}'. "
            "Set PROVIDER to 'local' or 'groq' in your .env and re-run."
        )
    print(f"[config] PROVIDER confirmed as '{configured_provider}'.")

    if not (BANK_CSV.exists() and LEDGER_CSV.exists() and GATEWAY_CSV.exists()):
        raise FileNotFoundError(
            f"Expected sample CSVs under {SAMPLES_DIR} "
            f"(bank_statement.csv / internal_ledger.csv / gateway_export.csv) -- not found."
        )
    if not GROUND_TRUTH_CSV.exists():
        raise FileNotFoundError(f"Ground truth not found at {GROUND_TRUTH_CSV}")

    bank_records = load_csv(BANK_CSV)
    ledger_records = load_csv(LEDGER_CSV)
    gateway_records = load_csv(GATEWAY_CSV)

    total_records = len(bank_records)
    print(f"\nLoaded {total_records} bank records, {len(ledger_records)} ledger records, "
          f"{len(gateway_records)} gateway records.")

    ledger_records_numeric = [{**r, "amount": float(r["amount"])} for r in ledger_records]
    gateway_records_numeric = [{**r, "amount": float(r["amount"])} for r in gateway_records]
    bank_records_numeric = {r["record_id"]: {**r, "amount": float(r["amount"])} for r in bank_records}

    def _fast_cb(completed: int, total: int, result: MatchResult) -> None:
        if fast_progress_cb:
            fast_progress_cb(completed, total, result)
        if result.status == "flagged" and exception_cb:
            exception_cb({
                "record_id": result.bank_id,
                "stage": "fast_path",
                "reason": "flagged",
                "provider": None,
                "detail": "Reference number matched but amount fell outside tolerance.",
            })

    fast_start = time.time()
    fast_results = fast_match_bank_records(
        bank_records, ledger_records, gateway_records, progress_callback=_fast_cb
    )
    fast_elapsed = time.time() - fast_start
    avg_fast_latency = fast_elapsed / total_records if total_records else 0.0

    fast_confirmed = [r for r in fast_results if r.status == "confirmed"]
    fast_flagged = [r for r in fast_results if r.status == "flagged"]
    needs_agent = [r for r in fast_results if r.status in ("ambiguous", "unresolved")]

    print(f"\n--- FAST PATH COMPLETE ({fast_elapsed:.3f}s total, {avg_fast_latency*1000:.2f}ms/record avg) ---")
    print(f"Fast-path confirmed: {len(fast_confirmed)}")
    print(f"Fast-path flagged:   {len(fast_flagged)}")
    print(f"Escalated to agent:  {len(needs_agent)}")
    log_event(
        "fast_stage_complete", run_id, total_time_sec=fast_elapsed,
        confirmed=len(fast_confirmed), flagged=len(fast_flagged), escalated=len(needs_agent),
    )

    escalated_bank_records = [bank_records_numeric[r.bank_id] for r in needs_agent]

    print(f"\n[Full pipeline] Escalating {len(escalated_bank_records)} records to resolve_batch()...")
    full_stdout = _TeeCapture(sys.stdout)
    agent_start = time.time()
    with redirect_stdout(full_stdout):
        agent_results = await _resolve_batch_with_progress(
            escalated_bank_records, ledger_records_numeric, gateway_records_numeric,
            run_id=run_id, progress_cb=agent_progress_cb, exception_cb=exception_cb,
            dlq_cb=dlq_cb, circuit_breaker=circuit_breaker,
        )
    agent_elapsed = time.time() - agent_start
    full_pipeline_waits = _count_provider_waits(full_stdout.getvalue())

    agent_by_record_id = {r["record_id"]: r for r in agent_results}
    avg_agent_latency = (agent_elapsed / len(agent_results)) if agent_results else 0.0
    est_seq_time_full = sum(r.get("wall_clock_time_sec", 0.0) for r in agent_results)
    speedup_full = (est_seq_time_full / agent_elapsed) if agent_elapsed > 0 else 1.0
    total_active_time_full = sum(r.get("active_processing_time_sec", 0.0) for r in agent_results)
    total_reactive_wait_full = sum(r.get("reactive_throttle_wait_sec", 0.0) for r in agent_results)
    total_self_paced_wait_full = sum(r.get("self_paced_wait_sec", 0.0) for r in agent_results)
    total_other_wait_full = sum(r.get("other_pacing_wait_sec", 0.0) for r in agent_results)

    agent_confirmed = [r for r in agent_results if r.get("final_status") == "confirmed"]
    agent_exception = [r for r in agent_results if r.get("final_status") not in ("confirmed",)]

    provider_counts_full: Dict[str, int] = {}
    key_counts_full: Dict[str, int] = {}
    for r in agent_results:
        provider_counts_full[r.get("provider", "unknown")] = provider_counts_full.get(r.get("provider", "unknown"), 0) + 1
        key_counts_full[r.get("handled_by_key", "unknown")] = key_counts_full.get(r.get("handled_by_key", "unknown"), 0) + 1

    print(f"\n--- AGENT STAGE (full pipeline) COMPLETE ---")
    print(f"Agent-confirmed: {len(agent_confirmed)}")
    print(f"Exception:       {len(agent_exception)}")
    print(f"Records per provider: {provider_counts_full}")
    print(f"Records per key: {key_counts_full}")
    print(f"Rate-limit / pacing waits observed: {full_pipeline_waits}")

    print(f"\n[Baseline] Running proposer-only baseline on the SAME {len(escalated_bank_records)} escalated records...")
    baseline_stdout = _TeeCapture(sys.stdout)
    baseline_start = time.time()
    with redirect_stdout(baseline_stdout):
        baseline_results = await run_single_agent_baseline(
            escalated_bank_records, ledger_records_numeric, gateway_records_numeric,
            run_id=run_id, circuit_breaker=circuit_breaker, dlq_cb=dlq_cb,
        )
    baseline_elapsed = time.time() - baseline_start
    baseline_waits = _count_provider_waits(baseline_stdout.getvalue())

    est_seq_time_baseline = sum(r.get("wall_clock_time_sec", 0.0) for r in baseline_results)
    speedup_baseline = (est_seq_time_baseline / baseline_elapsed) if baseline_elapsed > 0 else 1.0
    total_active_time_baseline = sum(r.get("active_processing_time_sec", 0.0) for r in baseline_results)
    total_reactive_wait_baseline = sum(r.get("reactive_throttle_wait_sec", 0.0) for r in baseline_results)
    total_self_paced_wait_baseline = sum(r.get("self_paced_wait_sec", 0.0) for r in baseline_results)
    total_other_wait_baseline = sum(r.get("other_pacing_wait_sec", 0.0) for r in baseline_results)

    provider_counts_baseline: Dict[str, int] = {}
    key_counts_baseline: Dict[str, int] = {}
    for r in baseline_results:
        provider_counts_baseline[r.get("provider", "unknown")] = provider_counts_baseline.get(r.get("provider", "unknown"), 0) + 1
        key_counts_baseline[r.get("handled_by_key", "unknown")] = key_counts_baseline.get(r.get("handled_by_key", "unknown"), 0) + 1

    baseline_confirmed = [r for r in baseline_results if r.get("final_status") == "confirmed"]
    baseline_exception = [r for r in baseline_results if r.get("final_status") not in ("confirmed",)]

    print(f"\n--- BASELINE STAGE COMPLETE ---")
    print(f"Baseline-confirmed: {len(baseline_confirmed)}")
    print(f"Exception:          {len(baseline_exception)}")
    print(f"Records per provider: {provider_counts_baseline}")
    print(f"Records per key: {key_counts_baseline}")
    print(f"Rate-limit / pacing waits observed: {baseline_waits}")

    ground_truth = load_ground_truth(GROUND_TRUTH_CSV)
    orphan_rows = ground_truth.pop("__orphans__", [])
    if orphan_rows:
        print(f"\n[Ground truth] {len(orphan_rows)} orphan ledger/gateway-only anomaly rows "
              f"have no bank_record_id and are structurally unreachable by this pipeline; "
              f"excluded from every score below (see caveat #2 at top of this file).")

    full_predictions: Dict[str, Dict[str, Any]] = {}
    for r in fast_results:
        p = _extract_prediction_from_fast(r)
        if p is not None:
            full_predictions[r.bank_id] = p
    for record_id, trace in agent_by_record_id.items():
        full_predictions[record_id] = _extract_prediction_from_agent(trace)

    baseline_predictions: Dict[str, Dict[str, Any]] = {}
    for r in fast_results:
        p = _extract_prediction_from_fast(r)
        if p is not None:
            baseline_predictions[r.bank_id] = p
    for r in baseline_results:
        decision = r.get("final_decision") or {}
        baseline_predictions[r["record_id"]] = {
            "status": r.get("final_status"),
            "ledger_ids": set(decision.get("matched_ledger_ids") or []),
            "gateway_ids": set(decision.get("matched_gateway_ids") or []),
        }

    full_scores = _score_predictions(full_predictions, ground_truth)
    baseline_scores = _score_predictions(baseline_predictions, ground_truth)

    overall_match_rate = pct_safe(len(fast_confirmed) + len(agent_confirmed), total_records)

    print("\n" + "=" * 78)
    print("COMPARISON: single-agent-only (proposer, no verifier) vs proposer+verifier")
    print("=" * 78)
    header = f"{'Pattern':<26}{'Baseline Acc':>14}{'Full Acc':>14}{'Baseline P/R':>18}{'Full P/R':>18}"
    print(header)
    print("-" * len(header))
    for p in PATTERN_ORDER:
        b = baseline_scores["by_pattern"][p]
        fscore = full_scores["by_pattern"][p]
        marker = " <-- decoy target" if p == "description_mismatch" else ""
        print(
            f"{p:<26}"
            f"{fmt_pct(b['accuracy']):>14}"
            f"{fmt_pct(fscore['accuracy']):>14}"
            f"{fmt_pr(b['precision'], b['recall']):>18}"
            f"{fmt_pr(fscore['precision'], fscore['recall']):>18}"
            f"{marker}"
        )
    print("-" * len(header))
    print(
        f"{'OVERALL':<26}"
        f"{fmt_pct(baseline_scores['overall']['accuracy']):>14}"
        f"{fmt_pct(full_scores['overall']['accuracy']):>14}"
        f"{fmt_pr(baseline_scores['overall']['precision'], baseline_scores['overall']['recall']):>18}"
        f"{fmt_pr(full_scores['overall']['precision'], full_scores['overall']['recall']):>18}"
    )

    dm_b = baseline_scores["by_pattern"]["description_mismatch"]
    dm_f = full_scores["by_pattern"]["description_mismatch"]
    print(f"\ndescription_mismatch called out: baseline accuracy {fmt_pct(dm_b['accuracy'])} "
          f"({dm_b['total_records']} records) vs full-pipeline accuracy {fmt_pct(dm_f['accuracy'])} "
          f"({dm_f['total_records']} records).")

    print("\n" + "=" * 78)
    print("PERFORMANCE REPORT")
    print("=" * 78)
    print(f"Total records:                         {total_records}")
    print(f"Overall match rate:                     {fmt_pct(overall_match_rate)}")
    print(f"Fast-path wall-clock time:               {fast_elapsed:.3f}s total, {avg_fast_latency*1000:.2f}ms/record avg")
    print(f"Full-pipeline agent stage wall-clock:     {agent_elapsed:.3f}s total, {avg_agent_latency:.3f}s/record avg")
    print(f"  Active processing time:                 {total_active_time_full:.2f}s")
    print(f"  Reactive throttle wait (429s / budget pacing): {total_reactive_wait_full:.2f}s")
    print(f"  Self-paced wait (fixed inter-turn sleeps):     {total_self_paced_wait_full:.2f}s")
    if total_other_wait_full > 0:
        print(f"  Other pacing wait (e.g. hallucination-retry):  {total_other_wait_full:.2f}s")
    print(f"Full-pipeline speedup vs sequential est.: {speedup_full:.2f}x "
          f"(est. sequential {est_seq_time_full:.2f}s / actual {agent_elapsed:.2f}s)")
    print(f"Full-pipeline records handled per key:       {key_counts_full}")
    print(f"Full-pipeline rate-limit/pacing waits:       {full_pipeline_waits}")
    print(f"Baseline agent stage wall-clock:          {baseline_elapsed:.3f}s total")
    print(f"  Active processing time:                 {total_active_time_baseline:.2f}s")
    print(f"  Reactive throttle wait (429s / budget pacing): {total_reactive_wait_baseline:.2f}s")
    print(f"  Self-paced wait (fixed inter-turn sleeps):     {total_self_paced_wait_baseline:.2f}s")
    if total_other_wait_baseline > 0:
        print(f"  Other pacing wait (e.g. hallucination-retry):  {total_other_wait_baseline:.2f}s")
    print(f"Baseline speedup vs sequential est.:      {speedup_baseline:.2f}x "
          f"(est. sequential {est_seq_time_baseline:.2f}s / actual {baseline_elapsed:.2f}s)")
    print(f"Baseline records handled per key:         {key_counts_baseline}")
    print(f"Baseline rate-limit/pacing waits:          {baseline_waits}")

    print("\n" + "=" * 78)
    print("SPOT-CHECK SAMPLE -- please manually verify these against ground_truth/mapping.csv yourself")
    print("(this script reporting a match is not the same as a human confirming one)")
    print("=" * 78)
    by_pattern_rows: Dict[str, List[Dict[str, Any]]] = {p: [] for p in PATTERN_ORDER}
    for row in full_scores["detail_rows"]:
        by_pattern_rows.setdefault(row["pattern"], []).append(row)
    for p in PATTERN_ORDER:
        rows = by_pattern_rows.get(p, [])[:3]
        print(f"\n[{p}]")
        for row in rows:
            print(f"  bank_record_id={row['bank_record_id']}")
            print(f"    ground truth: ledger={row['ground_truth_ledger_ids']} gateway={row['ground_truth_gateway_ids']}")
            print(f"    predicted:    status={row['predicted_status']} ledger={row['predicted_ledger_ids']} gateway={row['predicted_gateway_ids']}")
            print(f"    correct={row['correct']}")

    dead_letter_queue = [
        r for r in (agent_results + baseline_results) if r.get("final_status") == "dlq"
    ]

    results_payload = {
        # The caller creates the run identity. Persist it in the original
        # durable file rather than retrospectively selecting "the newest"
        # file, which can be wrong when evaluations overlap.
        "run_id": run_id,
        "run_started_at": run_started_at.isoformat(),
        "timestamp": timestamp,
        "provider_mode": configured_provider,
        "total_records": total_records,
        "overall_match_rate": overall_match_rate,
        "breakdown": {
            "fast_path_confirmed": len(fast_confirmed),
            "fast_path_flagged": len(fast_flagged),
            "agent_confirmed": len(agent_confirmed),
            "exception": len(agent_exception),
        },
        "full_pipeline_scores": full_scores,
        "baseline_scores": baseline_scores,
        "performance": {
            "fast_path": {
                "total_time_sec": round(fast_elapsed, 4),
                "avg_latency_sec_per_record": round(avg_fast_latency, 5),
            },
            "full_pipeline_agent_stage": {
                "total_time_sec": round(agent_elapsed, 4),
                "avg_latency_sec_per_record": round(avg_agent_latency, 4),
                "total_active_processing_time_sec": round(total_active_time_full, 4),
                "total_reactive_throttle_wait_sec": round(total_reactive_wait_full, 4),
                "total_self_paced_wait_sec": round(total_self_paced_wait_full, 4),
                "total_other_pacing_wait_sec": round(total_other_wait_full, 4),
                "est_sequential_time_sec": round(est_seq_time_full, 4),
                "speedup": round(speedup_full, 4),
                "records_per_key": key_counts_full,
                "rate_limit_waits": full_pipeline_waits,
            },
            "baseline_agent_stage": {
                "total_time_sec": round(baseline_elapsed, 4),
                "total_active_processing_time_sec": round(total_active_time_baseline, 4),
                "total_reactive_throttle_wait_sec": round(total_reactive_wait_baseline, 4),
                "total_self_paced_wait_sec": round(total_self_paced_wait_baseline, 4),
                "total_other_pacing_wait_sec": round(total_other_wait_baseline, 4),
                "est_sequential_time_sec": round(est_seq_time_baseline, 4),
                "speedup": round(speedup_baseline, 4),
                "records_per_key": key_counts_baseline,
                "rate_limit_waits": baseline_waits,
            },
        },
        "ground_truth_orphan_rows_excluded": len(orphan_rows),
        "dead_letter_queue": dead_letter_queue,
        "caveats": [
            "active_processing_time_sec / reactive_throttle_wait_sec / self_paced_wait_sec / "
            "other_pacing_wait_sec per record (and their sums here) come from wrapping asyncio.sleep() "
            "globally for measurement only -- every sleep still waits the same real duration, it's just "
            "also added to a per-task accumulator, in a bucket chosen by inspecting the IMMEDIATE "
            "CALLER'S stack frame (file, function, exact line number) at the moment asyncio.sleep() is "
            "called -- not sleep duration, which isn't a reliable classifier (1.0-1.5s self-paced sleeps "
            "can collide with short backoff waits). No proposer.py/verifier.py sleep call sites were "
            "changed. 'other_pacing_wait_sec' exists because _call_groq contains a sleep (invalid/"
            "hallucinated tool-name retry pacing) that is neither a reactive throttle wait nor a fixed "
            "per-turn self-paced sleep -- it's reported separately rather than guessed into either "
            "bucket. It also acts as a safety net: if proposer.py/verifier.py's sleep call sites move or "
            "change in the future, an unrecognized call site falls into 'other' instead of being "
            "silently misattributed.",
            "get_candidate_pool()/resolve_record()/resolve_batch() now accept and use all_gateway "
            "alongside all_ledger, so agent-stage matched_gateway_ids should be populated for real "
            "gateway candidates going forward (previously a known gap, now fixed).",
            "mapping.csv's real header is transaction_id,bank_record_id,ledger_record_ids,"
            "gateway_record_ids,pattern (plural ids, 'pattern' not 'pattern_type').",
            "speedup/est_sequential figures are recomputed outside resolve_batch() using its "
            "exact internal formula, since resolve_batch() only prints them and reconciler.py "
            "was not modified to return them.",
            "Records that raised an unrecoverable exception (connection/timeout after the circuit "
            "breaker tripped, or a JSON/model parsing failure) are excluded from full_pipeline_scores/"
            "baseline_scores as unscored 'dlq' predictions and are listed under dead_letter_queue "
            "instead, with their failure reason.",
        ],
    }

    results_path = RESULTS_DIR / f"eval_run_{timestamp}.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2, default=str)

    print(f"\nFull results written to: {results_path}")
    print(f"Run finished: {datetime.now().isoformat()}")
    log_event("run_complete", run_id, results_path=str(results_path), overall_match_rate=overall_match_rate)

    return results_payload


async def main() -> None:
    await run_evaluation()

def pct_safe(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None

def fmt_pct(v: Optional[float]) -> str:
    return f"{v*100:.1f}%" if v is not None else "n/a"

def fmt_pr(p: Optional[float], r: Optional[float]) -> str:
    return f"P{fmt_pct(p)}/R{fmt_pct(r)}"

if __name__ == "__main__":
    asyncio.run(main())
