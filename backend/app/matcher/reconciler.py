import asyncio
import contextvars
import sys
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

# ---------------------------------------------------------------------------
# Active-processing vs rate-limit-wait time tracking, split by WHY the sleep
# happened -- not by duration (1.0-1.5s self-paced sleeps can collide with
# short backoff waits, so duration is not a safe classifier).
#
# The actual `asyncio.sleep()` calls live inside proposer.py and verifier.py;
# none of them are touched here. asyncio.sleep is wrapped ONCE, globally,
# purely for measurement: every call still does exactly what it always did
# (delegates to the real sleep for the same duration). What's new is that
# the wrapper inspects its IMMEDIATE CALLER'S stack frame (file, function,
# exact line number) to classify each sleep, then adds its duration to the
# matching bucket in a per-task accumulator. Because each asyncio Task gets
# its own copy of the contextvars context, concurrent records tracked in the
# same asyncio.gather() batch never leak wait-time into each other.
#
# Call sites found in the current proposer.py / verifier.py (confirmed by
# reading them, not guessed):
#   proposer.py:129  _proactive_groq_throttle   -> reactive (proactive Groq TPM pacing)
#   proposer.py:149  _gemini_track_and_throttle -> reactive (proactive Gemini RPM pacing)
#   proposer.py:339  _call_groq (429 branch)    -> reactive (429 backoff)
#   proposer.py:370  _call_gemini (429 branch)  -> reactive (429/quota backoff)
#   proposer.py:551  run_proposer               -> self_paced (fixed 1.5s inter-turn courtesy sleep)
#   verifier.py:247  run_verifier               -> self_paced (fixed 1.0s inter-turn courtesy sleep)
#   proposer.py:328  _call_groq (hallucination-correction branch) -> NEITHER of the
#       above two categories: this sleep isn't triggered by a 429/budget
#       limit, and it isn't a fixed per-turn sleep that runs regardless of
#       what happened -- it only fires when Groq calls an invalid/hallucinated
#       tool name. Forcing it into "reactive" or "self_paced" would misrepresent
#       both numbers, so it gets its own bucket ("other") instead of a guess.
#
# _call_groq specifically contains BOTH a reactive sleep (line 339) and this
# unrelated one (line 328), so classification for that function needs the
# exact line number, not just the function name. If proposer.py/verifier.py
# are edited later and a sleep call ends up somewhere not in this table, it
# is bucketed into "other" and never silently guessed into "reactive" or
# "self_paced" -- check the "other" bucket if these numbers look off after
# an edit to those files.
# ---------------------------------------------------------------------------
_REACTIVE_FUNCS = {
    ("proposer.py", "_proactive_groq_throttle"),
    ("proposer.py", "_gemini_track_and_throttle"),
}
_SELF_PACED_FUNCS = {
    ("proposer.py", "run_proposer"),
    ("verifier.py", "run_verifier"),
}
_CALL_GROQ_REACTIVE_LINE = 339   # await asyncio.sleep(wait) -- 429 backoff
_CALL_GROQ_OTHER_LINE = 328      # await asyncio.sleep(1.0) -- hallucination-retry pacing, NOT a rate-limit wait
_CALL_GEMINI_REACTIVE_LINE = 370  # await asyncio.sleep(delay) -- 429/quota backoff

_WAIT_BUCKETS = ("reactive", "self_paced", "other")


def _classify_sleep_call_site(caller_frame) -> str:
    filename = Path(caller_frame.f_code.co_filename).name
    func_name = caller_frame.f_code.co_name
    lineno = caller_frame.f_lineno

    if filename == "proposer.py" and func_name == "_call_groq":
        if lineno == _CALL_GROQ_REACTIVE_LINE:
            return "reactive"
        if lineno == _CALL_GROQ_OTHER_LINE:
            return "other"
        return "other"  # unrecognized line inside _call_groq -- don't guess
    if filename == "proposer.py" and func_name == "_call_gemini":
        return "reactive" if lineno == _CALL_GEMINI_REACTIVE_LINE else "other"
    if (filename, func_name) in _REACTIVE_FUNCS:
        return "reactive"
    if (filename, func_name) in _SELF_PACED_FUNCS:
        return "self_paced"
    return "other"  # unrecognized call site -- surfaced, never guessed


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
    """Call at the start of a unit of work whose sleep time you want
    tracked (split by reactive/self_paced/other) separately from its
    active processing time. Returns a token -- pass it to
    stop_wait_tracking() when done."""
    return _WAIT_TIME_ACCUMULATOR.set({b: 0.0 for b in _WAIT_BUCKETS})


def get_accumulated_wait_breakdown() -> Dict[str, float]:
    acc = _WAIT_TIME_ACCUMULATOR.get()
    return dict(acc) if acc is not None else {b: 0.0 for b in _WAIT_BUCKETS}


def get_accumulated_wait_time() -> float:
    """Total wait time across all buckets (kept for callers that just want
    the combined figure)."""
    return sum(get_accumulated_wait_breakdown().values())


def stop_wait_tracking(token: contextvars.Token) -> Dict[str, float]:
    """Returns the accumulated wait-time breakdown and resets tracking for
    this task."""
    breakdown = get_accumulated_wait_breakdown()
    _WAIT_TIME_ACCUMULATOR.reset(token)
    return breakdown


async def resolve_record(
    bank_record: Dict[str, Any],
    all_ledger: List[Dict[str, Any]],
    all_gateway: List[Dict[str, Any]],
    provider: str
) -> Dict[str, Any]:
    sem = groq_semaphore if provider == "groq" else gemini_semaphore

    async with sem:
        start_time = time.time()
        record_id = bank_record.get("record_id", "UNKNOWN")
        wait_token = start_wait_tracking()
        try:
            candidates = get_candidate_pool(bank_record, all_ledger, all_gateway)

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
        finally:
            stop_wait_tracking(wait_token)


def _save_trace(record_id: str, trace: Dict[str, Any], start_time: float) -> None:
    elapsed = time.time() - start_time
    # Read (not stop -- resolve_record's finally block does that) the
    # accumulator for THIS task, since _save_trace always runs before the
    # finally block resets it.
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

    # THE ACTUAL FIX: config.PROVIDER is validated to only ever be "groq" or
    # "gemini" (see config.py), which means the old `else: providers =
    # ["groq", "gemini"]` branch here could NEVER execute — every batch run
    # was silently forced onto a single provider, regardless of what the
    # per-provider semaphores were designed to do. Fixed by introducing an
    # explicit "auto" mode, set via PROVIDER=auto in .env, which is the
    # ONLY way real dual-provider parallelism turns on.
    #
    # TEMPORARY OVERRIDE (see date this was changed in git blame / commit
    # message): Gemini's free tier for gemini-3.6-flash is 20 requests/day
    # PER PROJECT — not per-minute. That's confirmed exhausted, not just
    # rate-limited, so weighting 2-of-3 records toward it was actively
    # hurting the batch (guaranteed failures, not slower success). "auto"
    # is single-provider Groq only for now. The Gemini branch is left in
    # place, commented, so it's a one-line revert once the daily quota
    # resets or a paid tier is in place — this is NOT a redesign of the
    # dual-provider intent, just turning it off until Gemini can actually
    # serve requests.
    configured_provider = (config.PROVIDER or "groq").lower()

    if configured_provider == "auto":
        provider_cycle = ["groq"]  # was ["gemini", "gemini", "groq"] -- Gemini free tier exhausted (20 req/day)
    elif configured_provider in ("groq", "gemini"):
        provider_cycle = [configured_provider]
    else:
        provider_cycle = ["groq"]

    tasks = []
    assigned = []
    for i, record in enumerate(bank_records):
        assigned_provider = provider_cycle[i % len(provider_cycle)]
        assigned.append(assigned_provider)
        tasks.append(resolve_record(record, all_ledger, all_gateway, assigned_provider))

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