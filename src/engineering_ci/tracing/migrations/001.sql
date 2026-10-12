CREATE TABLE {schema}.archive_meta (
    singleton boolean PRIMARY KEY CHECK (singleton),
    component text NOT NULL,
    schema_version integer NOT NULL,
    migration_digest text NOT NULL
);
CREATE TABLE {schema}.sources (
    source text PRIMARY KEY,
    session_id text NOT NULL,
    header_digest text NOT NULL,
    runtime_version text,
    format text NOT NULL,
    cursor_offset bigint NOT NULL DEFAULT 0 CHECK (cursor_offset >= 0),
    cursor_digest text NOT NULL
);
CREATE TABLE {schema}.events (
    source text NOT NULL REFERENCES {schema}.sources(source),
    event_id text NOT NULL,
    record_offset bigint NOT NULL CHECK (record_offset >= 0),
    payload bytea NOT NULL CHECK (octet_length(payload) > 0),
    payload_digest text NOT NULL,
    attributes jsonb NOT NULL CHECK (jsonb_typeof(attributes) = 'object'),
    parse_status text NOT NULL CHECK (parse_status IN ('parsed', 'invalid_json', 'truncated')),
    fragment boolean NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source, event_id)
);
CREATE INDEX events_source_offset ON {schema}.events(source, record_offset);
CREATE TABLE {schema}.snapshots (
    snapshot_id text PRIMARY KEY,
    source text NOT NULL REFERENCES {schema}.sources(source),
    observed_bytes bigint NOT NULL CHECK (observed_bytes >= 0),
    capture_digest text NOT NULL,
    completeness text NOT NULL CHECK (completeness IN ('complete', 'incomplete', 'unknown')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE {schema}.snapshot_members (
    snapshot_id text NOT NULL REFERENCES {schema}.snapshots(snapshot_id),
    position integer NOT NULL CHECK (position >= 0),
    source text NOT NULL,
    event_id text NOT NULL,
    PRIMARY KEY (snapshot_id, position),
    UNIQUE (snapshot_id, source, event_id),
    FOREIGN KEY (source, event_id) REFERENCES {schema}.events(source, event_id)
);
REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM PUBLIC;
