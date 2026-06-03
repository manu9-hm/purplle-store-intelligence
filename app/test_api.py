# PROMPT:
# "Generate a comprehensive FastAPI test suite for a retail intelligence API. 
# Include tests for event ingestion (success and idempotency), data validation errors, 
# staff exclusion logic in metrics, funnel conversion with re-entry scenarios, 
# heatmap score normalization, and anomaly detection (dead_zone). 
# Also, mock database failures to test 503 error handling."

# CHANGES MADE:
# - Added specific logic to verify funnel dropoff calculation accuracy.
# - Implemented precise timestamp manipulation for the 'stale_feed' health test.
# - Added batch size limit validation test.

import os
import sqlite3
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app, DATABASE_PATH, init_database

client = TestClient(app)
TEST_DB = Path("data/test_events.sqlite")

@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    # Use a separate test database
    if TEST_DB.exists():
        TEST_DB.unlink()
    monkeypatch.setattr("app.main.DATABASE_PATH", TEST_DB)
    init_database()
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()

def test_health_endpoint_empty():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["feed_status"] == "STALE_FEED"
    assert data["stores_seen"] == []

def test_ingest_success_and_idempotency():
    event = {
        "event_id": "evt-123",
        "store_id": "store-A",
        "camera_id": "CAM1",
        "visitor_id": "visitor-999",
        "event_type": "ENTRY",
        "timestamp": "2024-01-01T12:00:00Z",
        "is_staff": False,
        "confidence": 0.95
    }
    
    # First ingest
    resp1 = client.post("/events/ingest", json={"events": [event]})
    assert resp1.status_code == 200
    assert resp1.json()["inserted_count"] == 1
    
    # Duplicate ingest (idempotency)
    resp2 = client.post("/events/ingest", json={"events": [event]})
    assert resp2.status_code == 200
    assert resp2.json()["inserted_count"] == 0
    assert resp2.json()["duplicate_count"] == 1

def test_ingest_invalid_event():
    bad_event = {
        "event_id": "evt-bad",
        "store_id": "store-A",
        # missing camera_id
        "visitor_id": "visitor-1",
        "event_type": "ENTRY",
        "timestamp": "2024-01-01T12:00:00Z",
        "is_staff": False,
        "confidence": 0.95
    }
    response = client.post("/events/ingest", json={"events": [bad_event]})
    assert response.status_code == 200
    assert response.json()["invalid_count"] == 1
    assert "camera_id" in response.json()["invalid_events"][0]["error"]

def test_metrics_and_staff_exclusion():
    events = [
        {
            "event_id": "e1", "store_id": "S1", "camera_id": "C1", "visitor_id": "V1",
            "event_type": "ZONE_ENTER", "timestamp": "2024-01-01T10:00:00Z",
            "is_staff": False, "confidence": 1.0, "zone_id": "billing_zone"
        },
        {
            "event_id": "e2", "store_id": "S1", "camera_id": "C1", "visitor_id": "STAFF1",
            "event_type": "ZONE_ENTER", "timestamp": "2024-01-01T10:05:00Z",
            "is_staff": True, "confidence": 1.0, "zone_id": "billing_zone"
        }
    ]
    client.post("/events/ingest", json={"events": events})
    
    response = client.get("/stores/S1/metrics")
    assert response.status_code == 200
    # STAFF1 should be excluded
    assert response.json()["unique_visitors"] == 1

def test_funnel_reentry_scenario():
    # Scenario: Visitor enters, visits zone, joins billing, then re-enters later.
    # Should count as 1 conversion in the funnel logic because it groups by visitor_id.
    visitor = "V-FUNNEL"
    events = [
        {"event_id": "f1", "store_id": "S1", "camera_id": "C3", "visitor_id": visitor, "event_type": "ENTRY", "timestamp": "2024-01-01T10:00:00Z", "is_staff": False, "confidence": 1.0},
        {"event_id": "f2", "store_id": "S1", "camera_id": "C1", "visitor_id": visitor, "event_type": "ZONE_ENTER", "timestamp": "2024-01-01T10:01:00Z", "is_staff": False, "confidence": 1.0, "zone_id": "makeup_zone"},
        {"event_id": "f3", "store_id": "S1", "camera_id": "C5", "visitor_id": visitor, "event_type": "BILLING_QUEUE_JOIN", "timestamp": "2024-01-01T10:05:00Z", "is_staff": False, "confidence": 1.0, "zone_id": "billing_zone"},
        {"event_id": "f4", "store_id": "S1", "camera_id": "C5", "visitor_id": visitor, "event_type": "PURCHASE", "timestamp": "2024-01-01T10:06:00Z", "is_staff": False, "confidence": 1.0},
        {"event_id": "f5", "store_id": "S1", "camera_id": "C3", "visitor_id": visitor, "event_type": "REENTRY", "timestamp": "2024-01-01T11:00:00Z", "is_staff": False, "confidence": 1.0}
    ]
    client.post("/events/ingest", json={"events": events})
    
    # Ingest a transaction to satisfy POS-based funnel purchase count
    transaction = {
        "transaction_id": "tx-funnel", "store_id": "1", "timestamp": "2024-01-01T10:07:00Z",
        "amount": 50.0, "items_count": 2
    }
    client.post("/pos/ingest", json={"transactions": [transaction]})
    
    response = client.get("/stores/S1/funnel")
    data = response.json()
    assert data["entry_count"] == 1
    assert data["purchase_count"] == 1
    assert data["dropoff"]["billing_to_purchase_pct"] == 0.0

def test_heatmap_normalization():
    events = [
        {"event_id": "h1", "store_id": "S1", "camera_id": "C1", "visitor_id": "v1", "event_type": "ZONE_ENTER", "timestamp": "2024-01-01T10:00:00Z", "is_staff": False, "confidence": 1.0, "zone_id": "Z1"},
        {"event_id": "h2", "store_id": "S1", "camera_id": "C1", "visitor_id": "v1", "event_type": "ZONE_ENTER", "timestamp": "2024-01-01T10:01:00Z", "is_staff": False, "confidence": 1.0, "zone_id": "Z1"},
        {"event_id": "h3", "store_id": "S1", "camera_id": "C1", "visitor_id": "v2", "event_type": "ZONE_ENTER", "timestamp": "2024-01-01T10:02:00Z", "is_staff": False, "confidence": 1.0, "zone_id": "Z2"}
    ]
    client.post("/events/ingest", json={"events": events})
    
    response = client.get("/stores/S1/heatmap")
    zones = {z["zone_id"]: z for z in response.json()["zones"]}
    assert zones["Z1"]["visit_frequency"] == 2
    assert zones["Z1"]["normalized_score"] == 100
    assert zones["Z2"]["normalized_score"] == 50

def test_anomalies_dead_zone():
    # If there are events but no ZONE_ENTER events in the window, dead_zone is triggered.
    event = {
        "event_id": "a1", "store_id": "S1", "camera_id": "C1", "visitor_id": "v1",
        "event_type": "OTHER", "timestamp": "2024-01-01T10:00:00Z",
        "is_staff": False, "confidence": 1.0
    }
    client.post("/events/ingest", json={"events": [event]})
    
    response = client.get("/stores/S1/anomalies")
    anomalies = [a["type"] for a in response.json()["anomalies"]]
    assert "dead_zone" in anomalies

def test_empty_store_endpoints():
    store_id = "non-existent"
    assert client.get(f"/stores/{store_id}/metrics").status_code == 200
    assert client.get(f"/stores/{store_id}/funnel").status_code == 200
    assert client.get(f"/stores/{store_id}/heatmap").status_code == 200
    assert client.get(f"/stores/{store_id}/anomalies").status_code == 200

def test_database_unavailable_handling(monkeypatch):
    def mock_get_connection():
        raise sqlite3.Error("Mock DB Error")
    
    monkeypatch.setattr("app.main.get_connection", mock_get_connection)
    
    # Metrics endpoint should now return 503
    response = client.get("/stores/any/metrics")
    assert response.status_code == 503
    assert response.json()["error"] == "DATABASE_UNAVAILABLE"

def test_ingest_batch_size_limit():
    too_many = [{"event_id": f"e{i}", "store_id": "S", "camera_id": "C", "visitor_id": "V", "event_type": "T", "timestamp": "2024-01-01T00:00:00Z", "is_staff": False, "confidence": 1.0} for i in range(501)]
    response = client.post("/events/ingest", json={"events": too_many})
    assert response.status_code == 422
    assert "Batch size" in response.json()["detail"]

def test_all_staff_events():
    event = {
        "event_id": "s1", "store_id": "S1", "camera_id": "C1", "visitor_id": "STAFF",
        "event_type": "ZONE_ENTER", "timestamp": "2024-01-01T10:00:00Z",
        "is_staff": True, "confidence": 1.0, "zone_id": "Z1"
    }
    client.post("/events/ingest", json={"events": [event]})
    
    # Metrics should show 0 visitors because only staff exists
    metrics = client.get("/stores/S1/metrics").json()
    assert metrics["unique_visitors"] == 0

def test_health_with_stale_feed():
    # Ingest an old event to verify STALE_FEED status (threshold is 10 mins)
    event = {
        "event_id": "old1", "store_id": "S1", "camera_id": "C1", "visitor_id": "v1",
        "event_type": "ENTRY", "timestamp": "2020-01-01T12:00:00Z", "is_staff": False, "confidence": 1.0
    }
    client.post("/events/ingest", json={"events": [event]})
    response = client.get("/health")
    assert response.json()["feed_status"] == "STALE_FEED"