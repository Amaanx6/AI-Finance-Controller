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