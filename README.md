# purplle-store-intelligence

## Intelligence API - Phase 1

### Local Development

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
  "feed_status": "STALE_FEED",
  "minutes_since_last_event": null,
  "last_event_timestamp": null,
  "stores_seen": []
}
```

`feed_status` is `OK` when the latest ingested event is 10 minutes old or
newer. It is `STALE_FEED` when there are no events, the latest timestamp cannot
be parsed, or the latest event is older than 10 minutes.

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
  "invalid_count": 0,
  "invalid_events": []
}
```

Submitting the same `event_id` again is idempotent:

```json
{
  "inserted_count": 0,
  "duplicate_count": 1,
  "invalid_count": 0,
  "invalid_events": []
}
```

Invalid events do not block valid events in the same batch. The response includes
one entry per invalid event:

```json
{
  "inserted_count": 1,
  "duplicate_count": 0,
  "invalid_count": 1,
  "invalid_events": [
    {
      "index": 1,
      "event_id": "bad-event-001",
      "error": "confidence: Field required"
    }
  ]
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

Anomaly assumptions for Phase 1:

* All anomaly checks exclude `is_staff=true`.
* `dead_zone` checks for no non-staff `ZONE_ENTER` events in the latest 30-minute event window.
* `queue_spike` compares the maximum current `metadata.queue_depth` from non-staff `BILLING_QUEUE_JOIN` events with the previous 120-minute average and flags only a materially higher current queue.
* `conversion_drop` uses the same billing-interaction conversion proxy as `conversion_rate` until POS/order events are ingested.
* Anomaly windows are anchored to the latest non-staff event timestamp in SQLite because the challenge data is offline video-derived data, not a live stream.

### Live Dashboard

The project includes a professional real-time dashboard built with Streamlit and Plotly.

```powershell
# 1. Ensure the API is running (FastAPI)
# 2. Launch the dashboard
streamlit run app.py
```

The dashboard automatically refreshes every 5 seconds and visualizes KPIs, visitor funnels, and zone heatmaps.

### Docker Usage

The project includes a `docker-compose.yml` for easy deployment.

```powershell
# Build and start the API
docker-compose up --build -d

# Stop the API
docker-compose down
```

### Testing

Automated tests cover ingest, analytics logic, and error handling.

```powershell
# Run all tests
pytest app/test_api.py -v

# Run tests with coverage report
pytest --cov=app app/
```

### Detection Pipeline Usage

#### 1. Zone Calibration
Use the calibrator to define zones (browsing, entrance, billing) for a camera.

```powershell
python pipeline/zone_calibrator.py --video data/videos/cam3.mp4 --output configs/zones/cam3_zones.json --camera-id CAM3 --zone-id entrance_zone --zone-type entrance
```

#### 2. Event Generation
Run the detection pipeline to process video and generate a JSONL event stream.

```powershell
python pipeline/generate_events.py --video data/videos/cam1.mp4 --camera-id CAM1 --zones configs/zones/cam1_zones.json --output data/events/cam1_events.jsonl
```

### Feeding Events into API

Once events are generated in `.jsonl` format, they can be ingested into the API.

```powershell
python ingest_events.py
```

### Troubleshooting

**1. Database Unavailable (503 Error)**
The API returns `DATABASE_UNAVAILABLE` if the SQLite file cannot be accessed. Check volume permissions if running in Docker.

**2. Stale Feed Warning**
The `/health` endpoint reports `STALE_FEED` if no events have been ingested in the last 10 minutes. 

**3. Dependency Conflicts**
Ensure `opencv-python` version is compatible with `numpy 1.24.4`.
