"""Fast deterministic matcher for AI Finance Controller.

Implements a deterministic fast path:
1. Exact reference-number match: records with non-empty reference_number are
   matched across bank/ledger/gateway on identical reference_number.
2. Tolerance match: amount within 1% AND date within 3 days. Only confirmed
   if exactly one candidate exists in each other source. Multiple candidates
   -> ambiguous; zero candidates -> unresolved.

This module provides `fast_match_bank_records(bank_csv, ledger_csv, gateway_csv)`
which returns a list of results per bank record.

It also supports running as a script (python -m backend.app.matcher.fast_matcher)
against the generated CSVs to print a summary.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any


@dataclass
class MatchResult:
    bank_id: str
    status: str  # 'confirmed' | 'ambiguous' | 'unresolved' | 'flagged'
    matched_ledger_id: Optional[str] = None
    matched_gateway_id: Optional[str] = None
    ledger_candidates: List[str] = None
    gateway_candidates: List[str] = None


def load_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def parse_amount(a: str) -> Optional[float]:
    try:
        return float(a)
    except Exception:
        return None


def parse_date(d: str) -> Optional[datetime.date]:
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return None


def index_by_reference(records: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    idx: Dict[str, List[Dict[str, str]]] = {}
    for r in records:
        ref = (r.get("reference_number") or "").strip()
        if not ref:
            continue
        idx.setdefault(ref, []).append(r)
    return idx


def fast_match_bank_records(
    bank_records: List[Dict[str, str]],
    ledger_records: List[Dict[str, str]],
    gateway_records: List[Dict[str, str]],
    amount_tolerance: float = 0.01,
    date_days_tolerance: int = 3,
    ref_amount_tolerance: float = 0.02,
) -> List[MatchResult]:
    """Perform deterministic fast-path matching for each bank record.

    Returns a list of MatchResult, one per bank record.
    """
    # Index by reference number for exact matches
    ledger_ref_idx = index_by_reference(ledger_records)
    gateway_ref_idx = index_by_reference(gateway_records)

    # Pre-parse ledger and gateway amounts/dates for efficiency
    ledger_parsed: List[Tuple[Dict[str, str], Optional[float], Optional[datetime.date]]] = []
    for r in ledger_records:
        ledger_parsed.append((r, parse_amount(r.get("amount", "")), parse_date(r.get("date", ""))))

    gateway_parsed: List[Tuple[Dict[str, str], Optional[float], Optional[datetime.date]]] = []
    for r in gateway_records:
        gateway_parsed.append((r, parse_amount(r.get("amount", "")), parse_date(r.get("date", ""))))

    results: List[MatchResult] = []

    for b in bank_records:
        bank_id = b.get("record_id")
        ref = (b.get("reference_number") or "").strip()
        b_amount = parse_amount(b.get("amount", ""))
        b_date = parse_date(b.get("date", ""))

        # Prepare defaults
        ledger_candidates: List[str] = []
        gateway_candidates: List[str] = []
        matched_ledger_id: Optional[str] = None
        matched_gateway_id: Optional[str] = None
        status = "unresolved"

        # 1) Exact reference-number match
        if ref:
            led_matches = ledger_ref_idx.get(ref, [])
            gw_matches = gateway_ref_idx.get(ref, [])

            # If multiples exist on either side, mark ambiguous
            if (len(led_matches) > 1) or (len(gw_matches) > 1):
                ledger_candidates = [r.get("record_id") for r in led_matches]
                gateway_candidates = [r.get("record_id") for r in gw_matches]
                status = "ambiguous"
            elif len(led_matches) == 1 and len(gw_matches) == 1:
                # Verify amount sanity across matched records (ref-based check)
                matched_ledger = led_matches[0]
                matched_gateway = gw_matches[0]
                matched_ledger_id = matched_ledger.get("record_id")
                matched_gateway_id = matched_gateway.get("record_id")

                # Parse amounts
                l_amount = parse_amount(matched_ledger.get("amount", ""))
                g_amount = parse_amount(matched_gateway.get("amount", ""))

                # If bank amount missing, flag as unresolved
                if b_amount is None:
                    status = "unresolved"
                else:
                    # Check amount differences (bank vs ledger and bank vs gateway)
                    ledger_diff = abs((l_amount - b_amount) / b_amount) if l_amount is not None else 1.0
                    gateway_diff = abs((g_amount - b_amount) / b_amount) if g_amount is not None else 1.0

                    if ledger_diff <= ref_amount_tolerance and gateway_diff <= ref_amount_tolerance:
                        status = "confirmed"
                    else:
                        # Reference matched but amounts disagree beyond sanity tolerance
                        status = "flagged"
                        # still record the matched ids for inspection
                        ledger_candidates = [matched_ledger_id]
                        gateway_candidates = [matched_gateway_id]
            else:
                # Reference exists on bank but missing on one or both other sources
                # Treat as unresolved so it can be inspected
                ledger_candidates = [r.get("record_id") for r in led_matches]
                gateway_candidates = [r.get("record_id") for r in gw_matches]
                status = "unresolved"

            results.append(
                MatchResult(
                    bank_id=bank_id,
                    status=status,
                    matched_ledger_id=matched_ledger_id,
                    matched_gateway_id=matched_gateway_id,
                    ledger_candidates=ledger_candidates,
                    gateway_candidates=gateway_candidates,
                )
            )
            continue

        # 2) Tolerance match (amount ± amount_tolerance, date ± date_days_tolerance)
        if b_amount is None or b_date is None:
            results.append(
                MatchResult(bank_id=bank_id, status="unresolved", ledger_candidates=[], gateway_candidates=[])
            )
            continue

        # Find ledger candidates
        for r, a, d in ledger_parsed:
            if a is None or d is None:
                continue
            amt_diff = abs(a - b_amount) / b_amount
            date_diff = abs((d - b_date).days)
            if amt_diff <= amount_tolerance and date_diff <= date_days_tolerance:
                ledger_candidates.append(r.get("record_id"))

        # Find gateway candidates
        for r, a, d in gateway_parsed:
            if a is None or d is None:
                continue
            amt_diff = abs(a - b_amount) / b_amount
            date_diff = abs((d - b_date).days)
            if amt_diff <= amount_tolerance and date_diff <= date_days_tolerance:
                gateway_candidates.append(r.get("record_id"))

        # Decide status per spec
        if len(ledger_candidates) == 1 and len(gateway_candidates) == 1:
            status = "confirmed"
            matched_ledger_id = ledger_candidates[0]
            matched_gateway_id = gateway_candidates[0]
        elif len(ledger_candidates) > 1 or len(gateway_candidates) > 1:
            status = "ambiguous"
        else:
            # zero candidates on at least one side -> unresolved
            status = "unresolved"

        results.append(
            MatchResult(
                bank_id=bank_id,
                status=status,
                matched_ledger_id=matched_ledger_id,
                matched_gateway_id=matched_gateway_id,
                ledger_candidates=ledger_candidates,
                gateway_candidates=gateway_candidates,
            )
        )

    return results


def _print_summary(results: List[MatchResult]):
    total = len(results)
    confirmed = [r for r in results if r.status == "confirmed"]
    ambiguous = [r for r in results if r.status == "ambiguous"]
    unresolved = [r for r in results if r.status == "unresolved"]
    flagged = [r for r in results if r.status == "flagged"]

    def pct(n: int) -> str:
        return f"{(n/total*100):.1f}%" if total else "0%"

    print(f"Total bank records processed: {total}")
    print(f"Confirmed: {len(confirmed)} ({pct(len(confirmed))})")
    print(f"Flagged (ref present but amount mismatch): {len(flagged)} ({pct(len(flagged))})")
    print(f"Ambiguous: {len(ambiguous)} ({pct(len(ambiguous))})")
    print(f"Unresolved: {len(unresolved)} ({pct(len(unresolved))})")

    if flagged:
        print("\nExample flagged bank records (up to 5):")
        for r in flagged[:5]:
            print(f"  - {r.bank_id}: matched_ledger={r.matched_ledger_id} matched_gateway={r.matched_gateway_id} ledger_candidates={r.ledger_candidates} gateway_candidates={r.gateway_candidates}")

    if ambiguous:
        print("\nExample ambiguous bank records (up to 5):")
        for r in ambiguous[:5]:
            print(f"  - {r.bank_id}: ledger_candidates={r.ledger_candidates} gateway_candidates={r.gateway_candidates}")

    if unresolved:
        print("\nExample unresolved bank records (up to 5):")
        for r in unresolved[:5]:
            print(f"  - {r.bank_id}")


if __name__ == "__main__":
    # Run against generated CSVs in data_generation/samples
    base = Path(__file__).parent.parent.parent / "app" / "data_generation"
    samples = base / "samples"
    bank_file = samples / "bank_statement.csv"
    ledger_file = samples / "internal_ledger.csv"
    gateway_file = samples / "gateway_export.csv"

    bank = load_csv(bank_file)
    ledger = load_csv(ledger_file)
    gateway = load_csv(gateway_file)

    results = fast_match_bank_records(bank, ledger, gateway)
    _print_summary(results)

    # Print counts for exact reference matches vs tolerance matches
    # We can classify confirmed by whether bank had a reference_number or not
    ref_confirmed = 0
    tol_confirmed = 0
    flagged_by_ref = 0
    for b, r in zip(bank, results):
        if r.status == "confirmed":
            if (b.get("reference_number") or "").strip():
                ref_confirmed += 1
            else:
                tol_confirmed += 1
        elif r.status == "flagged":
            # count flagged ones that originated from a reference-number
            if (b.get("reference_number") or "").strip():
                flagged_by_ref += 1

    print(f"\nConfirmed by reference-number: {ref_confirmed} ({(ref_confirmed/len(bank)*100):.1f}%)")
    print(f"Confirmed by tolerance match: {tol_confirmed} ({(tol_confirmed/len(bank)*100):.1f}%)")
    print(f"Flagged by reference-number amount-sanity check: {flagged_by_ref} ({(flagged_by_ref/len(bank)*100):.1f}%)")

