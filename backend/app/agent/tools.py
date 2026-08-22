from itertools import combinations
import re
from typing import Any, Dict, List

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    HAS_RAPIDFUZZ = False

# Hard cap on results returned to the caller/LLM, regardless of how many
# valid subsets exist. Keeps tool-result payloads small and bounded.
MAX_RESULTS = 5

# Safety cap on total combinations evaluated, so a larger future candidate
# pool can't make this hang or blow up combinatorially.
MAX_COMBINATIONS_EVALUATED = 50_000


def sum_check(
    target_amount: float,
    candidates: List[Dict[str, Any]],
    fee_tolerance_pct: float = 3.0
) -> Dict[str, Any]:
    """
    Finds subsets of 2-4 candidate records whose amounts sum to within
    fee_tolerance_pct of target_amount (accounting for fee deduction: Sum - Fee = Target).

    Returns at most MAX_RESULTS matching subsets, ranked by closeness
    (smallest absolute difference from target_amount).

    Performance safeguards:
    - Candidates are sorted by amount first so partial sums grow monotonically,
      which lets us prune a branch as soon as it exceeds the upper bound instead
      of continuing to extend it.
    - A hard cap (MAX_COMBINATIONS_EVALUATED) stops evaluation even if a pool
      is pathologically large, so the tool can never hang or explode in cost.
    """
    if target_amount <= 0 or not candidates:
        return {"match_found": False, "count": 0, "matches": []}

    upper_bound = target_amount * (1 + fee_tolerance_pct / 100.0)

    # Sort ascending by amount: with a sorted list, once a partial sum in a
    # growing combination exceeds upper_bound, every further extension of
    # that combination (adding more, equally-or-larger amounts) will also
    # exceed it — so we can stop extending that branch early.
    sorted_candidates = sorted(candidates, key=lambda c: c["amount"])

    valid_matches: List[Dict[str, Any]] = []
    combinations_evaluated = 0
    cap_hit = False

    max_k = min(5, len(sorted_candidates) + 1)
    for k in range(2, max_k):
        if cap_hit:
            break
        for combo in combinations(sorted_candidates, k):
            combinations_evaluated += 1
            if combinations_evaluated > MAX_COMBINATIONS_EVALUATED:
                cap_hit = True
                break

            cand_sum = round(sum(c["amount"] for c in combo), 2)

            # Early prune: since combo is built from a sorted list, amounts
            # only grow as k increases for a given prefix; if this exact
            # combo already exceeds the upper bound there's no point scoring it.
            if cand_sum > upper_bound:
                continue

            fee_amount = round(cand_sum - target_amount, 2)
            fee_pct = (fee_amount / cand_sum) * 100.0 if cand_sum > 0 else -1.0

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

    # Rank by closeness to target (smallest absolute difference first),
    # then by lowest fee percentage, then smallest subset size.
    valid_matches.sort(key=lambda x: (x["difference"], x["fee_percentage"], x["subset_size"]))

    capped_matches = valid_matches[:MAX_RESULTS]

    return {
        "match_found": len(capped_matches) > 0,
        "count": len(capped_matches),
        "total_valid_found": len(valid_matches),
        "combinations_evaluated": combinations_evaluated,
        "combination_cap_hit": cap_hit,
        "matches": capped_matches
    }


def description_similarity(desc_a: str, desc_b: str) -> Dict[str, Any]:
    """
    Computes a normalized similarity score (0.0 to 1.0) and structural explanation
    between two transaction descriptions.
    """
    if not desc_a or not desc_b:
        return {
            "similarity_score": 0.0,
            "explanation": "One or both description strings are empty."
        }

    str_a = desc_a.strip().lower()
    str_b = desc_b.strip().lower()

    tokens_a = set(re.findall(r'\b[a-z0-9]+\b', str_a))
    tokens_b = set(re.findall(r'\b[a-z0-9]+\b', str_b))
    shared_tokens = sorted(list(tokens_a.intersection(tokens_b)))

    nums_a = set(re.findall(r'\b\d+\b', str_a))
    nums_b = set(re.findall(r'\b\d+\b', str_b))
    shared_nums = sorted(list(nums_a.intersection(nums_b)))

    if not shared_tokens and not shared_nums:
        raw_score = 0.0
    else:
        if HAS_RAPIDFUZZ:
            raw_score = fuzz.token_set_ratio(str_a, str_b) / 100.0
        else:
            token_overlap = len(shared_tokens) / min(len(tokens_a), len(tokens_b))
            seq_ratio = difflib.SequenceMatcher(None, str_a, str_b).ratio()
            raw_score = max(token_overlap, seq_ratio)

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