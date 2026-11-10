CREATE TABLE IF NOT EXISTS clinic_records (
    collection text NOT NULL,
    id text NOT NULL,
    data jsonb NOT NULL,
    PRIMARY KEY (collection, id)
);
CREATE TABLE IF NOT EXISTS admin_account (
    singleton boolean PRIMARY KEY CHECK (singleton),
    email text NOT NULL,
    name text NOT NULL,
    password text NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash text PRIMARY KEY,
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS login_limits (
    scope text PRIMARY KEY,
    attempts integer NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL,
    action text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_action_id ON audit_events (action, id DESC);
