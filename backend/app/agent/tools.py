import os
import re
from itertools import combinations
from typing import Any, Dict, List, Optional

os.environ["RAPIDFUZZ_IMPLEMENTATION"] = "python"

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    HAS_RAPIDFUZZ = False

MAX_RESULTS = 5
MAX_COMBINATIONS_EVALUATED = 50_000

# Patterns that indicate two records belong to the same underlying batch/
# settlement, even when their raw description text differs otherwise.
# Extend this list if new description formats show up in the data.
_GROUP_KEY_PATTERNS = [
    re.compile(r'batch\s*#?\s*(\d+)', re.IGNORECASE),
    re.compile(r'settlement\s*#?\s*(\d+)', re.IGNORECASE),
    re.compile(r'#\s*(\d+)'),
]


def _extract_group_key(description: Optional[str]) -> Optional[str]:
    """
    Extracts a batch/settlement identifier from a description, e.g.
    "Payment component 1 for batch 28" -> "batch:28"
    "Settlement batch 28" -> "batch:28"
    Returns None if no recognizable grouping pattern is found — such
    candidates are treated as NOT sharing a group with anything else,
    which is the safe default (no false consistency credit).
    """
    if not description:
        return None
    for pattern in _GROUP_KEY_PATTERNS:
        match = pattern.search(description)
        if match:
            return f"batch:{match.group(1)}"
    return None


def _consistency_score(combo: tuple) -> Dict[str, Any]:
    """
    Scores how internally consistent a combination is, based on whether
    its members share a detectable group key (e.g. all reference the same
    batch number). This exists specifically to stop sum_check from
    preferring a numerically-closer combination that accidentally mixes
    components from two unrelated batches over a slightly-less-exact
    combination whose components genuinely belong together.
    """
    keys = [_extract_group_key(c.get("description")) for c in combo]
    non_null_keys = [k for k in keys if k is not None]

    if not non_null_keys:
        # No grouping info available at all (e.g. descriptions missing) —
        # neutral score, can't penalize or reward.
        return {"consistency_score": 0.5, "dominant_group": None, "outlier_ids": []}

    counts: Dict[str, int] = {}
    for k in non_null_keys:
        counts[k] = counts.get(k, 0) + 1
    dominant_group = max(counts, key=lambda k: counts[k])

    matching = sum(1 for k in keys if k == dominant_group)
    consistency_score = matching / len(combo)

    outlier_ids = [
        c["record_id"] for c, k in zip(combo, keys)
        if k is not None and k != dominant_group
    ]

    return {
        "consistency_score": round(consistency_score, 4),
        "dominant_group": dominant_group,
        "outlier_ids": outlier_ids,
    }


def sum_check(
    target_amount: float,
    candidates: List[Dict[str, Any]],
    fee_tolerance_pct: float = 3.0
) -> Dict[str, Any]:
    if target_amount <= 0 or not candidates:
        return {"match_found": False, "count": 0, "matches": []}

    upper_bound = target_amount * (1 + fee_tolerance_pct / 100.0)
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
            if cand_sum > upper_bound:
                continue

            fee_amount = round(cand_sum - target_amount, 2)
            fee_pct = (fee_amount / cand_sum) * 100.0 if cand_sum > 0 else -1.0
            if -0.01 <= fee_pct <= fee_tolerance_pct:
                consistency = _consistency_score(combo)
                valid_matches.append({
                    "candidate_ids": [c["record_id"] for c in combo],
                    "candidate_sum": cand_sum,
                    "target_amount": target_amount,
                    "fee_amount": fee_amount,
                    "fee_percentage": round(fee_pct, 2),
                    "subset_size": k,
                    "difference": round(abs(fee_amount), 2),
                    "consistency_score": consistency["consistency_score"],
                    "dominant_group": consistency["dominant_group"],
                    "outlier_ids": consistency["outlier_ids"],
                })

    # CRITICAL: sort by consistency FIRST, difference second. A
    # combination whose components don't share a common batch/settlement
    # reference should never outrank one that does, even if its sum is
    # numerically closer to the target — a coincidental exact sum across
    # unrelated records is a false positive, not a better match.
    valid_matches.sort(
        key=lambda x: (-x["consistency_score"], x["difference"], x["fee_percentage"], x["subset_size"])
    )

    capped_matches = valid_matches[:MAX_RESULTS]

    # Make the inconsistency explicit and readable, so the agent doesn't
    # have to infer it from raw scores — it can just read this.
    for m in capped_matches:
        if m["consistency_score"] < 1.0 and m["dominant_group"] is not None:
            m["consistency_note"] = (
                f"WARNING: {len(m['outlier_ids'])} of {m['subset_size']} components do NOT "
                f"share the dominant group '{m['dominant_group']}' — outliers: {m['outlier_ids']}. "
                f"This combination likely mixes records from different underlying transactions "
                f"despite the sum being within tolerance. Treat with caution."
            )
        elif m["dominant_group"] is None:
            m["consistency_note"] = (
                "No batch/settlement identifier detected in descriptions — consistency could not be verified."
            )
        else:
            m["consistency_note"] = f"All {m['subset_size']} components share group '{m['dominant_group']}'."

    return {
        "match_found": len(capped_matches) > 0,
        "count": len(capped_matches),
        "total_valid_found": len(valid_matches),
        "combinations_evaluated": combinations_evaluated,
        "combination_cap_hit": cap_hit,
        "matches": capped_matches
    }


def description_similarity(desc_a: str, desc_b: str) -> Dict[str, Any]:
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
            raw_score = fuzz.token_set_ratio(str_a, str_b) / 100.0 # type: ignore[possibly-unbound]
        else:
            token_overlap = len(shared_tokens) / min(len(tokens_a), len(tokens_b))
            seq_ratio = difflib.SequenceMatcher(None, str_a, str_b).ratio() # type: ignore[possibly-unbound]
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