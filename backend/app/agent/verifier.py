import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.app.agent.prompts import VERIFIER_SYSTEM_PROMPT
from backend.app.agent.tools import sum_check, description_similarity
from backend.app.agent.proposer import call_llm_with_retry, MATCH_TOLERANCE_PCT, RATE_LIMIT_EXHAUSTED
from backend.app.agent import config

MAX_VERIFIER_TURNS = 3

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = BASE_DIR / "logs" / "verifier"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 1. Lightweight in-memory cache for deterministic string similarity checks
_desc_sim_cache = {}

def cached_description_similarity(desc_a: str, desc_b: str) -> Dict[str, Any]:
    key = tuple(sorted([desc_a or "", desc_b or ""]))
    if key not in _desc_sim_cache:
        _desc_sim_cache[key] = description_similarity(desc_a, desc_b)
    return _desc_sim_cache[key]

# 2. Bind the tools available to the Verifier agent
VERIFIER_AVAILABLE_FUNCTIONS = {
    "sum_check": sum_check,
    "description_similarity": cached_description_similarity,
}


def run_verifier(
    bank_record: Dict[str, Any], 
    candidates: List[Dict[str, Any]], 
    proposed_match: Dict[str, Any], 
    proposer_reasoning: str,
    provider: Optional[str] = None
) -> Dict[str, Any]:
    record_id = bank_record.get("record_id", "UNKNOWN")
    active_provider = provider or config.PROVIDER

    context = {
        "bank_record": bank_record,
        "all_candidates": candidates,
        "proposed_match": proposed_match,
        "proposer_reasoning": proposer_reasoning
    }

    system_instruction = (
        f"{VERIFIER_SYSTEM_PROMPT}\n\n"
        f"GLOBAL CONSTANT: The maximum allowed fee tolerance is {MATCH_TOLERANCE_PCT}%.\n"
        "EFFICIENCY INSTRUCTION: Call multiple tools in the SAME response if verifying multiple components. "
        "When you are ready to conclude, DO NOT call a tool. Instead, output your final decision purely as a JSON code block in the message content."
    )

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f"Verify this proposed match:\n{json.dumps(context)}"}
    ]

    trace_log = []
    final_decision = None

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

            # The verifier loops as a true agent
            response = call_llm_with_retry(messages) #type: ignore
            message = response.choices[0].message

            # FIX FOR GEMINI 400: Use model_dump to safely echo back the tool calls.
            # This preserves `thought_signature` and all other provider-specific hidden fields.
            msg_dump = message.model_dump(exclude_unset=True)
            messages.append(msg_dump)
            trace_log.append(msg_dump)

            # If no tools were called, this must be the final JSON verdict turn
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
                    else:
                        raise ValueError("No JSON block found")
                except Exception as e:
                    final_decision = {"decision": "disagree", "reasoning": f"Failed to parse verifier output as JSON: {content_str}"}
                break

            # Execute tool calls natively
            reached_final_via_tool = False
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                
                # Resiliency: If the LLM sees `submit_final_decision` in AGENT_TOOLS and uses it instead of raw JSON
                if fn_name == "submit_final_decision":
                    try:
                        args = json.loads(tool_call.function.arguments or "{}")
                        final_decision = {
                            "decision": "agree" if args.get("status") in ["matched", "suggested_match"] else "disagree",
                            "reasoning": args.get("reasoning", "Extracted from submit_final_decision.")
                        }
                    except Exception:
                        final_decision = {"decision": "disagree", "reasoning": "Malformed submit tool arguments."}
                    reached_final_via_tool = True
                    break

                fn_callable = VERIFIER_AVAILABLE_FUNCTIONS.get(fn_name)
                if fn_callable:
                    try:
                        args_dict = json.loads(tool_call.function.arguments or "{}")
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
                
            time.sleep(1.0)

    except RuntimeError as e:
        if str(e) == RATE_LIMIT_EXHAUSTED:
            final_decision = {"decision": "disagree", "reasoning": "Rate limit exhausted."}
        else:
            raise

    if not final_decision or "decision" not in final_decision:
        final_decision = {"decision": "disagree", "reasoning": "Default disagree fallback."}

    _write_log(final_decision)
    return final_decision