-- Additive P1-A migration. Apply only to an approved database.
ALTER TABLE submission_gates ADD COLUMN IF NOT EXISTS source text
    NOT NULL DEFAULT 'upload' CHECK (source IN ('upload', 'nintendo_review_code'));
CREATE TABLE IF NOT EXISTS review_code_acquisitions (
    submission_id text PRIMARY KEY REFERENCES submission_gates(submission_id),
    submission_snapshot jsonb NOT NULL,
    review_code text CHECK (review_code IS NULL OR review_code ~ '^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$'),
    target_player_name text,
    requested_email text,
    contact_verified_at timestamptz,
    retention_days integer NOT NULL CHECK (retention_days > 0),
    sensitive_data_purged_at timestamptz,
    recording_handoff_at timestamptz,
    handoff_reference text,
    report_expired_at timestamptz,
    state text NOT NULL DEFAULT 'pending_payment' CHECK (state IN (
        'pending_payment', 'awaiting_recording', 'recording', 'uploading', 'ready',
        'recording_failed', 'expired', 'closed')),
    created_at timestamptz NOT NULL,
    queued_at timestamptz,
    target_confirmed_at timestamptz,
    target_confirmed_by uuid,
    confirmed_player_name text,
    customer_message text,
    version integer NOT NULL DEFAULT 0,
    CHECK (state NOT IN ('recording', 'uploading', 'ready') OR target_confirmed_at IS NOT NULL)
);
ALTER TABLE review_code_acquisitions ALTER COLUMN review_code DROP NOT NULL;
ALTER TABLE review_code_acquisitions ALTER COLUMN target_player_name DROP NOT NULL;
ALTER TABLE review_code_acquisitions ALTER COLUMN requested_email DROP NOT NULL;
ALTER TABLE review_code_acquisitions ADD COLUMN IF NOT EXISTS sensitive_data_purged_at timestamptz;
ALTER TABLE review_code_acquisitions ADD COLUMN IF NOT EXISTS recording_handoff_at timestamptz;
ALTER TABLE review_code_acquisitions ADD COLUMN IF NOT EXISTS handoff_reference text;
ALTER TABLE review_code_acquisitions ADD COLUMN IF NOT EXISTS report_expired_at timestamptz;
ALTER TABLE review_code_acquisitions ADD COLUMN IF NOT EXISTS retention_days integer;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'review_code_acquisitions_retention_days_check'
    ) THEN
        ALTER TABLE review_code_acquisitions
            ADD CONSTRAINT review_code_acquisitions_retention_days_check
            CHECK (retention_days > 0);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS review_code_acquisitions_queue
    ON review_code_acquisitions (state, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS review_code_acquisitions_token
    ON review_code_acquisitions ((submission_snapshot->>'access_token_hash'));
CREATE TABLE IF NOT EXISTS review_code_acquisition_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    submission_id text NOT NULL REFERENCES review_code_acquisitions(submission_id),
    kind text NOT NULL,
    actor_id uuid,
    details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS review_code_acquisition_paid_once
    ON review_code_acquisition_events(submission_id) WHERE kind = 'paid_recording_queued';
