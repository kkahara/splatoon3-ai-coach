# P1-B B2 release: report publishing gates and runbook

Companion to `REPORT_PUBLISHING.md`. Nothing here authorizes a real R2 write,
a production credential, a production configuration change, a deploy, a push
or a tag. Each gate below needs its own explicit approval; stop at the first
failure.

## Baseline

- Branch `p1b/r2-storage`, from `p1a-review-code-frozen` (P1-A, frozen; never
  modified). B1: `5748a85`, `e774608`. D1 clip metadata fix: `535cff7`.
- Production image before P1-B: `d6261c6e…` (the rollback target; verified
  below).
- All three `PUBLIC_REPORT_*` settings default to off. With them absent the
  build sends zero report R2 requests. What still differs from P1-A with every
  flag off is listed under Gate 4.

## Rollback image (verified on the host, 2026-11-12)

Read-only `docker inspect` / `docker image ls` on the production host, not
copied from earlier notes:

- Running container `s3-coaching-web`: image
  `sha256:d6261c6e1a57cecd9b537ad7f544508fb628b9b4f5be329c66185150da807eef`,
  `org.opencontainers.image.revision` =
  `23a304c91eb5989277c103c8376466c927880b63` (P1-A release, tag
  `p1a-review-code-frozen`), amd64, created 2026-11-09T19:23:37Z, started
  2026-11-09T21:36:48Z. Matches `PHASE1A_RELEASE.md` and the last
  `DEPLOYED_SHA` entry.
- Tags on that image: `s3-coaching-app:latest`,
  `s3-coaching-app:candidate-23a304c91eb5`,
  `s3-coaching-candidate:23a304c91eb5-amd64`. No repo digest: the image was
  loaded from a tarball. `d6261c6e…` is the overlay2 config digest; the build
  Mac's containerd index digest for the same image is `721071dc…`.
- Second source: `/root/s3-coach-backups/deploy-23a304c9/s3-coaching-candidate-23a304c91eb5-amd64.tar`
  (read-only, sha256 `1d14c07246c0…`, as recorded in `DEPLOYED_SHA`).
- Compose: project `s3-coaching`
  (`/srv/www/s3-coaching/docker-compose.yml`). Service `app` has `build:` and
  no `image:`, so it resolves to `s3-coaching-app:latest`
  (`docker compose config --images`). Restoring an image is therefore a
  retag of `s3-coaching-app:latest` plus
  `docker compose up -d --no-build --no-deps app`. Without `--no-build`,
  Compose may build from the host checkout instead of using the image.
- The older rollback `s3-coaching-app:rollback-d0bea423` is still present
  (`5114061227fc…`, revision `d0bea42…`).

## Disabled in this deployment: storage timeout (open)

The 600 s delete settle (A3) is **not** a proven guarantee. Gate 1 finding 1
(`PHASE1B_GATE1_RESULTS.md`) observed one storage request running about 16
minutes: `urlopen(timeout=60)` bounds each socket operation, not the whole
request. A PUT still in flight after expiry deletion can land after the
settle window and leave an object that nothing tracks.

Deploying the P1-B code (Gate 4) is allowed: with the settings below absent,
the build sends no report R2 requests. Until the timeout is resolved (an
overall request deadline, or evidence that bounds a late PUT below the settle
window) and that resolution is reviewed, these stay **absent** in production
and the gates that need them do not run:

| Setting | Gate | Why it stays off |
|---|---|---|
| `PUBLIC_REPORT_PUBLISH` | 5 | Enabled only together with expiry deletion |
| `PUBLIC_REPORT_EXPIRY_DELETE` | 5 | Relies on the 600 s settle |
| `PUBLIC_REPORT_SWEEP` | 6 | Deletes on one empty LIST; also waits for B3 |
| `PUBLIC_STORAGE=r2` (source video) | none | Source deletion has no settle; presigned replay is frozen debt (`docs/follow-ups/source-video-presigned-replay.md`) |
| `PUBLIC_R2_*` credentials | 2 | Not needed while the rows above are off |

Production on 2026-11-12 (names only): `PUBLIC_STORAGE` is `local`; no
`PUBLIC_REPORT_*`, `PUBLIC_R2_*`, `PUBLIC_REVIEW_CODES_ENABLED` or
`PUBLIC_RECORDING_STATION` is set. The Gate 4 deploy keeps it that way.

## B2 review corrections (R1–R4): behavioral changes

Each item changes behavior documented in the first B2 review package.

- **R1, scan/publisher parity.**
  - `scan` and the publisher build known values with one function.
  - The acquisition fields checked grow from `review_code`,
    `target_player_name` and `requested_email` to also include
    `confirmed_player_name`, `customer_message`, `handoff_reference` and the
    `submission_snapshot` email, display name, user ID and token hash.
  - Known values are also matched after JSON decoding (catching `\uXXXX`
    escapes), case-insensitively and ignoring whitespace and hyphens. This
    is stricter, so more content can block.
  - `scan` now reads acquisitions from `PUBLIC_DATABASE_URL` (SELECT only)
    when it is set, and reports `analyses_unchecked` / `unchecked`.
    `analyses_clean` no longer counts an analysis whose values could not
    all be retrieved.
  - The publisher now fails closed (`network`, backoff) when a value group
    is unknown. Before, a review-code submission without the `recordings`
    service was checked without its acquisition values. When review codes
    are disabled, the publisher now reads the same row from
    `PUBLIC_DATABASE_URL`.
  - A review-code submission with no acquisition row is unchecked, in both
    the publisher and `scan`. This covers no lookup, a failing lookup, a
    lookup that returns no row, and a database without the acquisition
    table. Uploads without an acquisition row stay fully checked.
- **R2, in-flight write barrier.**
  - A new non-blocking per-submission `flock`
    (`report_publications/.writers/<id>.lock`) is held by each publication
    attempt for its whole life, by expiry deletion and by each sweep row.
  - Expiry deletion that finds it busy defers 60 s; a sweep row that finds
    it busy is `skipped_on_recheck`; a publication attempt that finds it
    busy defers 60 s. None of these counts as a failure.
  - A publication attempt re-checks its run before every object and stops
    without recording a failure when the run was invalidated.
- **R3, unexpected exceptions.**
  - An unexpected exception in a publication attempt is now `local_missing`
    (`FileNotFoundError`) or `local_changed` (anything else), with backoff
    and a new run. Before, it escaped the attempt, left the record due, and
    a wake-up could retry it at once.
  - An unexpected exception while deleting is `network`.
  - The publisher thread pauses 60 s after any escaped exception, ignoring
    wake-ups.
  - Logs carry the exception type name only.
- **R4, JPEG.**
  - The whole marker structure is now walked, including segments between
    progressive scans and after the last scan. Before, inspection stopped at
    the first SOS.
  - New `jpeg_invalid` cases: a truncated or malformed segment, a missing
    EOI, a second SOI, or any byte after EOI.
- **Test helper.** The fake S3 gains `put_gate_after` (gate only after N
  PUTs).

The real-data scan of the 28 local dev analyses (571 files) is still clean
with these changes: 0 findings, 0 unchecked, empty inventory diff.

## Batch A follow-ups: behavioral changes

Details and operator procedures are in `REPORT_PUBLISHING.md`
("Review-code canary", "Delete settle (late PUTs)", "Retry policy").

- **A1, review-code HMAC canary.**
  - A per-submission HMAC-SHA-256 of the compacted, uppercased review code
    is written to `PUBLIC_ROOT/report_canary/<id>.json` (directory 0700,
    files 0600, owner-checked, `O_NOFOLLOW`) **before** the acquisition row
    is inserted. Capture failure refuses the submission with 503.
  - The key (`key.json`) is created once, under a lock, only when there is
    no key, no canary entry and no `report_canary.epoch.json` marker. It is
    never regenerated. A missing or unreadable key, or a missing entry for a
    post-epoch review-code submission, makes that submission unchecked
    (`review_code_canary`): the publisher fails closed and `scan` reports
    it unchecked. It is never a clean result.
  - The publisher and `scan` match the HMAC over every value, JSON string,
    key, filename, metadata field and text run (whitespace and hyphens
    between characters allowed). The canary outlives recording handoff and
    is removed only with the publication record, or by the hourly prune
    after the submission is gone.
  - `correct_code` stages `<id>.pending.json`, updates the database, then
    promotes it. A pending file is never a valid canary: while it exists the
    submission is unchecked. Startup repair promotes a pending record only
    after verifying its HMAC against the database code. Multiple pending
    files, a mismatch, an unreadable file or a failed lookup are left in
    place and fail closed.
  - New `public-site-reports canary-status [--json]`: counts only (never a
    code, HMAC or key), exit 1 on any pending record, unchecked submission,
    unsafe directory, or key/epoch marker without a valid key.
  - Backup: `report_canary/` and `report_canary.epoch.json` are backed up
    together (tar procedure in `REPORT_PUBLISHING.md`).
- **A2, privacy.**
  - JPEG: strict segment allowlist (SOFn, DHT, DQT, DRI, SOS, DNL, RST, at
    most one JFIF APP0 with a 14-byte body and no thumbnail, at most one
    `Lavc` COM). Every other APPn, COM, JFXX, thumbnail and DAC is
    `jpeg_invalid`. All 65 real stills fit the allowlist unchanged.
  - Known values are NFKC-normalized; player and display names under four
    characters match exactly (as a whole string value) instead of being
    skipped; a numeric `user_id` is checked as text.
- **A3, lifecycle.**
  - Expiry deletion keeps the record until its prefix has stayed empty for
    `DELETE_SETTLE_SECONDS` (600 s), re-listing every 60 s and deleting any
    late PUT. A failed delete restarts the settle window.
  - `run_once` attempts multiple due records per pass (at most
    `MAX_RECORDS_PER_PASS` = 50), each at most once per pass, each with its
    own exception handling and backoff. One record's failure never stops
    the next. A backoff write failure is contained to its record.
  - Publisher startup runs inside the loop and is retried after 60 s; no
    record is processed before startup succeeds.
  - The bundle PUT runs outside the record lock (barrier still held); the
    run is re-checked under the lock before it is marked verified.
- **Time budget.** The canary adds about 0.16 s per analysis on the real
  data (28 analyses: 13.3 s without, 17.8 s with), within the 1 s budget.
- **New tests.** `test_public_report_canary.py`,
  `test_public_report_canary_db.py`, `test_public_report_privacy.py`,
  `test_public_report_lifecycle.py`. Existing expiry tests call `settle()`.

## Gate 1 finding: upload confirmation race (fixed)

The Gate 1 end-to-end run returned 500 from `POST
/api/submissions/<token>/uploaded`. The request's `confirm` and the
maintenance pass (`_recover_ready` on a paid site) probed one submission at
the same time, and both downloads used `probe/.<id>.<ext>.part` and
`probe/<id>.<ext>`.

- `R2Storage.download` writes to a partial file unique to each call
  (`mkstemp` in the destination directory), so concurrent downloads never
  share or remove each other's partial data.
- Each probe downloads to its own `probe/<id>.<random><ext>`, so one probe
  can no longer delete the file another is still reading (which would have
  failed a valid upload as invalid).
- `attempt_queue` and the free-site `_probe_and_queue` run one at a time per
  submission (in-process `KeyedLocks`). The later caller re-reads the
  submission and returns the first caller's result without downloading or
  probing again.
- Tests: `test_public_probe_race.py`: concurrent downloads to one
  destination (fake S3 holds the first GET mid-body), the paid-site
  `confirm` racing `maintain` (database), the free-site `confirm` racing
  `maintain`, and the lock helper. The first two fail on the previous code
  (`FileNotFoundError`; two probes), and the paid-site test fails with the
  lock disabled.

## Local verification (before any gate)

```bash
docker run --rm -d --name p1b-test-pg -e POSTGRES_PASSWORD=p1btest \
  -p 127.0.0.1:55432:5432 postgres:16
export PUBLIC_DATABASE_URL=postgresql://postgres:p1btest@127.0.0.1:55432/postgres
pytest tests/test_public_*.py tests/test_architecture_*.py -p no:cacheprovider
pytest -m rollback -rs tests/test_public_report_rollback.py
docker stop p1b-test-pg
```

- [ ] Public and architecture suites pass.
- [ ] `pytest -m rollback -rs` shows **passed**, not skipped. A skip means the
      `p1a-review-code-frozen` tag or `git` is missing from that clone; fetch
      the tag and rerun.
- [ ] `ruff check` is clean.
- [ ] `public-site-reports scan --inventory tests/fixtures/report_bundle/key_inventory.json`
      over the local dev analyses: zero findings, zero unchecked, empty
      inventory diff.

## Gate 1: Mac staging against `s3-coaching-dev`

Dev bucket credentials only (the Mac `.env` never holds the production key).

- [ ] **Payload-hash probe:** record whether R2 rejects a PUT whose body does
      not match the signed `x-amz-content-sha256`. Integrity holds either way
      (`HashingReader` plus read-back).
- [ ] **End to end:** `PUBLISH=1`, `EXPIRY_DELETE=1`. One submission completes;
      the record is `verified`; `status --remote --details` shows one current
      run; the results page is byte-identical to a run with the settings off.
- [ ] **Outage, then recovery:** block R2 (wrong endpoint or network off),
      complete a submission; the record backs off (`network`); restore; it
      becomes `verified` on a new or resumed run as documented.
- [ ] **Expiry under each setting combination:** `EXPIRY_DELETE=1` deletes the
      whole prefix; `EXPIRY_DELETE=0` keeps it, the record stays
      `delete_pending` and `status` shows it overdue; turning deletion on later
      clears it.
- [ ] **Sweep:** dry run, inspect, then `--apply --confirm <digest>`; a stale
      digest aborts with exit code 2 and deletes nothing.
- [ ] **Container rollback rehearsal:** the section 10.1 scenario with the
      P1-A image and the P1-B image against `s3-coaching-dev`: P1-B publishes,
      P1-A expires a submission (no R2 request, records untouched), P1-B's
      sweep finds and deletes exactly that prefix.

## Gate 2: production key (created by the owner)

- [ ] "Object Read & Write" scoped to `s3-coaching` only, stored in the
      password manager and the production host `.env`, never on the Mac.
- [ ] On the host, a read-only scope check (LIST under `reports/`) succeeds;
      nothing is written.

## Gate 3: read-only production scan

- [ ] On the production host, inside the running image:
      `public-site-reports scan --inventory <inventory> --json` over the 143
      stored reports. No R2 request, no staging, no writes, no settings
      change. With `PUBLIC_DATABASE_URL` set (as in the running container) it
      also runs one read-only `SELECT` per analysis on
      `review_code_acquisitions` to load acquisition canaries; the values stay
      in memory and only counts are printed. Without the database,
      review-code analyses are reported as `unchecked` instead of clean.
      Whether the database read is acceptable for this gate belongs in the
      Gate 3 clarification below.
- [ ] `analyses_unchecked` is 0, or every unchecked analysis is explained
      before the gate is considered passed.
- [ ] Output is counts by rule plus the key-path inventory diff (key names,
      never values). It also gives B3 its count of historical clips with
      metadata.
- [ ] The Gate 3 execution clarification referenced in the B2 approval must be
      recorded here before this gate runs (it was not available when this
      runbook was written).

## Gate 4: deploy with all settings absent and no report credentials

### Candidate

- The candidate is the `p1b/r2-storage` commit that adds this checklist. Its
  full SHA is recorded in the release report and, at deploy, in
  `DEPLOYED_SHA`. Do not deploy a later commit without re-running
  "Local verification".
- Build off-host for `linux/amd64` with `--build-arg GIT_SHA=<full sha>` and
  label `org.opencontainers.image.revision=<full sha>`, deliver as a tarball,
  `docker load`, tag `s3-coaching-app:candidate-<sha12>` and
  `s3-coaching-app:latest`, following the tarball procedure in
  `PHASE1A_RELEASE.md` (steps 4 to 6). Record the host image ID (overlay2
  config digest), not the build Mac's index digest. Never run
  `docker compose ... --build` on the host.

### Behavior that changes with every flag off

- New moment clips carry no upload metadata (D1).
- Upload confirmation: one probe per submission, unique download paths.
- Analysis provenance records the image commit (`BUILD_COMMIT`, from
  `GIT_SHA`).
- Every payment gate has a support reference (`S3-XXXXXXXX`), shown to the
  customer and in notices; admins can look it up.
- Checkout recovery: paying again on a session Stripe already shows as paid
  records it and wakes the worker (missed-webhook case).
- History (`/api/me/submissions`) adds `display_status`; the card shows the
  11 customer-facing labels in English and Japanese.
- Not reachable with the flags off: review-code routes, the admin recording
  pages and the capture station (Mac only, `PUBLIC_RECORDING_STATION`).

### Production configuration and environment (names only)

- Unchanged: compose project `s3-coaching`, `/srv/www/s3-coaching/.env`,
  `postgres:16`, port 8770. No production setting is added or changed by
  Gate 4.
- New names read by P1-B, all **absent** at Gate 4: `PUBLIC_REPORT_PUBLISH`,
  `PUBLIC_REPORT_EXPIRY_DELETE`, `PUBLIC_REPORT_SWEEP`,
  `PUBLIC_RECORDING_STATION`, `PUBLIC_STATION_DIR`. Also absent:
  `PUBLIC_REVIEW_CODES_ENABLED`, `PUBLIC_R2_*`; `PUBLIC_STORAGE` stays
  `local`. No name was removed.
- Check before and after the restart, names only:
  `grep -oE '^[A-Z_]+=' .env` must not list any of the names above.
- Production secrets (Stripe, SMTP, database, Turnstile, voucher HMAC, LLM
  keys and any future production R2 key) live only in the host `.env` and the
  password manager. The Mac `.env` holds dev and test values only; no tracked
  file holds a secret value.

### Database (applied by startup `ensure_schema`, flags off)

| Change | P1-A after rollback |
|---|---|
| `submission_gates.public_reference`: backfill, format check, unique index, `NOT NULL`, default `s3_new_public_reference()` | Inserts omit the column; the default fills it (tested against the frozen tag) |
| `account_upload_limit.guest_uploads_per_hour`, `network_uploads_per_hour` (nullable) | Ignored |
| `vouchers.redemption_scope` `NOT NULL DEFAULT 'video_upload'` | Inserts omit it; ignored on redemption, so a review-code-only voucher would redeem for a video under P1-A |

- Review-code migrations `001` and `002` run only with
  `PUBLIC_REVIEW_CODES_ENABLED`; they do not run at Gate 4.
- No destructive change: nothing is dropped or rewritten except filling
  missing references once.

### Stripe

- Keys, webhook endpoint and signing secret, price rows and
  `pricing_version` are unchanged; Gate 4 touches none of them.
- The only new Stripe call (`Session.expire`) is used by review-code unpaid
  cancellation, which is off.

### Backup

- [ ] Fresh `pg_dump -Fc`, schema-only dump, table counts and a copy of the
      `coaching-data` volume on the operator Mac, with a restore check, as in
      `PHASE1A_RELEASE.md` steps 1 and 2.

### Smoke tests after the restart

- [ ] `/` and `/api/config` return 200; `review_codes_enabled` is false.
- [ ] Container image ID and revision label match the candidate.
- [ ] Sign in; history lists existing submissions with a `display_status`.
- [ ] One real video end to end (create, upload, pay or voucher, queue,
      complete with coaching); its support reference is shown.
- [ ] `public-site-reports status` shows all settings off; no R2 request.
- [ ] One maintenance/worker cycle with no new errors.
- [ ] Append release, image ID, rollback tag and backup folder to
      `DEPLOYED_SHA`.

### Gate 4 steps

- [ ] Before the deploy, re-verify the rollback image on the host (it must
      still match "Rollback image" above):
      `docker inspect s3-coaching-web --format '{{.Image}} {{index .Config.Labels "org.opencontainers.image.revision"}}'`
      prints `sha256:d6261c6e… 23a304c91eb5…`. Then tag it
      `s3-coaching-app:rollback-23a304c9` and confirm that tag's `{{.Id}}` is
      `d6261c6e…`.
- [ ] The candidate gives `submission_gates.public_reference` a database
      default. P1-B's startup makes the column `NOT NULL` and P1-A's insert
      omits it, so without the default a rollback breaks new submissions.
- [ ] Deploy the P1-B image with every setting in "Disabled in this
      deployment" absent.
- [ ] Behavior matches P1-A apart from D1; `status` shows all settings off;
      zero report R2 requests.
- [ ] Rollback target: `s3-coaching-app:rollback-23a304c9` (`d6261c6e…`).

## Gate 5: enable publishing and expiry deletion together

- [ ] **Blocked** until the storage timeout in "Disabled in this deployment"
      is resolved and reviewed. Do not treat the 600 s settle as sufficient.
- [ ] Add the production credentials, `PUBLIC_REPORT_PUBLISH=1` and
      `PUBLIC_REPORT_EXPIRY_DELETE=1` (never one without the other).
- [ ] One smoke-test submission becomes `verified`; the results page is
      unchanged; `status --remote` shows one current run.

## Gate 6: `PUBLIC_REPORT_SWEEP=1`

Only after B3, and blocked by the same storage timeout as Gate 5. Until then
superseded and partial runs are bounded by `expires_at` (expiry deletion
removes the whole prefix) and visible in `status --remote`.

## Gate 7: rollback

1. Remove the `PUBLIC_REPORT_*` settings, then from `/srv/www/s3-coaching`:
   `docker image tag s3-coaching-app:rollback-23a304c9 s3-coaching-app:latest`
   and `docker compose up -d --no-build --no-deps app`. Verify the container
   image is `sha256:d6261c6e…` with revision `23a304c91eb5…`. If the tag is
   missing, `docker load -i` the tarball under "Rollback image" and tag
   `d6261c6e…`. P1-A ignores `report_publications/` and `report_staging/` and
   never contacts R2 for reports.
2. Submissions that expire while P1-A runs keep their R2 objects.
3. On redeploying P1-B, recover them with either:
   - `PUBLIC_REPORT_EXPIRY_DELETE=1`: startup moves records of expired
     submissions to `delete_pending` and deletes their prefixes; or
   - `public-site-reports sweep` (dry run), review the `expired_submission`
     rows, then `sweep --apply --confirm <digest>`.

Tested by `tests/test_public_report_rollback.py` against the frozen tag.
