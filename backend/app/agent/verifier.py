import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.app.agent.prompts import VERIFIER_SYSTEM_PROMPT
from backend.app.agent.tools import sum_check, description_similarity
from backend.app.agent.proposer import call_llm_with_retry, AGENT_TOOLS, MATCH_TOLERANCE_PCT, RATE_LIMIT_EXHAUSTED
from backend.app.agent import config

logger = logging.getLogger("verifier")

MAX_VERIFIER_TURNS = 3

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = BASE_DIR / "logs" / "verifier"
LOG_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DIR = BASE_DIR / "logs" / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "sim_cache.json"

_desc_sim_cache = {}
if CACHE_FILE.exists():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _desc_sim_cache = json.load(f)
    except Exception:
        _desc_sim_cache = {}

def cached_description_similarity(desc_a: str, desc_b: str) -> Dict[str, Any]:
    key = str(tuple(sorted([desc_a or "", desc_b or ""])))
    if key not in _desc_sim_cache:
        result = description_similarity(desc_a, desc_b)
        _desc_sim_cache[key] = result
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(_desc_sim_cache, f)
        except Exception:
            pass
        return result
    return _desc_sim_cache[key]

# Verifier tools: AGENT_TOOLS[:2] plus submit_verifier_decision.
# Note: "commentary" is NOT a registered tool schema. We catch it defensively when leaked.
VERIFIER_TOOLS = AGENT_TOOLS[:2] + [
    {
        "type": "function",
        "function": {
            "name": "submit_verifier_decision",
            "description": "REQUIRED: call when finished to submit your final verification decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["agree", "disagree"]
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Evidence-backed explanation. If disagreeing, provide the specific objection."
                    }
                },
                "required": ["decision", "reasoning"]
            }
        }
    }
]

VERIFIER_AVAILABLE_FUNCTIONS = {
    "sum_check": sum_check,
    "description_similarity": cached_description_similarity,
}


async def run_verifier(
    bank_record: Dict[str, Any], 
    candidates: List[Dict[str, Any]], 
    proposed_match: Dict[str, Any], 
    proposer_reasoning: str,
    provider: Optional[str] = None
) -> Dict[str, Any]:
    record_id = bank_record.get("record_id", "UNKNOWN")
    active_provider = provider or config.PROVIDER

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
        for c in candidates[:25]
    ]

    context = {
        "bank_record": trimmed_bank_record,
        "all_candidates": trimmed_candidates,
        "proposed_match": proposed_match,
        "proposer_reasoning": proposer_reasoning
    }

    system_instruction = (
        f"{VERIFIER_SYSTEM_PROMPT}\n\n"
        f"GLOBAL CONSTANT: The maximum allowed fee tolerance is {MATCH_TOLERANCE_PCT}%.\n"
        "EFFICIENCY INSTRUCTION: Call multiple tools in the SAME response if verifying multiple components. "
        "CRITICAL INSTRUCTION: When you are ready to conclude, you MUST invoke the `submit_verifier_decision` tool call. Do not output raw JSON text."
    )

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f"Verify this proposed match:\n{json.dumps(context)}"}
    ]

    trace_log = []
    final_decision = None
    commentary_count = 0

    def _write_log(decision):
        log_file = LOG_DIR / f"{record_id}.json"
        with open(log_file, "w") as f:
            json.dump({
                "bank_record_id": record_id,
                "provider": active_provider,
                "trace": trace_log,
                "final_decision": decision
            }, f, indent=2)

    turn_count = 0
    try:
        while True:
            turn_count += 1
            if turn_count > MAX_VERIFIER_TURNS:
                final_decision = {
                    "decision": "disagree",
                    "reasoning": f"Verifier exhausted {MAX_VERIFIER_TURNS} turns without a verdict."
                }
                break

            response = await call_llm_with_retry(messages, temperature=0.1, tools=VERIFIER_TOOLS) #type: ignore
            message = response.choices[0].message

            msg_dump = message.model_dump(exclude_unset=True)
            messages.append(msg_dump)
            trace_log.append(msg_dump)

            if not message.tool_calls:
                content_str = (message.content or "").strip()
                if not content_str:
                    final_decision = {"decision": "disagree", "reasoning": "Verifier produced empty response."}
                    break
                
                try:
                    if "{" in content_str:
                        json_str = content_str[content_str.find("{"):content_str.rfind("}")+1]
                        parsed = json.loads(json_str)
                        final_decision = {
                            "decision": parsed.get("decision", "disagree"),
                            "reasoning": parsed.get("reasoning", content_str)
                        }
                        break
                except Exception:
                    pass

                messages.append({
                    "role": "user",
                    "content": "You returned plain text. You MUST call the `submit_verifier_decision` tool."
                })
                continue

            reached_final_via_tool = False
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                
                # --- DEFENSIVE DISCARD & RECOVERY FOR LEAKED "commentary" ---
                if fn_name == "commentary":
                    commentary_count += 1
                    raw_args = tool_call.function.arguments
                    print(f"\n[!] WARNING: Caught leaked 'commentary' tool call (occurrence #{commentary_count}) for record {record_id}.")
                    print(f"[!] Received raw arguments: {raw_args}")
                    
                    if commentary_count > 2:
                        print(f"[!] 'commentary' emission exceeded threshold (>2). Aborting loop and flagging record.")
                        final_decision = {"decision": "disagree", "reasoning": "Exceeded commentary loop limit; treated as invalid reasoning loop."}
                        reached_final_via_tool = True
                        break

                    tool_result = {
                        "error": "commentary is not a valid tool. Please use submit_verifier_decision or one of the provided tools (sum_check, description_similarity)."
                    }
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": fn_name,
                        "content": json.dumps(tool_result)
                    }
                    messages.append(tool_msg)
                    trace_log.append(tool_msg)
                    continue

                if fn_name == "submit_verifier_decision":
                    try:
                        args = json.loads(tool_call.function.arguments or "{}")
                        final_decision = {
                            "decision": args.get("decision", "disagree"),
                            "reasoning": args.get("reasoning", "No reasoning provided.")
                        }
                    except Exception:
                        final_decision = {"decision": "disagree", "reasoning": "Malformed submit args."}
                    reached_final_via_tool = True
                    break

                fn_callable = VERIFIER_AVAILABLE_FUNCTIONS.get(fn_name)
                if fn_callable:
                    try:
                        args_dict = json.loads(tool_call.function.arguments or "{}")
                        if fn_name == "sum_check":
                            args_dict["candidates"] = candidates
                            
                        tool_result = fn_callable(**args_dict)
                    except Exception as e:
                        tool_result = {"error": str(e)}
                else:
                    tool_result = {"error": f"Function '{fn_name}' not available to verifier."}

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": fn_name,
                    "content": json.dumps(tool_result)
                }
                messages.append(tool_msg)
                trace_log.append(tool_msg)
            
            if reached_final_via_tool:
                break
                
            await asyncio.sleep(1.0)

    except RuntimeError as e:
        if str(e) == RATE_LIMIT_EXHAUSTED:
            final_decision = {"decision": "disagree", "reasoning": "Rate limit exhausted."}
        else:
            raise

    if not final_decision or "decision" not in final_decision:
        final_decision = {"decision": "disagree", "reasoning": "Default disagree fallback."}

    _write_log(final_decision)
    return final_decision