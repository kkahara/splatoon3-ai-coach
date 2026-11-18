# P1-A: review-code acquisition

## Scope and compatibility

P1-A adds a separately gated Nintendo review-code submission path. It uses the
existing submission identifier, checkout provider, guest email verification,
account authentication, admin authorization, and analysis submission model.
The video-upload path remains the default and retains its existing notification
preference and upload/analysis behavior. `PUBLIC_REVIEW_CODES_ENABLED` defaults
to false. P1-A does not alter global storage settings or move existing videos
to R2.

There is no reliable player-slot identifier exposed by the existing review-code
workflow before the replay is recorded. The customer therefore provides the
player's in-game name as a hint. An operator must inspect the replay and confirm
the visible target name before any later recording/upload/analysis handoff.
Neither account identity, Nintendo account identity, nor replay ordering is
used to infer the target. A corrected review code clears any earlier target
confirmation.

A review-code request creates a durable PostgreSQL acquisition row and payment
gate in one transaction. The row stores the normalized code, target-name hint,
requested contact, submission snapshot, acquisition state, and versioned
operator audit events. A paid Stripe webhook changes the acquisition to
`awaiting_recording` only when its contact address is verified. Reconciliation
repeats this transition idempotently and restores missing filesystem
projections from the committed database snapshot, covering a crash after payment
recording and before queue publication. The existing upload queue and its
capacity accounting exclude review-code rows.

Guest email verification reuses the existing browser-bound notice flow. An
account must already be verified. Checkout itself also requires verified
contact. Review-code account notices respect the saved `notify_by_email`
preference; guest verification enables contact notices through the existing
guest flow. Existing upload notification defaults and settings are unchanged.

Unpaid checkout sessions expire under the existing payment hold. The
review-code acquisition has its own state and remains valid after payment even
when the checkout's `expires_at` has passed. Existing 15-minute upload
abandonment and normal submission expiry explicitly skip review-code
acquisitions. Unpaid sensitive acquisition data is removed only after the
checkout expiry plus the per-submission retention interval described below.
Paid jobs waiting for recording or marked `recording_failed` do not expire
automatically and retain the raw code until operator resolution.

## P1-A boundary

P1-A does not record or upload replays and does not publish review-code
submissions to the analysis worker. The operator queue can confirm the target,
mark a recording exception, correct a code, or record a reference for a refund
that was completed separately. It does not perform a Stripe refund. The
P1-A has no recording runner or caller that can verify analysis acceptance.
The storage method `confirm_recording_handoff` is a guarded boundary for P1-B:
it accepts only a target-confirmed `uploading` job, records the durable
handoff reference, changes state to `ready`, and clears the raw review code.
P1-B must call it only after verifying that the artifact was accepted by the
existing analysis pipeline. Until then, P1-A paid jobs remain held with their
code retained. No P1-A review-code submission can reach coaching.

## Sensitive-data retention

At creation, the acquisition stores the normalized review code, player-name
hint, requested email, contact-verification timestamp, and submission
snapshot. Its `retention_days` value is copied from the existing configured
guest or account retention setting; existing rows are backfilled from the
current settings when the additive migration first runs. There are no new
fixed retention periods in the migration.

| State or transition | Fields removed | Records retained |
| --- | --- | --- |
| Unpaid guest/account after checkout expiry + captured `retention_days` | `review_code`, `target_player_name`, `requested_email`, `contact_verified_at`; snapshot `email`, `display_name`, `user_id` (set null) and `notify` (false); matching guest notice request/challenges and the shared verified-email marker only when no other request still uses it; the browser row when it has no remaining request, challenge, or verified-email reference | Submission, payment gate and payment references, access-token hash, acquisition row/state, `sensitive_data_purged_at`, and audit/event history |
| Paid awaiting recording or `recording_failed` | Nothing automatically | Raw review code and all operator-required acquisition data remain until resolution; these states do not follow unpaid or report expiry |
| Externally completed refund recorded by an operator | `review_code`, target-name hint, requested email, contact timestamp, and snapshot identity/notification fields | Submission, payment records/references, closed acquisition, refund reference and audit history |
| Verified recording handoff accepted by the analysis pipeline | `review_code` | Submission and payment records, handoff timestamp/reference, acquisition and audit history; target confirmation remains as audit/domain history |
| Existing report-retention deadline after successful handoff | Requested email, target-name hint, contact timestamp, snapshot identity/notification fields; existing expiry removes report files, clears submission identity and coaching-history link | Submission, payment records/references, acquisition row, handoff reference, and required audit history |

Cleanup is retriable: unpaid purge is guarded by `sensitive_data_purged_at`,
report expiry is durably marked before filesystem deletion, and handoff replay
with the same reference is idempotent. A failed handoff cannot clear the code
because the only clearing transition requires a verified handoff reference and
the `uploading` plus target-confirmed state. Video-upload retention periods are
unchanged. The shared expiry step now marks a submission expired before deleting
its analysis directory, clears identity even when that deletion fails, and keeps
`analysis_dir` set so the next pass retries it (previously a failed deletion was
ignored). Reconciliation republishes an acquisition projection only when its
committed row changed since the last successful publish in the running process,
so settled rows add no per-pass database work; a restart re-sweeps all rows once.

## Database migration

`tools/public_site/migrations/001_review_code_acquisitions.sql` is additive. The
existing payment schema ensure performs only a read-only check for
`submission_gates.source`; it does not add review-code schema. When the review
feature is enabled, the migration adds `source` with existing rows defaulting
to `upload`, creates the acquisition and audit-event tables and indexes, and
adds acquisition retention/handoff markers. It does not rewrite existing video
submissions or their storage. Runtime migration is idempotent and only runs
when the feature is enabled and the existing PostgreSQL/payment services are
available.

This work has not applied the migration to production. Before enabling the
feature, review and apply the migration to the intended PostgreSQL database,
verify the schema, deploy the isolated candidate, and only then set the feature
flag. Keep the flag off until the database and operator workflow are ready.
Tests use a disposable local PostgreSQL database and fake checkout events; no
real Stripe charges are made.

## Release isolation from production

Production is at `d0bea4230a54f1750c1edaa0259e93755be79ea2`. The current local
checkout is newer and includes unrelated changes, including the newer strict
coaching-output validation in `tools/public_site/worker.py`. Do not build or
deploy P1-A from the current checkout or merge its unrelated changes as part of
this release.

Prepare a release branch or clean worktree from the production revision, then
apply only the reviewed P1-A allowlist: the review-code store/service/routes and
models, additive migration, feature-flag/config and source-isolation changes in
the existing app/payment/submission services, public-site React form/status and
admin exception UI with translations, and the dedicated P1-A tests and this
document. Exclude `tools/public_site/worker.py`, `tools/public_site/clips.py`,
coaching/analysis changes, diagnostics, VMV changes, and other local edits.
Run the allowlisted candidate's relevant database/payment/auth/compatibility
suite from that clean production-based tree, and compare the diff against the
allowlist before building an image. Preserve the current production image and
record the candidate image digest. No production migration or deployment is
authorized by P1-A approval.

A rollback needs special handling: the production worker predates review-code
source isolation and cannot safely consume pending review-code submissions.
Before any rollback to the old worker, disable new review-code submissions and
ensure all paid acquisitions are either safely held outside its worker queue or
resolved. Do not replay pending review-code rows into the old upload worker.

## Next checkpoint

Stop here for review. P1-B can be planned only after this P1-A diff and test
report are accepted. P1-B should define the recording artifact contract and
operator confirmation evidence, upload verification, idempotent durable handoff
to the existing analysis worker, and recovery behavior before implementation.
