-- Capability feedback is independent of raw trace snapshots and derived results.
CREATE TABLE {schema}.workflow_feedback (
    feedback_id text PRIMARY KEY CHECK (feedback_id ~ '^[0-9a-f]{{64}}$'),
    approval_id text NOT NULL CHECK (approval_id ~ '^[0-9a-f]{{64}}$'),
    document_digest text NOT NULL CHECK (document_digest ~ '^[0-9a-f]{{64}}$'),
    document bytea NOT NULL CHECK (octet_length(document) <= 2097152),
    archived_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON TABLE {schema}.workflow_feedback FROM PUBLIC;
