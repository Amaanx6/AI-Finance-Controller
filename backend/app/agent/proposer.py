from dotenv import load_dotenv
import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, cast

from groq import Groq
from groq.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam
from backend.app.agent.prompts import PROPOSER_SYSTEM_PROMPT
from backend.app.agent.tools import sum_check, description_similarity

load_dotenv()

# Initialize Groq client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
MODEL_NAME = "openai/gpt-oss-120b"

# Ensure logging directory exists
LOG_DIR = Path(__file__).parent.parent.parent.parent / "logs" / "proposer"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------
# Tool Schemas for Groq (Explicitly typed for Pylance)
# ---------------------------------------------------------
AGENT_TOOLS: List[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "sum_check",
            "description": "Finds subsets of candidate records whose amounts sum to the target amount (accounting for up to a 3% fee).",
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
                    "fee_tolerance_pct": {"type": "number", "default": 3.0}
                },
                "required": ["target_amount", "candidates"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "description_similarity",
            "description": "Computes a normalized similarity score (0.0 to 1.0) between two transaction descriptions.",
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
            "description": "REQUIRED: Call this tool when you have finished all checks to submit your final decision.",
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
                    "reasoning": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"]
                    }
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

def run_proposer(bank_record: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Runs the LLM loop for a single ambiguous/unresolved record."""
    record_id = bank_record.get("record_id", "UNKNOWN")
    
    context_data = {
        "bank_record": bank_record,
        "candidates": candidates
    }

    # Add explicit directive to use the function call for final output
    system_instruction = (
        f"{PROPOSER_SYSTEM_PROMPT}\n\n"
        "CRITICAL INSTRUCTION: Never output raw JSON as plain text responses. "
        "When you reach a final verdict, you MUST invoke the `submit_final_decision` tool call."
    )

    # Annotated explicitly to fix ChatCompletionMessageParam type checking
    messages: List[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": f"Resolve this transaction context:\n{json.dumps(context_data, indent=2)}"}
    ]

    trace_log: List[Dict[str, Any]] = []
    final_decision: Optional[Dict[str, Any]] = None

    while True:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=AGENT_TOOLS,
            tool_choice="auto"
        )

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
                # Guard against message.content being None
                raw_content = message.content or "{}"
                final_decision = json.loads(raw_content)
            except Exception:
                final_decision = {"error": "No tool call issued", "raw_content": message.content}
            break

        # Process tool calls
        for tool_call in message.tool_calls:
            function_name = tool_call.function.name
            
            # Terminal condition: Model submitted final decision via tool
            if function_name == "submit_final_decision":
                try:
                    args_str = tool_call.function.arguments or "{}"
                    final_decision = json.loads(args_str)
                except Exception as e:
                    final_decision = {"error": str(e), "raw_args": tool_call.function.arguments}
                
                # Save trace log & exit loop
                log_file = LOG_DIR / f"{record_id}.json"
                with open(log_file, "w") as f:
                    json.dump({
                        "bank_record_id": record_id,
                        "trace": trace_log,
                        "final_decision": final_decision
                    }, f, indent=2)

                return final_decision or {}

            # Execute standard tools safely
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

    # Save log if loop breaks without tool call
    log_file = LOG_DIR / f"{record_id}.json"
    with open(log_file, "w") as f:
        json.dump({
            "bank_record_id": record_id,
            "trace": trace_log,
            "final_decision": final_decision
        }, f, indent=2)

    return final_decision or {}

# ---------------------------------------------------------
# Test Execution Block
# ---------------------------------------------------------
if __name__ == "__main__":
    bank_record_1 = {
        "record_id": "B-101",
        "date": "2023-10-01",
        "amount": 100.00,
        "description": "AWS CLOUD HOSTING SERVICES RECURRING",
        "reference_number": ""
    }
    candidates_1 = [
        {"source": "ledger", "record_id": "L-999", "date": "2023-10-01", "amount": 100.00, "description": "STARBUCKS STORE #1209 COFFEE"},
        {"source": "ledger", "record_id": "L-101", "date": "2023-09-30", "amount": 102.00, "description": "AMAZON WEB SERVICES"}
    ]

    bank_record_2 = {
        "record_id": "B-201",
        "date": "2023-10-05",
        "amount": 195.00,
        "description": "Stripe Settlement Payout"
    }
    candidates_2 = [
        {"source": "ledger", "record_id": "L-201", "amount": 50.00},
        {"source": "ledger", "record_id": "L-202", "amount": 50.00},
        {"source": "ledger", "record_id": "L-203", "amount": 100.00},
        {"source": "ledger", "record_id": "L-204", "amount": 80.00}
    ]

    print("Running Test Case 1 (Decoy Detection)...")
    result_1 = run_proposer(bank_record_1, candidates_1)
    print("\nFINAL DECISION 1:", json.dumps(result_1, indent=2))
    
    print("\n-------------------------------------------------\n")

    print("Running Test Case 2 (Many-to-One Subset Sum)...")
    result_2 = run_proposer(bank_record_2, candidates_2)
    print("\nFINAL DECISION 2:", json.dumps(result_2, indent=2))