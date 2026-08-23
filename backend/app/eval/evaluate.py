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
from typing import Any, Dict, List, Optional, Set

from backend.app.matcher.fast_matcher import load_csv, fast_match_bank_records, MatchResult
from backend.app.matcher.reconciler import resolve_batch, start_wait_tracking, stop_wait_tracking
from backend.app.agent.proposer import run_proposer, get_candidate_pool
from backend.app.agent import config

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Same depth-from-file convention as reconciler.py's BASE_DIR
# (backend/app/eval/evaluate.py -> eval -> app -> backend -> project root).
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

# ---------------------------------------------------------------------------
# stdout tee, for capturing rate-limit / pacing lines per provider without
# touching proposer.py to add counters itself.
# ---------------------------------------------------------------------------
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
]
_GEMINI_WAIT_PATTERNS = [
    re.compile(r"Gemini: at RPM budget"),
    re.compile(r"Gemini rate limited"),
]


def _count_provider_waits(captured_text: str) -> Dict[str, int]:
    groq_waits = sum(len(p.findall(captured_text)) for p in _GROQ_WAIT_PATTERNS)
    gemini_waits = sum(len(p.findall(captured_text)) for p in _GEMINI_WAIT_PATTERNS)
    return {"groq": groq_waits, "gemini": gemini_waits}


# ---------------------------------------------------------------------------
# Ground truth loading
# ---------------------------------------------------------------------------
def _split_ids(field: str) -> Set[str]:
    if not field:
        return set()
    return {x.strip() for x in field.split(",") if x.strip()}


def load_ground_truth(path: Path) -> Dict[str, Dict[str, Any]]:
    """
    Returns dict keyed by bank_record_id (only rows that HAVE a bank_record_id --
    orphan ledger/gateway-only anomaly rows have none and are structurally
    unreachable by a bank-record-driven pipeline, so they're returned
    separately for transparency rather than silently dropped).
    """
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
# Naive single-agent baseline (proposer only, no verifier).
# Reuses run_proposer() and get_candidate_pool() as-is; this orchestration
# loop itself is new code (there is no proposer-only mode in reconciler.py
# to reuse), mirroring the same per-provider semaphore caps and the same
# gemini-weighted provider cycle so the comparison is apples-to-apples.
# Uses its own local semaphores (not reconciler's) so the two runs never
# contend with each other if you inspect them independently.
# ---------------------------------------------------------------------------
_baseline_groq_sem = asyncio.Semaphore(1)
_baseline_gemini_sem = asyncio.Semaphore(2)


async def _baseline_resolve_one(
    bank_record: Dict[str, Any],
    all_ledger: List[Dict[str, Any]],
    all_gateway: List[Dict[str, Any]],
    provider: str,
) -> Dict[str, Any]:
    sem = _baseline_groq_sem if provider == "groq" else _baseline_gemini_sem
    async with sem:
        start = time.time()
        record_id = bank_record.get("record_id", "UNKNOWN")
        wait_token = start_wait_tracking()
        try:
            candidates = get_candidate_pool(bank_record, all_ledger, all_gateway)

            config.PROVIDER = provider
            prop_res = await run_proposer(bank_record, candidates, temperature=0.1)

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
            "provider": provider,
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
) -> List[Dict[str, Any]]:
    # Mirrors reconciler.py's current "auto" cycle exactly (see the
    # TEMPORARY OVERRIDE comment there) -- Gemini free tier (20 req/day) is
    # exhausted, so this is single-provider Groq only for now, same as the
    # full pipeline, to keep baseline-vs-full a fair comparison.
    provider_cycle = ["groq"]
    tasks = []
    assigned = []
    for i, record in enumerate(bank_records):
        p = provider_cycle[i % len(provider_cycle)]
        assigned.append(p)
        tasks.append(_baseline_resolve_one(record, all_ledger, all_gateway, p))

    print(f"\n[Baseline] Resolving {len(bank_records)} records (proposer-only, no verifier). "
          f"Provider cycle: {provider_cycle} -> assignment: {assigned}")
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _score_predictions(
    predictions: Dict[str, Dict[str, Any]],
    ground_truth: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    predictions: bank_record_id -> {"ledger_ids": set, "gateway_ids": set, "status": <terminal status str>}
    Only bank records that appear in ground_truth (i.e. have a row in mapping.csv
    with a non-blank bank_record_id) are scored -- see load_ground_truth().
    """
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
            # genuine anomaly with a bank record but no true counterpart:
            # correct behavior is to NOT assert a match.
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
    return None  # ambiguous / unresolved -> goes to agent stage instead


def _extract_prediction_from_agent(trace: Dict[str, Any]) -> Dict[str, Any]:
    decision = trace.get("final_decision") or {}
    ledger_ids = set(decision.get("matched_ledger_ids") or [])
    gateway_ids = set(decision.get("matched_gateway_ids") or [])
    status = "confirmed" if trace.get("final_status") == "confirmed" else "exception"
    return {"status": status, "ledger_ids": ledger_ids, "gateway_ids": gateway_ids}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    run_started_at = datetime.now()
    timestamp = run_started_at.strftime("%Y%m%d_%H%M%S")

    print("=" * 78)
    print("RECONCILIATION SYSTEM -- FULL EVALUATION RUN")
    print(f"Started: {run_started_at.isoformat()}")
    print("=" * 78)

    # --- provider check: must be auto, and we do NOT force it ourselves ---
    configured_provider = (os.environ.get("PROVIDER") or getattr(config, "PROVIDER", "") or "").lower()
    if configured_provider != "auto":
        raise RuntimeError(
            f"PROVIDER is configured as '{configured_provider or '<unset>'}', not 'auto'. "
            "This eval is required to capture real dual-provider performance data "
            "(see instruction: do not force PROVIDER=groq/gemini inside evaluate.py). "
            "Set PROVIDER=auto in your .env and re-run."
        )
    print(f"[config] PROVIDER confirmed as 'auto' (not forced by this script).")

    # --- load CSVs ---
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

    # Numeric-amount copies for the agent-facing path only (sum_check does
    # arithmetic on c["amount"] directly). fast_matcher gets the raw string
    # rows, unchanged, exactly as its own __main__ block does.
    ledger_records_numeric = [{**r, "amount": float(r["amount"])} for r in ledger_records]
    gateway_records_numeric = [{**r, "amount": float(r["amount"])} for r in gateway_records]
    bank_records_numeric = {r["record_id"]: {**r, "amount": float(r["amount"])} for r in bank_records}

    # --- fast path ---
    fast_start = time.time()
    fast_results = fast_match_bank_records(bank_records, ledger_records, gateway_records)
    fast_elapsed = time.time() - fast_start
    avg_fast_latency = fast_elapsed / total_records if total_records else 0.0

    fast_confirmed = [r for r in fast_results if r.status == "confirmed"]
    fast_flagged = [r for r in fast_results if r.status == "flagged"]
    needs_agent = [r for r in fast_results if r.status in ("ambiguous", "unresolved")]

    print(f"\n--- FAST PATH COMPLETE ({fast_elapsed:.3f}s total, {avg_fast_latency*1000:.2f}ms/record avg) ---")
    print(f"Fast-path confirmed: {len(fast_confirmed)}")
    print(f"Fast-path flagged:   {len(fast_flagged)}")
    print(f"Escalated to agent:  {len(needs_agent)}")

    escalated_bank_records = [bank_records_numeric[r.bank_id] for r in needs_agent]

    # --- full pipeline: proposer + verifier via resolve_batch (reused as-is) ---
    print(f"\n[Full pipeline] Escalating {len(escalated_bank_records)} records to resolve_batch()...")
    full_stdout = _TeeCapture(sys.stdout)
    agent_start = time.time()
    with redirect_stdout(full_stdout):
        agent_results = await resolve_batch(escalated_bank_records, ledger_records_numeric, gateway_records_numeric)
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
    agent_exception = [r for r in agent_results if r.get("final_status") != "confirmed"]

    provider_counts_full: Dict[str, int] = {}
    for r in agent_results:
        provider_counts_full[r.get("provider", "unknown")] = provider_counts_full.get(r.get("provider", "unknown"), 0) + 1

    print(f"\n--- AGENT STAGE (full pipeline) COMPLETE ---")
    print(f"Agent-confirmed: {len(agent_confirmed)}")
    print(f"Exception:       {len(agent_exception)}")
    print(f"Records per provider: {provider_counts_full}")
    print(f"Rate-limit / pacing waits observed: {full_pipeline_waits}")

    # --- naive single-agent baseline, same escalated records ---
    print(f"\n[Baseline] Running proposer-only baseline on the SAME {len(escalated_bank_records)} escalated records...")
    baseline_stdout = _TeeCapture(sys.stdout)
    baseline_start = time.time()
    with redirect_stdout(baseline_stdout):
        baseline_results = await run_single_agent_baseline(escalated_bank_records, ledger_records_numeric, gateway_records_numeric)
    baseline_elapsed = time.time() - baseline_start
    baseline_waits = _count_provider_waits(baseline_stdout.getvalue())

    est_seq_time_baseline = sum(r.get("wall_clock_time_sec", 0.0) for r in baseline_results)
    speedup_baseline = (est_seq_time_baseline / baseline_elapsed) if baseline_elapsed > 0 else 1.0
    total_active_time_baseline = sum(r.get("active_processing_time_sec", 0.0) for r in baseline_results)
    total_reactive_wait_baseline = sum(r.get("reactive_throttle_wait_sec", 0.0) for r in baseline_results)
    total_self_paced_wait_baseline = sum(r.get("self_paced_wait_sec", 0.0) for r in baseline_results)
    total_other_wait_baseline = sum(r.get("other_pacing_wait_sec", 0.0) for r in baseline_results)

    provider_counts_baseline: Dict[str, int] = {}
    for r in baseline_results:
        provider_counts_baseline[r.get("provider", "unknown")] = provider_counts_baseline.get(r.get("provider", "unknown"), 0) + 1

    baseline_confirmed = [r for r in baseline_results if r.get("final_status") == "confirmed"]
    baseline_exception = [r for r in baseline_results if r.get("final_status") != "confirmed"]

    print(f"\n--- BASELINE STAGE COMPLETE ---")
    print(f"Baseline-confirmed: {len(baseline_confirmed)}")
    print(f"Exception:          {len(baseline_exception)}")
    print(f"Records per provider: {provider_counts_baseline}")
    print(f"Rate-limit / pacing waits observed: {baseline_waits}")

    # --- ground truth + scoring ---
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
    # baseline reuses the exact same fast-path results for the non-escalated records
    # (fast path isn't part of what's being compared -- only the agent stage is),
    # and its own proposer-only decisions for the escalated ones.
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

    # --- print comparison table ---
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

    # --- performance report ---
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
    print(f"Full-pipeline records handled per provider: {provider_counts_full}")
    print(f"Full-pipeline rate-limit/pacing waits:       {full_pipeline_waits}")
    print(f"Baseline agent stage wall-clock:          {baseline_elapsed:.3f}s total")
    print(f"  Active processing time:                 {total_active_time_baseline:.2f}s")
    print(f"  Reactive throttle wait (429s / budget pacing): {total_reactive_wait_baseline:.2f}s")
    print(f"  Self-paced wait (fixed inter-turn sleeps):     {total_self_paced_wait_baseline:.2f}s")
    if total_other_wait_baseline > 0:
        print(f"  Other pacing wait (e.g. hallucination-retry):  {total_other_wait_baseline:.2f}s")
    print(f"Baseline speedup vs sequential est.:      {speedup_baseline:.2f}x "
          f"(est. sequential {est_seq_time_baseline:.2f}s / actual {baseline_elapsed:.2f}s)")
    print(f"Baseline records handled per provider:    {provider_counts_baseline}")
    print(f"Baseline rate-limit/pacing waits:          {baseline_waits}")

    # --- spot-check sample for manual verification (NOT a substitute for it) ---
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

    # --- save results JSON ---
    results_payload = {
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
                "records_per_provider": provider_counts_full,
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
                "records_per_provider": provider_counts_baseline,
                "rate_limit_waits": baseline_waits,
            },
        },
        "ground_truth_orphan_rows_excluded": len(orphan_rows),
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
        ],
    }

    results_path = RESULTS_DIR / f"eval_run_{timestamp}.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2, default=str)

    print(f"\nFull results written to: {results_path}")
    print(f"Run finished: {datetime.now().isoformat()}")


def pct_safe(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


def fmt_pct(v: Optional[float]) -> str:
    return f"{v*100:.1f}%" if v is not None else "n/a"


def fmt_pr(p: Optional[float], r: Optional[float]) -> str:
    return f"P{fmt_pct(p)}/R{fmt_pct(r)}"


if __name__ == "__main__":
    asyncio.run(main())