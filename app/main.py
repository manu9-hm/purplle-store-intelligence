import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError


DATABASE_PATH = Path("data/intelligence/events.sqlite")
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


class IngestResponse(BaseModel):
    inserted_count: int
    duplicate_count: int
    invalid_count: int


class HealthResponse(BaseModel):
    status: str
    total_events: int
    last_event_timestamp: Optional[str]
    stores_seen: List[str]


class StoreMetricsResponse(BaseModel):
    store_id: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: Dict[str, float]
    queue_depth: int
    abandonment_rate: float


app = FastAPI(title="Purplle Store Intelligence API")


def get_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_database() -> None:
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


@app.on_event("startup")
def on_startup() -> None:
    init_database()


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
    valid_events: List[EventIn] = []
    invalid_count = 0

    for raw_event in raw_events:
        try:
            valid_events.append(EventIn.model_validate(raw_event))
        except ValidationError:
            invalid_count += 1

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
        invalid_count=invalid_count,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    init_database()
    with get_connection() as connection:
        total_events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        last_event_timestamp = connection.execute(
            "SELECT MAX(timestamp) FROM events"
        ).fetchone()[0]
        stores_seen = [
            row["store_id"]
            for row in connection.execute(
                "SELECT DISTINCT store_id FROM events ORDER BY store_id"
            ).fetchall()
        ]

    return HealthResponse(
        status="ok",
        total_events=total_events,
        last_event_timestamp=last_event_timestamp,
        stores_seen=stores_seen,
    )


def metadata_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        metadata = json.loads(row["metadata"])
    except json.JSONDecodeError:
        return {}
    return metadata if isinstance(metadata, dict) else {}


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
