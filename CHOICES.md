# CHOICES.md - Engineering Trade-offs

## Decision 1: Detection Model Choice
*   **Options Considered**: YOLOv8, Faster R-CNN, DETR.
*   **AI Suggestion**: YOLOv8 for "real-world edge compatibility."
*   **Final Choice**: **YOLOv8**.
*   **Why**: It provides the best trade-off between inference speed and detection accuracy, especially for detecting small objects (foot points) in high-angle retail cameras.

## Decision 2: Event Schema Design
*   **Options Considered**: 
    1. Raw Detection Logs (Every bounding box)
    2. Aggregated Daily Stats
    3. Normalized Event Schema (Atomic state changes)
*   **AI Suggestion**: Normalized Event Schema.
*   **Final Choice**: **Normalized Event Schema**.
*   **Why**: Raw detections are too heavy for API ingestion, while daily stats lose the granularity needed for funnel analysis. The normalized schema (ENTRY, ZONE_DWELL, etc.) allows for complex re-entry logic and retro-active analytics without re-processing video.

## Decision 3: API Architecture Choice
*   **Options Considered**: Flask, FastAPI, Django.
*   **AI Suggestion**: FastAPI.
*   **Final Choice**: **FastAPI**.
*   **Why**: 
    1.  **Speed**: Essential for the `/events/ingest` endpoint which may handle high-frequency bursts.
    2.  **Validation**: Built-in Pydantic integration ensures data integrity before it touches the database.
    3.  **Documentation**: Auto-generated Swagger/OpenAPI docs simplified the integration with the detection pipeline.

## Decision 4: Database Selection
*   **Options Considered**: PostgreSQL, MongoDB, SQLite.
*   **AI Suggestion**: PostgreSQL.
*   **Final Choice**: **SQLite**.
*   **Why**: For Phase 1 and the specific challenge requirements, the "zero-configuration" nature of SQLite was superior. It allows the entire system to be containerized and portable without a separate DB management overhead, while still supporting the complex CTEs (Common Table Expressions) used in the Funnel analytics.

## Decision 5: Funnel Session Validation
*   **Observation**: CAM3 (entrance camera) produced no entry events in the provided sample; customers were already present in CAM1/CAM2 when recording started.
*   **Logic**: **Evidence-First Analytics**. Funnel stages are counted based on observed behavior (e.g., reaching billing) even if the entrance was not captured.
*   **Why**: Practical retail intelligence must account for "pre-existing" visitors. Removing strict sequence requirements ensures the funnel remains representative even when recording sessions are fragmented.

## Decision 6: Multi-Camera Visitor Tracking
*   **Observation**: The system treats each camera as an independent tracking domain (local Re-ID).
*   **Impact**: Unique visitor counts may be inflated because a single customer moving between cameras (e.g., from CAM1 to CAM2) is assigned a new `visitor_id` by each local tracker.
*   **Reasoning**: Full cross-camera Re-ID is a Phase 2 objective. For Phase 1, per-camera event accuracy was prioritized over global visitor deduplication.

## Decision 7: Conversion Metric Definition
*   **Standard**: Purchases / Total Visitors.
*   **Final Choice**: **Actual POS Transactions**.
*   **Why**: POS ground truth from `transactions.csv` has been integrated. This provides the most accurate business KPI by dividing actual sales by unique footfall detected by the AI pipeline.
*   **Note**: The system prioritizes business truth (POS) but falls back to visual evidence (Queue Completion) to ensure the funnel remains representative even when external data feeds are mismatched or recording sessions are fragmented.
 
## North Star Metric Alignment
**Business Metric**: Offline Store Conversion Rate (Purchases ÷ Unique Visitors).

How subsystems contribute:
*   **Detection/Ingest**: Defines the denominator by identifying unique non-staff tracks.
*   **Funnel Logic**: Enforces the "Session Window" requirement by validating that journeys start with an entry and end with a purchase.
*   **Heatmap/Anomalies**: Act as diagnostic tools to explain *why* the North Star Metric might be dropping (e.g., high browsing but no checkout).

**Known Limitations & Trade-offs**:
1.  **Re-ID Fragmentation**: May slightly inflate the denominator (Total Visitors) until cross-camera tracking is implemented in Phase 2.
2.  **Missing Entries (CAM3)**: The system implements "Evidence-First Analytics", allowing funnel stages to be populated based on observed downstream activity (like billing) even if the entrance was missed.
---
*Part D Compliance - Purplle Store Intelligence Challenge*