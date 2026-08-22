import asyncio
import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from backend.app.agent.proposer import run_proposer, get_candidate_pool
from backend.app.agent.verifier import run_verifier
from backend.app.agent import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
TRACE_DIR = BASE_DIR / "logs" / "reasoning_trace"
TRACE_DIR.mkdir(parents=True, exist_ok=True)

groq_semaphore = asyncio.Semaphore(1)
gemini_semaphore = asyncio.Semaphore(2)  # Gemini has more headroom, allow 2 in flight

async def resolve_record(
    bank_record: Dict[str, Any],
    all_ledger: List[Dict[str, Any]],
    provider: str
) -> Dict[str, Any]:
    sem = groq_semaphore if provider == "groq" else gemini_semaphore

    async with sem:
        start_time = time.time()
        record_id = bank_record.get("record_id", "UNKNOWN")
        candidates = get_candidate_pool(bank_record, all_ledger)

        trace: Dict[str, Any] = {
            "record_id": record_id,
            "provider": provider,
            "history": []
        }

        config.PROVIDER = provider

        prop_res = await run_proposer(bank_record, candidates, temperature=0.1)
        trace["history"].append({"agent": "proposer", "attempt": 1, "result": prop_res})

        status = prop_res.get("status")
        if status in ["no_match", "flagged"] or not prop_res.get("matched_ledger_ids"):
            trace["final_status"] = "exception"
            trace["final_decision"] = prop_res
            _save_trace(record_id, trace, start_time)
            return trace

        ver_res = await run_verifier(
            bank_record=bank_record,
            candidates=candidates,
            proposed_match=prop_res,
            proposer_reasoning=prop_res.get("reasoning", ""),
            provider=provider
        )
        trace["history"].append({"agent": "verifier", "attempt": 1, "result": ver_res})

        if ver_res.get("decision") == "agree":
            trace["final_status"] = "confirmed"
            trace["final_decision"] = prop_res
            _save_trace(record_id, trace, start_time)
            return trace

        objection = ver_res.get("reasoning", "Disagreed on candidate validity.")
        retry_bank_record = dict(bank_record)
        retry_bank_record["notes"] = f"Previous proposal was rejected by verifier: {objection}"

        prop_res_retry = await run_proposer(retry_bank_record, candidates, temperature=0.3)
        trace["history"].append({"agent": "proposer", "attempt": 2, "result": prop_res_retry})

        if prop_res_retry.get("status") == "suggested_match" and prop_res_retry.get("matched_ledger_ids"):
            ver_res_retry = await run_verifier(
                bank_record=retry_bank_record,
                candidates=candidates,
                proposed_match=prop_res_retry,
                proposer_reasoning=prop_res_retry.get("reasoning", ""),
                provider=provider
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


def _save_trace(record_id: str, trace: Dict[str, Any], start_time: float) -> None:
    elapsed = time.time() - start_time
    trace["wall_clock_time_sec"] = round(elapsed, 2)
    filepath = TRACE_DIR / f"{record_id}.json"
    with open(filepath, "w") as f:
        json.dump(trace, f, indent=2)


async def resolve_batch(
    bank_records: List[Dict[str, Any]],
    all_ledger: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    start_time = time.time()

    # THE ACTUAL FIX: config.PROVIDER is validated to only ever be "groq" or
    # "gemini" (see config.py), which means the old `else: providers =
    # ["groq", "gemini"]` branch here could NEVER execute — every batch run
    # was silently forced onto a single provider, regardless of what the
    # per-provider semaphores were designed to do. Fixed by introducing an
    # explicit "auto" mode, set via PROVIDER=auto in .env, which is the
    # ONLY way real dual-provider parallelism turns on.
    #
    # Weighting: every log from this session shows Groq hitting 40-55s
    # pacing waits on nearly every call (its ~8k TPM ceiling on a 120b model
    # is the dominant bottleneck), while Gemini has not shown a comparable
    # wait once. So in "auto" mode, Gemini gets 2 of every 3 records —
    # Groq stays in the mix for genuine dual-provider evidence in the
    # eval/pitch, but isn't allowed to be the majority bottleneck anymore.
    configured_provider = (config.PROVIDER or "groq").lower()

    if configured_provider == "auto":
        provider_cycle = ["gemini", "gemini", "groq"]
    elif configured_provider in ("groq", "gemini"):
        provider_cycle = [configured_provider]
    else:
        provider_cycle = ["gemini", "gemini", "groq"]

    tasks = []
    assigned = []
    for i, record in enumerate(bank_records):
        assigned_provider = provider_cycle[i % len(provider_cycle)]
        assigned.append(assigned_provider)
        tasks.append(resolve_record(record, all_ledger, assigned_provider))

    print(f"\n[Reconciler] Resolving batch of {len(bank_records)} records. "
          f"Provider cycle: {provider_cycle} -> assignment: {assigned}")
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