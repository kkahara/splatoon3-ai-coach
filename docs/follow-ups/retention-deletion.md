# Retention and deletion follow-up

Do not treat the current expiry behavior as a privacy promise until these gaps are decided. This note does not change the implementation.

## What the code does today

`PUBLIC_RETENTION_DAYS` defaults to 30 and `PUBLIC_ACCOUNT_RETENTION_DAYS` defaults to 365. Those values are written onto `submission.expires_at`. They are configuration, not fixed guarantees.

The worker calls `maintain()` on each tick. A submission already in `processing` is skipped even after `expires_at`.

When the retention pass does run, it deletes the video object and the analysis directory, clears the submission's email, display name, and user id, deletes the coaching-history pointer, and clears `job_runs.user_id`. It does not delete the account.

## Records the retention pass does not remove

- Feedback text in `feedback`.
- Guest notice addresses in `guest_notice_requests`, verified addresses in `guest_verified_emails`, and idle-expired `guest_notice_browsers` rows. An idle browser expires after 180 days and absolutely after 365 days, but the row is left in place.
- Telemetry run rows, events, and coaching-count snapshots. Expiry only unlinks the user id.
- The account row: name, email, password hash, signup IP hash, role, last login, last seen, and notice preference.

## Unpaid submissions take a different path

`PUBLIC_PAYMENT_HOLD_SECONDS` defaults to 24 hours. That pass deletes the video and marks the submission `expired`, but it leaves the email, display name, user id, and coaching-history row in place. The later retention pass then skips the row because its status is already `expired`.

## Decision needed

Decide which of those leftovers should be deleted, and whether unpaid expiry should use the same cleanup as the retention pass. Wording for a future Privacy page has to follow that decision.

## Review-code acquisition retention (P1-A)

The review-code acquisition path has a separate, approved cleanup rule. It uses
the existing configured `PUBLIC_RETENTION_DAYS` (guest) and
`PUBLIC_ACCOUNT_RETENTION_DAYS` (account) values captured as
`review_code_acquisitions.retention_days` at submission creation. For an
unpaid review-code submission, sensitive acquisition fields are removed when
`submission_gates.expires_at + retention_days` is reached. This does not change
the unpaid video-upload behavior described above.

At that cutoff, cleanup nulls `review_code`, `target_player_name`,
`requested_email`, and `contact_verified_at`; clears email, display name, user
ID, and notification preference in `submission_snapshot`; deletes the
submission's guest notice request and challenges; deletes the associated
browser/email verified marker only when no other guest request uses it; and
deletes the browser row only when no request, challenge, or verified-email
reference remains. It
retains the submission, payment gate and payment references, token hash,
acquisition row and state, purge timestamp, and audit events.

Paid jobs awaiting recording and `recording_failed` jobs have no automatic
expiry and retain the raw code until operator resolution. A raw code is cleared
after P1-B verifies the recording was accepted by the existing analysis
pipeline and records the durable handoff reference. After that successful
handoff, the existing submission report-retention deadline applies: report
files are removed, submission identity/history pointers are cleared, and the
acquisition/payment/audit history is retained. A failed handoff leaves the
code available for retry. Refund resolution clears acquisition contact/code
fields while retaining the payment and audit records.

Unpaid review cleanup is safe to retry using its purge timestamp. Report
expiry is marked in PostgreSQL before deleting files, so retries finish a
partially completed cleanup. These rules do not change video-upload retention
periods. One shared change applies to both paths: the retention pass now marks a
submission expired before deleting its analysis directory, clears identity even
if that deletion fails, and keeps `analysis_dir` set so a later pass retries the
deletion (previously a failed deletion was silently ignored).
