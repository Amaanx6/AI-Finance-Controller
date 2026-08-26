from dotenv import load_dotenv
import re
import os
import time
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, cast, Tuple

from groq import AsyncGroq
from openai import AsyncOpenAI

from backend.app.agent.prompts import PROPOSER_SYSTEM_PROMPT
from backend.app.agent.tools import sum_check, description_similarity
from backend.app.agent import config

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = BASE_DIR / "logs" / "proposer"
LOG_DIR.mkdir(parents=True, exist_ok=True)

MAX_TURNS = config.MAX_TURNS
MATCH_TOLERANCE_PCT = 3.0
TIGHT_MATCH_THRESHOLD = 0.005  

AGENT_TOOLS = [
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
                        "items": {"type": "string"}
                    },
                    "matched_gateway_ids": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "reasoning": {"type": "string", "description": "Concise 1-2 sentence explanation."},
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

class KeyState:
    def __init__(self, provider: str, api_key: str, model_name: str, key_id: str):
        self.provider = provider
        self.api_key = api_key
        self.model_name = model_name
        self.key_id = key_id
        
        self.semaphore = asyncio.Semaphore(1) if provider == "groq" else asyncio.Semaphore(4)
        self.groq_tokens_this_minute = 0
        self.groq_minute_start = time.time()
        self.groq_lock = asyncio.Lock()
        
        self.cooldown_until = 0.0
        
        if provider == "local":
            self.client = AsyncOpenAI(api_key="ollama", base_url=config.LOCAL_API_BASE)
        elif provider == "groq":
            self.client = AsyncGroq(api_key=api_key, max_retries=0)
            
    async def proactive_throttle(self, estimated_tokens=1200):
        if self.provider == "local":
            return
            
        if self.provider == "groq":
            async with self.groq_lock:
                now = time.time()
                if now - self.groq_minute_start >= 60:
                    self.groq_tokens_this_minute = 0
                    self.groq_minute_start = now
                if self.groq_tokens_this_minute + estimated_tokens > config.GROQ_TPM_BUDGET:
                    wait = 60 - (now - self.groq_minute_start)
                    if wait > 0:
                        print(f"[~] Proactive Pacing: {self.key_id} budget near limit ({self.groq_tokens_this_minute} TPM). Pacing {wait:.1f}s...")
                        await asyncio.sleep(wait)
                    self.groq_tokens_this_minute = 0
                    self.groq_minute_start = time.time()
                self.groq_tokens_this_minute += estimated_tokens
            
    def get_load_score(self):
        if self.provider == "local":
            return 999999
        if self.provider == "groq":
            return config.GROQ_TPM_BUDGET - self.groq_tokens_this_minute
        return 0

KEY_STATES = []
for i, (prov, api_key, model) in enumerate(config.KEY_POOL, start=1):
    key_id = f"local_gpu" if prov == "local" else f"groq_key_{i}"
    KEY_STATES.append(KeyState(prov, api_key, model, key_id))

def get_least_loaded_key() -> KeyState:
    now = time.time()
    available = [k for k in KEY_STATES if k.cooldown_until < now]
    if not available:
        return min(KEY_STATES, key=lambda k: k.cooldown_until)
    available.sort(key=lambda k: k.get_load_score(), reverse=True)
    return available[0]

async def _rate_limit_sleep(wait: float):
    await asyncio.sleep(wait)

async def _hallucination_sleep(wait: float):
    await asyncio.sleep(wait)

def _estimate_prompt_tokens(messages: List[Any]) -> int:
    raw_text = json.dumps(messages)
    return max(100, int(len(raw_text) / 3.2))

def _extract_retry_after(exc: Exception, err_msg: str) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers:
        for key in ("retry-after", "Retry-After"):
            val = headers.get(key) if hasattr(headers, "get") else None
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
                    
    if "limit 200000" in err_msg or "tokens per day" in err_msg or "resource_exhausted" in err_msg:
        return 86400.0

    match = re.search(r"try again in (\d+(?:\.\d+)?)s", err_msg)
    if match:
        return float(match.group(1))
        
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
    max_candidates: int = 20
) -> List[Dict[str, Any]]:
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
        direct_candidates = [
            l for l in combined_filtered
            if abs(float(l["amount"]) - target_amount) / max(target_amount, 1.0) <= 0.10
        ]
        return sorted(direct_candidates, key=lambda l: abs(float(l["amount"]) - target_amount))[:6]
    else:
        ledger_component = [l for l in ledger_filtered if float(l["amount"]) <= target_amount * 1.05][:max_candidates]
        gateway_component = [l for l in gateway_filtered if float(l["amount"]) <= target_amount * 1.05][:max_candidates]
        return ledger_component + gateway_component

def _extract_invalid_tool_name(exc: Exception) -> Optional[str]:
    msg = str(exc)
    match = re.search(r"attempted to call tool '([^']+)' which was not in request\.tools", msg)
    if match:
        return match.group(1)
    return None

async def call_llm_with_retry(messages: List[Any], max_retries: int = 7, temperature: float = 0.1, tools: Optional[List[Any]] = None, assigned_key: Optional[KeyState] = None) -> Any:
    actual_tools = tools if tools is not None else AGENT_TOOLS
    delay = 2.0
    current_key = assigned_key or get_least_loaded_key()
    last_exc = None
    hallucination_corrections = 0
    MAX_HALLUCINATION_CORRECTIONS = 3

    for attempt in range(max_retries):
        if current_key.cooldown_until > time.time():
            wait_time = current_key.cooldown_until - time.time()
            if assigned_key or all(k.cooldown_until > time.time() for k in KEY_STATES):
                await _rate_limit_sleep(wait_time)
            else:
                current_key = get_least_loaded_key()

        est_tokens = _estimate_prompt_tokens(messages)
        safe_max_tokens = min(config.GROQ_MAX_TOKENS, max(384, 7500 - est_tokens)) if current_key.provider == "groq" else 4096

        try:
            async with current_key.semaphore:
                await current_key.proactive_throttle(estimated_tokens=est_tokens + safe_max_tokens)
                
                response = await current_key.client.chat.completions.create(
                    model=current_key.model_name,
                    messages=messages,
                    tools=actual_tools,
                    tool_choice="auto",
                    temperature=temperature,
                    max_tokens=safe_max_tokens
                )

                if current_key.provider == "groq":
                    usage = getattr(response, "usage", None)
                    if usage is not None:
                        actual_tokens = getattr(usage, "total_tokens", 0)
                        async with current_key.groq_lock:
                            current_key.groq_tokens_this_minute = max(0, current_key.groq_tokens_this_minute - (est_tokens + safe_max_tokens) + actual_tokens)

                return response
                
        except Exception as e:
            last_exc = e
            err_msg = str(e).lower()
            
            invalid_tool = _extract_invalid_tool_name(e)
            if invalid_tool is not None:
                hallucination_corrections += 1
                valid_names = ", ".join(t["function"]["name"] for t in actual_tools)
                print(f"[!] {current_key.key_id} rejected invalid tool '{invalid_tool}' ({hallucination_corrections}/{MAX_HALLUCINATION_CORRECTIONS}).")
                if hallucination_corrections > MAX_HALLUCINATION_CORRECTIONS:
                    raise RuntimeError(f"TOOL_HALLUCINATION_EXHAUSTED: repeatedly attempted invalid tool '{invalid_tool}'") from e

                messages.append(cast(Any, {
                    "role": "user",
                    "content": f"Invalid tool '{invalid_tool}'. Valid tools: {valid_names}. Call submit_final_decision if ready."
                }))
                await _hallucination_sleep(1.0)
                continue

            if "failed to parse tool call" in err_msg or "tool_use_failed" in err_msg or "json" in err_msg:
                hallucination_corrections += 1
                if hallucination_corrections > MAX_HALLUCINATION_CORRECTIONS:
                    raise RuntimeError("TOOL_HALLUCINATION_EXHAUSTED: malformed JSON output.") from e
                messages.append(cast(Any, {
                    "role": "user",
                    "content": "Tool call JSON was truncated or malformed. Submit concise, valid JSON."
                }))
                await _hallucination_sleep(1.0)
                continue

            if any(term in err_msg for term in ["429", "413", "too many requests", "request too large", "rate_limit_exceeded"]):
                if attempt == max_retries - 1:
                    raise RuntimeError(RATE_LIMIT_EXHAUSTED) from e
                
                server_suggested = _extract_retry_after(e, err_msg)
                wait_val = server_suggested if server_suggested is not None else delay
                cooldown_duration = max(wait_val, 30.0)
                
                current_key.cooldown_until = time.time() + cooldown_duration
                print(f"[!] {current_key.key_id} throttled/paced. Cooling down {cooldown_duration:.1f}s. Switching keys...")
                
                current_key = get_least_loaded_key()
                await _rate_limit_sleep(min(wait_val, 1.0))
                delay *= 2.0
            else:
                print(f"[!] Unhandled exception from LLM provider: {e}")
                if attempt == max_retries - 1:
                    raise
                await _rate_limit_sleep(delay)
                delay *= 2.0

    raise RuntimeError(RATE_LIMIT_EXHAUSTED) from last_exc

async def run_proposer(bank_record: Dict[str, Any], candidates: List[Dict[str, Any]], temperature: float = 0.1, message_history: Optional[List[Dict[str, Any]]] = None, assigned_key: Optional[KeyState] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
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
        f"GLOBAL CONSTANT: Maximum allowed fee tolerance is {MATCH_TOLERANCE_PCT}%.\n"
        "CANDIDATES: Contains 'LEDG_' (internal ledger) and 'GW_' (gateway) records. "
        "matched_ledger_ids must contain ONLY LEDG_ ids; matched_gateway_ids ONLY GW_ ids.\n"
        "EFFICIENCY INSTRUCTION: Keep reasoning CONCISE (1-2 sentences). "
        "Call submit_final_decision tool when finished."
    )

    if message_history is not None:
        messages = list(message_history)
    else:
        messages = [
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
                "provider": assigned_key.provider if assigned_key else config.PROVIDER,
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
                    "reasoning": f"Exceeded max turns ({MAX_TURNS}).",
                    "confidence": "low"
                }
                break

            response = await call_llm_with_retry(messages, temperature=temperature, assigned_key=assigned_key)
            message = response.choices[0].message
            msg_dict = message.model_dump(exclude_unset=True)
            
            messages.append(msg_dict)
            trace_log.append(msg_dict)

            if not message.tool_calls:
                content_str = (message.content or "").strip()
                if not content_str:
                    if turn_count < MAX_TURNS:
                        messages.append(cast(Any, {
                            "role": "user",
                            "content": "Empty response. Call `submit_final_decision`."
                        }))
                        continue
                    else:
                        final_decision = {
                            "status": "flagged",
                            "matched_ledger_ids": [],
                            "matched_gateway_ids": [],
                            "reasoning": "Turn limit reached.",
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
                        "reasoning": "Model produced invalid non-JSON output.",
                        "confidence": "low"
                    }
                break

            reached_final = False
            for tool_call in message.tool_calls:
                function_name = tool_call.function.name

                if function_name == "sum_check":
                    try:
                        args_dict = json.loads(tool_call.function.arguments or "{}")
                        print(f"\n  [AGENT TOOL CALL] -> sum_check (Target: {args_dict.get('target_amount')})")
                    except:
                        pass
                elif function_name == "description_similarity":
                    print(f"\n  [AGENT TOOL CALL] -> description_similarity")

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
                        if function_name == "sum_check":
                            function_args["candidates"] = candidates
                            
                        tool_result = function_to_call(**function_args)
                    except Exception as e:
                        tool_result = {"error": str(e)}
                else:
                    tool_result = {"error": f"Function '{function_name}' not recognized."}

                tool_msg: Dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": json.dumps(tool_result)
                }
                messages.append(tool_msg)
                trace_log.append(tool_msg)

            if reached_final:
                break

            await asyncio.sleep(1.0)

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
    return final_decision or {}, messages