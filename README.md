# Purplle Store Intelligence

## System Overview

Purplle Store Intelligence transforms retail CCTV footage into actionable business intelligence using a modular AI pipeline. The platform analyzes customer movement, browsing behavior, billing activity, and sales conversion to provide store managers with real-time operational insights.

Key capabilities:

* Footfall analytics
* Customer journey funnel analysis
* Zone heatmaps and dwell-time analysis
* Billing queue monitoring
* POS transaction correlation
* Multi-store support
* Real-time dashboard visualization

---

## Project Architecture

The system follows the pipeline below:

```text
Raw Video
    ↓
YOLOv8 Person Detection
    ↓
ByteTrack Multi-Object Tracking
    ↓
Zone-Based Event Generation
    ↓
JSONL Event Streams
    ↓
Ingestion API
    ↓
SQLite Analytics Database
    ↓
FastAPI Analytics Endpoints
    ↓
Streamlit Dashboard
```

### Processing Flow

1. YOLOv8 detects people in CCTV footage.
2. ByteTrack maintains person identities across frames.
3. Zone logic generates behavioral events.
4. Events are written as JSONL files.
5. Ingestion service loads events into SQLite.
6. Analytics endpoints compute metrics and funnels.
7. Streamlit dashboard visualizes store performance.

---

## Setup & Installation

### Prerequisites

* Python 3.11+
* pip
* YOLOv8 model weights
* ByteTrack configuration

### Install Dependencies

```powershell
pip install -r requirements.txt
```

### Optional Docker Setup

```powershell
docker-compose up --build -d
```

---

## Detection Pipeline

The detection pipeline converts CCTV footage into structured customer-behavior events.

### Components

* YOLOv8 for person detection
* ByteTrack for multi-object tracking
* Polygon-based zone engine
* Event generation pipeline

---

## Zone Calibration

Each camera requires calibrated zones before event generation.

Example:

```powershell
python pipeline/zone_calibrator.py --video data/videos/cam3.mp4 --output configs/zones/cam3_zones.json --camera-id CAM3 --zone-id entrance_zone --zone-type entrance
```

### Zone Types

| Zone Type | Purpose                                   |
| --------- | ----------------------------------------- |
| entrance  | Entry / Exit counting                     |
| browsing  | Dwell-time and browsing analytics         |
| billing   | Queue monitoring and conversion analytics |

### Calibration Notes

* Draw polygons on floor regions where customers stand.
* Entrance zones should be narrow thresholds.
* Billing zones should exclude cashier-only areas.
* Browsing zones should cover customer standing areas.

---

## Event Generation

Generate JSONL behavioral events from CCTV footage.

Example:

```powershell
python pipeline/generate_events.py --video data/videos/cam1.mp4 --camera-id CAM1 --zones configs/zones/cam1_zones.json --output data/events/cam1_events.jsonl
```

---

## Running Against Challenge Clips

Store 1008 events were generated directly from the provided CCTV footage using the YOLOv8 + ByteTrack pipeline and manually calibrated zones.

### Store 1008 - Browsing Area

```powershell
python pipeline/generate_events.py --video data/store2/cam1.mp4 --camera-id CAM1 --store-id 1008 --zones configs/zones/store2/cam1_zones.json --output data/events/store2_cam1_events.jsonl
```

### Store 1008 - Billing Counter

```powershell
python pipeline/generate_events.py --video data/store2/cam5.mp4 --camera-id CAM5 --store-id 1008 --zones configs/zones/store2/cam5_zones.json --output data/events/store2_cam5_events.jsonl
```

### Store 1008 - Entrance Camera

```powershell
python pipeline/generate_events.py --video "data/store2/entry 2.mp4" --camera-id ENTRY2 --store-id 1008 --zones configs/zones/store2/entry2_zones.json --output data/events/store2_entry2_events.jsonl
```

The resulting JSONL files are automatically ingested by the analytics platform using the ingestion utility.

---

## Ingestion

The ingestion utility automatically discovers JSONL event files under `data/events` and loads them into the analytics database.

```powershell
python ingest_events.py
```

The script also ingests POS transaction data and performs verification checks against analytics endpoints.

---

## Starting the API

Start the FastAPI backend:

```powershell
uvicorn app.main:app --reload
```

Default API URL:

```text
http://127.0.0.1:8000
```

---

## API Usage Examples

### Store Metrics

```http
GET /stores/1076/metrics
GET /stores/1008/metrics
```

### Funnel Analytics

```http
GET /stores/1076/funnel
GET /stores/1008/funnel
```

### Heatmaps

```http
GET /stores/1076/heatmap
GET /stores/1008/heatmap
```

### Anomaly Detection

```http
GET /stores/1076/anomalies
GET /stores/1008/anomalies
```

---

## Multi-Store Support

The platform supports multiple stores simultaneously.

### Store 1076

* Organizer-provided diagnostic dataset
* POS correlation enabled
* Funnel analytics enabled

### Store 1008

* Generated from actual challenge CCTV clips
* YOLOv8 + ByteTrack processing
* Manual zone calibration
* Heatmaps, funnel analytics, and billing analytics enabled

### Store ID Normalization

The ingestion layer normalizes identifiers such as:

```text
store_1076 → 1076
ST1076 → 1076
```

This ensures reliable correlation between events and POS transactions.

---

## Dashboard (Part E)

A real-time dashboard is implemented using Streamlit and Plotly.

Start the dashboard:

```powershell
streamlit run app.py
```

Local URL:

```text
http://localhost:8501
```

Dashboard features:

* Footfall analytics
* Conversion metrics
* Funnel visualization
* Zone heatmaps
* Queue monitoring
* Multi-store support

---

## Testing

Run API tests:

```powershell
pytest app/test_api.py -v
```

---

## Repository Structure

```text
app/
├── main.py
├── test_api.py

pipeline/
├── generate_events.py
├── zone_calibrator.py

configs/
└── zones/

data/
├── events/
├── store2/
└── POS/

README.md
DESIGN.md
CHOICES.md
ingest_events.py
```

---

## Documentation

Additional project documentation:

* README.md — Setup, execution, and usage
* DESIGN.md — Architecture and AI-assisted decisions
* CHOICES.md — Model selection, schema design, and API design rationale

---

## Submission Notes

Store 1008 analytics were generated from real CCTV footage using the detection pipeline and manually calibrated zones.

The solution supports:

* Multi-store analytics
* YOLOv8 detection
* ByteTrack tracking
* POS correlation
* FastAPI analytics
* Streamlit dashboard
* Heatmaps
* Funnel analytics

---

*Purplle Store Intelligence Challenge Submission*
