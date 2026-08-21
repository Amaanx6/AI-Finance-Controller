from itertools import combinations
import re
from typing import Any, Dict, List
import difflib

try:
    from rapidfuzz import fuzz 
    HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    HAS_RAPIDFUZZ = False


def sum_check(
    target_amount: float,
    candidates: List[Dict[str, Any]],
    fee_tolerance_pct: float = 3.0
) -> Dict[str, Any]:
    """
    Finds subsets of 2-4 candidate records whose amounts sum to within 
    fee_tolerance_pct of target_amount (accounting for fee deduction: Sum - Fee = Target).
    
    Returns all matching subsets ranked by closest match / lowest fee variance.
    """
    if target_amount <= 0 or not candidates:
        return {"match_found": False, "count": 0, "matches": []}

    valid_matches = []

    # Evaluate subsets of size 2, 3, and 4
    max_k = min(5, len(candidates) + 1)
    for k in range(2, max_k):
        for combo in combinations(candidates, k):
            cand_sum = round(sum(c["amount"] for c in combo), 2)
            
            # Settlement calculation: Fee = Candidate Sum - Bank Target
            fee_amount = round(cand_sum - target_amount, 2)
            
            if cand_sum > 0:
                fee_pct = (fee_amount / cand_sum) * 100.0
            else:
                fee_pct = -1.0

            # Valid match if fee is non-negative and within fee_tolerance_pct
            # (allowing a tiny -0.01% float rounding tolerance)
            if -0.01 <= fee_pct <= fee_tolerance_pct:
                valid_matches.append({
                    "candidate_ids": [c["record_id"] for c in combo],
                    "candidate_sum": cand_sum,
                    "target_amount": target_amount,
                    "fee_amount": fee_amount,
                    "fee_percentage": round(fee_pct, 2),
                    "subset_size": k,
                    "difference": round(abs(fee_amount), 2)
                })

    # Rank by lowest fee percentage, then smallest subset size
    valid_matches.sort(key=lambda x: (x["fee_percentage"], x["subset_size"]))

    return {
        "match_found": len(valid_matches) > 0,
        "count": len(valid_matches),
        "matches": valid_matches
    }


def description_similarity(desc_a: str, desc_b: str) -> Dict[str, Any]:
    """
    Computes a normalized similarity score (0.0 to 1.0) and structural explanation
    between two transaction descriptions.

    Metric Choice Explanation:
    Rapidfuzz `token_set_ratio` (with difflib fallback) is chosen over sentence-transformers.
    It has zero cold-start model download overhead, executes in microseconds,
    and handles word order permutations ("RZRPY SETL 08/19" vs "Razorpay settlement #08/19")
    exceptionally well for financial text without hallucinating semantic similarity on numbers.
    """
    if not desc_a or not desc_b:
        return {
            "similarity_score": 0.0,
            "explanation": "One or both description strings are empty."
        }

    str_a = desc_a.strip().lower()
    str_b = desc_b.strip().lower()

    # Extract tokens and numeric identifiers
    tokens_a = set(re.findall(r'\b[a-z0-9]+\b', str_a))
    tokens_b = set(re.findall(r'\b[a-z0-9]+\b', str_b))
    shared_tokens = sorted(list(tokens_a.intersection(tokens_b)))

    nums_a = set(re.findall(r'\b\d+\b', str_a))
    nums_b = set(re.findall(r'\b\d+\b', str_b))
    shared_nums = sorted(list(nums_a.intersection(nums_b)))

    # Zero shared words AND zero shared numbers means completely unrelated
    if not shared_tokens and not shared_nums:
        raw_score = 0.0
    else:
        if HAS_RAPIDFUZZ:
            raw_score = fuzz.token_set_ratio(str_a, str_b) / 100.0 # type: ignore
        else:
            token_overlap = len(shared_tokens) / min(len(tokens_a), len(tokens_b))
            seq_ratio = difflib.SequenceMatcher(None, str_a, str_b).ratio()
            raw_score = max(token_overlap, seq_ratio)

    # Build human-readable breakdown for LLM explanation
    explanation_parts = []
    if shared_tokens:
        explanation_parts.append(f"Shared terms: [{', '.join(shared_tokens)}]")
    else:
        explanation_parts.append("No shared textual tokens")

    if shared_nums:
        explanation_parts.append(f"Shared reference numbers: [{', '.join(shared_nums)}]")

    explanation_parts.append(f"Token overlap score: {raw_score:.2f}")

    return {
        "similarity_score": round(raw_score, 4),
        "explanation": "; ".join(explanation_parts)
    }