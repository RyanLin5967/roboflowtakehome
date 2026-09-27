-- Append-only Roboflow annotation snapshots, explicit reviews, and write-ahead
-- dataset-version effects. A server ACK is not proof of version contents.
CREATE TABLE IF NOT EXISTS counterpart_annotation_snapshots (
 id uuid PRIMARY KEY,
 release_id uuid NOT NULL REFERENCES counterpart_dataset_releases(id),
 request_key text NOT NULL,
 content_sha256 text NOT NULL CHECK(length(content_sha256)=64),
 manifest jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(release_id,request_key)
);
CREATE TABLE IF NOT EXISTS counterpart_annotation_approvals (
 snapshot_id uuid PRIMARY KEY REFERENCES counterpart_annotation_snapshots(id),
 request_key text NOT NULL,
 reviewer text NOT NULL,
 reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS counterpart_roboflow_deliveries (
 id uuid PRIMARY KEY,
 snapshot_id uuid NOT NULL UNIQUE REFERENCES counterpart_annotation_snapshots(id),
 request_key text NOT NULL,
 status text NOT NULL CHECK(status IN ('planned','issuing','uncertain','generated','verified','failed')),
 generation_reserved boolean NOT NULL DEFAULT false,
 budget_evidence_sha256 text,
 before_versions jsonb NOT NULL DEFAULT '[]',
 provider_version integer CHECK(provider_version>0),
 receipt jsonb NOT NULL DEFAULT '{}',
 last_error text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS counterpart_roboflow_delivery_events (
 id bigserial PRIMARY KEY,
 delivery_id uuid NOT NULL REFERENCES counterpart_roboflow_deliveries(id),
 kind text NOT NULL,
 data jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
