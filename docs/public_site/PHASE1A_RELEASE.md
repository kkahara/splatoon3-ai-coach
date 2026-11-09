# P1-A release preparation: candidate, rollback, deployment checklist

Companion to `PHASE1A_REVIEW_CODE.md`. Nothing here authorizes a production
migration or deployment; each production step below needs explicit approval.

## Release mode: dark until P1-B

P1-A ships with `PUBLIC_REVIEW_CODES_ENABLED` absent. P1-A has no recording
runner, so a paid review-code job could only wait or be refunded; the flag is
turned on only with P1-B. A dark deploy is code-only: with the flag off the
candidate creates no table, column or index, the review-code routes are 404,
and maintenance never creates review-code projections. Phase 2 below belongs to
the P1-B release.

## Candidate

- Base: production `d0bea4230a54f1750c1edaa0259e93755be79ea2`.
- Overlay: exactly the P1-A allowlist below. Every tracked allowlisted file is
  byte-identical at `d0bea42` and at the local `HEAD` it was edited against, so
  the overlay adds only the P1-A diff. `tests/test_public_site.py` differs
  between those revisions and receives only the P1-A test hunk as a patch.
- Excluded: `tools/public_site/worker.py`, `tools/public_site/clips.py`, all of
  `src/`, coaching/analysis, diagnostics, studies, VMV, README, clip docs.

Allowlist (30 paths):

```text
tools/public_site/{app,guest_notices,models,payments,service,settings,store}.py
tools/public_site/recording_{models,routes,service,store}.py
tools/public_site/migrations/001_review_code_acquisitions.sql
tests/test_public_payments.py
tests/test_public_recordings.py
tests/test_public_site.py            (patch: one P1-A test only)
docs/follow-ups/retention-deletion.md
docs/public_site/PHASE1A_REVIEW_CODE.md
docs/public_site/PHASE1A_RELEASE.md
web/public/src/{App.jsx,api.js}
web/public/src/i18n/{en,ja}.json
web/public/src/pages/{AdminLayout,Review,Submit}.jsx
web/public/src/pages/{AdminRecordings,ReviewCodeForm,ReviewCodeStatus}.jsx
web/public/src/pages/recordingForm{,.test}.js
```

Before building, `git status` in the candidate tree must list exactly these
paths and nothing else.

The Dockerfile requires `GIT_SHA` to be the commit whose tree is the build
context. A deployable image therefore needs a release commit (for example on a
`release/p1a` branch from `d0bea42`); an uncommitted tree must not be labeled
with `d0bea42` or any other SHA.

## Migration

`001_review_code_acquisitions.sql` is additive: `submission_gates.source text
NOT NULL DEFAULT 'upload'` plus a CHECK, and two new tables
(`review_code_acquisitions`, `review_code_acquisition_events`) with indexes. No
existing column, constraint, index or row value is altered. It runs at app
startup only when `PUBLIC_REVIEW_CODES_ENABLED` is on, inside one transaction
under advisory lock `8273341907`, and is safe to repeat.

Lock profile: `ALTER TABLE submission_gates` takes an ACCESS EXCLUSIVE lock for
the column add (metadata-only for a constant default on PostgreSQL 11+) and the
CHECK validation (one scan of `submission_gates`). Apply while the app is
stopped or idle.

With the flag off, the candidate does not touch the schema (rehearsal: schema
dump byte-identical to the one `d0bea42` creates). The live production schema
(PostgreSQL 16.15, `pg_dump --schema-only`) matches the schema `d0bea42`
creates except for column order in `job_runs`, where production added columns
later with `ALTER TABLE`. Rehearsals restored from that production schema
behaved the same as rehearsals on a fresh database.

## Deployment checklist

Each box is a go/no-go gate. Stop at the first failure.

Preparation (no production contact):

- [ ] Release commit created from `d0bea42` + allowlist; `git diff --stat
      d0bea42` lists exactly the 30 allowlisted paths.
- [ ] Candidate suites green from the release commit: `tests/test_public_*`,
      `tests/test_admin_*`, architecture boundary, `web/public` `npm test`,
      `npm run build`. Known pre-existing failures documented, none new.
- [ ] Image builds with `GIT_SHA=<release commit>` for `linux/amd64` (the
      production host is x86_64); record the image ID and revision label.
- [ ] Migration rehearsal re-run against the release commit.
- [ ] Stripe test mode: one real Checkout paid with a test card through the
      test-mode webhook endpoint (signature, `paid_recording_queued` once).
- [ ] SMTP: one guest confirmation and one account verification delivered to
      an operator-controlled mailbox with production SMTP settings.
- [ ] Operator has an admin account and has read the admin recordings UI flow.

Production, phase 1 (code only, flag off). This follows the procedure recorded
in `/srv/www/s3-coaching/DEPLOYED_SHA`: compose project `s3-coaching`, service
`app` runs `s3-coaching-app:latest`, images are tagged
`candidate-<sha12>` / `rollback-<sha8>`, and activation never builds. The
compose build context `/srv/www/s3-coaching` holds an older tree; never run
`docker compose ... --build` there.

- [ ] Backup into `/root/s3-coach-backups/deploy-<sha8>/`:
      `docker exec s3-coaching-db pg_dump -Fc -U coach coach > predeploy.dump`,
      checked with `pg_restore --list`; `pg_dump --schema-only` saved as
      `schema-before.sql`; archive of the `s3-coaching_coaching-data` volume.
      Copy both off the host before continuing.
- [ ] Current image preserved: `docker image tag s3-coaching-app:latest
      s3-coaching-app:rollback-d0bea423`; its ID must be
      `sha256:5114061227fc4a942ce03f8069e0242d9c12b416cf38b198b1612a34ff979277`.
- [ ] `PUBLIC_REVIEW_CODES_ENABLED` absent (not merely `0`): `grep -c` on
      `/srv/www/s3-coaching/.env` and on `docker compose config` both `0`.
- [ ] Release image present as `s3-coaching-app:candidate-<sha12>`, either
      loaded from the verified release tarball (`docker load`; the image ID
      equals the recorded release ID) or built on the host from a clean
      snapshot in `/srv/www/s3-coaching-staging/<sha12>` with
      `GIT_SHA=<release commit>`. Python dependencies are unpinned, so a host
      build resolves them again and gets a different image ID; record it.
- [ ] Activate: `docker image tag s3-coaching-app:candidate-<sha12>
      s3-coaching-app:latest`, then from `/srv/www/s3-coaching`:
      `docker compose up -d --no-build --no-deps app`. Append the deploy to
      `DEPLOYED_SHA`.
- [ ] Verify: container image ID equals the release ID and the revision label
      equals the release commit; `/` and `/api/config` return 200;
      `/api/config` has `review_codes_enabled: false`;
      `POST /api/review-code-submissions` is 404; `pg_dump --schema-only`
      identical to `schema-before.sql`.
- [ ] Video smoke test: create, upload, pay (or voucher), queue, complete.
- [ ] Observe at least one maintenance/worker cycle with no new errors.

Production, phase 2 (enable): P1-B release only, not part of P1-A. Kept here
because the migration and rollback facts below were rehearsed against it.

- [ ] Fresh `pg_dump -Fc` backup.
- [ ] Set `PUBLIC_REVIEW_CODES_ENABLED=1`;
      `docker compose up -d --no-build --no-deps app`.
- [ ] Verify schema: `submission_gates.source` exists, every pre-existing row
      is `upload`; both new tables and the `review_code_acquisition_paid_once`
      index exist.
- [ ] Verify `/api/config` shows `review_codes_enabled: true`; admin
      Recordings nav visible to admins only.
- [ ] Controlled live test with an operator-owned mailbox and a real low-value
      payment: submission, confirmation mail, checkout, webhook,
      `awaiting_recording`, admin queue entry; resolve it with the refund
      action and an external Stripe refund.
- [ ] Video smoke test again; queue capacity unaffected by review backlog.
- [ ] Monitoring for 24 h: worker loop errors, webhook non-2xx, SMTP failures,
      `review_code_acquisitions` states.

## Rollback

### Dark release (flag never enabled)

No schema change and no review-code projection exists, so rollback is the
image swap alone:

1. Confirm nothing review-code exists: no `review_code_acquisitions` table and
   no projection with `"input_type": "review_code"` under
   `/data/public/submissions`. If either exists, the flag was enabled at some
   point; use Level 2 instead.
2. `docker image tag s3-coaching-app:rollback-d0bea423 s3-coaching-app:latest`;
   from `/srv/www/s3-coaching`: `docker compose up -d --no-build --no-deps app`.
3. Verify the container image ID is `sha256:5114061227fc…`, the revision label
   is `d0bea42…`, and run the video smoke test. Append the rollback to
   `DEPLOYED_SHA`.

Levels 1 to 3 apply once the flag has been enabled (P1-B).

### Level 1: disable the feature (preferred, no code change)

Set `PUBLIC_REVIEW_CODES_ENABLED=0` and restart the app. Review-code routes
disappear and maintenance leaves review-code submissions untouched (both the
abandon and expiry sweeps skip them). Late Stripe payments are still recorded
on the gate and are picked up when the flag is turned back on. Video uploads
are unaffected. The migrated schema stays; nothing is dropped.

### Level 2: roll the code back to `d0bea42`

The production code predates review-code isolation. Rehearsal: when it sees
review-code projections, its maintenance marks paid and unpaid review-code
jobs `failed` within the upload window and emails paying customers a failure
notice, while their database rows still say paid/`awaiting_recording`. Never
run `d0bea42` with review-code projections in the data root.

1. Level 1 first; confirm the flag is off.
2. `docker compose stop app`; take `pg_dump -Fc` and a copy of `/data/public`.
3. Quarantine review-code projections (keep the token index files; they only
   point to missing projections):

   ```bash
   docker compose run --rm --no-deps app python - <<'EOF'
   import json, shutil
   from pathlib import Path
   root = Path("/data/public"); hold = root.parent / "review_code_quarantine"
   for path in sorted((root / "submissions").glob("*.json")):
       if json.loads(path.read_text()).get("input_type") == "review_code":
           hold.mkdir(exist_ok=True); shutil.move(str(path), hold / path.name)
           print("quarantined", path.name)
   EOF
   ```

4. Restore the preserved image:
   `docker image tag s3-coaching-app:rollback-d0bea423 s3-coaching-app:latest`;
   `docker compose up -d --no-build --no-deps app`.
5. Verify the video smoke test. Review-code status pages return 404 during the
   rollback; their payments and acquisition rows are preserved in PostgreSQL.

Rehearsal: with projections quarantined, `d0bea42` left review-code jobs and
mail untouched and queued new paid videos normally.

### Roll forward after Level 2

Deploy the candidate with the flag on. Reconciliation rebuilds every missing
review-code projection from its committed database snapshot (rehearsal: both
jobs restored, the paid one still `awaiting_recording`). After verifying, delete
`/data/review_code_quarantine` (it may hold contact data).

### Level 3: schema rollback

Not provided. The migration is additive and `d0bea42` runs on the migrated
schema (rehearsal). Dropping the acquisition tables would destroy payment-linked
audit history and needs separate approval after every paid acquisition is
resolved.
