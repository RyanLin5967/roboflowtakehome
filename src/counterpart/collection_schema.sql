-- Operator collections augment immutable machine-verified pairs. Reviews and
-- releases never rewrite model decisions or the original publication manifest.
CREATE TABLE IF NOT EXISTS counterpart_campaigns (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    recipe text NOT NULL CHECK (recipe IN ('spatial','temporal')),
    mode text NOT NULL CHECK (mode IN ('live','replay')),
    object_a text NOT NULL,
    object_b text NOT NULL,
    recipe_snapshot jsonb NOT NULL,
    target_pairs integer NOT NULL CHECK (target_pairs BETWEEN 1 AND 100000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS counterpart_sessions_campaign ON counterpart_sessions(campaign_id,created_at);
ALTER TABLE counterpart_campaigns ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='counterpart_sessions_campaign_fk'
                   AND conrelid='counterpart_sessions'::regclass) THEN
        ALTER TABLE counterpart_sessions ADD CONSTRAINT counterpart_sessions_campaign_fk
            FOREIGN KEY(campaign_id) REFERENCES counterpart_campaigns(id);
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS counterpart_collection_invites (
    id uuid PRIMARY KEY,
    campaign_id uuid NOT NULL REFERENCES counterpart_campaigns(id),
    token_hash text NOT NULL UNIQUE,
    label text NOT NULL,
    expires_at timestamptz NOT NULL,
    max_sessions integer NOT NULL CHECK (max_sessions BETWEEN 1 AND 1000),
    sessions_created integer NOT NULL DEFAULT 0 CHECK (sessions_created >= 0),
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS counterpart_invite_sessions (
    invite_id uuid NOT NULL REFERENCES counterpart_collection_invites(id),
    request_key text NOT NULL,
    session_id uuid NOT NULL UNIQUE REFERENCES counterpart_sessions(id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(invite_id,request_key)
);
CREATE TABLE IF NOT EXISTS counterpart_pair_reviews (
    pair_id uuid PRIMARY KEY REFERENCES counterpart_pairs(id),
    status text NOT NULL CHECK (status IN ('approved','rejected','needs_revision')),
    version integer NOT NULL CHECK (version >= 1),
    reviewer text NOT NULL,
    reason text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS counterpart_review_events (
    id uuid PRIMARY KEY,
    pair_id uuid NOT NULL REFERENCES counterpart_pairs(id),
    request_key text NOT NULL,
    fingerprint text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved','rejected','needs_revision')),
    version integer NOT NULL CHECK (version >= 1),
    reviewer text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(pair_id,request_key),
    UNIQUE(pair_id,version)
);
CREATE TABLE IF NOT EXISTS counterpart_dataset_releases (
    id uuid PRIMARY KEY,
    campaign_id uuid NOT NULL REFERENCES counterpart_campaigns(id),
    manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE counterpart_dataset_releases ADD COLUMN IF NOT EXISTS request_key text;
ALTER TABLE counterpart_dataset_releases ADD COLUMN IF NOT EXISTS request_fingerprint text;
CREATE UNIQUE INDEX IF NOT EXISTS counterpart_dataset_releases_request
    ON counterpart_dataset_releases(campaign_id,request_key) WHERE request_key IS NOT NULL;
