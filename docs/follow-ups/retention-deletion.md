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
