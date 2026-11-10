# P1-B B2 release: report publishing gates and runbook

Companion to `REPORT_PUBLISHING.md`. Nothing here authorizes a real R2 write,
a production credential, a production configuration change, a deploy, a push
or a tag. Each gate below needs its own explicit approval; stop at the first
failure.

## Baseline

- Branch `p1b/r2-storage`, from `p1a-review-code-frozen` (P1-A, frozen; never
  modified). B1: `5748a85`, `e774608`. D1 clip metadata fix: `535cff7`.
- Production image before P1-B: `d6261c6e…` (the rollback target).
- All three `PUBLIC_REPORT_*` settings default to off. With them absent the
  build behaves like P1-A apart from D1 (clean new clips) and sends zero report
  R2 requests.

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

- [ ] Deploy the P1-B image with every `PUBLIC_REPORT_*` setting absent.
- [ ] Behavior matches P1-A apart from D1; `status` shows all settings off;
      zero report R2 requests.
- [ ] Rollback target: `d6261c6e…`.

## Gate 5: enable publishing and expiry deletion together

- [ ] Add the production credentials, `PUBLIC_REPORT_PUBLISH=1` and
      `PUBLIC_REPORT_EXPIRY_DELETE=1` (never one without the other).
- [ ] One smoke-test submission becomes `verified`; the results page is
      unchanged; `status --remote` shows one current run.

## Gate 6: `PUBLIC_REPORT_SWEEP=1`

Only after B3. Until then superseded and partial runs are bounded by
`expires_at` (expiry deletion removes the whole prefix) and visible in
`status --remote`.

## Gate 7: rollback

1. Swap the image back to `d6261c6e…` and remove the `PUBLIC_REPORT_*`
   settings. P1-A ignores `report_publications/` and `report_staging/` and
   never contacts R2 for reports.
2. Submissions that expire while P1-A runs keep their R2 objects.
3. On redeploying P1-B, recover them with either:
   - `PUBLIC_REPORT_EXPIRY_DELETE=1`: startup moves records of expired
     submissions to `delete_pending` and deletes their prefixes; or
   - `public-site-reports sweep` (dry run), review the `expired_submission`
     rows, then `sweep --apply --confirm <digest>`.

Tested by `tests/test_public_report_rollback.py` against the frozen tag.
