from dotenv import load_dotenv
import os
import time
import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, cast

from groq import Groq
from groq.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from backend.app.agent.prompts import PROPOSER_SYSTEM_PROMPT
from backend.app.agent.tools import sum_check, description_similarity

load_dotenv()

client = Groq(api_key=os.environ.get("GROQ_API_KEY"), max_retries=0)
MODEL_NAME = "openai/gpt-oss-120b"

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = BASE_DIR / "logs" / "proposer"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TPM_BUDGET = 7000
MAX_TURNS = 4

# SINGLE SOURCE OF TRUTH FOR MATCH TOLERANCE
MATCH_TOLERANCE_PCT = 3.0
TIGHT_MATCH_THRESHOLD = 0.005  # 0.5%

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
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "record_id": {"type": "string"},
                                "amount": {"type": "number"}
                            },
                            "required": ["record_id", "amount"]
                        }
                    },
                    "fee_tolerance_pct": {"type": "number", "default": MATCH_TOLERANCE_PCT}
                },
                "required": ["target_amount", "candidates"]
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
                    "matched_ledger_ids": {"type": "array", "items": {"type": "string"}},
                    "matched_gateway_ids": {"type": "array", "items": {"type": "string"}},
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

_tokens_this_minute = 0
_minute_start = time.time()

def _track_and_throttle(usage_tokens: int) -> None:
    global _tokens_this_minute, _minute_start
    now = time.time()
    if now - _minute_start >= 60:
        _tokens_this_minute = 0
        _minute_start = now
    _tokens_this_minute += usage_tokens
    if _tokens_this_minute > TPM_BUDGET:
        wait = max(60 - (now - _minute_start), 0)
        if wait > 0:
            print(f"[~] Approaching TPM budget ({_tokens_this_minute}). Pausing {wait:.1f}s...")
            time.sleep(wait)
        _tokens_this_minute = 0
        _minute_start = time.time()

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

def get_candidate_pool(
    bank_record: Dict[str, Any],
    all_ledger: List[Dict[str, Any]],
    date_window_days: int = 5,
    max_candidates: int = 25  # Capped at 25 to keep the prompt payload efficient
) -> List[Dict[str, Any]]:
    target_amount = float(bank_record["amount"])
    target_date_str = bank_record.get("date", "")
    
    date_filtered: List[Dict[str, Any]] = []
    for l in all_ledger:
        date_diff_days = 0
        if target_date_str and l.get("date"):
            try:
                d1 = datetime.strptime(target_date_str, "%Y-%m-%d")
                d2 = datetime.strptime(l.get("date", ""), "%Y-%m-%d")
                date_diff_days = abs((d1 - d2).days)
            except ValueError:
                date_diff_days = 0
        
        if date_diff_days <= date_window_days:
            date_filtered.append(l)

    # STRICT 0.5% THRESHOLD: Check if an exact/near-exact match exists.
    has_tight_match = any(
        abs(float(l["amount"]) - target_amount) / max(target_amount, 1.0) <= TIGHT_MATCH_THRESHOLD
        for l in date_filtered
    )

    if has_tight_match:
        # A tight 1:1 match exists. Return it along with close decoys within 10%.
        direct_candidates = [
            l for l in date_filtered
            if abs(float(l["amount"]) - target_amount) / max(target_amount, 1.0) <= 0.10
        ]
        return sorted(direct_candidates, key=lambda l: abs(float(l["amount"]) - target_amount))[:max_candidates]
    else:
        # No tight match. Pull ALL ledger records in window up to target_amount * 1.05.
        component_candidates = [
            l for l in date_filtered
            if float(l["amount"]) <= target_amount * 1.05
        ]
        return component_candidates[:max_candidates]

def call_groq_with_retry(messages: List[ChatCompletionMessageParam], max_retries: int = 5) -> Any:
    delay = 2.0
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                tools=AGENT_TOOLS,
                tool_choice="auto",
                max_tokens=1024  # <-- PREVENTS GROQ TPM 413 EXPLOSIONS
            )
            usage = getattr(response, "usage", None)
            if usage is not None:
                _track_and_throttle(getattr(usage, "total_tokens", 0))
            return response
        except Exception as e:
            err_msg = str(e).lower()
            last_exc = e
            if "429" in err_msg or "too many requests" in err_msg:
                if attempt == max_retries - 1:
                    raise RuntimeError(RATE_LIMIT_EXHAUSTED) from e
                retry_after = _extract_retry_after(e)
                wait = retry_after if retry_after is not None else delay
                print(f"[!] Rate limited (429). Waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
                delay *= 2.0
            else:
                raise

    raise RuntimeError(RATE_LIMIT_EXHAUSTED) from last_exc

def run_proposer(bank_record: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    record_id = bank_record.get("record_id", "UNKNOWN")

    trimmed_bank_record = {
        "record_id": bank_record.get("record_id"),
        "amount": bank_record.get("amount"),
        "description": bank_record.get("description"),
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

            response = call_groq_with_retry(messages)
            message = response.choices[0].message

            msg_dict: Dict[str, Any] = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    } for tc in message.tool_calls
                ]

            messages.append(cast(ChatCompletionMessageParam, msg_dict))
            trace_log.append(msg_dict)

            if not message.tool_calls:
                try:
                    raw_content = message.content or "{}"
                    final_decision = json.loads(raw_content)
                except Exception:
                    final_decision = {"error": "No tool call issued", "raw_content": message.content}
                break

            reached_final = False
            for tool_call in message.tool_calls:
                function_name = tool_call.function.name
                
                # --- LOGGING TOOL INVOCATIONS ---
                if function_name == "sum_check":
                    try:
                        args_dict = json.loads(tool_call.function.arguments or "{}")
                        print(f"\n  [AGENT TOOL CALL] -> sum_check")
                        print(f"  Target: {args_dict.get('target_amount')}")
                        print(f"  Candidates supplied to sum_check:")
                        for c in args_dict.get('candidates', []):
                            print(f"    - {c.get('record_id')}: {c.get('amount')}")
                    except:
                        pass
                elif function_name == "description_similarity":
                    try:
                        args_dict = json.loads(tool_call.function.arguments or "{}")
                        print(f"\n  [AGENT TOOL CALL] -> description_similarity")
                        print(f"  Comparing: '{args_dict.get('desc_a')}' vs '{args_dict.get('desc_b')}'")
                    except:
                        pass
                # ---------------------------------

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

            time.sleep(1.5)

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


if __name__ == "__main__":
    sample_dir = BASE_DIR / "backend" / "app" / "data_generation" / "samples"
    bank_csv = sample_dir / "bank_statement.csv"
    ledger_csv = sample_dir / "internal_ledger.csv"

    if not bank_csv.exists() or not ledger_csv.exists():
        print("[!] Dataset missing. Run `python -m backend.app.data_generation.generator` first.")
    else:
        with open(bank_csv, mode="r", encoding="utf-8") as f:
            bank_records = list(csv.DictReader(f))

        with open(ledger_csv, mode="r", encoding="utf-8") as f:
            all_ledger = list(csv.DictReader(f))

        for item in bank_records:
            item["amount"] = float(item["amount"])
        for item in all_ledger:
            item["amount"] = float(item["amount"])

        target_ids = ["BANK_0028", "BANK_0042"]
        test_cases = [b for b in bank_records if b.get("record_id") in target_ids]

        if not test_cases:
            test_cases = bank_records[:2]

        for idx, target in enumerate(test_cases, 1):
            candidates = get_candidate_pool(target, all_ledger)

            print(f"=================================================")
            print(f"RUNNING REAL CASE {idx}: {target.get('record_id')}")
            print(f"Target: Amount={target.get('amount')}, Desc='{target.get('description')}', Date='{target.get('date', 'N/A')}'")
            print(f"Pre-filtered Candidates Count: {len(candidates)}")
            print(f"FULL CANDIDATE POOL HANDED TO AGENT:")
            for c in candidates:
                print(f"  - {c.get('record_id')}: Amount={c.get('amount')}, Date={c.get('date', 'N/A')}, Desc='{c.get('description', '')}'")
            print(f"=================================================")

            decision = run_proposer(target, candidates)
            print(f"\nFINAL DECISION {idx}:")
            print(json.dumps(decision, indent=2))
            print("\n")

            if idx < len(test_cases):
                time.sleep(3)