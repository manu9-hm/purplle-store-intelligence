import json
import requests
import glob
import os

API_BASE = "http://127.0.0.1:8000"
INGEST_URL = f"{API_BASE}/events/ingest"
EVENTS_DIR = "data/events/*.jsonl"
BATCH_SIZE = 500

def run_ingestion():
    files = glob.glob(EVENTS_DIR)
    total_inserted = 0
    
    if not files:
        print("No event files found in data/events/")
        return

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
                batch.append(json.loads(line))

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
    store_id = "purplle_store_001"
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