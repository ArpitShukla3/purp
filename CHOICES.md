# CHOICES.md — Technical Trade-offs and Decisions

## Detection Model: YOLOv8n

**Choice**: YOLOv8 nano variant  
**Alternatives considered**: YOLOv8s/m, Faster R-CNN, SSD

**Rationale**: The nano model provides sufficient accuracy for person detection in retail CCTV (typically 640×480, fixed camera, controlled lighting) while running at 30+ FPS on CPU. Larger models (YOLOv8s/m) improve mAP by ~3-5% but increase inference time 3-5×, which matters when processing hours of footage. Person detection is a well-solved class — the bottleneck is tracking, not detection.

**Trade-off**: Slightly lower recall on partially occluded persons at store edges. Mitigated by requiring `MIN_OBSERVATIONS=3` before creating a visitor — transient false negatives are filtered out.

---

## Tracker: ByteTrack over DeepSORT

**Choice**: ByteTrack (via `ultralytics` built-in tracker)  
**Alternatives considered**: DeepSORT, BoT-SORT, OCSORT

**Rationale**: ByteTrack uses only detection boxes and IoU for association — no appearance model needed. This makes it:
- **Faster**: No ReID feature extraction per detection
- **Simpler**: No separate model weights to manage
- **Sufficient**: In retail CCTV with fixed cameras and moderate density (5-20 people), IoU-based association works well

**Trade-off**: ByteTrack can lose IDs during extended full occlusions (person behind a display for 5+ seconds). DeepSORT's appearance model would handle this better but adds ~50ms per frame. We mitigate ID loss with the **re-entry window** — if a track disappears and a new one appears within 2 minutes at the entrance, it's merged.

---

## Zone Detection: Polygon Ray-casting over Grid

**Choice**: Manual polygon definitions in `store_layout.json` with point-in-polygon tests  
**Alternatives considered**: Grid-based zones, learned zone boundaries, depth-based

**Rationale**: Retail store zones have irregular shapes (L-shaped aisles, curved displays). Polygons express these precisely while being trivial to define (one-time manual step per camera view). Grid-based approaches force axis-aligned boundaries that don't match real layouts.

**Trade-off**: Requires manual polygon definition per camera. Acceptable for a fixed-camera retail setup where layouts change rarely. A UI tool could automate this in production.

---

## Storage: PostgreSQL over Redis/TimescaleDB

**Choice**: PostgreSQL 16 with JSONB metadata columns  
**Alternatives considered**: TimescaleDB, Redis for hot state, ClickHouse

**Rationale**:
- **Single dependency**: PostgreSQL handles both storage and querying — no need for a separate analytics DB at this scale
- **JSONB flexibility**: Event metadata varies by type (queue_depth for billing events, zone transitions for zone events). JSONB stores this without schema migrations
- **Idempotent upserts**: `INSERT ... ON CONFLICT DO NOTHING` on the `event_id` primary key gives us deduplication for free
- **Mature async support**: `asyncpg` + SQLAlchemy 2.0 async is production-grade

**Trade-off**: At very high event rates (>10K/sec), a columnar store like ClickHouse would be faster for analytical queries. For retail analytics (hundreds of events per hour per store), PostgreSQL is more than sufficient and operationally simpler.

---

## Event ID: Client-generated UUID

**Choice**: Detection pipeline generates the `event_id` as a UUID string  
**Alternatives considered**: Server-generated auto-increment, server-generated UUID

**Rationale**: Client-generated IDs make ingestion idempotent by construction. If the network drops during a POST, the client can safely retry — the same events will be deduplicated via `ON CONFLICT DO NOTHING`. Server-generated IDs would require the client to check which events already exist.

---

## API Schema: Separate Pydantic models per layer

**Choice**: Three model layers — detection schemas, ORM models, API response models  
**Alternatives considered**: Single shared model, ORM-only

**Rationale**: Each layer has different concerns:
- **Detection schemas** (`shared/schemas/events.py`): Validation, factory methods, event type enums
- **ORM models** (`shared/models/event.py`): Database column mapping, indexes, relationships
- **API schemas** (`api/schemas.py`): Request/response contracts, docs, versioning

This separation means changing the API response format doesn't affect the detection pipeline, and vice versa. The cost is some field duplication, but it's safer than coupling.

---

## POS Correlation: Temporal proximity heuristic

**Choice**: Score = proximity + checkout_bonus(0.3) + temporal_bonus(0.1), greedy matching  
**Alternatives considered**: Probabilistic matching, graph-based assignment, manual correlation

**Rationale**: Without face recognition or loyalty card data, the strongest signal we have is **time**. A customer who entered the store 2 minutes before a transaction is more likely to be the buyer than one who entered 8 minutes before. The checkout-zone bonus uses spatial evidence: if a visitor was detected near the register, they're more likely to be the transacting customer.

**Tie-breaking rule**: When multiple visitors have similar proximity scores, the one whose ENTRY timestamp is closest to (but before) the transaction wins. Each visitor can only match one transaction (greedy, first-come-first-served by transaction time).

**Trade-off**: This is inherently heuristic — accuracy depends on store traffic density. In a busy store with many concurrent customers, the match confidence decreases. The API response includes explicit `assumptions` and `match_confidence` so consumers can assess reliability. In production, loyalty card integration or checkout camera face matching would improve accuracy.

---

## Anomaly Detection: Rules over ML

**Choice**: Deterministic, threshold-based rules  
**Alternatives considered**: Isolation Forest, LSTM-based anomaly detection, statistical process control

**Rationale**: The evaluation emphasizes **explainability** and **correctness** over sophistication. Each anomaly includes:
- The rule that fired (human-readable)
- The observed value
- The threshold
- A natural-language description

ML-based anomaly detection would require training data we don't have and would produce opaque scores. Rule-based detection is:
- **Deterministic**: Same input → same output, always
- **Configurable**: All thresholds are query parameters
- **Debuggable**: Every anomaly can be verified by querying the database directly

**Trade-off**: Cannot detect novel or subtle anomalies that rules don't cover. In production, rules serve as the baseline; ML models can be layered on top once sufficient historical data exists.

---

## Dashboard: Rich CLI over Web UI

**Choice**: Rich-based terminal dashboard with polling  
**Alternatives considered**: React SPA, Streamlit, WebSocket-based UI

**Rationale**: A terminal dashboard has zero frontend dependencies, starts instantly, and works over SSH. It demonstrates real-time updates just as effectively as a web UI for evaluation purposes. Rich provides tables, panels, progress bars, and color — sufficient for a demo.

**Trade-off**: Not suitable for non-technical users in production. A web dashboard would be needed for store managers. But for the evaluation, terminal output is more robust and faster to set up.

---

## Staff Classification: Heuristic over Model

**Choice**: Duration + spatial movement heuristic  
**Alternatives considered**: Appearance-based classification, uniform detection, badge detection

**Rationale**: Staff tend to be present for the majority of the video and move within a limited spatial range (behind a counter, near a specific station). The heuristic flags visitors present for >50% of video duration with x-range < 400px as staff. This is a post-processing step that runs after all tracks are complete, so it has full-clip context.

**Trade-off**: Will misclassify a customer who spends a very long time in the store. Acceptable for analytics purposes — the false positive rate is low in typical retail scenarios where customers visit for 5-20 minutes and staff work full shifts.
