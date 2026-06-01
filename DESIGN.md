# DESIGN.md — Architecture Overview

## System Overview

Store Intelligence is a two-service Python system that processes CCTV footage to generate real-time retail analytics without face recognition.

```
┌──────────────┐     JSONL events    ┌──────────────┐     SQL     ┌──────────────┐
│   Detection  │ ──────────────────► │   FastAPI    │ ◄─────────► │  PostgreSQL  │
│   Pipeline   │                     │   Service    │             │              │
│              │  YOLOv8 + ByteTrack │              │  asyncpg    │  events      │
│  (offline)   │                     │  (port 8000) │             │  pos_txns    │
└──────────────┘                     └──────┬───────┘             └──────────────┘
                                            │
                                     ┌──────┴───────┐
                                     │  Dashboard   │
                                     │  (Rich CLI)  │
                                     └──────────────┘
```

## Detection Pipeline

**Input**: Raw CCTV video files  
**Output**: Structured JSONL event stream

### Processing stages

1. **Frame sampling** — Extract every Nth frame (`DETECTION_SAMPLE_RATE`) to balance throughput vs accuracy
2. **Person detection** — YOLOv8n detects bounding boxes for all persons in frame, filtered by confidence threshold
3. **Multi-object tracking** — ByteTrack maintains stable track IDs across frames, handling occlusions at doorways
4. **Zone classification** — Ray-casting point-in-polygon test assigns each track centroid to a named zone from `store_layout.json`
5. **Visitor state machine** — Per-visitor state tracker manages:
   - Entry/exit detection via zone transitions
   - Re-entry matching (same visitor returning within 2-minute window)
   - Zone enter/exit events and dwell time accumulation
   - Session sequence numbering
6. **Queue tracking** — Monitors the checkout zone for join/abandon events and tracks queue depth
7. **Staff classification** — Post-processing heuristic flags tracks that are present for >50% of video duration with limited spatial movement
8. **Schema validation** — Every emitted event is validated against the `StoreEvent` Pydantic model before output

### Event types

| Type | Trigger | Key fields |
|------|---------|------------|
| `ENTRY` | Track first enters store boundary | visitor_id, confidence |
| `EXIT` | Track leaves store boundary | visitor_id, dwell_ms |
| `REENTRY` | Same visitor returns within window | visitor_id, session_seq |
| `ZONE_ENTER` | Track centroid enters a zone polygon | zone_id |
| `ZONE_EXIT` | Track centroid leaves a zone polygon | zone_id, dwell_ms |
| `ZONE_DWELL` | Track stays in zone beyond threshold | zone_id, dwell_ms |
| `BILLING_QUEUE_JOIN` | Track enters checkout zone | queue_depth |
| `BILLING_QUEUE_ABANDON` | Track leaves checkout without completing | queue_depth |

## API Service

**Framework**: FastAPI with async SQLAlchemy (asyncpg)  
**Database**: PostgreSQL 16

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/events/ingest` | Batch event ingestion with ON CONFLICT dedup |
| `GET` | `/stores/{id}/metrics` | Aggregated store KPIs |
| `GET` | `/stores/{id}/funnel` | 5-stage conversion funnel |
| `GET` | `/stores/{id}/heatmap` | Zone × time-bucket heatmap |
| `GET` | `/stores/{id}/anomalies` | Rule-based anomaly detection |
| `POST` | `/stores/{id}/pos/correlate` | POS-to-visitor session matching |
| `GET` | `/visitors/active` | Active visitors by zone |
| `GET` | `/health` | Service health with DB status |
| `GET` | `/metrics` | Prometheus-format metrics |

### Layered architecture

```
HTTP Layer (routes/)         — Request parsing, response formatting, error handling
  ↓
Analytics Layer (analytics/) — Funnel, heatmap, anomaly, POS correlation engines
  ↓
Repository Layer             — All SQL queries, accepts AsyncSession for testability
  ↓
ORM Models (models/)         — SQLAlchemy declarative models
  ↓
PostgreSQL                   — events, pos_transactions, zones tables
```

### Failure handling

- All DB-touching endpoints catch exceptions and return JSON 503 responses without stack traces
- The health endpoint reports `"degraded"` when the database is unreachable
- Connection pooling with `pool_pre_ping=True` handles transient DB failures

## Data flow

```
Video → Detection Pipeline → JSONL file → Ingest sidecar → POST /events/ingest → PostgreSQL
                                                                    ↓
                                                            Dashboard polls API
                                                            every 3 seconds
```

## Observability

- **Structured logging**: JSON via structlog, with logger name, level, timestamp
- **Prometheus metrics**: `http_requests_total`, `http_request_duration_seconds` via middleware
- **Health endpoint**: Reports DB connectivity, total events, data freshness (`last_event_at`)

## Deployment

```bash
# One-command start
docker compose up --build

# Or for demo with dashboard
./demo.sh
```

The Docker Compose stack starts PostgreSQL (with schema auto-creation), the API service, and the detection service. The API waits for Postgres to be healthy before starting.
