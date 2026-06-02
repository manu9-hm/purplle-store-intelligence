import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError


DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", "data/intelligence/events.sqlite"))
MAX_BATCH_SIZE = 500


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    visitor_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    zone_id: Optional[str] = None
    dwell_ms: Optional[int] = Field(default=None, ge=0)
    is_staff: bool
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InvalidEvent(BaseModel):
    index: int
    event_id: Optional[str]
    error: str


class IngestResponse(BaseModel):
    inserted_count: int
    duplicate_count: int
    invalid_count: int
    invalid_events: List[InvalidEvent] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    feed_status: str
    minutes_since_last_event: Optional[float]
    last_event_timestamp: Optional[str]
    stores_seen: List[str]


class StoreMetricsResponse(BaseModel):
    store_id: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: Dict[str, float]
    queue_depth: int
    abandonment_rate: float


class FunnelDropoff(BaseModel):
    entry_to_zone_pct: float
    zone_to_billing_pct: float
    billing_to_purchase_pct: float


class StoreFunnelResponse(BaseModel):
    store_id: str
    entry_count: int
    zone_visit_count: int
    billing_queue_count: int
    purchase_count: int
    dropoff: FunnelDropoff


class HeatmapZone(BaseModel):
    zone_id: str
    visit_frequency: int
    avg_dwell_ms: float
    normalized_score: int


class StoreHeatmapResponse(BaseModel):
    store_id: str
    zones: List[HeatmapZone]
    data_confidence: str


class StoreAnomaly(BaseModel):
    type: str
    severity: str
    suggested_action: str


class StoreAnomaliesResponse(BaseModel):
    store_id: str
    anomalies: List[StoreAnomaly]


app = FastAPI(title="Purplle Store Intelligence API")

@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    start_time = time.time()
    trace_id = str(uuid.uuid4())
    
    response: Response = await call_next(request)
    
    latency_ms = round((time.time() - start_time) * 1000, 2)
    
    # Extract store_id from path if present (e.g., /stores/purplle_001/metrics)
    store_id = None
    path_parts = request.url.path.strip("/").split("/")
    if len(path_parts) >= 2 and path_parts[0] == "stores":
        store_id = path_parts[1]

    log_data = {
        "trace_id": trace_id,
        "store_id": store_id,
        "endpoint": request.url.path,
        "latency_ms": latency_ms,
        "status_code": response.status_code,
    }
    
    # Add event_count for ingest endpoint if available in request state
    if request.url.path == "/events/ingest" and hasattr(request.state, "event_count"):
        log_data["event_count"] = request.state.event_count
        
    print(json.dumps(log_data))
    return response


class DatabaseUnavailableError(Exception):
    pass


@app.exception_handler(DatabaseUnavailableError)
def database_unavailable_handler(
    request: Request, exc: DatabaseUnavailableError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "DATABASE_UNAVAILABLE"},
    )


@app.exception_handler(sqlite3.Error)
def sqlite_error_handler(request: Request, exc: sqlite3.Error) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "DATABASE_UNAVAILABLE"},
    )


def get_connection() -> sqlite3.Connection:
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DATABASE_PATH)
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseUnavailableError() from exc
    connection.row_factory = sqlite3.Row
    return connection


def init_database() -> None:
    try:
        with get_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    visitor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    zone_id TEXT,
                    dwell_ms INTEGER,
                    is_staff INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    metadata TEXT NOT NULL,
                    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_store_id ON events(store_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_store_staff
                ON events(store_id, is_staff)
                """
            )
    except sqlite3.Error as exc:
        raise DatabaseUnavailableError() from exc


@app.on_event("startup")
def on_startup() -> None:
    try:
        init_database()
    except DatabaseUnavailableError:
        pass


def parse_event_payload(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        events = payload
    elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
        events = payload["events"]
    else:
        raise HTTPException(
            status_code=422,
            detail="Request body must be an event array or an object with an events array.",
        )

    if len(events) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=422,
            detail=f"Batch size must be <= {MAX_BATCH_SIZE} events.",
        )
    return events


@app.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(request: Request) -> IngestResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc

    raw_events = parse_event_payload(payload)
    request.state.event_count = len(raw_events)
    valid_events: List[EventIn] = []
    invalid_events: List[InvalidEvent] = []

    for index, raw_event in enumerate(raw_events):
        try:
            valid_events.append(EventIn.model_validate(raw_event))
        except ValidationError as exc:
            event_id = raw_event.get("event_id") if isinstance(raw_event, dict) else None
            first_error = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(part) for part in first_error.get("loc", ()))
            message = first_error.get("msg", "Invalid event.")
            error = f"{location}: {message}" if location else message
            invalid_events.append(
                InvalidEvent(index=index, event_id=event_id, error=error)
            )

    inserted_count = 0
    duplicate_count = 0

    with get_connection() as connection:
        for event in valid_events:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id,
                    store_id,
                    camera_id,
                    visitor_id,
                    event_type,
                    timestamp,
                    zone_id,
                    dwell_ms,
                    is_staff,
                    confidence,
                    metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.store_id,
                    event.camera_id,
                    event.visitor_id,
                    event.event_type,
                    event.timestamp,
                    event.zone_id,
                    event.dwell_ms,
                    int(event.is_staff),
                    event.confidence,
                    json.dumps(event.metadata, separators=(",", ":")),
                ),
            )
            if cursor.rowcount == 1:
                inserted_count += 1
            else:
                duplicate_count += 1

    return IngestResponse(
        inserted_count=inserted_count,
        duplicate_count=duplicate_count,
        invalid_count=len(invalid_events),
        invalid_events=invalid_events,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    init_database()
    with get_connection() as connection:
        last_event_timestamp = connection.execute(
            "SELECT MAX(timestamp) FROM events"
        ).fetchone()[0]
        stores_seen = [
            row["store_id"]
            for row in connection.execute(
                "SELECT DISTINCT store_id FROM events ORDER BY store_id"
            ).fetchall()
        ]

    minutes_since_last_event = None
    feed_status = "STALE_FEED"
    if last_event_timestamp is not None:
        last_event_time = parse_event_timestamp(last_event_timestamp)
        if last_event_time is not None:
            now = datetime.now(last_event_time.tzinfo)
            minutes_since_last_event = round(
                max(0.0, (now - last_event_time).total_seconds() / 60),
                2,
            )
            feed_status = (
                "STALE_FEED" if minutes_since_last_event > 10 else "OK"
            )

    return HealthResponse(
        status="ok",
        feed_status=feed_status,
        minutes_since_last_event=minutes_since_last_event,
        last_event_timestamp=last_event_timestamp,
        stores_seen=stores_seen,
    )


def metadata_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        metadata = json.loads(row["metadata"])
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


def pct_dropoff(previous_count: int, next_count: int) -> float:
    if previous_count == 0:
        return 0.0
    retained_pct = next_count / previous_count
    return round(max(0.0, 1.0 - retained_pct) * 100, 2)


def parse_event_timestamp(timestamp: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def conversion_proxy_rate(rows: List[sqlite3.Row]) -> float:
    visitors = {row["visitor_id"] for row in rows}
    if not visitors:
        return 0.0
    converted_visitors = {
        row["visitor_id"]
        for row in rows
        if row["event_type"] == "BILLING_QUEUE_JOIN"
        or "billing" in (row["zone_id"] or "").lower()
    }
    return len(converted_visitors) / len(visitors)


@app.get("/stores/{store_id}/metrics", response_model=StoreMetricsResponse)
def store_metrics(store_id: str) -> StoreMetricsResponse:
    init_database()
    with get_connection() as connection:
        unique_visitors = connection.execute(
            """
            SELECT COUNT(DISTINCT visitor_id)
            FROM events
            WHERE store_id = ? AND is_staff = 0
            """,
            (store_id,),
        ).fetchone()[0]

        dwell_rows = connection.execute(
            """
            SELECT zone_id, AVG(dwell_ms) AS avg_dwell_ms
            FROM events
            WHERE
                store_id = ?
                AND is_staff = 0
                AND event_type = 'ZONE_DWELL'
                AND zone_id IS NOT NULL
                AND dwell_ms IS NOT NULL
            GROUP BY zone_id
            ORDER BY zone_id
            """,
            (store_id,),
        ).fetchall()
        avg_dwell_per_zone = {
            row["zone_id"]: round(float(row["avg_dwell_ms"]), 2)
            for row in dwell_rows
        }

        # Placeholder conversion: until POS/order events are ingested, treat a
        # non-staff visitor reaching billing as a conversion proxy.
        converted_visitors = connection.execute(
            """
            SELECT COUNT(DISTINCT visitor_id)
            FROM events
            WHERE
                store_id = ?
                AND is_staff = 0
                AND (
                    event_type = 'BILLING_QUEUE_JOIN'
                    OR lower(COALESCE(zone_id, '')) LIKE '%billing%'
                )
            """,
            (store_id,),
        ).fetchone()[0]
        conversion_rate = (
            round(converted_visitors / unique_visitors, 4)
            if unique_visitors
            else 0.0
        )

        billing_rows = connection.execute(
            """
            SELECT metadata
            FROM events
            WHERE
                store_id = ?
                AND is_staff = 0
                AND event_type = 'BILLING_QUEUE_JOIN'
            """,
            (store_id,),
        ).fetchall()

    queue_depth = 0
    for row in billing_rows:
        raw_queue_depth = metadata_from_row(row).get("queue_depth", 0)
        if isinstance(raw_queue_depth, (int, float)):
            queue_depth = max(queue_depth, int(raw_queue_depth))

    return StoreMetricsResponse(
        store_id=store_id,
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell_per_zone,
        queue_depth=queue_depth,
        # No explicit cart/payment-abandonment signal exists yet.
        abandonment_rate=0.0,
    )


@app.get("/stores/{store_id}/funnel", response_model=StoreFunnelResponse)
def store_funnel(store_id: str) -> StoreFunnelResponse:
    init_database()
    with get_connection() as connection:
        funnel_row = connection.execute(
            """
            WITH session_flags AS (
                SELECT
                    visitor_id,
                    MAX(
                        CASE
                            WHEN event_type IN ('ENTRY', 'REENTRY') THEN 1
                            ELSE 0
                        END
                    ) AS has_entry,
                    MAX(
                        CASE
                            WHEN
                                event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
                                AND zone_id IS NOT NULL
                                AND lower(zone_id) NOT LIKE '%billing%'
                            THEN 1
                            ELSE 0
                        END
                    ) AS has_zone_visit,
                    MAX(
                        CASE
                            WHEN event_type = 'BILLING_QUEUE_JOIN' THEN 1
                            ELSE 0
                        END
                    ) AS has_billing_queue,
                    MAX(
                        CASE
                            WHEN event_type IN (
                                'PURCHASE',
                                'PURCHASE_COMPLETE',
                                'ORDER_COMPLETED',
                                'PAYMENT_SUCCESS',
                                'CHECKOUT_COMPLETE'
                            )
                            THEN 1
                            ELSE 0
                        END
                    ) AS has_purchase
                FROM events
                WHERE store_id = ? AND is_staff = 0
                GROUP BY visitor_id
            )
            SELECT
                SUM(has_entry) AS entry_count,
                SUM(has_zone_visit) AS zone_visit_count,
                SUM(
                    CASE 
                        WHEN has_entry = 1 AND has_billing_queue = 1 THEN 1 
                        ELSE 0 
                    END
                ) AS billing_queue_count,
                SUM(
                    CASE 
                        WHEN has_entry = 1 AND has_purchase = 1 THEN 1 
                        ELSE 0 
                    END
                ) AS purchase_count
            FROM session_flags
            """,
            (store_id,),
        ).fetchone()

    entry_count = int(funnel_row["entry_count"] or 0)
    zone_visit_count = int(funnel_row["zone_visit_count"] or 0)
    billing_queue_count = int(funnel_row["billing_queue_count"] or 0)
    purchase_count = int(funnel_row["purchase_count"] or 0)

    return StoreFunnelResponse(
        store_id=store_id,
        entry_count=entry_count,
        zone_visit_count=zone_visit_count,
        billing_queue_count=billing_queue_count,
        purchase_count=purchase_count,
        dropoff=FunnelDropoff(
            entry_to_zone_pct=pct_dropoff(entry_count, zone_visit_count),
            zone_to_billing_pct=pct_dropoff(zone_visit_count, billing_queue_count),
            billing_to_purchase_pct=pct_dropoff(
                billing_queue_count, purchase_count
            ),
        ),
    )


@app.get("/stores/{store_id}/heatmap", response_model=StoreHeatmapResponse)
def store_heatmap(store_id: str) -> StoreHeatmapResponse:
    init_database()
    with get_connection() as connection:
        unique_sessions = connection.execute(
            """
            SELECT COUNT(DISTINCT visitor_id)
            FROM events
            WHERE store_id = ? AND is_staff = 0
            """,
            (store_id,),
        ).fetchone()[0]

        zone_rows = connection.execute(
            """
            WITH zone_ids AS (
                SELECT DISTINCT zone_id
                FROM events
                WHERE
                    store_id = ?
                    AND is_staff = 0
                    AND zone_id IS NOT NULL
                    AND event_type IN ('ZONE_ENTER', 'ZONE_DWELL')
            ),
            visit_counts AS (
                SELECT zone_id, COUNT(*) AS visit_frequency
                FROM events
                WHERE
                    store_id = ?
                    AND is_staff = 0
                    AND event_type = 'ZONE_ENTER'
                    AND zone_id IS NOT NULL
                GROUP BY zone_id
            ),
            dwell_averages AS (
                SELECT zone_id, AVG(dwell_ms) AS avg_dwell_ms
                FROM events
                WHERE
                    store_id = ?
                    AND is_staff = 0
                    AND event_type = 'ZONE_DWELL'
                    AND zone_id IS NOT NULL
                    AND dwell_ms IS NOT NULL
                GROUP BY zone_id
            )
            SELECT
                zone_ids.zone_id,
                COALESCE(visit_counts.visit_frequency, 0) AS visit_frequency,
                COALESCE(dwell_averages.avg_dwell_ms, 0) AS avg_dwell_ms
            FROM zone_ids
            LEFT JOIN visit_counts ON visit_counts.zone_id = zone_ids.zone_id
            LEFT JOIN dwell_averages ON dwell_averages.zone_id = zone_ids.zone_id
            ORDER BY zone_ids.zone_id
            """,
            (store_id, store_id, store_id),
        ).fetchall()

    max_frequency = max(
        (int(row["visit_frequency"]) for row in zone_rows),
        default=0,
    )
    zones = []
    for row in zone_rows:
        visit_frequency = int(row["visit_frequency"])
        normalized_score = (
            round((visit_frequency / max_frequency) * 100)
            if max_frequency
            else 0
        )
        zones.append(
            HeatmapZone(
                zone_id=row["zone_id"],
                visit_frequency=visit_frequency,
                avg_dwell_ms=round(float(row["avg_dwell_ms"]), 2),
                normalized_score=normalized_score,
            )
        )

    return StoreHeatmapResponse(
        store_id=store_id,
        zones=zones,
        data_confidence="HIGH" if unique_sessions >= 20 else "LOW",
    )


@app.get("/stores/{store_id}/anomalies", response_model=StoreAnomaliesResponse)
def store_anomalies(store_id: str) -> StoreAnomaliesResponse:
    init_database()
    with get_connection() as connection:
        latest_timestamp = connection.execute(
            """
            SELECT MAX(timestamp)
            FROM events
            WHERE store_id = ? AND is_staff = 0
            """,
            (store_id,),
        ).fetchone()[0]

        if latest_timestamp is None:
            return StoreAnomaliesResponse(store_id=store_id, anomalies=[])

        event_rows = connection.execute(
            """
            SELECT visitor_id, event_type, timestamp, zone_id, metadata
            FROM events
            WHERE store_id = ? AND is_staff = 0
            """,
            (store_id,),
        ).fetchall()

    anchor_time = parse_event_timestamp(latest_timestamp)
    if anchor_time is None:
        return StoreAnomaliesResponse(store_id=store_id, anomalies=[])

    current_window_start = anchor_time - timedelta(minutes=30)
    baseline_window_start = anchor_time - timedelta(minutes=150)

    current_rows = []
    baseline_rows = []
    for row in event_rows:
        event_time = parse_event_timestamp(row["timestamp"])
        if event_time is None:
            continue
        if event_time >= current_window_start:
            current_rows.append(row)
        elif event_time >= baseline_window_start:
            baseline_rows.append(row)

    anomalies: List[StoreAnomaly] = []

    # Offline challenge videos are not live streams, so "last 30 minutes" is
    # anchored to the latest non-staff event in SQLite, not the server clock.
    recent_zone_visit_count = sum(
        1
        for row in current_rows
        if row["event_type"] == "ZONE_ENTER" and row["zone_id"] is not None
    )
    if recent_zone_visit_count == 0:
        anomalies.append(
            StoreAnomaly(
                type="dead_zone",
                severity="WARN",
                suggested_action="Inspect merchandising and customer flow.",
            )
        )

    # Queue spike heuristic: compare the maximum observed queue depth in the
    # latest 30 minutes with the average queue depth from the previous 120
    # minutes. Flag only when the current max is meaningfully above baseline.
    current_queue_depths = []
    baseline_queue_depths = []
    for rows, target in (
        (current_rows, current_queue_depths),
        (baseline_rows, baseline_queue_depths),
    ):
        for row in rows:
            if row["event_type"] != "BILLING_QUEUE_JOIN":
                continue
            raw_queue_depth = metadata_from_row(row).get("queue_depth")
            if isinstance(raw_queue_depth, (int, float)):
                target.append(int(raw_queue_depth))

    if current_queue_depths and baseline_queue_depths:
        current_max_queue_depth = max(current_queue_depths)
        baseline_avg_queue_depth = sum(baseline_queue_depths) / len(
            baseline_queue_depths
        )
        if (
            current_max_queue_depth >= 4
            and current_max_queue_depth >= baseline_avg_queue_depth * 1.5
            and current_max_queue_depth - baseline_avg_queue_depth >= 2
        ):
            anomalies.append(
                StoreAnomaly(
                    type="queue_spike",
                    severity="CRITICAL",
                    suggested_action="Open another billing counter or redirect staff to checkout.",
                )
            )

    return StoreAnomaliesResponse(store_id=store_id, anomalies=anomalies)