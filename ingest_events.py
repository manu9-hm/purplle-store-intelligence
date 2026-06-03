import json
import requests
import glob
import os
import csv
import uuid

API_BASE = "http://127.0.0.1:8000"
INGEST_URL = f"{API_BASE}/events/ingest"
POS_INGEST_URL = f"{API_BASE}/pos/ingest"

# Specific files provided by Purplle organizers
EVENT_FILES = ["data/sample_eventsbe42122.jsonl"] + glob.glob("data/events/**/*.jsonl", recursive=True)
POS_FILE = "data/POS - sample transactionsb1e826f.csv"

BATCH_SIZE = 500

def run_ingestion():
    files = [f for f in EVENT_FILES if os.path.exists(f)]
    total_inserted = 0
    
    if not files:
        print("No event files found in data/events/")
        return

    def normalize_store_id(sid):
        if not sid:
            return "unknown"
        s = str(sid).strip()
        if s.lower().startswith("store_"):
            return s[6:]
        if s.lower().startswith("st"):
            return s[2:]
        return s

    for file_path in files:
        if os.path.getsize(file_path) == 0:
            print(f"Skipping empty file: {file_path}")
            continue

        print(f"Processing: {os.path.basename(file_path)}...")
        batch = []
        file_inserted = 0
        file_duplicate = 0
        file_invalid = 0

        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                # Map organizer keys to our schema
                mapped = {
                    "event_id": raw.get("event_id") or raw.get("queue_event_id") or str(uuid.uuid4()),
                    "store_id": normalize_store_id(raw.get("store_id") or raw.get("store_code")),
                    "camera_id": raw.get("camera_id") or "unknown",
                    "visitor_id": str(raw.get("visitor_id") or raw.get("id_token") or raw.get("track_id")),
                    "event_type": (raw.get("event_type") or "unknown").upper(),
                    "timestamp": (raw.get("timestamp") or raw.get("event_timestamp") or raw.get("event_time") or raw.get("queue_join_ts")),
                    "is_staff": raw.get("is_staff", False),
                    "confidence": raw.get("confidence") or 1.0,
                    "zone_id": raw.get("zone_id"),
                    "dwell_ms": int((raw.get("dwell_ms") or (raw.get("wait_seconds") * 1000 if raw.get("wait_seconds") is not None else 0)) or 0),
                    "metadata": raw
                }
                batch.append(mapped)

                if len(batch) >= BATCH_SIZE:
                    stats = send_batch(batch)
                    file_inserted += stats.get('inserted_count', 0)
                    file_duplicate += stats.get('duplicate_count', 0)
                    file_invalid += stats.get('invalid_count', 0)
                    batch = []

            if batch:
                stats = send_batch(batch)
                file_inserted += stats.get('inserted_count', 0)
                file_duplicate += stats.get('duplicate_count', 0)
                file_invalid += stats.get('invalid_count', 0)

        print(f"  Inserted: {file_inserted} | Duplicate: {file_duplicate} | Invalid: {file_invalid}")
        total_inserted += file_inserted

    # Process POS Transactions from CSV
    if os.path.exists(POS_FILE):
        print(f"\nProcessing POS Transactions: {POS_FILE}...")
        transactions = []
        with open(POS_FILE, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Convert DD-MM-YYYY + HH:MM:SS to ISO YYYY-MM-DDTHH:MM:SS for API compatibility
                raw_date = row.get("order_date", "")
                raw_time = row.get("order_time", "")
                try:
                    d, m, y = raw_date.split("-")
                    iso_ts = f"{y}-{m}-{d}T{raw_time}"
                except (ValueError, AttributeError) as e:
                    print(f"  [DEBUG] Failed to parse timestamp for order {row.get('order_id')}: {e}")
                    iso_ts = None

                if iso_ts:
                    transactions.append({
                        "transaction_id": row.get("order_id"),
                        "store_id": normalize_store_id(row.get("store_id")),
                        "timestamp": iso_ts,
                        "amount": float(row.get("total_amount") or 0.0),
                        "items_count": 1
                    })
                else:
                    print(f"  [DEBUG] Skipping row due to invalid/missing date components: {row}")
                
                if len(transactions) >= BATCH_SIZE:
                    resp = requests.post(POS_INGEST_URL, json={"transactions": transactions})
                    print(f"  [DEBUG] API Status: {resp.status_code} | Body: {resp.text}")
                    print(f"  Inserted {resp.json().get('inserted_count')} POS transactions.")
                    transactions = []
            
            if transactions:
                resp = requests.post(POS_INGEST_URL, json={"transactions": transactions})
                print(f"  [DEBUG] API Status: {resp.status_code} | Body: {resp.text}")
                print(f"  Inserted {resp.json().get('inserted_count')} POS transactions.")
    else:
        print(f"\nPOS file not found at {POS_FILE}")

    print(f"\nTotal events successfully inserted: {total_inserted}")
    
    if total_inserted > 0:
        verify_ingestion()

def send_batch(batch):
    try:
        response = requests.post(INGEST_URL, json={"events": batch})
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  Batch ingestion error: {e}")
        return {}

def verify_ingestion():
    try:
        health = requests.get(f"{API_BASE}/health").json()
        stores = health.get("stores_seen", [])
    except Exception as e:
        print(f"Could not fetch stores for verification: {e}")
        return

    if not stores:
        print("No stores found in database to verify.")
        return

    for store_id in stores:
        print(f"\nVerification for store: {store_id}")
        
        endpoints = [
            f"/stores/{store_id}/metrics",
            f"/stores/{store_id}/funnel",
            f"/stores/{store_id}/heatmap"
        ]
        
        for endpoint in endpoints:
            resp = requests.get(f"{API_BASE}{endpoint}")
            print(f"\nGET {endpoint} [{resp.status_code}]:\n{json.dumps(resp.json(), indent=2)}")

if __name__ == "__main__":
    run_ingestion()