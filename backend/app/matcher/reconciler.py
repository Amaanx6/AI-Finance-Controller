import asyncio
import contextvars
import sys
import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from backend.app.agent.proposer import run_proposer, get_candidate_pool, KeyState, KEY_STATES, get_least_loaded_key
from backend.app.agent.verifier import run_verifier
from backend.app.agent import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
TRACE_DIR = BASE_DIR / "logs" / "reasoning_trace"
TRACE_DIR.mkdir(parents=True, exist_ok=True)

_REACTIVE_FUNCS = {
    ("proposer.py", "proactive_throttle"),
    ("proposer.py", "_rate_limit_sleep"),
}
_SELF_PACED_FUNCS = {
    ("proposer.py", "run_proposer"),
    ("verifier.py", "run_verifier"),
}

_WAIT_BUCKETS = ("reactive", "self_paced", "other")

def _classify_sleep_call_site(caller_frame) -> str:
    filename = Path(caller_frame.f_code.co_filename).name
    func_name = caller_frame.f_code.co_name

    if (filename, func_name) in _REACTIVE_FUNCS:
        return "reactive"
    if (filename, func_name) in _SELF_PACED_FUNCS:
        return "self_paced"
    return "other"

_WaitAccumulator = Dict[str, float]

_WAIT_TIME_ACCUMULATOR: "contextvars.ContextVar[Optional[_WaitAccumulator]]" = contextvars.ContextVar(
    "reconciler_wait_time_accumulator", default=None
)

_original_asyncio_sleep = asyncio.sleep

async def _instrumented_asyncio_sleep(delay: float, result: Any = None) -> Any:
    acc = _WAIT_TIME_ACCUMULATOR.get()
    if acc is not None:
        caller_frame = sys._getframe(1)
        category = _classify_sleep_call_site(caller_frame)
        acc[category] += delay
    return await _original_asyncio_sleep(delay, result)

if not getattr(asyncio.sleep, "_reconciler_instrumented", False):
    _instrumented_asyncio_sleep._reconciler_instrumented = True  # type: ignore[attr-defined]
    asyncio.sleep = _instrumented_asyncio_sleep  # type: ignore[assignment]

def start_wait_tracking() -> contextvars.Token:
    return _WAIT_TIME_ACCUMULATOR.set({b: 0.0 for b in _WAIT_BUCKETS})

def get_accumulated_wait_breakdown() -> Dict[str, float]:
    acc = _WAIT_TIME_ACCUMULATOR.get()
    return dict(acc) if acc is not None else {b: 0.0 for b in _WAIT_BUCKETS}

def get_accumulated_wait_time() -> float:
    return sum(get_accumulated_wait_breakdown().values())

def stop_wait_tracking(token: contextvars.Token) -> Dict[str, float]:
    breakdown = get_accumulated_wait_breakdown()
    _WAIT_TIME_ACCUMULATOR.reset(token)
    return breakdown


async def resolve_record(
    bank_record: Dict[str, Any],
    all_ledger: List[Dict[str, Any]],
    all_gateway: List[Dict[str, Any]],
    assigned_key: KeyState
) -> Dict[str, Any]:
    
    start_time = time.time()
    record_id = bank_record.get("record_id", "UNKNOWN")
    wait_token = start_wait_tracking()
    
    try:
        candidates = get_candidate_pool(bank_record, all_ledger, all_gateway)

        trace: Dict[str, Any] = {
            "record_id": record_id,
            "handled_by_key": assigned_key.key_id,
            "provider": assigned_key.provider,
            "history": []
        }

        # Proposer Call 1
        prop_res, messages = await run_proposer(bank_record, candidates, temperature=0.1, assigned_key=assigned_key)
        trace["history"].append({"agent": "proposer", "attempt": 1, "result": prop_res})

        status = prop_res.get("status")
        if status in ["no_match", "flagged"] or not prop_res.get("matched_ledger_ids"):
            trace["final_status"] = "exception"
            trace["final_decision"] = prop_res
            _save_trace(record_id, trace, start_time)
            return trace

        # Verifier Call 1
        ver_res = await run_verifier(
            bank_record=bank_record,
            candidates=candidates,
            proposed_match=prop_res,
            proposer_reasoning=prop_res.get("reasoning", ""),
            assigned_key=assigned_key
        )
        trace["history"].append({"agent": "verifier", "attempt": 1, "result": ver_res})

        if ver_res.get("decision") == "agree":
            trace["final_status"] = "confirmed"
            trace["final_decision"] = prop_res
            _save_trace(record_id, trace, start_time)
            return trace

        # ONE-TURN CONTINUATION APPEND
        objection = ver_res.get("reasoning", "Disagreed on candidate validity.")
        messages.append({
            "role": "user",
            "content": f"The verifier rejected your proposal with this objection: '{objection}'. Please address this objection, use tools if needed, and submit a corrected final decision."
        })

        # Proposer Call 2
        prop_res_retry, _ = await run_proposer(bank_record, candidates, temperature=0.3, message_history=messages, assigned_key=assigned_key)
        trace["history"].append({"agent": "proposer", "attempt": 2, "result": prop_res_retry})

        # SET-BASED ORDER-INDEPENDENT COMPARISON
        old_ledger = set(prop_res.get("matched_ledger_ids", []))
        old_gateway = set(prop_res.get("matched_gateway_ids", []))
        new_ledger = set(prop_res_retry.get("matched_ledger_ids", []))
        new_gateway = set(prop_res_retry.get("matched_gateway_ids", []))

        if prop_res_retry.get("status") == "suggested_match" and new_ledger:
            if old_ledger == new_ledger and old_gateway == new_gateway:
                trace["final_status"] = "exception"
                trace["final_decision"] = prop_res_retry
                _save_trace(record_id, trace, start_time)
                return trace

            ver_res_retry = await run_verifier(
                bank_record=bank_record,
                candidates=candidates,
                proposed_match=prop_res_retry,
                proposer_reasoning=prop_res_retry.get("reasoning", ""),
                assigned_key=assigned_key
            )
            trace["history"].append({"agent": "verifier", "attempt": 2, "result": ver_res_retry})

            if ver_res_retry.get("decision") == "agree":
                trace["final_status"] = "confirmed"
                trace["final_decision"] = prop_res_retry
                _save_trace(record_id, trace, start_time)
                return trace

        trace["final_status"] = "exception"
        trace["final_decision"] = prop_res_retry
        _save_trace(record_id, trace, start_time)
        return trace
    finally:
        stop_wait_tracking(wait_token)


def _save_trace(record_id: str, trace: Dict[str, Any], start_time: float) -> None:
    elapsed = time.time() - start_time
    breakdown = get_accumulated_wait_breakdown()
    reactive = breakdown["reactive"]
    self_paced = breakdown["self_paced"]
    other = breakdown["other"]
    total_wait = reactive + self_paced + other
    active_time = max(0.0, elapsed - total_wait)
    trace["wall_clock_time_sec"] = round(elapsed, 2)
    trace["active_processing_time_sec"] = round(active_time, 2)
    trace["reactive_throttle_wait_sec"] = round(reactive, 2)
    trace["self_paced_wait_sec"] = round(self_paced, 2)
    trace["other_pacing_wait_sec"] = round(other, 2)
    filepath = TRACE_DIR / f"{record_id}.json"
    with open(filepath, "w") as f:
        json.dump(trace, f, indent=2)


async def resolve_batch(
    bank_records: List[Dict[str, Any]],
    all_ledger: List[Dict[str, Any]],
    all_gateway: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    start_time = time.time()

    concurrency = len(KEY_STATES) if KEY_STATES else 1
    batch_sem = asyncio.Semaphore(max(4, concurrency))

    tasks = []
    
    async def process_record(record):
        async with batch_sem:
            key = get_least_loaded_key()
            return await resolve_record(record, all_ledger, all_gateway, key)

    for record in bank_records:
        tasks.append(process_record(record))

    print(f"\n[Reconciler] Resolving batch of {len(bank_records)} records with dynamic load balancing (Concurrency: {max(4, concurrency)}).")
    results = await asyncio.gather(*tasks)

    total_time = time.time() - start_time
    avg_time = total_time / len(bank_records) if bank_records else 0.0
    est_seq_time = sum(r.get("wall_clock_time_sec", 0.0) for r in results)
    speedup = (est_seq_time / total_time) if total_time > 0 else 1.0

    print("\n--- BATCH RESOLUTION COMPLETE ---")
    print(f"Total Wall-Clock Time:    {total_time:.2f}s")
    print(f"Est. Sequential Time:     {est_seq_time:.2f}s (Speedup: {speedup:.2f}x)")
    print(f"Avg Time / Record:        {avg_time:.2f}s\n")

    return results