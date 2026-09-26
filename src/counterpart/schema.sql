-- Counterpart schema v1. No cloud I/O occurs while these rows are locked.
CREATE TABLE IF NOT EXISTS counterpart_sessions (
    id uuid PRIMARY KEY,
    capability_hash text NOT NULL,
    recipe text NOT NULL CHECK (recipe IN ('spatial', 'temporal')),
    mode text NOT NULL CHECK (mode IN ('live', 'replay')),
    object_a text NOT NULL,
    object_b text NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    epoch bigint NOT NULL DEFAULT 0 CHECK (epoch >= 0),
    state text NOT NULL DEFAULT 'collecting'
        CHECK (state IN ('collecting', 'baseline_ready', 'ready', 'published')),
    recovery_required boolean NOT NULL DEFAULT false,
    baseline_id uuid,
    counterpart_id uuid,
    latest_capture_id uuid,
    feedback_capture_id uuid,
    feedback_seq bigint NOT NULL DEFAULT -1,
    latest_preview_seq bigint NOT NULL DEFAULT -1,
    preview_served_at timestamptz NOT NULL DEFAULT '1970-01-01T00:00:00Z',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS counterpart_captures (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES counterpart_sessions(id),
    idempotency_key text NOT NULL,
    fingerprint text NOT NULL,
    slot text NOT NULL CHECK (slot IN ('baseline', 'counterpart', 'preview', 'recovery')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'accepted', 'rejected', 'superseded', 'failed')),
    revision bigint NOT NULL,
    epoch bigint NOT NULL,
    client_seq bigint NOT NULL DEFAULT 0,
    interrupted boolean NOT NULL DEFAULT false,
    frames jsonb NOT NULL,
    observed_frames jsonb,
    result jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    UNIQUE (session_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS counterpart_captures_session ON counterpart_captures(session_id, created_at);
CREATE TABLE IF NOT EXISTS counterpart_jobs (
    id uuid PRIMARY KEY,
    capture_id uuid NOT NULL UNIQUE REFERENCES counterpart_captures(id),
    session_id uuid NOT NULL REFERENCES counterpart_sessions(id),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'retry_wait', 'succeeded', 'failed', 'superseded')),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 3),
    lease_token uuid,
    worker_id text,
    lease_expires_at timestamptz,
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_error text
);
CREATE INDEX IF NOT EXISTS counterpart_jobs_claim ON counterpart_jobs(status, available_at, lease_expires_at);
CREATE TABLE IF NOT EXISTS counterpart_events (
    id bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES counterpart_sessions(id),
    kind text NOT NULL,
    data jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS counterpart_events_session ON counterpart_events(session_id, id);
CREATE TABLE IF NOT EXISTS counterpart_pairs (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL UNIQUE REFERENCES counterpart_sessions(id),
    manifest jsonb NOT NULL,
    export_status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS counterpart_export_intents (
    id uuid PRIMARY KEY,
    pair_id uuid NOT NULL UNIQUE REFERENCES counterpart_pairs(id),
    status text NOT NULL CHECK (status IN ('pending', 'disabled', 'running', 'uncertain', 'complete', 'failed')),
    receipts jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS counterpart_budget_accounts (
    provider text PRIMARY KEY,
    reserved_units bigint NOT NULL DEFAULT 0 CHECK (reserved_units >= 0)
);
CREATE TABLE IF NOT EXISTS counterpart_budget_reservations (
    provider text NOT NULL REFERENCES counterpart_budget_accounts(provider),
    request_key text NOT NULL,
    units bigint NOT NULL CHECK (units > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (provider, request_key)
);

-- Compatibility with the initial development schema; Python datetimes are finite.
ALTER TABLE counterpart_sessions ALTER COLUMN preview_served_at SET DEFAULT '1970-01-01T00:00:00Z';
UPDATE counterpart_sessions SET preview_served_at='1970-01-01T00:00:00Z' WHERE preview_served_at='-infinity';

ALTER TABLE counterpart_export_intents ADD COLUMN IF NOT EXISTS lease_token uuid;
ALTER TABLE counterpart_export_intents ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;
ALTER TABLE counterpart_export_intents ADD COLUMN IF NOT EXISTS worker_id text;
ALTER TABLE counterpart_export_intents ADD COLUMN IF NOT EXISTS attempt integer NOT NULL DEFAULT 0;
ALTER TABLE counterpart_export_intents ADD COLUMN IF NOT EXISTS last_error text;
ALTER TABLE counterpart_export_intents ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT clock_timestamp();
CREATE INDEX IF NOT EXISTS counterpart_exports_claim ON counterpart_export_intents(status, updated_at);

-- Provider search may lag successful uploads; persist bounded read-only retry time.
ALTER TABLE counterpart_export_intents ADD COLUMN IF NOT EXISTS available_at timestamptz NOT NULL DEFAULT clock_timestamp();
CREATE INDEX IF NOT EXISTS counterpart_exports_available ON counterpart_export_intents(status, available_at);
