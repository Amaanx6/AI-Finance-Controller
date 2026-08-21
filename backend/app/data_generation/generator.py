"""Generate synthetic transaction data from three sources"""
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple
import sys


class TransactionGenerator:
    """Generate synthetic financial transactions across three data sources."""

    def __init__(self, seed: int = 42):
        """Initialize generator with a fixed seed for reproducibility."""
        random.seed(seed)
        self.base_date = datetime(2025, 8, 1)
        self.num_transactions = 70
        self.records = {
            "bank_statement": [],
            "internal_ledger": [],
            "gateway_export": []
        }
        self.ground_truth_mapping = []
        self.transaction_id_counter = 0

    def generate_amount(self, base: int, variance_percent: float = 0) -> int:
        """Generate amount in INR (integers for simplicity)."""
        if variance_percent == 0:
            return base
        variance = base * variance_percent / 100
        return int(base + random.uniform(-variance, variance))

    def generate_date(self, base_offset: int, day_variance: int = 0) -> str:
        """Generate date as string (YYYY-MM-DD)."""
        date = self.base_date + timedelta(days=base_offset + random.randint(-day_variance, day_variance))
        return date.strftime("%Y-%m-%d")

    def add_record(self, source: str, record_id: str, date: str, amount: int,
                   description: str, reference_number: str = ""):
        """Add a record to a specific source."""
        self.records[source].append({
            "record_id": record_id,
            "date": date,
            "amount": amount,
            "description": description,
            "reference_number": reference_number
        })

    def pattern_1_clean_matches(self, count: int) -> List[Dict]:
        """1:1 matches (~40%): All three sources match perfectly."""
        mappings = []
        for i in range(count):
            txn_id = self.transaction_id_counter
            self.transaction_id_counter += 1

            base_amount = random.randint(5000, 50000)
            base_date_offset = random.randint(0, 20)
            ref_number = f"REF{txn_id:05d}"

            date_str = self.generate_date(base_date_offset)

            # Bank statement
            bank_id = f"BANK_{txn_id:04d}"
            self.add_record("bank_statement", bank_id, date_str, base_amount,
                          f"Transfer to vendor {txn_id}", ref_number)

            # Internal ledger (same day or ±1 day)
            ledger_date = self.generate_date(base_date_offset, day_variance=1)
            ledger_id = f"LEDG_{txn_id:04d}"
            self.add_record("internal_ledger", ledger_id, ledger_date, base_amount,
                          f"Vendor payment {txn_id}", ref_number)

            # Gateway export (same)
            gateway_date = self.generate_date(base_date_offset, day_variance=1)
            gateway_id = f"GW_{txn_id:04d}"
            self.add_record("gateway_export", gateway_id, gateway_date, base_amount,
                          f"Payout to vendor {txn_id}", ref_number)

            mappings.append({
                "transaction_id": txn_id,
                "bank_record_id": bank_id,
                "ledger_record_id": ledger_id,
                "gateway_record_id": gateway_id,
                "pattern": "clean_1to1_match"
            })

        return mappings

    def pattern_2_many_to_one(self, count: int) -> List[Dict]:
        """Many-to-one settlement batches (~20%)."""
        mappings = []
        for i in range(count):
            txn_id = self.transaction_id_counter
            self.transaction_id_counter += 1

            # 2-4 ledger entries sum to bank entry (minus 2-3% fee)
            num_ledger_entries = random.randint(2, 4)
            individual_amounts = [random.randint(2000, 15000) for _ in range(num_ledger_entries)]
            total_before_fee = sum(individual_amounts)
            fee_percent = random.uniform(2, 3)
            bank_amount = int(total_before_fee * (1 - fee_percent / 100))

            base_date_offset = random.randint(0, 20)
            date_str = self.generate_date(base_date_offset)
            ref_number = f"BATCH{txn_id:05d}"

            # Bank statement: one record
            bank_id = f"BANK_{txn_id:04d}"
            self.add_record("bank_statement", bank_id, date_str, bank_amount,
                          f"Settlement batch {txn_id}", ref_number)

            # Internal ledger: multiple entries (same amount, individual)
            ledger_ids = []
            for j, amount in enumerate(individual_amounts):
                ledger_id = f"LEDG_{txn_id:04d}_{j}"
                ledger_date = self.generate_date(base_date_offset, day_variance=1)
                self.add_record("internal_ledger", ledger_id, ledger_date, amount,
                              f"Payment component {j+1} for batch {txn_id}", "")
                ledger_ids.append(ledger_id)

            # Gateway export: matches the ledger entries (not the batch)
            gateway_ids = []
            for j, amount in enumerate(individual_amounts):
                gateway_id = f"GW_{txn_id:04d}_{j}"
                gateway_date = self.generate_date(base_date_offset, day_variance=1)
                self.add_record("gateway_export", gateway_id, gateway_date, amount,
                              f"Individual payout {j+1} in batch", "")
                gateway_ids.append(gateway_id)

            mappings.append({
                "transaction_id": txn_id,
                "bank_record_id": bank_id,
                "ledger_record_ids": ",".join(ledger_ids),
                "gateway_record_ids": ",".join(gateway_ids),
                "pattern": "many_to_one_settlement"
            })

        return mappings

    def pattern_3_description_mismatch(self, count: int) -> List[Dict]:
        """Description mismatches (~15%): same txn, no shared reference.

        To induce ambiguity for an amount+date-only matcher, add a single
        decoy record (unrelated transaction) in one of the other sources for
        each transaction. The decoy has an amount within ±1% and date within
        ±2-3 days of the true values, but is not part of the ground truth
        mapping.
        """
        mappings = []
        for i in range(count):
            txn_id = self.transaction_id_counter
            self.transaction_id_counter += 1

            base_amount = random.randint(5000, 50000)
            base_date_offset = random.randint(0, 20)
            date_str = self.generate_date(base_date_offset)

            # Bank statement (one of the sources)
            bank_id = f"BANK_{txn_id:04d}"
            bank_desc = f"RZRPY SETL {base_date_offset:02d}/19"
            self.add_record("bank_statement", bank_id, date_str, base_amount, bank_desc, "")

            # Internal ledger (wildly different description, no ref)
            ledger_id = f"LEDG_{txn_id:04d}"
            ledger_date = self.generate_date(base_date_offset, day_variance=1)
            ledger_desc = f"Razorpay settlement batch #{4000 + txn_id}"
            self.add_record("internal_ledger", ledger_id, ledger_date, base_amount, ledger_desc, "")

            # Gateway export (yet another description)
            gateway_id = f"GW_{txn_id:04d}"
            gateway_date = self.generate_date(base_date_offset, day_variance=1)
            gateway_desc = "Payment gateway payout"
            self.add_record("gateway_export", gateway_id, gateway_date, base_amount, gateway_desc, "")

            # Add a decoy in one of the other sources (randomly choose ledger or gateway)
            decoy_source = random.choice(["internal_ledger", "gateway_export"])
            # Decoy amount within ±1%
            decoy_amount = int(base_amount * (1 + random.uniform(-0.01, 0.01)))
            # Decoy date within ±3 days
            decoy_date = self.generate_date(base_date_offset, day_variance=3)

            if decoy_source == "internal_ledger":
                decoy_id = f"LEDG_{txn_id:04d}_DECOY"
                decoy_desc = f"Unrelated vendor {txn_id} (decoy)"
                self.add_record("internal_ledger", decoy_id, decoy_date, decoy_amount, decoy_desc, "")
            else:
                decoy_id = f"GW_{txn_id:04d}_DECOY"
                decoy_desc = f"Unrelated payout {txn_id} (decoy)"
                self.add_record("gateway_export", decoy_id, decoy_date, decoy_amount, decoy_desc, "")

            mappings.append({
                "transaction_id": txn_id,
                "bank_record_id": bank_id,
                "ledger_record_id": ledger_id,
                "gateway_record_id": gateway_id,
                "pattern": "description_mismatch"
            })

        return mappings

    def pattern_4_near_miss(self, count: int) -> List[Dict]:
        """Near-miss amount/date noise (~15%)."""
        mappings = []
        for i in range(count):
            txn_id = self.transaction_id_counter
            self.transaction_id_counter += 1

            base_amount = random.randint(5000, 50000)
            base_date_offset = random.randint(0, 20)

            # Bank statement: base
            bank_id = f"BANK_{txn_id:04d}"
            bank_date = self.generate_date(base_date_offset)
            bank_amount = base_amount
            ref_number = f"REF{txn_id:05d}"
            self.add_record("bank_statement", bank_id, bank_date, bank_amount,
                          f"Transfer {txn_id}", ref_number)

            # Internal ledger: small variance in amount (0.5-1% off) and date (±2 days)
            ledger_id = f"LEDG_{txn_id:04d}"
            ledger_date = self.generate_date(base_date_offset, day_variance=2)
            ledger_amount = self.generate_amount(base_amount, variance_percent=random.uniform(0.5, 1.0))
            self.add_record("internal_ledger", ledger_id, ledger_date, ledger_amount,
                          f"Entry {txn_id}", ref_number)

            # Gateway: different variance
            gateway_id = f"GW_{txn_id:04d}"
            gateway_date = self.generate_date(base_date_offset, day_variance=3)
            gateway_amount = self.generate_amount(base_amount, variance_percent=random.uniform(0.5, 1.0))
            self.add_record("gateway_export", gateway_id, gateway_date, gateway_amount,
                          f"Payout {txn_id}", ref_number)

            mappings.append({
                "transaction_id": txn_id,
                "bank_record_id": bank_id,
                "ledger_record_id": ledger_id,
                "gateway_record_id": gateway_id,
                "pattern": "near_miss_amount_date"
            })

        return mappings

    def pattern_5_genuine_anomalies(self, count: int) -> List[Dict]:
        """Genuine anomalies (~10%): record in only ONE source."""
        mappings = []
        for i in range(count):
            txn_id = self.transaction_id_counter
            self.transaction_id_counter += 1

            base_amount = random.randint(5000, 50000)
            base_date_offset = random.randint(0, 20)
            date_str = self.generate_date(base_date_offset)

            # Pick which source has this orphan record
            source = random.choice(["bank_statement", "internal_ledger", "gateway_export"])
            
            if source == "bank_statement":
                record_id = f"BANK_{txn_id:04d}"
                desc = f"Orphan bank transfer {txn_id}"
            elif source == "internal_ledger":
                record_id = f"LEDG_{txn_id:04d}"
                desc = f"Orphan ledger entry {txn_id}"
            else:
                record_id = f"GW_{txn_id:04d}"
                desc = f"Orphan gateway entry {txn_id}"

            self.add_record(source, record_id, date_str, base_amount, desc, "")

            mappings.append({
                "transaction_id": txn_id,
                "bank_record_id": record_id if source == "bank_statement" else None,
                "ledger_record_id": record_id if source == "internal_ledger" else None,
                "gateway_record_id": record_id if source == "gateway_export" else None,
                "pattern": "genuine_anomaly"
            })

        return mappings

    def generate_all(self) -> Tuple[List[Dict], Dict[str, int]]:
        """Generate all transaction patterns according to distribution."""
        all_mappings = []

        # Calculate counts for each pattern
        pattern_counts = {
            "clean_1to1": int(self.num_transactions * 0.40),  # 28
            "many_to_one": int(self.num_transactions * 0.20),  # 14
            "desc_mismatch": int(self.num_transactions * 0.15),  # 10
            "near_miss": int(self.num_transactions * 0.15),  # 10
            "anomaly": int(self.num_transactions * 0.10),  # 8
        }

        print("\n[*] Generating synthetic transactions...")
        print(f"Pattern distribution:")
        print(f"  - Clean 1:1 matches: {pattern_counts['clean_1to1']}")
        print(f"  - Many-to-one settlements: {pattern_counts['many_to_one']}")
        print(f"  - Description mismatches: {pattern_counts['desc_mismatch']}")
        print(f"  - Near-miss amount/date: {pattern_counts['near_miss']}")
        print(f"  - Genuine anomalies: {pattern_counts['anomaly']}")

        all_mappings.extend(self.pattern_1_clean_matches(pattern_counts["clean_1to1"]))
        all_mappings.extend(self.pattern_2_many_to_one(pattern_counts["many_to_one"]))
        all_mappings.extend(self.pattern_3_description_mismatch(pattern_counts["desc_mismatch"]))
        all_mappings.extend(self.pattern_4_near_miss(pattern_counts["near_miss"]))
        all_mappings.extend(self.pattern_5_genuine_anomalies(pattern_counts["anomaly"]))

        stats = {
            "clean_1to1": pattern_counts["clean_1to1"],
            "many_to_one": pattern_counts["many_to_one"],
            "desc_mismatch": pattern_counts["desc_mismatch"],
            "near_miss": pattern_counts["near_miss"],
            "anomaly": pattern_counts["anomaly"],
            "bank_records": len(self.records["bank_statement"]),
            "ledger_records": len(self.records["internal_ledger"]),
            "gateway_records": len(self.records["gateway_export"]),
        }

        return all_mappings, stats

    def save_csvs(self, output_dir: Path = None):
        """Save generated data to CSV files."""
        if output_dir is None:
            output_dir = Path(__file__).parent

        samples_dir = output_dir / "samples"
        ground_truth_dir = output_dir / "ground_truth"

        samples_dir.mkdir(exist_ok=True)
        ground_truth_dir.mkdir(exist_ok=True)

        # Save bank_statement.csv
        bank_file = samples_dir / "bank_statement.csv"
        self._write_csv(bank_file, self.records["bank_statement"])

        # Save internal_ledger.csv
        ledger_file = samples_dir / "internal_ledger.csv"
        self._write_csv(ledger_file, self.records["internal_ledger"])

        # Save gateway_export.csv
        gateway_file = samples_dir / "gateway_export.csv"
        self._write_csv(gateway_file, self.records["gateway_export"])

        # Save ground_truth/mapping.csv
        mapping_file = ground_truth_dir / "mapping.csv"
        self._write_mapping_csv(mapping_file, self.ground_truth_mapping)

        return {
            "bank_file": bank_file,
            "ledger_file": ledger_file,
            "gateway_file": gateway_file,
            "mapping_file": mapping_file,
        }

    def _write_csv(self, filepath: Path, records: List[Dict]):
        """Write records to CSV file."""
        if not records:
            return

        fieldnames = ["record_id", "date", "amount", "description", "reference_number"]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)

    def _write_mapping_csv(self, filepath: Path, mappings: List[Dict]):
        """Write ground truth mapping to CSV file."""
        # Flatten mappings for CSV (handle multiple ledger/gateway IDs)
        flattened = []
        for mapping in mappings:
            flattened.append({
                "transaction_id": mapping["transaction_id"],
                "bank_record_id": mapping.get("bank_record_id"),
                "ledger_record_ids": mapping.get("ledger_record_ids") or mapping.get("ledger_record_id"),
                "gateway_record_ids": mapping.get("gateway_record_ids") or mapping.get("gateway_record_id"),
                "pattern": mapping["pattern"],
            })

        fieldnames = ["transaction_id", "bank_record_id", "ledger_record_ids", "gateway_record_ids", "pattern"]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flattened)


def main():
    """Generate synthetic data and save to files."""
    gen = TransactionGenerator(seed=42)
    mappings, stats = gen.generate_all()
    gen.ground_truth_mapping = mappings

    files = gen.save_csvs()

    print("\n[OK] Data generation complete!")
    print(f"\n[FILES] Saved to:")
    print(f"  - {files['bank_file']}")
    print(f"  - {files['ledger_file']}")
    print(f"  - {files['gateway_file']}")
    print(f"  - {files['mapping_file']}")

    print(f"\n[STATS] Record counts:")
    print(f"  - Bank statement: {stats['bank_records']} records")
    print(f"  - Internal ledger: {stats['ledger_records']} records")
    print(f"  - Gateway export: {stats['gateway_records']} records")
    print(f"  - Total records across all sources: {stats['bank_records'] + stats['ledger_records'] + stats['gateway_records']}")


if __name__ == "__main__":
    main()
