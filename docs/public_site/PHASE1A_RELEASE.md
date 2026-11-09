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

- Release: `23a304c91eb5989277c103c8376466c927880b63`; image
  `sha256:721071dc5499a05d5709a7216cab017c05020f72be2cb4b101b735aa46a7a417`
  (linux/amd64, revision label = release). That is the OCI index digest the
  build Mac's containerd image store shows as the image ID. The production
  host uses the classic `overlay2` store, which shows the config digest
  `sha256:d6261c6e1a57cecd9b537ad7f544508fb628b9b4f5be329c66185150da807eef`
  for the same image: in the release tarball the index `721071dc…` lists one
  amd64 manifest `61b1aa3c…` whose config is `d6261c6e…`. Later commits on the release
  branch change documentation only; `docs/` is not copied into the image.
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

### Production, phase 1: authoritative procedure

This is the only phase 1 procedure. The release image is the verified
tarball; nothing is built, synced or rsynced on the host. The compose project
`s3-coaching` runs service `app` from `s3-coaching-app:latest`; the compose
build context `/srv/www/s3-coaching` holds an older tree, so
`docker compose ... --build` is never run there.

Phase 1 scope: deploy release `23a304c`, preserve the current image, keep
`PUBLIC_REVIEW_CODES_ENABLED` absent, run no migration, enable no review-code
submission, restart only the `app` service, verify the video workflow. Not in
scope: feature enablement, database migration, review-code transactions,
schema rollback, destructive cleanup, P1-B.

Every numbered step is a gate: if any check prints something other than the
expected value, stop. Run from the operator Mac (`ssh Hetzner` is the
production host):

```bash
REL=23a304c91eb5989277c103c8376466c927880b63
NEW_ID=sha256:d6261c6e1a57cecd9b537ad7f544508fb628b9b4f5be329c66185150da807eef  # host (overlay2) ID
OLD_ID=sha256:5114061227fc4a942ce03f8069e0242d9c12b416cf38b198b1612a34ff979277
TAR=s3-coaching-candidate-23a304c91eb5-amd64.tar
TAR_SHA=1d14c07246c05e718cf0f422e129e728e23d62562713f8d1e048e6364c432a1f
HOST_DIR=/root/s3-coach-backups/deploy-23a304c9
VOL=/var/lib/docker/volumes/s3-coaching_coaching-data/_data
umask 077
B=~/backup/s3-coach/$(date -u +%Y%m%dT%H%M%SZ)-predeploy; mkdir -p "$B"
cat > "$B/counts.sql" <<'SQL'
select format('select %L, count(*) from public.%I', tablename, tablename)
from pg_tables where schemaname = 'public' order by tablename \gexec
SQL
```

1. **Start the predeployment backup** (mandatory even after the pre-check
   backup). Volume first (about 17 minutes at 4 MB/s), then the database, so
   the dump is the last state before activation. Run at a quiet time with no
   job in progress.

   ```bash
   ssh Hetzner "tar -C $VOL -cf - ." > "$B/coaching-data.tar"
   ssh Hetzner "find $VOL -type f | wc -l; find $VOL -type d | wc -l; \
     find $VOL -type f -printf '%s\n' | awk '{s+=\$1} END {print s}'" \
     > "$B/volume_manifest_host.txt"
   ssh Hetzner 'docker exec s3-coaching-db pg_dump -Fc -U coach coach' > "$B/coach.dump"
   ssh Hetzner 'docker exec s3-coaching-db pg_dump --schema-only -U coach coach' \
     > "$B/schema-before.sql"
   ssh Hetzner 'docker exec -i s3-coaching-db psql -X -At -F"|" -U coach -d coach' \
     < "$B/counts.sql" > "$B/prod_table_counts.txt"
   ```

2. **Verify checksums and restore.** All three must hold: checksums verify, the
   restored row counts equal production's, and the archive's file count,
   directory count and byte total equal the host manifest. A row-count
   difference caused by traffic between dump and count means: retake step 1's
   database part.

   ```bash
   (cd "$B" && shasum -a 256 coach.dump schema-before.sql coaching-data.tar \
     > SHA256SUMS && shasum -c SHA256SUMS)
   docker run -d --name restore-check -e POSTGRES_PASSWORD=check postgres:16.15
   until docker exec restore-check pg_isready -U postgres; do sleep 1; done; sleep 2
   docker exec restore-check createdb -U postgres restore_check
   docker exec -i restore-check pg_restore --no-owner --no-privileges \
     --exit-on-error -U postgres -d restore_check < "$B/coach.dump"
   docker exec -i restore-check psql -X -At -F"|" -U postgres -d restore_check \
     < "$B/counts.sql" > "$B/restored_table_counts.txt"
   diff "$B/prod_table_counts.txt" "$B/restored_table_counts.txt"   # no output
   tar -tvf "$B/coaching-data.tar" | awk '$1 ~ /^-/ {f++; s+=$5} $1 ~ /^d/ {d++}
     END {print f; print d; print s}' | diff - "$B/volume_manifest_host.txt"   # no output
   docker rm -f restore-check
   ```

3. **Preserve the current production image.**

   ```bash
   ssh Hetzner "docker inspect s3-coaching-web --format '{{.Image}}'"      # $OLD_ID
   ssh Hetzner 'docker image tag s3-coaching-app:latest s3-coaching-app:rollback-d0bea423'
   ssh Hetzner "docker image inspect s3-coaching-app:rollback-d0bea423 --format '{{.Id}}'"   # $OLD_ID
   ```

4. **Upload and verify the candidate tarball.**

   ```bash
   shasum -a 256 ~/p1a-release-evidence/$TAR                              # $TAR_SHA
   ssh Hetzner "mkdir -p $HOST_DIR"
   scp ~/p1a-release-evidence/$TAR Hetzner:$HOST_DIR/
   ssh Hetzner "sha256sum $HOST_DIR/$TAR"                                  # $TAR_SHA
   ```

5. **Load the image and verify its image ID.**

   ```bash
   ssh Hetzner "docker load -i $HOST_DIR/$TAR"
   ssh Hetzner "docker image inspect s3-coaching-candidate:23a304c91eb5-amd64 --format \
     '{{.Id}} {{.Architecture}} {{index .Config.Labels \"org.opencontainers.image.revision\"}}'"
   # $NEW_ID amd64 $REL
   ```

6. **Tag the candidate for the Compose configuration.**

   ```bash
   ssh Hetzner "docker image tag $NEW_ID s3-coaching-app:candidate-23a304c91eb5 && \
     docker image tag $NEW_ID s3-coaching-app:latest"
   ssh Hetzner "docker image inspect s3-coaching-app:latest --format '{{.Id}}'"   # $NEW_ID
   ```

7. **Restart only the application service.** Re-check the flag first; both
   counts must be `0`.

   ```bash
   ssh Hetzner 'cd /srv/www/s3-coaching && grep -c PUBLIC_REVIEW_CODES_ENABLED .env; \
     docker compose config | grep -c PUBLIC_REVIEW_CODES_ENABLED'          # 0, 0
   ssh Hetzner 'cd /srv/www/s3-coaching && docker compose up -d --no-build --no-deps app'
   ```

8. **Verify the running image revision and feature flag.**

   ```bash
   ssh Hetzner "docker inspect s3-coaching-web --format \
     '{{.Image}} {{index .Config.Labels \"org.opencontainers.image.revision\"}}'"   # $NEW_ID $REL
   ssh Hetzner 'docker exec s3-coaching-web printenv PUBLIC_REVIEW_CODES_ENABLED; echo "exit=$?"'   # exit=1
   ssh Hetzner 'curl -s -o /dev/null -w "%{http_code}\n" 127.0.0.1:8770/'               # 200
   ssh Hetzner 'curl -s 127.0.0.1:8770/api/config'               # "review_codes_enabled": false
   ssh Hetzner 'curl -s -o /dev/null -w "%{http_code}\n" -X POST \
     -H "content-type: application/json" -d "{}" 127.0.0.1:8770/api/review-code-submissions'   # 404
   ssh Hetzner 'docker exec s3-coaching-db pg_dump --schema-only -U coach coach' \
     > "$B/schema-after.sql"
   diff <(grep -v '^\\\(un\)\?restrict ' "$B/schema-before.sql") \
        <(grep -v '^\\\(un\)\?restrict ' "$B/schema-after.sql")       # no output
   ```

After step 8:

- [ ] Existing video workflow: one real video through the public site (create,
      upload, pay or voucher, queue, complete with coaching).
- [ ] At least one maintenance/worker cycle with no new errors.
- [ ] Append the deploy (release, image ID, rollback tag, backup folder) to
      `/srv/www/s3-coaching/DEPLOYED_SHA`.

Any failure from step 6 on: roll back as described under "Dark release" below.

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
