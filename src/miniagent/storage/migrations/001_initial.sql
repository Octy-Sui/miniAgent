CREATE TABLE runs (
    run_id TEXT PRIMARY KEY NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    status TEXT NOT NULL CHECK (status IN (
        'created', 'preparing', 'running', 'waiting_approval',
        'recovering', 'suspended', 'evaluating', 'succeeded', 'failed', 'cancelled'
    )),
    state_json TEXT NOT NULL CHECK (json_valid(state_json)),
    recovery_context_json TEXT CHECK (
        recovery_context_json IS NULL OR json_valid(recovery_context_json)
    ),
    last_event_sequence INTEGER NOT NULL DEFAULT 0
        CHECK (last_event_sequence >= 0),
    latest_snapshot_version INTEGER NOT NULL DEFAULT 0
        CHECK (latest_snapshot_version >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX runs_status_created ON runs(status, created_at, run_id);

CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_id TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    artifacts_json TEXT NOT NULL CHECK (json_valid(artifacts_json)),
    step_number INTEGER CHECK (step_number >= 1),
    span_id TEXT,
    parent_span_id TEXT,
    PRIMARY KEY (run_id, sequence),
    UNIQUE (run_id, event_id),
    CHECK (parent_span_id IS NULL OR span_id IS NOT NULL)
);

CREATE TABLE run_snapshots (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    version INTEGER NOT NULL CHECK (version >= 1),
    snapshot_id TEXT NOT NULL UNIQUE,
    event_sequence INTEGER NOT NULL CHECK (event_sequence >= 0),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, version)
);

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    decision_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    decision_json TEXT NOT NULL CHECK (json_valid(decision_json)),
    outcome TEXT CHECK (outcome IN (
        'approved', 'rejected', 'expired', 'invalidated'
    )),
    resolution_json TEXT CHECK (
        resolution_json IS NULL OR json_valid(resolution_json)
    ),
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    request_event_id TEXT NOT NULL,
    resolution_event_id TEXT,
    authorization_invalidated_event_id TEXT,
    UNIQUE (run_id, decision_id),
    CHECK (expires_at > requested_at),
    CHECK (
        (outcome IS NULL AND resolution_json IS NULL
            AND resolution_event_id IS NULL)
        OR (outcome IS NOT NULL AND resolution_json IS NOT NULL
            AND resolution_event_id IS NOT NULL)
    ),
    CHECK (authorization_invalidated_event_id IS NULL
        OR (outcome IS NOT NULL AND outcome = 'approved')),
    FOREIGN KEY (run_id, request_event_id)
        REFERENCES run_events(run_id, event_id),
    FOREIGN KEY (run_id, resolution_event_id)
        REFERENCES run_events(run_id, event_id),
    FOREIGN KEY (run_id, authorization_invalidated_event_id)
        REFERENCES run_events(run_id, event_id)
);

CREATE UNIQUE INDEX approvals_one_pending_per_run
    ON approvals(run_id) WHERE outcome IS NULL;
CREATE INDEX approvals_pending_expiry
    ON approvals(expires_at, approval_id) WHERE outcome IS NULL;
CREATE INDEX approvals_run_history
    ON approvals(run_id, request_event_id);

CREATE TRIGGER run_events_no_update
BEFORE UPDATE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run events are immutable');
END;

CREATE TRIGGER run_events_no_delete
BEFORE DELETE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run events are immutable');
END;

CREATE TRIGGER run_snapshots_no_update
BEFORE UPDATE ON run_snapshots
BEGIN
    SELECT RAISE(ABORT, 'run snapshots are immutable');
END;

CREATE TRIGGER run_snapshots_no_delete
BEFORE DELETE ON run_snapshots
BEGIN
    SELECT RAISE(ABORT, 'run snapshots are immutable');
END;

PRAGMA user_version = 3;
