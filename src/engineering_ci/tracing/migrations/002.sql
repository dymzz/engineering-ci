CREATE TABLE {schema}.derived_results (
    result_id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('projection', 'analysis')),
    snapshot_id text NOT NULL REFERENCES {schema}.snapshots(snapshot_id),
    parent_result_id text REFERENCES {schema}.derived_results(result_id),
    input_digest text NOT NULL,
    document_digest text NOT NULL,
    document bytea NOT NULL,
    summary jsonb NOT NULL CHECK (jsonb_typeof(summary) = 'object'),
    origin text NOT NULL CHECK (origin IN ('computed', 'file')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'projection' AND parent_result_id IS NULL)
        OR (kind = 'analysis' AND parent_result_id IS NOT NULL))
);
CREATE INDEX results_snapshot_created ON {schema}.derived_results(snapshot_id, created_at, result_id);
REVOKE ALL ON {schema}.derived_results FROM PUBLIC;
