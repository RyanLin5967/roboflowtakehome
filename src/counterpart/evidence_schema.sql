-- Append-only alternatives to original machine decisions. No record here rewrites
-- a capture, review, or previously frozen release.
CREATE TABLE IF NOT EXISTS counterpart_evidence_revisions (
 id uuid PRIMARY KEY,
 session_id uuid NOT NULL REFERENCES counterpart_sessions(id),
 capture_id uuid NOT NULL REFERENCES counterpart_captures(id),
 version integer NOT NULL CHECK (version > 0),
 request_key text NOT NULL,
 request_fingerprint text NOT NULL,
 evidence_sha256 text NOT NULL,
 record jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(capture_id,version), UNIQUE(capture_id,request_key)
);
CREATE TABLE IF NOT EXISTS counterpart_attempt_reviews (
 id uuid PRIMARY KEY,
 session_id uuid NOT NULL REFERENCES counterpart_sessions(id),
 capture_id uuid NOT NULL REFERENCES counterpart_captures(id),
 version integer NOT NULL CHECK (version > 0),
 request_key text NOT NULL,
 request_fingerprint text NOT NULL,
 record jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(capture_id,version), UNIQUE(capture_id,request_key)
);
CREATE TABLE IF NOT EXISTS counterpart_evidence_adjudications (
 id uuid PRIMARY KEY,
 session_id uuid NOT NULL REFERENCES counterpart_sessions(id),
 pair_id uuid NOT NULL UNIQUE REFERENCES counterpart_pairs(id),
 request_key text NOT NULL,
 request_fingerprint text NOT NULL,
 record jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(session_id,request_key)
);

CREATE TABLE IF NOT EXISTS counterpart_baseline_authorizations (
 id uuid PRIMARY KEY,
 session_id uuid NOT NULL REFERENCES counterpart_sessions(id),
 capture_id uuid NOT NULL REFERENCES counterpart_captures(id),
 request_key text NOT NULL,
 request_fingerprint text NOT NULL,
 record jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(session_id,request_key)
);

CREATE OR REPLACE FUNCTION counterpart_immutable_evidence_record() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Evidence decision history is append-only' USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;
DO $$
DECLARE relation_name text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY['counterpart_evidence_revisions',
        'counterpart_attempt_reviews', 'counterpart_evidence_adjudications',
        'counterpart_baseline_authorizations'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname=relation_name || '_immutable'
            AND tgrelid=relation_name::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                'FOR EACH ROW EXECUTE FUNCTION counterpart_immutable_evidence_record()',
                relation_name || '_immutable', relation_name);
        END IF;
    END LOOP;
END $$;

-- Active copies must point to exactly the separately immutable authorization.
CREATE OR REPLACE FUNCTION counterpart_bound_baseline_authorization() RETURNS trigger AS $$
DECLARE original_capture uuid;
BEGIN
    IF NEW.baseline_authorization IS NULL THEN RETURN NEW; END IF;
    IF TG_TABLE_NAME = 'counterpart_sessions' THEN
        original_capture := NEW.baseline_id;
        IF NOT EXISTS (SELECT 1 FROM counterpart_baseline_authorizations a
            WHERE a.id=(NEW.baseline_authorization->>'id')::uuid AND a.session_id=NEW.id
            AND a.capture_id=original_capture AND a.record=NEW.baseline_authorization) THEN
            RAISE EXCEPTION 'Baseline authorization is not bound to immutable evidence' USING ERRCODE='23514';
        END IF;
    ELSE
        original_capture := NEW.reference_capture_id;
        IF NOT EXISTS (SELECT 1 FROM counterpart_baseline_authorizations a
            WHERE a.id=(NEW.baseline_authorization->>'id')::uuid AND a.session_id=NEW.session_id
            AND a.capture_id=original_capture AND a.record=NEW.baseline_authorization) THEN
            RAISE EXCEPTION 'Capture authorization is not bound to immutable evidence' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DO $$
DECLARE relation_name text;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY['counterpart_sessions','counterpart_captures'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname=relation_name || '_baseline_auth'
            AND tgrelid=relation_name::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON %I '
                'FOR EACH ROW EXECUTE FUNCTION counterpart_bound_baseline_authorization()',
                relation_name || '_baseline_auth', relation_name);
        END IF;
    END LOOP;
END $$;
