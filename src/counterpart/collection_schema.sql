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
ALTER TABLE counterpart_campaigns ADD COLUMN IF NOT EXISTS execution_contract jsonb;
ALTER TABLE counterpart_sessions ADD COLUMN IF NOT EXISTS split_group_snapshot jsonb;
CREATE TABLE IF NOT EXISTS counterpart_split_groups (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
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
ALTER TABLE counterpart_collection_invites ADD COLUMN IF NOT EXISTS split_group_id uuid
    REFERENCES counterpart_split_groups(id);

-- A group or execution contract is assigned before collection, never retargeted
-- after media exists. NULL historical values deliberately retain legacy policy.
CREATE OR REPLACE FUNCTION counterpart_frozen_collection_contracts() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    IF TG_TABLE_NAME='counterpart_sessions' THEN
        IF NEW.split_group_snapshot IS DISTINCT FROM OLD.split_group_snapshot THEN
            RAISE EXCEPTION 'session split group provenance is immutable' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='counterpart_campaigns' THEN
        IF NEW.execution_contract IS DISTINCT FROM OLD.execution_contract THEN
            RAISE EXCEPTION 'campaign execution contract is immutable' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='counterpart_collection_invites' THEN
        IF NEW.split_group_id IS DISTINCT FROM OLD.split_group_id THEN
            RAISE EXCEPTION 'invitation split group is immutable' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS counterpart_frozen_group ON counterpart_sessions;
CREATE TRIGGER counterpart_frozen_group BEFORE UPDATE OF split_group_snapshot ON counterpart_sessions
    FOR EACH ROW EXECUTE FUNCTION counterpart_frozen_collection_contracts();
DROP TRIGGER IF EXISTS counterpart_frozen_execution ON counterpart_campaigns;
CREATE TRIGGER counterpart_frozen_execution BEFORE UPDATE OF execution_contract ON counterpart_campaigns
    FOR EACH ROW EXECUTE FUNCTION counterpart_frozen_collection_contracts();
DROP TRIGGER IF EXISTS counterpart_frozen_invite_group ON counterpart_collection_invites;
CREATE TRIGGER counterpart_frozen_invite_group BEFORE UPDATE OF split_group_id ON counterpart_collection_invites
    FOR EACH ROW EXECUTE FUNCTION counterpart_frozen_collection_contracts();
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
