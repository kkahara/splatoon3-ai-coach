# P1-A release preparation: candidate, rollback, deployment checklist

Companion to `PHASE1A_REVIEW_CODE.md`. Nothing here authorizes a production
migration or deployment; each production step below needs explicit approval.

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
dump byte-identical to the one `d0bea42` creates; the live production schema
has not been compared yet, see the checklist).

## Deployment checklist

Each box is a go/no-go gate. Stop at the first failure.

Preparation (no production contact):

- [ ] Release commit created from `d0bea42` + allowlist; `git diff --stat
      d0bea42` lists exactly the 30 allowlisted paths.
- [ ] Candidate suites green from the release commit: `tests/test_public_*`,
      `tests/test_admin_*`, architecture boundary, `web/public` `npm test`,
      `npm run build`. Known pre-existing failures documented, none new.
- [ ] Image builds with `GIT_SHA=<release commit>`; record the image ID.
- [ ] Migration rehearsal re-run against the release commit.
- [ ] Stripe test mode: one real Checkout paid with a test card through the
      test-mode webhook endpoint (signature, `paid_recording_queued` once).
- [ ] SMTP: one guest confirmation and one account verification delivered to
      an operator-controlled mailbox with production SMTP settings.
- [ ] Operator has an admin account and has read the admin recordings UI flow.

Production, phase 1 (code only, flag off):

- [ ] Backup: `pg_dump -Fc` of the production database stored off-host;
      snapshot or copy of the `coaching-data` volume (`/data/public`).
- [ ] `pg_dump --schema-only` from production compared with the rehearsal's
      pre-migration schema; any difference reviewed before continuing.
- [ ] Current image preserved:
      `docker image tag <current app image> s3-coaching-app:prod-d0bea42`.
- [ ] `PUBLIC_REVIEW_CODES_ENABLED` absent or `0` in `/srv/www/s3-coaching/.env`.
- [ ] Sync from the release-commit tree only (never the development checkout):
      `src/ tools/ configs/ calibration/templates/ web/public/dist/
      pyproject.toml README.md Dockerfile docker-compose.yml .dockerignore`;
      `web/public/dist` built from the release commit.
- [ ] `docker compose up -d --build app` with `GIT_SHA=<release commit>`.
- [ ] Verify: image revision label equals the release commit; `/api/config`
      has `review_codes_enabled: false`; `POST /api/review-code-submissions`
      is 404; schema unchanged (`\d submission_gates` has no `source`).
- [ ] Video smoke test: create, upload, pay (or voucher), queue, complete.
- [ ] Observe at least one maintenance/worker cycle with no new errors.

Production, phase 2 (enable):

- [ ] Fresh `pg_dump -Fc` backup.
- [ ] Set `PUBLIC_REVIEW_CODES_ENABLED=1`; `docker compose up -d app`.
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

4. Restore the preserved image
   (`docker image tag s3-coaching-app:prod-d0bea42 <compose app image>`) or
   re-sync the `d0bea42` tree and rebuild with `GIT_SHA=d0bea42…`;
   `docker compose up -d app`.
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
