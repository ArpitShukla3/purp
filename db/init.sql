-- ====================================================================
-- Store Intelligence — Database Schema
-- ====================================================================
-- This file is mounted into the Postgres container and executed on
-- first boot only (when the data volume is empty).
-- ====================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Events table ─────────────────────────────────────────────────────
-- Primary event store.  event_id is a client-generated UUID string
-- that enables idempotent ingestion (INSERT ... ON CONFLICT DO NOTHING).
CREATE TABLE IF NOT EXISTS events (
    event_id    VARCHAR(64)  PRIMARY KEY,
    store_id    VARCHAR(64)  NOT NULL,
    camera_id   VARCHAR(64)  NOT NULL,
    visitor_id  VARCHAR(128) NOT NULL,
    timestamp   TIMESTAMPTZ  NOT NULL,
    event_type  VARCHAR(64)  NOT NULL,
    confidence  DOUBLE PRECISION NOT NULL,
    zone_id     VARCHAR(64),
    dwell_ms    INTEGER,
    is_staff    BOOLEAN      NOT NULL DEFAULT false,
    session_seq INTEGER,
    metadata    JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_events_store_id    ON events (store_id);
CREATE INDEX IF NOT EXISTS idx_events_visitor_id  ON events (visitor_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp   ON events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_event_type  ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_store_type  ON events (store_id, event_type);
CREATE INDEX IF NOT EXISTS idx_events_store_ts    ON events (store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_visitor_ts  ON events (visitor_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_zone        ON events (zone_id);

-- ── Zones table ──────────────────────────────────────────────────────
-- Store regions of interest (loaded from store_layout.json).
CREATE TABLE IF NOT EXISTS zones (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zone_id     VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(128) NOT NULL,
    camera_id   VARCHAR(64)  NOT NULL,
    zone_type   VARCHAR(64)  NOT NULL DEFAULT 'aisle',
    polygon     JSONB        NOT NULL DEFAULT '[]',
    dwell_threshold_ms INTEGER NOT NULL DEFAULT 15000,
    metadata    JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ── POS Transactions table ───────────────────────────────────────────
-- Point-of-sale records, correlated to visitor sessions heuristically.
CREATE TABLE IF NOT EXISTS pos_transactions (
    transaction_id  VARCHAR(64)  PRIMARY KEY,
    store_id        VARCHAR(64)  NOT NULL,
    timestamp       TIMESTAMPTZ  NOT NULL,
    amount          DOUBLE PRECISION NOT NULL,
    items           INTEGER      NOT NULL DEFAULT 1,
    payment_method  VARCHAR(32),
    matched_visitor_id VARCHAR(128),
    match_confidence   DOUBLE PRECISION,
    match_method       VARCHAR(64),
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pos_store_id  ON pos_transactions (store_id);
CREATE INDEX IF NOT EXISTS idx_pos_timestamp ON pos_transactions (timestamp);
CREATE INDEX IF NOT EXISTS idx_pos_visitor   ON pos_transactions (matched_visitor_id);
