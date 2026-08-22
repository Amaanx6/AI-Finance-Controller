import pytest
from app.agent.tools import sum_check, description_similarity


def test_sum_check_valid_many_to_one():
    """Target bank settlement of 97.50 matches 2 ledger entries summing to 100.00 (2.5% fee)."""
    target_amount = 97.50
    candidates = [
        {"record_id": "L1", "amount": 60.00},
        {"record_id": "L2", "amount": 40.00},
        {"record_id": "L3", "amount": 500.00},  # Irrelevant record
    ]
    
    result = sum_check(target_amount, candidates, fee_tolerance_pct=3.0)
    
    assert result["match_found"] is True
    assert result["count"] == 1
    
    best_match = result["matches"][0]
    assert sorted(best_match["candidate_ids"]) == ["L1", "L2"]
    assert best_match["candidate_sum"] == 100.00
    assert best_match["fee_amount"] == 2.50
    assert best_match["fee_percentage"] == 2.50


def test_sum_check_no_match():
    """Candidates do not sum within the fee tolerance limit."""
    target_amount = 100.00
    candidates = [
        {"record_id": "L1", "amount": 20.00},
        {"record_id": "L2", "amount": 30.00},
        {"record_id": "L3", "amount": 10.00},
    ]
    
    result = sum_check(target_amount, candidates, fee_tolerance_pct=3.0)
    
    assert result["match_found"] is False
    assert result["count"] == 0
    assert result["matches"] == []


def test_sum_check_consistency_beats_exact_numeric_match():
    """Consistency score must outrank exact numerical matches mixing different batches."""
    target_amount = 40504.0
    
    candidates = [
        # REAL: Batch 28 components (Sum = 41650, 2.75% fee)
        {"record_id": "LEDG_0028_0", "amount": 13808.0, "description": "Payment component 1 for batch 28"},
        {"record_id": "LEDG_0028_1", "amount": 3876.0, "description": "Payment component 2 for batch 28"},
        {"record_id": "LEDG_0028_2", "amount": 13169.0, "description": "Payment component 3 for batch 28"},
        {"record_id": "LEDG_0028_3", "amount": 10797.0, "description": "Payment component 4 for batch 28"},
        
        # DECOY: From batch 37. Swapping this in makes the sum EXACTLY 40504.0 (0% fee).
        {"record_id": "LEDG_0037_1", "amount": 9651.0, "description": "Payment component 2 for batch 37"}
    ]

    result = sum_check(target_amount, candidates, fee_tolerance_pct=3.0)

    assert result["match_found"] is True
    matches = result["matches"]

    # Top match must be the 100% consistent batch 28 combination
    top_match = matches[0]
    assert top_match["consistency_score"] == 1.0
    assert top_match["dominant_group"] == "batch:28"
    assert set(top_match["candidate_ids"]) == {"LEDG_0028_0", "LEDG_0028_1", "LEDG_0028_2", "LEDG_0028_3"}

    # Decoy match should be flagged with an outlier warning note
    decoy_match = next((m for m in matches if "LEDG_0037_1" in m["candidate_ids"]), None)
    assert decoy_match is not None
    assert decoy_match["consistency_score"] == 0.75
    assert "WARNING" in decoy_match["consistency_note"]
    assert "LEDG_0037_1" in decoy_match["outlier_ids"]


def test_description_similarity_matching():
    """Abbreviated settlement text vs expanded batch description."""
    desc_a = "RZRPY SETL 08/19 #4471"
    desc_b = "Razorpay settlement batch #4471 date 08/19"

    result = description_similarity(desc_a, desc_b)

    assert result["similarity_score"] >= 0.60
    assert "4471" in result["explanation"]
    assert "08" in result["explanation"] or "19" in result["explanation"]


def test_description_similarity_non_matching():
    """Unrelated transaction descriptions."""
    desc_a = "AWS CLOUD HOSTING SERVICES RECURRING"
    desc_b = "STARBUCKS STORE #1209 COFFEE"

    result = description_similarity(desc_a, desc_b)

    assert result["similarity_score"] < 0.30
    assert "No shared textual tokens" in result["explanation"] or result["similarity_score"] == 0.0