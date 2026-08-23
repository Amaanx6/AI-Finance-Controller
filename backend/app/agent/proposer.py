from dotenv import load_dotenv
import re
import os
import time
import json
import csv
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, cast

from groq import AsyncGroq
from groq.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

# Gemini via its OpenAI-compatible endpoint
from openai import AsyncOpenAI as AsyncGeminiClient

from backend.app.agent.prompts import PROPOSER_SYSTEM_PROMPT
from backend.app.agent.tools import sum_check, description_similarity
from backend.app.agent import config

load_dotenv()

groq_client = AsyncGroq(api_key=config.GROQ_API_KEY, max_retries=0)
gemini_client = AsyncGeminiClient(
    api_key=config.GEMINI_API_KEY,
    base_url=config.GEMINI_BASE_URL,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = BASE_DIR / "logs" / "proposer"
LOG_DIR.mkdir(parents=True, exist_ok=True)

MAX_TURNS = config.MAX_TURNS

# SINGLE SOURCE OF TRUTH FOR MATCH TOLERANCE
MATCH_TOLERANCE_PCT = 3.0
TIGHT_MATCH_THRESHOLD = 0.005  # 0.5%

# AGENT_TOOLS, business logic below: EXACTLY the same regardless of provider.
AGENT_TOOLS: List[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "sum_check",
            "description": f"Finds subsets of candidates whose amounts sum to target_amount (up to {MATCH_TOLERANCE_PCT}% fee).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_amount": {"type": "number"},
                    "fee_tolerance_pct": {"type": "number", "default": MATCH_TOLERANCE_PCT}
                },
                "required": ["target_amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "description_similarity",
            "description": "Normalized similarity (0-1) between two transaction descriptions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "desc_a": {"type": "string"},
                    "desc_b": {"type": "string"}
                },
                "required": ["desc_a", "desc_b"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "submit_final_decision",
            "description": "REQUIRED: call when finished to submit your final decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["matched", "no_match", "flagged", "suggested_match"]
                    },
                    "matched_ledger_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "record_id values ONLY from the internal ledger (ids starting with LEDG_). Do not include gateway ids here."
                    },
                    "matched_gateway_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "record_id values ONLY from the gateway export (ids starting with GW_). Do not include ledger ids here."
                    },
                    "reasoning": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]}
                },
                "required": ["status", "reasoning", "confidence"]
            }
        }
    }
]

AVAILABLE_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "sum_check": sum_check,
    "description_similarity": description_similarity
}

RATE_LIMIT_EXHAUSTED = "RATE_LIMIT_EXHAUSTED"

# ---------------------------------------------------------------------------
# Proactive Groq Token Bucket Throttling
# ---------------------------------------------------------------------------
_groq_tokens_this_minute = 0
_groq_minute_start = time.time()
_groq_lock = asyncio.Lock()

async def _proactive_groq_throttle(estimated_tokens: int = 1500) -> None:
    global _groq_tokens_this_minute, _groq_minute_start
    async with _groq_lock:
        now = time.time()
        if now - _groq_minute_start >= 60:
            _groq_tokens_this_minute = 0
            _groq_minute_start = now
            
        if _groq_tokens_this_minute + estimated_tokens > config.GROQ_TPM_BUDGET:
            wait = 60 - (now - _groq_minute_start)
            if wait > 0:
                print(f"[~] Proactive Pacing: Budget near limit ({_groq_tokens_this_minute} TPM). Pacing request for {wait:.1f}s...")
                await asyncio.sleep(wait)
            _groq_tokens_this_minute = 0
            _groq_minute_start = time.time()
            
        _groq_tokens_this_minute += estimated_tokens

# ---------------------------------------------------------------------------
# Gemini throttling
# ---------------------------------------------------------------------------
_gemini_request_timestamps: List[float] = []

async def _gemini_track_and_throttle() -> None:
    global _gemini_request_timestamps
    now = time.time()
    _gemini_request_timestamps = [t for t in _gemini_request_timestamps if now - t < 60]
    if len(_gemini_request_timestamps) >= config.GEMINI_RPM_BUDGET:
        oldest = _gemini_request_timestamps[0]
        wait = max(60 - (now - oldest), 0)
        if wait > 0:
            print(f"[~] Gemini: at RPM budget ({config.GEMINI_RPM_BUDGET}/min). Pausing {wait:.1f}s...")
            await asyncio.sleep(wait)
        now = time.time()
        _gemini_request_timestamps = [t for t in _gemini_request_timestamps if now - t < 60]
    _gemini_request_timestamps.append(time.time())

def _extract_retry_after(exc: Exception) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    for key in ("retry-after", "Retry-After"):
        val = headers.get(key) if hasattr(headers, "get") else None
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                return None
    return None

def _date_window_filter(
    records: List[Dict[str, Any]],
    target_date_str: str,
    date_window_days: int,
) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for r in records:
        date_diff_days = 0
        if target_date_str and r.get("date"):
            try:
                d1 = datetime.strptime(target_date_str, "%Y-%m-%d")
                d2 = datetime.strptime(r.get("date", ""), "%Y-%m-%d")
                date_diff_days = abs((d1 - d2).days)
            except ValueError:
                date_diff_days = 0
        if date_diff_days <= date_window_days:
            filtered.append(r)
    return filtered


def get_candidate_pool(
    bank_record: Dict[str, Any],
    all_ledger: List[Dict[str, Any]],
    all_gateway: List[Dict[str, Any]],
    date_window_days: int = 5,
    max_candidates: int = 25
) -> List[Dict[str, Any]]:
    """
    Builds the candidate pool the agent searches over. Pulls from BOTH the
    internal ledger and the gateway export -- three-way reconciliation
    (bank <-> ledger <-> gateway) requires the agent to actually see gateway
    candidates, not just ledger ones, or matched_gateway_ids can never be
    populated correctly.

    Same date-window filtering and same tight-match/broad-search logic as
    before, just applied over the union of both sources instead of ledger
    alone. Candidate record_ids already carry a source-identifying prefix
    (LEDG_ / GW_), so no extra tagging is needed for the agent (or for
    sum_check/description_similarity, which are source-agnostic) to tell
    which source a given candidate came from.
    """
    target_amount = float(bank_record["amount"])
    target_date_str = bank_record.get("date", "")

    ledger_filtered = _date_window_filter(all_ledger, target_date_str, date_window_days)
    gateway_filtered = _date_window_filter(all_gateway, target_date_str, date_window_days)
    combined_filtered = ledger_filtered + gateway_filtered

    has_tight_match = any(
        abs(float(l["amount"]) - target_amount) / max(target_amount, 1.0) <= TIGHT_MATCH_THRESHOLD
        for l in combined_filtered
    )

    if has_tight_match:
        # Global rank by amount closeness across both sources -- source doesn't
        # bias this one, the closest candidates win regardless of where they
        # came from, so a single combined-then-truncated list is fine here.
        direct_candidates = [
            l for l in combined_filtered
            if abs(float(l["amount"]) - target_amount) / max(target_amount, 1.0) <= 0.10
        ]
        return sorted(direct_candidates, key=lambda l: abs(float(l["amount"]) - target_amount))[:max_candidates]
    else:
        # Broad/component search (used for sum_check-style multi-part
        # settlements): cap EACH source at max_candidates independently,
        # then combine. A naive "concatenate both sources, then slice to
        # max_candidates" here would silently starve whichever source is
        # listed second (gateway) any time the first source (ledger) alone
        # already fills the cap -- which is exactly what happened on the
        # many_to_one_settlement regression case (BANK_0028) during testing:
        # ledger alone had 30 candidates within tolerance, so gateway
        # candidates never appeared in the pool at all. Capping per-source
        # guarantees the agent always sees candidates from both sides.
        ledger_component = [l for l in ledger_filtered if float(l["amount"]) <= target_amount * 1.05][:max_candidates]
        gateway_component = [l for l in gateway_filtered if float(l["amount"]) <= target_amount * 1.05][:max_candidates]
        return ledger_component + gateway_component


def _extract_invalid_tool_name(exc: Exception) -> Optional[str]:
    """
    Groq validates tool calls server-side. When the model (specifically
    openai/gpt-oss-120b, which uses OpenAI's Harmony format internally and
    can leak its 'commentary' scratch-reasoning channel as a fake tool call)
    emits a call to a tool that isn't registered, Groq rejects the WHOLE
    request with a 400 BEFORE any message is ever returned to us. This means
    it can only be caught here, at the API-call boundary — by the time a
    response object exists to inspect message.tool_calls, this failure mode
    has already either happened (request rejected) or not (request succeeded).
    """
    msg = str(exc)
    match = re.search(r"attempted to call tool '([^']+)' which was not in request\.tools", msg)
    if match:
        return match.group(1)
    return None


async def _call_groq(
    messages: List[ChatCompletionMessageParam],
    max_retries: int,
    temperature: float,
    tools: List[ChatCompletionToolParam],
) -> Any:
    delay = 2.0
    last_exc: Optional[Exception] = None
    hallucination_corrections = 0
    MAX_HALLUCINATION_CORRECTIONS = 3  # separate budget from rate-limit retries

    for attempt in range(max_retries):
        try:
            await _proactive_groq_throttle(estimated_tokens=1500)

            response = await groq_client.chat.completions.create(
                model=config.GROQ_MODEL_NAME,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                max_tokens=config.GROQ_MAX_TOKENS
            )

            usage = getattr(response, "usage", None)
            if usage is not None:
                actual_tokens = getattr(usage, "total_tokens", 0)
                global _groq_tokens_this_minute
                async with _groq_lock:
                    _groq_tokens_this_minute = max(0, _groq_tokens_this_minute - 1500 + actual_tokens)

            return response

        except Exception as e:
            last_exc = e

            # --- Handle the actual failure point: server-side tool validation ---
            invalid_tool = _extract_invalid_tool_name(e)
            if invalid_tool is not None:
                hallucination_corrections += 1
                valid_names = ", ".join(t["function"]["name"] for t in tools)
                print(
                    f"[!] Groq rejected a call to invalid tool '{invalid_tool}' "
                    f"(correction {hallucination_corrections}/{MAX_HALLUCINATION_CORRECTIONS})."
                )
                if hallucination_corrections > MAX_HALLUCINATION_CORRECTIONS:
                    raise RuntimeError(
                        f"TOOL_HALLUCINATION_EXHAUSTED: repeatedly attempted invalid tool '{invalid_tool}'"
                    ) from e

                # No assistant message was ever returned for this failed call,
                # so there's nothing to append except a plain corrective note —
                # this is intentionally NOT a "tool" role message, since there's
                # no preceding tool_call_id in the conversation to respond to.
                messages.append(cast(ChatCompletionMessageParam, {
                    "role": "user",
                    "content": (
                        f"Your previous response attempted to call a tool named "
                        f"'{invalid_tool}', which does not exist. You may ONLY call "
                        f"these tools: {valid_names}. Retry using only a valid tool, "
                        f"or call the decision tool now if you already have enough "
                        f"information to conclude."
                    )
                }))
                await asyncio.sleep(1.0)
                continue

            # --- Existing rate-limit handling, unchanged ---
            err_msg = str(e).lower()
            if "429" in err_msg or "too many requests" in err_msg:
                if attempt == max_retries - 1:
                    raise RuntimeError(RATE_LIMIT_EXHAUSTED) from e
                retry_after = _extract_retry_after(e)
                wait = retry_after if retry_after is not None else delay
                print(f"[!] Groq rate limited (429). Waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries})...")
                await asyncio.sleep(wait)
                delay *= 2.0
            else:
                raise

    raise RuntimeError(RATE_LIMIT_EXHAUSTED) from last_exc


async def _call_gemini(messages: List[ChatCompletionMessageParam], max_retries: int, temperature: float, tools: List[ChatCompletionToolParam]) -> Any:
    delay = 4.0
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            await _gemini_track_and_throttle()
            response = await gemini_client.chat.completions.create(
                model=config.GEMINI_MODEL_NAME,
                messages=messages, # type: ignore[arg-type]
                tools=tools, # type: ignore[arg-type]
                tool_choice="auto",
                temperature=temperature,
                max_tokens=config.GEMINI_MAX_TOKENS
            )
            return response
        except Exception as e:
            err_msg = str(e).lower()
            last_exc = e
            if "429" in err_msg or "resource_exhausted" in err_msg or "quota" in err_msg:
                if attempt == max_retries - 1:
                    raise RuntimeError(RATE_LIMIT_EXHAUSTED) from e
                print(f"[!] Gemini rate limited. Waiting {delay:.1f}s (attempt {attempt + 1}/{max_retries})...")
                await asyncio.sleep(delay)
                delay *= 2.0
            else:
                raise

    raise RuntimeError(RATE_LIMIT_EXHAUSTED) from last_exc


async def call_llm_with_retry(messages: List[ChatCompletionMessageParam], max_retries: int = 7, temperature: float = 0.1, tools: Optional[List[ChatCompletionToolParam]] = None) -> Any:
    """Provider-agnostic dispatch."""
    actual_tools = tools if tools is not None else AGENT_TOOLS
    if config.PROVIDER == "gemini":
        return await _call_gemini(messages, max_retries, temperature, actual_tools)
    return await _call_groq(messages, max_retries, temperature, actual_tools)


async def run_proposer(bank_record: Dict[str, Any], candidates: List[Dict[str, Any]], temperature: float = 0.1) -> Dict[str, Any]:
    record_id = bank_record.get("record_id", "UNKNOWN")

    trimmed_bank_record = {
        "record_id": bank_record.get("record_id"),
        "amount": bank_record.get("amount"),
        "description": bank_record.get("description"),
        "notes": bank_record.get("notes")
    }
    trimmed_candidates = [
        {
            "record_id": c.get("record_id"),
            "amount": c.get("amount"),
            "description": c.get("description"),
        }
        for c in candidates
    ]

    context_data = {
        "bank_record": trimmed_bank_record,
        "candidates": trimmed_candidates
    }

    system_instruction = (
        f"{PROPOSER_SYSTEM_PROMPT}\n\n"
        f"GLOBAL CONSTANT: The maximum allowed fee tolerance for any match (single or sum) is exactly {MATCH_TOLERANCE_PCT}%.\n"
        "CANDIDATE SOURCES: The `candidates` list contains records from TWO different sources mixed "
        "together — the internal ledger and the gateway export. You can tell them apart by their "
        "record_id prefix: ids starting with 'LEDG_' are ledger records, ids starting with 'GW_' are "
        "gateway records. This is a three-way reconciliation (bank <-> ledger <-> gateway): when you "
        "submit your decision, matched_ledger_ids must contain ONLY LEDG_ ids and matched_gateway_ids "
        "must contain ONLY GW_ ids — never mix them, and never put a ledger id in matched_gateway_ids "
        "or vice versa.\n"
        "CRITICAL INSTRUCTION: Never output raw JSON as plain text responses. "
        "When you reach a final verdict, you MUST invoke the `submit_final_decision` tool call.\n"
        "EFFICIENCY INSTRUCTION: If you need both sum_check and description_similarity, "
        "call them together in the SAME response — do not spread tool calls across multiple turns."
    )

    messages: List[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f"Resolve this transaction context:\n{json.dumps(context_data)}"}
    ]

    trace_log: List[Dict[str, Any]] = []
    final_decision: Optional[Dict[str, Any]] = None

    def _write_log(decision: Optional[Dict[str, Any]]) -> None:
        log_file = LOG_DIR / f"{record_id}.json"
        with open(log_file, "w") as f:
            json.dump({
                "bank_record_id": record_id,
                "provider": config.PROVIDER,
                "trace": trace_log,
                "final_decision": decision
            }, f, indent=2)

    turn_count = 0
    try:
        while True:
            turn_count += 1
            if turn_count > MAX_TURNS:
                final_decision = {
                    "status": "flagged",
                    "matched_ledger_ids": [],
                    "matched_gateway_ids": [],
                    "reasoning": f"Exceeded max turns ({MAX_TURNS}) without a final decision.",
                    "confidence": "low"
                }
                break

            response = await call_llm_with_retry(messages, temperature=temperature)
            message = response.choices[0].message
            msg_dict = message.model_dump(exclude_unset=True)
            
            messages.append(cast(ChatCompletionMessageParam, msg_dict))
            trace_log.append(msg_dict)

            if not message.tool_calls:
                content_str = (message.content or "").strip()
                if not content_str:
                    if turn_count < MAX_TURNS:
                        print("  [!] Model produced an empty response without tool calls. Forcing follow-up reminder...")
                        messages.append(cast(ChatCompletionMessageParam, {
                            "role": "user",
                            "content": "You returned an empty response. You must call the `submit_final_decision` tool with your conclusions based on the results you have gathered. Do not return an empty message."
                        }))
                        continue
                    else:
                        final_decision = {
                            "status": "flagged",
                            "matched_ledger_ids": [],
                            "matched_gateway_ids": [],
                            "reasoning": "Model failed to produce a decision after reaching max turns.",
                            "confidence": "low"
                        }
                        break

                try:
                    final_decision = json.loads(content_str)
                except Exception:
                    final_decision = {
                        "status": "flagged",
                        "matched_ledger_ids": [],
                        "matched_gateway_ids": [],
                        "reasoning": "Model produced invalid non-JSON output without tool calls.",
                        "confidence": "low"
                    }
                break

            reached_final = False
            for tool_call in message.tool_calls:
                function_name = tool_call.function.name

                if function_name == "sum_check":
                    try:
                        args_dict = json.loads(tool_call.function.arguments or "{}")
                        print(f"\n  [AGENT TOOL CALL] -> sum_check")
                        print(f"  Target: {args_dict.get('target_amount')}")
                    except:
                        pass
                elif function_name == "description_similarity":
                    try:
                        args_dict = json.loads(tool_call.function.arguments or "{}")
                        print(f"\n  [AGENT TOOL CALL] -> description_similarity")
                    except:
                        pass

                if function_name == "submit_final_decision":
                    try:
                        args_str = tool_call.function.arguments or "{}"
                        final_decision = json.loads(args_str)
                    except Exception as e:
                        final_decision = {"error": str(e), "raw_args": tool_call.function.arguments}
                    reached_final = True
                    break

                function_to_call = AVAILABLE_FUNCTIONS.get(function_name)
                if function_to_call is not None:
                    try:
                        args_str = tool_call.function.arguments or "{}"
                        function_args = json.loads(args_str)
                        
                        # --- INJECT NATIVELY ---
                        if function_name == "sum_check":
                            function_args["candidates"] = candidates
                            
                        tool_result = function_to_call(**function_args)
                    except Exception as e:
                        tool_result = {"error": str(e)}
                else:
                    tool_result = {"error": f"Function '{function_name}' is not recognized."}

                tool_msg: Dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": json.dumps(tool_result)
                }
                messages.append(cast(ChatCompletionMessageParam, tool_msg))
                trace_log.append(tool_msg)

            if reached_final:
                break

            await asyncio.sleep(1.5)

    except RuntimeError as e:
        if str(e) == RATE_LIMIT_EXHAUSTED:
            final_decision = {
                "status": "flagged",
                "matched_ledger_ids": [],
                "matched_gateway_ids": [],
                "reasoning": "Rate limit exhausted after retries.",
                "confidence": "low"
            }
        else:
            raise

    _write_log(final_decision)
    return final_decision or {}