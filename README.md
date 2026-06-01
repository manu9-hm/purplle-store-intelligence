# purplle-store-intelligence

## Intelligence API - Phase 1

Run the ingest API:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The API creates a SQLite database at:

```text
data/intelligence/events.sqlite
```

Health check:

```powershell
curl http://127.0.0.1:8000/health
```

Example response:

```json
{
  "status": "ok",
  "total_events": 0,
  "last_event_timestamp": null,
  "stores_seen": []
}
```

Ingest events:

```powershell
curl -X POST http://127.0.0.1:8000/events/ingest `
  -H "Content-Type: application/json" `
  -d "{\"events\":[{\"event_id\":\"evt-001\",\"store_id\":\"purplle_store_001\",\"camera_id\":\"CAM5\",\"visitor_id\":\"CAM5-124\",\"event_type\":\"ZONE_ENTER\",\"timestamp\":\"2026-06-01T05:10:13.593Z\",\"zone_id\":\"billing_zone\",\"dwell_ms\":null,\"is_staff\":true,\"confidence\":0.8687,\"metadata\":{\"track_id\":124,\"staff_confidence\":0.7241,\"staff_reason\":\"stable_staff_majority_vote\",\"staff_observations\":233}}]}"
```

Example response:

```json
{
  "inserted_count": 1,
  "duplicate_count": 0,
  "invalid_count": 0
}
```

Submitting the same `event_id` again is idempotent:

```json
{
  "inserted_count": 0,
  "duplicate_count": 1,
  "invalid_count": 0
}
```

Store metrics:

```powershell
curl http://127.0.0.1:8000/stores/purplle_store_001/metrics
```

Example response:

```json
{
  "store_id": "purplle_store_001",
  "unique_visitors": 42,
  "conversion_rate": 0.2143,
  "avg_dwell_per_zone": {
    "billing_zone": 3002.5,
    "skincare_zone": 7421.33
  },
  "queue_depth": 3,
  "abandonment_rate": 0.0
}
```

Metrics assumptions for Phase 1:

* All customer metrics exclude `is_staff=true`.
* `unique_visitors` counts distinct non-staff `visitor_id` values.
* `avg_dwell_per_zone` uses non-staff `ZONE_DWELL` events with `dwell_ms`.
* `queue_depth` is the maximum `metadata.queue_depth` observed on non-staff `BILLING_QUEUE_JOIN` events.
* `conversion_rate` is a placeholder until POS/order events are ingested. For now it means distinct non-staff visitors with a billing interaction divided by distinct non-staff visitors.
* `abandonment_rate` is `0.0` until explicit abandonment or payment outcome signals are available.
