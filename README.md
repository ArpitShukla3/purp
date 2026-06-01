# Store Intelligence

Real-time CCTV analytics for retail stores — powered by YOLOv8, ByteTrack, FastAPI, and PostgreSQL.

> **Privacy-first**: No face recognition. The system tracks bodies and objects, not identities.

## Quick Start

```bash
# 1. Clone and enter the repo
cd Purplle

# 2. Launch everything (Postgres + API + Detection)
docker compose up --build

# 3. Verify
curl http://localhost:8000/health   # API health
curl http://localhost:8000/metrics  # Prometheus metrics
```

## Demo (with live dashboard)

```bash
# Full demo with detection pipeline + Rich terminal dashboard
./demo.sh

# Or run the dashboard standalone (API must be running)
python dashboard/live.py --api http://localhost:8010 --interval 2
```

## Project Structure

```
├── api/                  # FastAPI REST service
│   ├── main.py           # App entry point — /health, /metrics, routes
│   ├── analytics/        # Funnel, heatmap, anomaly, POS correlation engines
│   ├── routes/           # HTTP route handlers
│   ├── repository.py     # Database query layer
│   ├── schemas.py        # Pydantic request/response models
│   ├── Dockerfile
│   └── requirements.txt
├── detection/            # CCTV detection pipeline
│   ├── pipeline.py       # Main detection pipeline orchestrator
│   ├── detector.py       # YOLOv8 person detector
│   ├── tracker.py        # ByteTrack multi-object tracker
│   ├── visitor_state.py  # Per-visitor state machine
│   ├── queue_tracker.py  # Queue depth tracking
│   ├── staff_classifier.py  # Staff heuristic classifier
│   ├── cli.py            # CLI entry point
│   └── store_layout.json # Zone polygon definitions
├── dashboard/
│   └── live.py           # Rich-based terminal dashboard
├── shared/               # Shared Python package
│   ├── config.py         # Pydantic Settings (env-driven)
│   ├── logging.py        # Structured JSON logging
│   ├── database.py       # Async SQLAlchemy engine
│   ├── models/           # ORM models (Event, POSTransaction)
│   └── schemas/          # Pydantic schemas (StoreEvent, EventType)
├── db/
│   └── init.sql          # Bootstrap schema (events, zones, pos_transactions)
├── tests/                # Test suite (37 tests)
│   ├── test_schema.py    # Schema validation tests
│   ├── test_api.py       # API endpoint tests
│   └── conftest.py       # Shared fixtures
├── docker-compose.yml    # One-command deployment
├── demo.sh               # Full demo launcher
├── DESIGN.md             # Architecture document
├── CHOICES.md            # Technical trade-off decisions
└── .env.example
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/events/ingest` | Batch event ingestion (idempotent) |
| `GET` | `/stores/{id}/metrics` | Store KPIs, dwell, queue depth |
| `GET` | `/stores/{id}/funnel` | 5-stage conversion funnel |
| `GET` | `/stores/{id}/heatmap` | Zone × time-bucket heatmap |
| `GET` | `/stores/{id}/anomalies` | Rule-based anomaly detection |
| `POST` | `/stores/{id}/pos/correlate` | POS-to-visitor session matching |
| `GET` | `/visitors/active` | Active visitors by zone |
| `GET` | `/health` | Service health + DB status |
| `GET` | `/metrics` | Prometheus-format metrics |

## Configuration

All settings are controlled via environment variables (see [`.env.example`](.env.example)):

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `local` | `local` / `dev` / `prod` |
| `LOG_LEVEL` | `INFO` | Python log level |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Async Postgres connection |
| `DEBUG` | `false` | Enable Swagger docs at /docs |

## Running Tests

```bash
# Install test dependencies
pip install pytest httpx anyio pytest-anyio

# Run all tests (requires PostgreSQL running)
DATABASE_URL="postgresql+asyncpg://store_intel:store_intel@localhost:5432/store_intel" \
    pytest tests/ -v

# Schema-only tests (no DB needed)
pytest tests/test_schema.py -v
```

## Detection Pipeline

```bash
# Process a video clip
python -m detection.cli \
    --input "path/to/video.mp4" \
    --output detection/output/events.jsonl \
    --layout detection/store_layout.json \
    --sample-rate 3

# Ingest the output into the API
cat detection/output/events.jsonl | \
    python -c "import sys,json; events=[json.loads(l) for l in sys.stdin]; \
    print(json.dumps({'events':events}))" | \
    curl -X POST http://localhost:8000/events/ingest \
    -H 'Content-Type: application/json' -d @-
```

## Architecture & Design

- **[DESIGN.md](DESIGN.md)** — System architecture, data flow, and component details
- **[CHOICES.md](CHOICES.md)** — Technical trade-off decisions and rationale
- **[docs/architecture.md](docs/architecture.md)** — High-level overview

## Observability

- **Structured JSON logging** via structlog — every log line has `logger`, `level`, `timestamp`
- **Prometheus metrics** at `/metrics` — HTTP request count and latency histograms
- **Health endpoint** at `/health` — DB connectivity, total events, data freshness
# purp
