#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Store Intelligence — One-Command Demo
# ─────────────────────────────────────────────────────────────────────
#
# Starts the full stack:
#   1. PostgreSQL (via Docker Compose)
#   2. API service (FastAPI on port 8010)
#   3. Detection pipeline (processes video, emits events)
#   4. Live dashboard (polls API, shows metrics)
#
# Usage:
#   chmod +x demo.sh
#   ./demo.sh
#
# To stop: press Ctrl+C (the dashboard), then the script cleans up.
# ─────────────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

VENV=".venv/bin/python"
API_PORT=8010
API_URL="http://localhost:${API_PORT}"
STORE_ID="purplle-001"
VIDEO_PATH="CCTV Footage-20260529T160731Z-3-00144614ea/CCTV Footage/CAM 3.mp4"
LAYOUT_PATH="detection/store_layout.json"
OUTPUT_PATH="detection/output/demo_events.jsonl"

# Track PIDs for cleanup
API_PID=""
PIPELINE_PID=""
INGEST_PID=""

cleanup() {
    echo -e "\n${CYAN}Cleaning up...${NC}"
    [ -n "$INGEST_PID" ] && kill "$INGEST_PID" 2>/dev/null || true
    [ -n "$PIPELINE_PID" ] && kill "$PIPELINE_PID" 2>/dev/null || true
    [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
    echo -e "${GREEN}Done.${NC}"
}
trap cleanup EXIT

echo -e "${BOLD}${CYAN}"
echo "  ┌────────────────────────────────────────┐"
echo "  │    🏪 Store Intelligence Demo          │"
echo "  └────────────────────────────────────────┘"
echo -e "${NC}"

# ── 1. Ensure Postgres is running ────────────────────────────────────
echo -e "${CYAN}[1/5]${NC} Starting PostgreSQL..."
docker compose up -d postgres 2>&1 | grep -v "^$" || true
sleep 2

# Re-apply schema (idempotent)
docker compose exec -T postgres psql -U store_intel -d store_intel \
    -f /docker-entrypoint-initdb.d/01_init.sql 2>&1 | tail -1

echo -e "${GREEN}  ✓ PostgreSQL ready${NC}"

# ── 2. Start API ────────────────────────────────────────────────────
echo -e "${CYAN}[2/5]${NC} Starting API service on port ${API_PORT}..."
DATABASE_URL="postgresql+asyncpg://store_intel:store_intel@localhost:5432/store_intel" \
    ENVIRONMENT=local DEBUG=false \
    $VENV -m uvicorn api.main:app --host 0.0.0.0 --port $API_PORT \
    --log-level warning &
API_PID=$!
sleep 3

# Verify API is up
if curl -s "${API_URL}/health" | grep -q '"status"'; then
    echo -e "${GREEN}  ✓ API ready at ${API_URL}${NC}"
else
    echo -e "${RED}  ✗ API failed to start${NC}"
    exit 1
fi

# ── 3. Run POS correlation (seed the funnel) ────────────────────────
echo -e "${CYAN}[3/5]${NC} Running POS correlation..."
curl -s -X POST "${API_URL}/stores/${STORE_ID}/pos/correlate?window_minutes=10" > /dev/null 2>&1
echo -e "${GREEN}  ✓ POS data correlated${NC}"

# ── 4. Start pipeline + live ingestion in background ────────────────
echo -e "${CYAN}[4/5]${NC} Starting detection pipeline (background)..."

# Pipeline writes JSONL; a sidecar script tails it and POSTs to the API
$VENV -m detection.cli \
    --input "$VIDEO_PATH" \
    --output "$OUTPUT_PATH" \
    --layout "$LAYOUT_PATH" \
    --sample-rate 3 \
    --store-id "$STORE_ID" \
    --camera-id cam3 2>/dev/null &
PIPELINE_PID=$!

# Give pipeline a head start, then start tailing + ingesting
sleep 5
(
    # Tail the output file and POST batches to the API every 3 seconds
    LAST_LINE=0
    while kill -0 "$PIPELINE_PID" 2>/dev/null || [ -f "$OUTPUT_PATH" ]; do
        TOTAL=$(wc -l < "$OUTPUT_PATH" 2>/dev/null || echo 0)
        if [ "$TOTAL" -gt "$LAST_LINE" ]; then
            # Extract new lines as a JSON batch
            NEW_LINES=$(tail -n +"$((LAST_LINE + 1))" "$OUTPUT_PATH" | head -n 50)
            if [ -n "$NEW_LINES" ]; then
                # Build JSON array from JSONL
                EVENTS=$(echo "$NEW_LINES" | $VENV -c "
import sys, json
events = [json.loads(l) for l in sys.stdin if l.strip()]
print(json.dumps({'events': events}))
")
                curl -s -X POST "${API_URL}/events/ingest" \
                    -H 'Content-Type: application/json' \
                    -d "$EVENTS" > /dev/null 2>&1
            fi
            LAST_LINE=$TOTAL
        fi
        sleep 3
    done
) &
INGEST_PID=$!

echo -e "${GREEN}  ✓ Pipeline running, events streaming to API${NC}"

# ── 5. Launch dashboard ─────────────────────────────────────────────
echo -e "${CYAN}[5/5]${NC} Launching live dashboard..."
echo -e "${BOLD}  Press Ctrl+C to stop${NC}"
sleep 2

$VENV dashboard/live.py --api "$API_URL" --store "$STORE_ID" --interval 3
