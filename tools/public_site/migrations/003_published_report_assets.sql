-- Published coaching reports and per-object R2 asset inventory.
-- PostgreSQL is authoritative for publication/cleanup state and result projections.
-- R2 bundle.json remains the immutable run inventory; this table stores pointers only.
-- Never stores presigned URLs.
--
-- Upload objects live under generation-specific prefixes:
--   reports/<submission_id>/<run_id>/attempt-<attempt_generation>/...
-- so a stale worker cannot overwrite a newer generation's objects.

CREATE TABLE IF NOT EXISTS published_reports (
    submission_id text PRIMARY KEY,
    active_run_id text,
    attempt_run_id text,
    attempt_lease_until timestamptz,
    attempt_generation bigint NOT NULL DEFAULT 0 CHECK (attempt_generation >= 0),
    storage_provider text NOT NULL CHECK (storage_provider = 'r2'),
    bucket text NOT NULL,
    bundle_key text,
    bundle_sha256 text,
    outcome text CHECK (outcome IS NULL OR outcome IN ('coached', 'no_deaths')),
    battle_mode_id text,
    stage_id text,
    moment_count integer CHECK (moment_count IS NULL OR moment_count >= 0),
    result_json jsonb,
    localized_cache jsonb NOT NULL DEFAULT '{}'::jsonb,
    publication_state text NOT NULL CHECK (publication_state IN (
        'pending',
        'publishing',
        'verified_r2',
        'verified',
        'blocked',
        'delete_pending',
        'deleted'
    )),
    source_object_key text,
    source_upload_expires_at timestamptz,
    source_cleanup_state text NOT NULL DEFAULT 'pending' CHECK (source_cleanup_state IN (
        'pending',
        'pending_settle',
        'completed',
        'not_applicable'
    )),
    local_cleanup_state text NOT NULL DEFAULT 'pending' CHECK (local_cleanup_state IN (
        'pending',
        'completed'
    )),
    published_at timestamptz,
    expires_at timestamptz,
    cleanup_completed_at timestamptz,
    delete_empty_at timestamptz,
    updated_at timestamptz NOT NULL
);

-- Idempotent for databases that already created the table before fencing existed.
ALTER TABLE published_reports
    ADD COLUMN IF NOT EXISTS attempt_generation bigint NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS published_assets (
    asset_id uuid PRIMARY KEY,
    submission_id text NOT NULL
        REFERENCES published_reports(submission_id) ON DELETE CASCADE,
    run_id text NOT NULL,
    attempt_generation bigint NOT NULL DEFAULT 0 CHECK (attempt_generation >= 0),
    asset_type text NOT NULL CHECK (asset_type IN (
        'clip',
        'frame',
        'thumbnail',
        'visual_asset',
        'coaching_artifact',
        'bundle_manifest'
    )),
    moment_index integer CHECK (moment_index IS NULL OR moment_index >= 0),
    safe_id text,
    storage_provider text NOT NULL CHECK (storage_provider = 'r2'),
    bucket text NOT NULL,
    object_key text NOT NULL UNIQUE,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    sha256 text NOT NULL,
    publication_state text NOT NULL CHECK (publication_state IN ('verified', 'superseded')),
    deletion_state text NOT NULL DEFAULT 'retained' CHECK (deletion_state IN (
        'retained',
        'delete_pending',
        'deleted'
    )),
    created_at timestamptz NOT NULL,
    expires_at timestamptz,
    deleted_at timestamptz
);

ALTER TABLE published_assets
    ADD COLUMN IF NOT EXISTS attempt_generation bigint NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS published_assets_lookup_idx
    ON published_assets (submission_id, run_id, asset_type, moment_index);

CREATE INDEX IF NOT EXISTS published_assets_deletion_idx
    ON published_assets (submission_id, deletion_state);

CREATE INDEX IF NOT EXISTS published_reports_expiry_idx
    ON published_reports (publication_state, expires_at);

CREATE INDEX IF NOT EXISTS published_reports_cleanup_idx
    ON published_reports (publication_state, source_cleanup_state, local_cleanup_state);
