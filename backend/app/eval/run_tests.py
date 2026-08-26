import sys
from pathlib import Path
import os
import csv
import json
import asyncio
import argparse

CURRENT_FILE = Path(__file__).resolve()
BACKEND_DIR = CURRENT_FILE.parents[2]
PROJECT_ROOT = CURRENT_FILE.parents[3]

for p in [str(PROJECT_ROOT), str(BACKEND_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.app.agent.verifier import run_verifier
from backend.app.agent.proposer import get_candidate_pool
from backend.app.matcher.reconciler import resolve_batch
from backend.app.agent import config

SAMPLE_DIR = BACKEND_DIR / "app" / "data_generation" / "samples"
BANK_CSV = SAMPLE_DIR / "bank_statement.csv"
LEDGER_CSV = SAMPLE_DIR / "internal_ledger.csv"
GATEWAY_CSV = SAMPLE_DIR / "gateway_export.csv"

def load_data():
    if not BANK_CSV.exists() or not LEDGER_CSV.exists() or not GATEWAY_CSV.exists():
        raise FileNotFoundError("Run data generator first: python -m backend.app.data_generation.generator")
    with open(BANK_CSV, "r", encoding="utf-8") as f:
        bank_records = list(csv.DictReader(f))
    with open(LEDGER_CSV, "r", encoding="utf-8") as f:
        ledger_records = list(csv.DictReader(f))
    with open(GATEWAY_CSV, "r", encoding="utf-8") as f:
        gateway_records = list(csv.DictReader(f))
        
    for b in bank_records:
        b["amount"] = float(b["amount"])
    for l in ledger_records:
        l["amount"] = float(l["amount"])
    for g in gateway_records:
        g["amount"] = float(g["amount"])
        
    return bank_records, ledger_records, gateway_records


async def test_verifier_catches_decoy(bank_records, ledger_records, gateway_records):
    print("\n=======================================================")
    print("TEST 1: Verifier Catches Hardcoded Decoy")
    print("=======================================================")
    
    bank_0042 = next((b for b in bank_records if b.get("record_id") == "BANK_0042"), bank_records[0])
    
    # 1. PRE-FILTER CANDIDATES DOWN TO MAX 25 (Saves 80%+ tokens)
    # NOW passing both ledger and gateway records
    candidates = get_candidate_pool(bank_0042, ledger_records, gateway_records)
    
    bad_proposal = {
        "status": "suggested_match",
        "matched_ledger_ids": ["LEDG_0042_1"],
        "matched_gateway_ids": [],
        "reasoning": "Matched LEDG_0042_1 strictly because the amount matches the target.",
        "confidence": "high"
    }

    # 2. Pass the pre-filtered candidates pool
    result = await run_verifier(
        bank_record=bank_0042,
        candidates=candidates,
        proposed_match=bad_proposal,
        proposer_reasoning=bad_proposal["reasoning"]
    )

    print(f"\nVerifier Decision: {result.get('decision')}")
    print(f"Verifier Reasoning: {result.get('reasoning')}\n")
    assert result.get("decision") == "disagree", "Assertion Failed: Verifier agreed with a decoy match!"
    print("PASSED: Verifier correctly rejected decoy proposal.")


async def test_batch_resolution(bank_records, ledger_records, gateway_records):
    print("\n=======================================================")
    print("TEST 2: Concurrent Batch Reconciliation (BANK_0028, BANK_0042)")
    print("=======================================================")
    
    target_ids = ["BANK_0028", "BANK_0042"]
    test_batch = [b for b in bank_records if b.get("record_id") in target_ids]
    if len(test_batch) < 2:
        test_batch = bank_records[:4]

    # NOW passing gateway_records to resolve_batch
    results = await resolve_batch(test_batch, ledger_records, gateway_records)
    
    confirmed = sum(1 for r in results if r.get("final_status") == "confirmed")
    exceptions = sum(1 for r in results if r.get("final_status") == "exception")
    print(f"Batch Summary: {confirmed} confirmed, {exceptions} exceptions out of {len(results)} records.")


async def main():
    bank_records, ledger_records, gateway_records = load_data()
    await test_verifier_catches_decoy(bank_records, ledger_records, gateway_records)
    await test_batch_resolution(bank_records, ledger_records, gateway_records)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run only Test 1 (fast, no rate-limit-heavy batch calls). "
             "Use this for quick sanity checks during iteration; run the "
             "full suite (no flag) before trusting a fix.",
    )
    args = parser.parse_args()
 
    async def main_dispatch():
        bank_records, ledger_records, gateway_records = load_data()
        await test_verifier_catches_decoy(bank_records, ledger_records, gateway_records)
        if not args.quick:
            await test_batch_resolution(bank_records, ledger_records, gateway_records)
        else:
            print("\n[--quick] Skipped Test 2 (batch resolution) — "
                  "run without --quick before considering a fix confirmed.")
 
    asyncio.run(main_dispatch())