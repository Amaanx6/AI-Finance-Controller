import csv
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
SAMPLES = BASE / "samples"
GROUND = BASE / "ground_truth"

bank_file = SAMPLES / "bank_statement.csv"
ledger_file = SAMPLES / "internal_ledger.csv"
gateway_file = SAMPLES / "gateway_export.csv"
map_file = GROUND / "mapping.csv"

# Load CSVs
def load_csv(path):
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)

bank = load_csv(bank_file)
ledger = load_csv(ledger_file)
gateway = load_csv(gateway_file)
mapping = load_csv(map_file)

# Index by record_id
bank_idx = {r['record_id']: r for r in bank}
ledger_idx = {r['record_id']: r for r in ledger}
gateway_idx = {r['record_id']: r for r in gateway}

# Helper
def parse_amount(a):
    try:
        return float(a)
    except:
        return None

def parse_date(d):
    try:
        return datetime.strptime(d, '%Y-%m-%d').date()
    except:
        return None

# Find mappings of interest
targets = [m for m in mapping if 'description_mismatch' in m.get('pattern','')]

print(f"Checking {len(targets)} description-mismatch transactions using amount±1% and date±3 days\n")

for t in targets:
    txn = t['transaction_id']
    bank_id = t.get('bank_record_id')
    # get bank record
    b = bank_idx.get(bank_id)
    if not b:
        print(f"Transaction {txn}: bank record {bank_id} not found")
        continue
    b_amount = parse_amount(b['amount'])
    b_date = parse_date(b['date'])
    print(f"---- Transaction {txn} (Bank {bank_id}) amount={b_amount} date={b_date}")
    # search ledger candidates
    ledger_candidates = []
    for r in ledger:
        a = parse_amount(r['amount'])
        d = parse_date(r['date'])
        if a is None or d is None:
            continue
        amt_diff = abs(a - b_amount) / b_amount
        date_diff = abs((d - b_date).days)
        if amt_diff <= 0.01 and date_diff <= 3:
            ledger_candidates.append((r, amt_diff, date_diff))
    gateway_candidates = []
    for r in gateway:
        a = parse_amount(r['amount'])
        d = parse_date(r['date'])
        if a is None or d is None:
            continue
        amt_diff = abs(a - b_amount) / b_amount
        date_diff = abs((d - b_date).days)
        if amt_diff <= 0.01 and date_diff <= 3:
            gateway_candidates.append((r, amt_diff, date_diff))

    print(f"  Ledger candidates ({len(ledger_candidates)}):")
    for r, amt_diff, date_diff in ledger_candidates:
        flag = ' (DECOY)' if 'DECOY' in r['record_id'] or 'decoy' in r.get('description','').lower() else ''
        print(f"    - {r['record_id']}, amount={r['amount']}, date={r['date']}, desc={r['description']}{flag}  (amt_diff={amt_diff:.4f}, days={date_diff})")

    print(f"  Gateway candidates ({len(gateway_candidates)}):")
    for r, amt_diff, date_diff in gateway_candidates:
        flag = ' (DECOY)' if 'DECOY' in r['record_id'] or 'decoy' in r.get('description','').lower() else ''
        print(f"    - {r['record_id']}, amount={r['amount']}, date={r['date']}, desc={r['description']}{flag}  (amt_diff={amt_diff:.4f}, days={date_diff})")

    print("")

print('Done.')
