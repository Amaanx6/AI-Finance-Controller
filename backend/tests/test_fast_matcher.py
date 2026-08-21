import importlib.util
from pathlib import Path

# Dynamically import fast_matcher module from the codebase so tests run regardless
# of PYTHONPATH
spec = importlib.util.spec_from_file_location(
    "fast_matcher",
    Path(__file__).resolve().parents[1] / "app" / "matcher" / "fast_matcher.py",
)
import sys
fast_matcher = importlib.util.module_from_spec(spec)
# Ensure module is available in sys.modules so dataclass and typing resolve properly
sys.modules[spec.name] = fast_matcher
spec.loader.exec_module(fast_matcher)
fast_match_bank_records = fast_matcher.fast_match_bank_records


def make_record(record_id, date, amount, description="", reference_number=""):
    return {
        "record_id": record_id,
        "date": date,
        "amount": str(amount),
        "description": description,
        "reference_number": reference_number,
    }


def test_confirmed_via_tolerance():
    # Bank record without reference number
    bank = [make_record("BANK_TOL", "2025-08-10", 10000, "Bank tol", "")]

    # Ledger and gateway have single candidates within 1% and within 3 days
    ledger = [make_record("LEDG_TOL", "2025-08-11", 10050, "Ledger tol", "")]
    gateway = [make_record("GW_TOL", "2025-08-09", 9950, "Gateway tol", "")]

    results = fast_match_bank_records(bank, ledger, gateway)
    assert len(results) == 1
    r = results[0]
    assert r.status == "confirmed"
    assert r.matched_ledger_id == "LEDG_TOL"
    assert r.matched_gateway_id == "GW_TOL"


def test_flagged_by_reference_amount_mismatch():
    # Bank record with reference number
    bank = [make_record("BANK_FLAG", "2025-08-10", 10000, "Bank flag", "REFFLAG")]

    # Ledger has same reference but amount differs by >2% (e.g., 10400 -> 4%)
    ledger = [make_record("LEDG_FLAG", "2025-08-10", 10400, "Ledger flag", "REFFLAG")]
    # Gateway matches reference and amount close
    gateway = [make_record("GW_FLAG", "2025-08-10", 10050, "Gateway ok", "REFFLAG")]

    results = fast_match_bank_records(bank, ledger, gateway)
    assert len(results) == 1
    r = results[0]
    assert r.status == "flagged"
    # matched ids should be present in candidates or matched fields
    assert r.ledger_candidates == ["LEDG_FLAG"] or r.matched_ledger_id == "LEDG_FLAG"
    assert r.gateway_candidates == ["GW_FLAG"] or r.matched_gateway_id == "GW_FLAG"


def test_confirmed_by_reference():
    # Bank record with reference number and matching amounts
    bank = [make_record("BANK_REF", "2025-08-12", 20000, "Bank ref", "REF123")]

    ledger = [make_record("LEDG_REF", "2025-08-12", 19980, "Ledger ref", "REF123")]
    gateway = [make_record("GW_REF", "2025-08-12", 20020, "Gateway ref", "REF123")]

    results = fast_match_bank_records(bank, ledger, gateway)
    assert len(results) == 1
    r = results[0]
    assert r.status == "confirmed"
    assert r.matched_ledger_id == "LEDG_REF"
    assert r.matched_gateway_id == "GW_REF"
