# P1-B Gate 1 results: independent review and dev R2 staging

Date: 2026-11-10 (UTC). Branch `p1b/r2-storage`. Nothing pushed, tagged,
deployed or migrated. No production credential, bucket, endpoint or
configuration was touched. All three `PUBLIC_REPORT_*` settings stayed off.

## Reviewed commits and package

| Item | Value |
|---|---|
| Baseline | `c91ef54` |
| R1–R4 corrections | `6c5442c` (code), `666208c` (docs) |
| R1 final correction | `d0264c5` (review-code submission without an acquisition row is unchecked) |
| Package | `p1b-b2-corrections-review-666208c.zip` |
| SHA-256 (expected = actual) | `2298b935727dd682abbac037abbf9c4100e8bc12c22c39364d06aac0e687fee5` |
| Package diff vs repository | byte-identical to `git diff c91ef54..666208c` |

The review covered `c91ef54..d0264c5`: the implementation and the tests, not
only the README. It was done by a separate reviewer agent with a fresh
context, and then the main agent verified the material claims. Both are AI
agents, and the main agent wrote the code; this is not a human review.

## Independent review findings

### PASS

- **R1, privacy parity.**
  - The publisher and `scan` both call
    `report_known.submission_known_values`.
  - It covers all six acquisition text fields, plus the
    `submission_snapshot` email, display name, user ID and token hash.
  - Values are also matched as JSON-escaped bytes, and against every decoded
    JSON key and string value, case-folded, with a whitespace- and
    hyphen-stripped form.
  - Unchecked groups make the publisher fail closed (`network`, backoff, no
    PUT). `scan` counts them as unchecked and never as clean.
  - A review-code submission is unchecked when there is no lookup, the
    lookup raises, or there is no row (including a missing table). Uploads
    stay checkable.
- **R2, write barrier.**
  - `write_barrier` is a non-blocking `flock` per submission
    (`O_NOFOLLOW|O_CLOEXEC`).
  - A publish attempt holds it for the whole attempt, including the bundle
    PUT in `_commit`. Expiry deletion and sweep `_apply_row` also hold it.
  - When it is busy, publishing and deletion defer and the sweep row is
    `skipped_on_recheck`.
  - `_ensure_current` runs before every upload.
  - The lock order is always barrier, then record lock, and the barrier
    never waits, so there is no deadlock.
  - The kernel releases the lock on a crash; the test SIGKILLs a separate
    process to show it.
  - The publisher is the only caller of `put_file`. The writer has no
    delete, and the eraser has no put and no read (confirmed again at
    runtime, below).
- **R3, unexpected exceptions.**
  - Preparation, upload and commit exceptions map to `local_missing` or
    `local_changed`, then back off with a new run. Deletion exceptions map to
    `network`.
  - `_loop` pauses `ERROR_PAUSE_SECONDS` on `_stop`, so a wake-up cannot
    shorten it.
  - Logs carry exception type names only.
- **R4, JPEG.**
  - The parser walks every marker from SOI to EOI, across scans. It skips
    `FF00` stuffing, RST markers and fill bytes.
  - APP1, APP13 and any comment other than the Lavc encoder string are
    rejected anywhere in the file.
  - It also rejects:
    - truncated files and segment lengths under 2;
    - a second SOI;
    - bytes after EOI.
  - Every loop iteration advances, so the parser cannot loop forever.
- **Focused tests** (run by the reviewer): 65 passed, 1 skipped (the
  database-only test).

### BLOCKER

None for Gate 1, which used synthetic data on the dev bucket only.

### FOLLOW-UP (non-blocking for Gate 1; items 1–3 must be decided before Gate 5)

1. **The review code is never a usable canary in practice.** P1-A sets
   `review_code = NULL` at recording handoff (`recording_store.py`,
   `confirm_recording_handoff`), and handoff happens before analysis and
   publishing. At publish time the row exists without the code, so it is
   "checked" without it.
   - The `review_code` JSON key is still forbidden, and every other field
     is still checked.
   - The tests use a row that still has the code, which is not the real
     post-handoff state.
   - Decide between keeping a salted hash through publishing and accepting
     the gap by design (documented). Marking these rows unchecked would
     block every review-code report.
2. **Other JPEG APP segments are allowed.** APP2 (ICC/MPF), APP3, APP11
   (JUMBF/C2PA), APP12 and APP14 pass and can carry text. Consider an
   allowlist: APP0, the image structure segments and the Lavc comment.
3. **No Unicode NFKC normalization.** Full-width and half-width variants of
   Japanese names escape case-folding.
4. **Player names under 4 characters are not canaries** (`_MIN_PERSONAL`).
   A non-string snapshot `user_id` is skipped.
5. **A PUT that R2 completes after the client abandons it** (timeout or
   crash) can land after deletion has been declared complete. Only an
   enabled sweep's `expired_submission` row would remove it. Consider
   re-listing once after a delay, or keeping a tombstone.
6. **A persistent error opening the barrier** (for example `ELOOP` on a
   symlinked lock file) escapes to `_loop` without per-record backoff. The
   most overdue record is then retried every 60 s and starves the others.
7. **`startup()` runs outside the `_loop` try.** An exception there kills
   the publisher thread, and Python's default thread hook prints a full
   traceback.
8. **`_put_bundle` makes its network request while holding the record
   lock**, which contradicts the `lock()` docstring ("never during
   uploads").

## Development environment (no secrets)

The values come from the existing main-tree `.env`, loaded in-process with
`python-dotenv` into the app's `PublicSettings.from_env()`. Only booleans,
lengths, counts and digests were printed. Nothing was displayed or changed.

| Property | Observation |
|---|---|
| Bucket | `s3-coaching-dev` |
| Endpoint | `https://<32-hex account id>.r2.cloudflarestorage.com` (R2 default from the account ID; no endpoint override), region `auto` |
| Access key ID / secret | present; lengths 32 / 64 (values not shown) |

## Gate 1 preflight

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Dev endpoint and bucket configuration | PASS | `r2_ready`; https; host matches `<32hex>.r2.cloudflarestorage.com`; no `PUBLIC_R2_ENDPOINT` override |
| 2 | Bucket is `s3-coaching-dev` | PASS | equality true; equality with `s3-coaching` false |
| 3 | Credentials are dev-only | PASS (indirect) | Account-level ListBuckets returned **403**, so the key is not account-wide or admin. Whether its bucket scope also includes `s3-coaching` cannot be checked without a production request, which is forbidden. No document records the dev key's scope. **The owner should confirm the token scope in the Cloudflare dashboard.** |
| 4 | Required dev-bucket permissions | PASS | LIST, PUT, HEAD, GET and DELETE all succeeded on `s3-coaching-dev` (Phase 3) |
| 5 | Client endpoint and region | PASS | `R2Storage` builds the host from the account ID; SigV4 with region `auto`, service `s3` |
| 6 | Three settings disabled | PASS | `report_publish`, `report_expiry_delete` and `report_sweep` are false, `reports_enabled` is false, and none of the variables is set |
| 7 | Test prefix isolated | PASS | Test prefix `reports/6a7e1de0000000000000000000000000/` (`6a7e1de0` reads as "gate1 dev"); it held 0 objects before. Every allowed top-level prefix (`submissions/`, `processing/`, `reports/`, `exports/`) held 0 objects before the test. |
| 8 | No production endpoint or credentials used | PASS | The bucket is `s3-coaching-dev` and the client is bound to it; the only account-level call was the ListBuckets probe (403). The production key is never on the Mac (standing rule). |

## Staging operations (dev bucket only, synthetic data)

Script: `/tmp/gate1_staging.py`, not committed. It uses the existing
`StagingArea` (owner-only staging under a throwaway
`/private/tmp/gate1_public_root`), `ReportWriter`, `ReportLister` and
`ReportEraser` over the configured `R2Storage`. The payload was 109 bytes of
synthetic JSON (`gate1_synthetic`, the run ID and a random nonce).

| Step | Result |
|---|---|
| Staging run | `20261110T055738Z-8e5c3153`; staged file mode `0600` |
| Key | `reports/6a7e1de0000000000000000000000000/20261110T055738Z-8e5c3153/gate1_synthetic.json` |
| Before | prefix LIST empty; HEAD 404 |
| PUT (writer, signed payload SHA-256, `HashingReader`) | 109 bytes; SHA-256 `159b8ea6…0bcc` matches |
| HEAD | 109 bytes; `x-amz-meta-sha256` matches |
| Streamed read-back (`read_sha256`) | 109 bytes; SHA-256 matches |
| Independent GET (raw bytes) | 200; byte-identical; `Content-Type: application/json` |
| LIST (lister) | 1 object; key and size match; `LastModified` `2026-11-10T05:57:39.249Z`, within 10 minutes of the start |
| DELETE (eraser, this key only) | done |
| After | HEAD 404; GET 404; prefix LIST 0; local staging removed |
| Unrelated objects | full listing of all allowed prefixes identical before and after (0 objects; snapshot SHA-256 `6a3107ef…056c`) |
| Capability separation (runtime) | writer has no `delete`; eraser has no `put_file` and no `read_sha256`; lister exposes only `list_objects` |

### Earlier attempts (all accounted for)

1. `StagingUnsafe` at `/tmp/...`: macOS `/tmp` is a symlink, and staging
   rejects symlinked components. No R2 request was made.
2. `StagingUnsafe` again: my script passed the suffix `".json"`, but the
   API takes `"json"`. No R2 request was made.
3. The PUT succeeded, then my evidence code used the wrong attribute name
   (`size` instead of `size_bytes`). The script's `finally` block deleted
   that key with the eraser. Afterwards the test prefix held 0 objects, and
   the bucket listing was identical to the before snapshot.

Total test objects created: 2. Deleted: 2. Remaining: 0.

## Remaining issues

- Follow-ups 1–8 above. Items 1–3 are pre-production decisions.
- Preflight item 3 is indirect: the owner should confirm in the dashboard
  that the dev token is scoped to `s3-coaching-dev` only.
- The runbook's broader Gate 1 checklist is **not** covered by this
  infrastructure staging test. Those items require enabling the report
  settings against the dev bucket and were not in scope:
  - the payload-hash probe;
  - end-to-end publishing with `PUBLISH=1` and `EXPIRY_DELETE=1`;
  - outage and recovery;
  - expiry under each setting combination;
  - sweep apply;
  - the container rollback rehearsal.

## Decision

- The independent review found no blocker.
- Infrastructure preflight passed, with item 3 indirect.
- Real dev R2 staging succeeded end to end, and every test object is
  accounted for.

**Gate 1 infrastructure staging: PASSED.** The runbook's end-to-end Gate 1
items remain open. Stopped for approval; Gate 2, Gate 3, deployment,
enabling publishing, backfill and migration were not started.

---

# Gate 1 end-to-end: isolated stack (Batch B)

Date: 2026-11-10 (UTC), 07:19–07:27. Approved by the owner after Batch A,
with the dev token scope confirmed by the owner (preflight item 3 is now
**PASS**). Nothing pushed, tagged, deployed or migrated. No production
resource, no Stripe key, no B5. Only `PUBLIC_REPORT_PUBLISH=1` was set, and
only inside the throwaway containers; expiry deletion and sweep stayed off.

## Stack (B1)

| Item | Value |
|---|---|
| Compose project | `s3gate1`, files in `/private/tmp/s3gate1/` (0700), own network (no `edge`), port `127.0.0.1:8771` |
| P1-B image | `s3gate1-p1b:0e3188239287`, built from `git archive` of `0e31882392876a724bb7fa0c1dc9563cabf75bf5`; label matches; arm64; no `.env` in the image |
| P1-A image | `s3-coaching-candidate:23a304c91eb5-arm64` (label `23a304c91eb5989277c103c8376466c927880b63`) |
| Database / data | `postgres:16` on volume `s3gate1-db`; data volume `s3gate1-data` (`PUBLIC_ROOT=/data/public`) |
| Mail | `axllent/mailpit` v1.33.1, self-signed STARTTLS (the app always calls `starttls()`) |
| Env file | `gate1.env`, 0600, written by a script that copied only the four `PUBLIC_R2_*` names and `S3_COACH_CURSOR_API_KEY` from the main `.env` without displaying them; it refused any bucket other than `s3-coaching-dev`. Synthetic values: dev mode, `PUBLIC_STORAGE=r2`, throwaway DB URL, mailpit SMTP, `PUBLIC_LLM_PROVIDER=cursor`, `PUBLISH=1`, `EXPIRY_DELETE=0`, `SWEEP=0`. Deleted at the end. |

The Cursor key was added on the owner's instruction (GPT-5.6 Luna for LLM
needs); the plan otherwise excluded LLM secrets.

## Preflight

With exactly the container's R2 values: bucket `s3-coaching-dev` (not
`s3-coaching`), https, host `<32hex>.r2.cloudflarestorage.com`. All four
allowed prefixes (`submissions/`, `processing/`, `reports/`, `exports/`)
held **0 objects** before the first upload (snapshot SHA-256 `d2db42a7…`).

## End-to-end run (B2)

1. Synthetic admin `gate1-admin@example.test`: register (Turnstile dev
   token), verify link received through mailpit, `s3-coach public-admin
   grant` (exit 0, user → admin), password login; `/api/me` role `admin`,
   `/api/admin/overview` 200.
2. Upload through the site API as that admin (no payment):
   `en-live-playerview-2026-10-12 19-59-26.mov` (owner-approved),
   127,669,020 bytes, SHA-256 `5c4dbac92702d098…`, sent as
   `gate1-sample.mov`. Presigned PUT to dev R2: 200 in 4.6 s.
3. Pipeline: analyze exit 0 (about 55 s), coach-inputs and coach-prototype
   exit 0, frames and clips, `complete` / `coached` at about 07:23:43 (about
   2 min after upload).
4. Publication: run `20261110T072336Z-d6066ff9`, 19 objects
   (18 files + `bundle.json`, written last), 39,119,606 bytes, about 18 s;
   record `verified`. `status --remote --details`: one current run, no
   superseded, partial or other prefixes.

### Read-back

Every published object was streamed back from R2 inside the container:

- All 18 files: GET size and SHA-256 equal the bundle's `published_*`;
  HEAD `x-amz-meta-sha256` matches; the local file equals the bundle's
  `local_*`; the 10 untransformed files are byte-identical to R2.
- `bundle.json` (6,865 bytes, `09bff0bd3b0e…`) listed size matches;
  `s3coach.report_bundle.v1`; `missing_media` empty.
- The R2 listing under the submission equals the bundle's file set exactly.

### Website retrieval

- curl: `/review/<token>` 200, `/api/submissions/<token>` 200
  (`complete`, `coached`), both moments' still and clip 200. The served
  stills and clips have the same SHA-256 as the R2 objects.
- Browser: the review page renders "Review complete", both death moments
  with match evidence, and "available until November 10, 2027".

### Objects created by this test (dev bucket)

| Key | Bytes | SHA-256 (12) | Now |
|---|---|---|---|
| `submissions/f598ff796bae4bbb874e878aed228535/source.mov` | 127,669,020 | (= local video) | deleted by P1-A expiry (rehearsal step 3) |
| `reports/f598ff796bae4bbb874e878aed228535/20261110T072336Z-d6066ff9/bundle.json` | 6,865 | `09bff0bd3b0e` | present |
| `…/coach_inputs/coaching_index.json` | 1,610 | `9ddb6aaead84` | present |
| `…/coach_inputs/death_episode_37.500.coach_input.json` | 432,736 | `6aa9d038bc2d` | present |
| `…/coach_inputs/death_episode_37.500.coaching.json` | 5,310 | `65abab67a362` | present |
| `…/coach_inputs/death_episode_37.500.llm_view.json` | 27,738 | `7f10772b1c4a` | present |
| `…/coach_inputs/death_episode_59.000.coach_input.json` | 419,265 | `036247675b58` | present |
| `…/coach_inputs/death_episode_59.000.coaching.json` | 5,196 | `a683c76ba84d` | present |
| `…/coach_inputs/death_episode_59.000.llm_view.json` | 28,574 | `8a70bfb05e68` | present |
| `…/coach_prototype/coaching_index.json` | 2,171 | `c72996ea65e4` | present |
| `…/coach_prototype/death_episode_37.500.gpt-5.6-luna.flags.json` | 1,716 | `f9ef22edde3e` | present |
| `…/coach_prototype/death_episode_37.500.gpt-5.6-luna.output.json` | 323 | `1ac6fe064cfa` | present |
| `…/coach_prototype/death_episode_59.000.gpt-5.6-luna.flags.json` | 1,716 | `f9ef22edde3e` | present |
| `…/coach_prototype/death_episode_59.000.gpt-5.6-luna.output.json` | 323 | `1ac6fe064cfa` | present |
| `…/match_identity.json` | 1,567 | `c18482e6b373` | present |
| `…/public_clips/death_episode_37.500.mp4` | 18,092,769 | `156f9782db6f` | present |
| `…/public_clips/death_episode_59.000.mp4` | 16,940,198 | `cb96ad83c4d5` | present |
| `…/public_frames/death_episode_37.500.jpg` | 90,369 | `a188ed7957ed` | present |
| `…/public_frames/death_episode_59.000.jpg` | 80,109 | `deaf099c6a4d` | present |
| `…/vision_manifest.json` | 2,981,051 | `895a7653a04b` | present |

`…` is `reports/f598ff796bae4bbb874e878aed228535/20261110T072336Z-d6066ff9`.
Created: 20. Deleted: 1 (the source, by P1-A's normal expiry). Remaining:
19, all under `reports/f598ff796bae4bbb874e878aed228535/`, left in place
pending the separately authorized deletion scenarios. No other object in
any allowed prefix changed at any point (four snapshots compared).

## Rollback rehearsal, non-destructive (B3)

1. P1-B stopped; P1-A started on the same volume and database. Startup
   clean.
2. P1-A served the review: page, API, both stills and clips 200,
   byte-identical to P1-B's responses.
3. `expires_at` moved 5 minutes into the past with the image's own
   `PublicStore.update` (record lock). P1-A `maintain` expired the
   submission within about 10 s: status `expired`, local analysis removed,
   source object deleted from `submissions/`. The page now reports the
   review as expired; media 404.
4. P1-A left `report_publications/`, `report_staging/` (and the absent
   `report_canary/`) untouched: fingerprint `f478719b4e80abe2…` identical
   before and after. The `reports/` listing was unchanged (19 objects).
5. P1-B restarted: startup moved the record to `delete_pending` (expiry
   deletion off, so nothing was deleted; the expected warning was logged).
   `status --remote --details`: `expired_holding` 1 run, 19 objects,
   39,119,606 bytes. `sweep` dry run: exactly one row,
   `reports/f598ff796bae4bbb874e878aed228535/`, reason
   `expired_submission`, 19 objects, digest `144df859…`, below the
   threshold, no skips. `canary-status`: all zero, directory safe, exit 0.

Every evidence file and both container logs were checked against the R2
values, the Cursor key, the database URL, the admin password and the review
token: none appears.

## Findings

1. **Probe download race (P1-B regression; fixed in `d8084c9`, see
   `PHASE1B_B2_RELEASE.md`; end-to-end retest passed, see "Gate 1 retest
   (d8084c9)" below).** `POST
   /api/submissions/<token>/uploaded` returned 500. The periodic
   `maintain()` → `_recover_ready()` and the request's `confirm()` both ran
   `attempt_queue` → `_probe_facts` for the same received submission. Both
   streamed the source to the same `probe/.<id>.mov.part` (P1-B B1
   `R2Storage.download` uses a fixed partial name); one thread's rename
   made the other's `stat` fail with `FileNotFoundError`. The other probe
   queued the job once, so the run continued, but the browser would show
   an upload error. P1-A wrote the whole body in one call, so its window
   was smaller, though the duplicate probe also exists there. A fix (a
   unique partial name per call, and not probing the same submission twice
   concurrently) needs its own authorization.
2. **`bundle.json` `producer.git_sha` is `null`** in the image, although the
   image is labelled with the full SHA. The build arg is not exported to the
   runtime environment. (Fixed in `55bb9f7`, see "Follow-ups after the
   retest" below; not yet re-run end to end.)
3. **The LLM was not called.** Both death episodes scored 0.0 and were not
   selected for the LLM, so the outcome was `coached` with "no
   recommendations". The Cursor key path was configured but not exercised.
4. **Evidence text language.** With the site switched to English, the
   moment evidence lines still render in Japanese (pre-existing
   presentation behavior, not P1-B).
5. P1-A's container log was not kept (container removed during teardown);
   its startup lines were clean and it logged no warnings or errors while
   running.

## Left in place

- Dev R2: the 19 `reports/` objects above.
- Docker volumes `s3gate1-data` and `s3gate1-db` (containers and network
  removed).
- `/private/tmp/s3gate1/`: scripts, evidence, `db.env` (synthetic DB
  password) and `state.json` (synthetic admin password and review token,
  0600). `gate1.env` deleted; re-create it with `prepare_env.py` (reusing
  the `db.env` password) for any later scenario.

## Decision

Gate 1 end-to-end publication, read-back, website retrieval and the
non-destructive rollback rehearsal **passed**, with finding 1 open. Outage,
expiry deletion (eraser and the 600 s settle), sweep apply, the
payload-hash probe and the final cleanup remain separately authorized
scenarios. Stopped for approval.

---

# Gate 1 retest (d8084c9)

Date: 2026-11-10 (UTC), 07:42–07:50. A narrowly scoped retest of the upload
confirmation fix, authorized by the owner. Nothing pushed, tagged,
deployed, migrated or committed. No production resource, no Stripe key, no
LLM. Only `PUBLIC_REPORT_PUBLISH=1` was set, and only inside the throwaway
containers; expiry deletion and sweep stayed off, and no expiry, deletion
or sweep apply was run.

## Stack

| Item | Value |
|---|---|
| Compose project | `s3gate1r`, files in `/private/tmp/s3gate1r/` (0700), own network, port `127.0.0.1:8771`; P1-B only (no P1-A service) |
| P1-B image | `s3gate1-p1b:d8084c95519d`, built from `git archive` of `d8084c95519de439a29c98c46ebd969335ab39e9`; label matches; arm64; no `.env`; contains `keyed_locks.py` and the `mkstemp` download |
| Database / data | new `postgres:16` on volume `s3gate1r-db`; new data volume `s3gate1r-data` (the Batch B volumes were not used) |
| Mail | `axllent/mailpit`, self-signed STARTTLS, API on `127.0.0.1:8026` |
| Env file | `gate1.env`, 0600, written by the same script; it copied only the four `PUBLIC_R2_*` names (no Cursor key this time) without displaying them, and refused any bucket other than `s3-coaching-dev`. Synthetic values as in Batch B. Deleted at the end. |

The container had no `S3_COACH_*` variables, so no LLM call was possible.
Startup was clean apart from the expected warning that publishing is on
without expiry deletion.

## Before the upload

- Read-only R2 snapshot: identical to the Batch B final snapshot
  (`reports/` 19 objects, other allowed prefixes 0).
- Synthetic admin `gate1-retest-admin@example.test`: register 200, verify
  link through mailpit 200, `public-admin grant` exit 0, login;
  `/api/me` role `admin`, `/api/admin/overview` 200.
- `public-site-reports status`: no records; publish on, expiry deletion
  off, sweep off.

## Run

1. Upload through the site API as that admin (no payment):
   `en-2026-10-17 22-41-01.mov`, 200,224,580 bytes, SHA-256
   `7dba5eee3cb89d54…`, sent as `gate1-retest-sample.mov`. Presigned PUT to
   dev R2: 200 in 7.0 s.
2. **Upload confirmation: `POST /api/submissions/<token>/uploaded` returned
   200 JSON `{"status": "queued", "step": "queued", "error": null}`** in
   7.1 s (the probe streamed the 200 MB source back from R2).
3. **Queued exactly once:**
   - `job_runs`: one row, attempt 1.
   - `job_events`: `submitted`, `upload_confirmed`, `validation_started`,
     `queued`, `validation_finished` and `completed` once each, and each
     stage (`analyze`, `coach_inputs`, `coach_prototype`, `frames`) started
     and finished once.
   - Container log: one `stage analyze`, no traceback, no rejected telemetry
     transition.
   - `/data/public/probe/` empty afterwards (no leftover partial file).
4. Processing: analyze exit 0 (about 70 s; English detected, Undertow
   Spillway, Splat Zones), coach-inputs exit 0, coach-prototype exit 1 (no
   Cursor key, as intended), then `complete` / `coached` at 07:45:01
   (`job_runs` 2 min 23 s from confirmation to finish).

This run shows correct end-to-end behavior with the fix. It does not prove
that the maintenance loop actually overlapped the confirmation this time
(nothing logs that); the interleaving itself is covered deterministically
by `tests/test_public_probe_race.py`.

## Publication and read-back

- Run `20261110T074452Z-fcf06708` for submission
  `d123347227b44f26bdc34ec659b7728e`: 14 objects (13 files +
  `bundle.json`), 43,218,892 bytes, written 07:44:54–07:45:07; record
  `verified`. There are no `coach_prototype/` files because that stage
  failed without the LLM key.
- `status --remote --details`: this submission has one current run, with no
  superseded or partial runs. The Batch B run shows as `superseded` for
  `f598ff79…` only because this new database has no record of it. This is
  classification only: sweep is off, and nothing was deleted.
- Read-back inside the container: all 13 files GET size and SHA-256 equal
  the bundle's `published_*`; HEAD `x-amz-meta-sha256` matches; local equals
  `local_*`; the untransformed files are byte-identical to R2. `bundle.json`
  5,039 bytes, `1e33e107340f…`, listed size matches,
  `s3coach.report_bundle.v1`, `missing_media` empty. Listing equals the
  bundle's file set.

## Website retrieval

- curl: `/review/<token>` 200, `/api/submissions/<token>` 200 (`complete`,
  `coached`, step `ready`), both moments' still and clip 200; the served
  bytes have the same SHA-256 as the R2 objects (`a1322b94e460`,
  `4649592fa59e`, `96b225f2751a`, `f87c89c5af09`).
- Browser (opened through a temporary local redirect so the token was never
  typed; server stopped and its directory deleted): the page renders the
  progress steps through "Ready", "Your match", and both death moments (4:34
  and 3:19 remaining) with match evidence in English. The still loads
  (1280 px). Both clips use `preload="none"`; after a load request each
  reached `readyState` 4, 18 s, 1280 px, no error.

## Objects in the dev bucket

The 19 Batch B objects are untouched: same keys, sizes and last-modified
times before and after (0 removed, 0 changed, 19 unchanged). New objects
created by the retest:

| Key | Bytes | SHA-256 (12) |
|---|---|---|
| `submissions/d123347227b44f26bdc34ec659b7728e/source.mov` | 200,224,580 | (= local video) |
| `…/bundle.json` | 5,039 | `1e33e107340f` |
| `…/coach_inputs/coaching_index.json` | 1,623 | `85a2ef1a3506` |
| `…/coach_inputs/death_episode_121.500.coach_input.json` | 456,247 | `a33880e54f98` |
| `…/coach_inputs/death_episode_121.500.coaching.json` | 5,454 | `85f6035b7cba` |
| `…/coach_inputs/death_episode_121.500.llm_view.json` | 19,107 | `35c8f8e00760` |
| `…/coach_inputs/death_episode_46.000.coach_input.json` | 410,720 | `043f734d0fda` |
| `…/coach_inputs/death_episode_46.000.coaching.json` | 5,202 | `edbe1a87fdcc` |
| `…/coach_inputs/death_episode_46.000.llm_view.json` | 17,493 | `b339c6d68f35` |
| `…/match_identity.json` | 1,693 | `658d60ea1657` |
| `…/public_clips/death_episode_121.500.mp4` | 19,256,947 | `f87c89c5af09` |
| `…/public_clips/death_episode_46.000.mp4` | 18,698,481 | `4649592fa59e` |
| `…/public_frames/death_episode_121.500.jpg` | 106,806 | `96b225f2751a` |
| `…/public_frames/death_episode_46.000.jpg` | 81,857 | `a1322b94e460` |
| `…/vision_manifest.json` | 4,152,223 | `6159549abcd8` |

`…` is `reports/d123347227b44f26bdc34ec659b7728e/20261110T074452Z-fcf06708`.
Created: 15 (the source stays in R2 because dev mode skips the release).
Deleted: 0. The bucket now holds 34 objects: 19 from Batch B and these 15.
`processing/` and `exports/` stayed empty. After-snapshot SHA-256
`6c913aaa…`.

## Findings

- Finding 1 (probe race): **not reproduced.** Confirmation returned 200
  JSON, and the submission was queued and run exactly once.
- Finding 2 (`producer.git_sha` null): **still present** in this build.
- Finding 3 (LLM): not tested, by instruction.
- Finding 4 (evidence language): with this English recording and the
  English UI, the evidence lines rendered in English. The cause of the
  Batch B observation was not investigated.

Every evidence file (20) and all three container logs were checked against
the R2 values, the database URL and password, the admin password and the
review token: none appears.

## Left in place

- Dev R2: the 15 new objects above, in addition to the 19 from Batch B.
- Docker volumes `s3gate1r-data` and `s3gate1r-db` (containers and network
  removed).
- `/private/tmp/s3gate1r/`: scripts, evidence, logs, `db.env` (synthetic DB
  password) and `state.json` (synthetic admin password and review token,
  0600). `gate1.env` deleted.

## Decision

The upload confirmation fix passed the end-to-end retest: confirmation,
single queueing, processing, publication with SHA-256 read-back, and browser
and API delivery. Expiry deletion, sweep apply, the outage scenario and
cleanup of the 34 dev objects remain separately authorized. Stopped for
approval.

## Follow-ups after the retest

No R2 access and no stack was started for these.

### Git SHA propagation (finding 2): fixed in `55bb9f7`

- Cause: `vision/provenance.py` resolved the commit with `git rev-parse
  --short HEAD`. The image has neither `git` nor `.git`, so the lookup
  failed silently and `AnalysisIdentity.git_sha` was `null`. The bundle's
  `producer.git_sha` (and the job trace provenance) copy that field.
  `GIT_SHA` only reached the image label.
- Fix: after checking `GIT_SHA`, the Dockerfile writes it to
  `src/splatoon3_ai_coach/BUILD_COMMIT` (gitignored). The provenance code
  reads that file first (full 40-hex SHA only; anything else is ignored with
  a warning), then falls back to git. No environment variable is read in
  `vision/`. The commit stays run provenance only; it is not part of
  `analysis_id` or result IDs.
- Checks: 7 new test cases in `tests/test_vision_ids.py`; public, admin,
  architecture and vision ID suites 714 passed; ruff clean. Image
  `s3gate1-p1b:55bb9f7cbb4a` (no `git`, no `.git`) returns
  `55bb9f7cbb4abb063f1fd3176f6cc82dab6450d2`, equal to its label; the
  `d8084c9` image returns `None`.

### "superseded" label in `status --remote`: fixed in `af3d9f4`

- `remote_status` labels every committed run that is not the publication
  record's current run as `superseded`, including runs whose submission has
  no local projection or no publication record.
- The sweep planner never deletes those: it skips them as
  `projection_absent` or `complete_without_record`. A reproduction on the
  test fake S3 showed the same objects labelled `superseded` by status and
  skipped by the sweep.
- In the retest, the Batch B run was in exactly this case (new database
  and data volume, so no projection and no record). The label was
  misleading. The objects were never at risk, since the sweep was off and
  would have skipped them anyway.
- Only the status output is affected; nothing acts on the label.
- Fix (owner-approved): a new `unrecorded` row, both in the table and in
  each submission's details. It counts runs whose submission has no local
  projection or no publication record, which are exactly the cases the sweep
  skips. `superseded` and `partial` now require a record. The sweep is
  unchanged.
- Checks: a new regression test asserts that status and sweep agree on the
  same objects. It and the updated totals test fail on the old code. Report
  suites 274 passed; public, admin, architecture and vision ID suites 715
  passed; ruff clean.
- With this build, the 19 Batch B objects seen from a stack that has no
  record of them would show as `unrecorded`, not `superseded`.

## Next, per the owner

| Step | Status |
|---|---|
| Outage and recovery | **passed**, see below |
| Expiry deletion and the 600 s settle | **passed**, see below |
| Sweep apply | **passed**, see below |
| Payload-hash probe | **done**: R2 rejects a mismatched payload |
| Final cleanup of the 34 dev objects | **done**: all recorded keys verified absent |

---

# Gate 1 infrastructure scenarios (af3d9f4)

Date: 2026-11-10 (UTC), 08:03–08:46. Authorized by the owner after the
retest and the two follow-up fixes. Nothing pushed, tagged, deployed,
migrated or committed. No production resource, no Stripe key, no LLM. The
report settings were set only inside the throwaway containers. Every R2
listing used the dev bucket (`s3-coaching-dev`, checked before each
snapshot). Scripts and evidence are in `/private/tmp/s3gate1x/` (0700).

## Image and stacks

- Image `s3gate1-p1b:af3d9f4d05f8`, built from `git archive` of
  `af3d9f4d05f87852522133e3f80d761917385d99`; inside it the provenance
  lookup returns that full SHA.
- Env files were written by the same kind of script as before: the four
  `PUBLIC_R2_*` names copied without display, synthetic values otherwise,
  existing `db.env` passwords reused. All were deleted at the end.

| Stack | Data | Port | Settings | Used for |
|---|---|---|---|---|
| `s3gate1o` (new) | fresh volumes `s3gate1o-*`, `PUBLIC_STORAGE=local` | 8772 | publish 1, expiry delete 1, sweep 0 | outage, expiry deletion on |
| `s3gate1r` (retest) | existing volumes, `PUBLIC_STORAGE=r2` | 8771 | publish 1, expiry delete 0, then 1 | expiry with deletion off, then on |
| `s3gate1` (Batch B) | existing volumes, `PUBLIC_STORAGE=r2` | 8773 | publish 1, expiry delete 0, sweep 0 | sweep apply |

Baseline snapshot: 34 objects (`reports/` 33, `submissions/` 1), identical
to the retest's final snapshot (`6c913aaa…`).

## Outage and recovery

The outage stack's P1-B container sat on two networks: an internal one
(database, mail; no route out) and an egress one.

1. Synthetic admin `gate1-outage-admin@example.test` (register, verify via
   mailpit, grant, login). The same English recording as the retest was
   uploaded through the local dev upload grant. `/uploaded` returned 200
   JSON `queued`.
2. 08:05:54: the egress network was disconnected. Inside the container, DNS
   for R2 failed (`gaierror`) and the database stayed reachable.
3. The pipeline completed offline (analyze exit 0, `complete` /
   `coached`). Publishing then failed with `network`:
   - attempt 1 at 08:07:52, next attempt +1 min;
   - attempt 2 at 08:08:52, next attempt +5 min (08:13:52).

   Both attempts kept the same `attempt_run_id`. Each logged one type-only
   WARNING. No object was written.
4. 08:09:04: egress was reconnected (DNS works, the host port answers).
   The scheduled attempt published run `20261110T080751Z-3854b10e` at
   08:14:09: 14 objects, record `verified`, the same run ID resumed.
5. Read-back: 13 files plus `bundle.json` all match (GET size and SHA-256,
   HEAD metadata, local files, listing equals bundle). **`producer.git_sha`
   is `af3d9f4d05f87852522133e3f80d761917385d99`**, so finding 2 is fixed
   end to end.
6. Delivery: page, API (`complete` / `coached`), both stills and clips 200,
   with SHA-256 matching the R2 objects.
7. R2: exactly 14 objects were added under
   `reports/52fa4a14fd794aa5b3f1b4318511ba19/20261110T080751Z-3854b10e/`, with no
   partial objects from the outage window. The other 34 were unchanged.
8. `status --remote --details` listed the two submissions this stack has
   no record of (`d123…`, `f598…`, 33 objects) as **`unrecorded`**,
   confirming the `af3d9f4` label fix on real data.

## Expiry deletion, deletion on (`s3gate1o`)

- 08:31:37: `expires_at` was moved 5 minutes into the past. `maintain`
  expired the submission, and the record became `delete_pending`.
- 08:31:39: the eraser deleted the prefix, and `delete_empty_at` was set.
  A snapshot showed exactly the 14 keys under `reports/52fa…/` removed.
  The bucket listing was back to the baseline (`6c913aaa…`).
- The record stayed `delete_pending` at every 30 s poll until 08:41:22. It
  was removed by the re-list at 08:41:42 ("report prefix deleted"), at
  least 600 s after `delete_empty_at`. The canary directory does not
  exist (no review-code submission).

## Expiry with deletion off, then on (`s3gate1r`, submission `d123…`)

- The retest stack restarted on the new image with expiry delete 0. The
  expected startup WARNING was logged.
- 08:32:11: expired. The record became `delete_pending` with no
  `delete_empty_at`, and no deletion was attempted (checked again 75 s
  later). `status`: `delete_pending overdue: 1`, `expired_holding` 1 run,
  14 objects.
- Normal expiry deleted the R2 source
  `submissions/d123347227b44f26bdc34ec659b7728e/source.mov`, independent of
  the report settings. The 14 report objects were unchanged.
- 08:33:46: the container was recreated with expiry delete 1. Startup
  deleted the prefix (`delete_empty_at` 08:33:49). The record stayed until
  the re-list at 08:43:52 ("report prefix deleted"), at least 600 s later.

## Sweep apply (`s3gate1`, submission `f598…`)

- The Batch B stack restarted on the new image with expiry delete 0. The
  `f598…` record had been `delete_pending` since the rollback rehearsal
  (07:25:47).
- Dry run: exactly one row, `reports/f598ff796bae4bbb874e878aed228535/`,
  `expired_submission`, 19 objects, 39,119,606 bytes. Digest `144df859…`,
  the same as the rehearsal's dry run. Total prefixes 1, limit 5.
- `--apply --confirm` with a stale digest (64 zeros): **exit 2**,
  `digest_mismatch`. A snapshot showed all 19 `f598…` objects unchanged.
- `--apply --confirm 144df859…`: exit 0, `deleted` =
  [`reports/f598…/`], none failed or skipped. The record was removed (sweep
  apply needs one empty LIST). A snapshot showed exactly those 19 keys
  removed. This completes the rollback rehearsal's deletion step.

## Payload-hash probe

Under the isolated test prefix
`reports/6a7e1de0000000000000000000000000/20261110T083443Z-6a7e1de0/`:

- Control: a correctly signed PUT of 6,912 synthetic bytes
  (`2fc1b66927c8…`) was stored. Read-back SHA-256 and HEAD
  `x-amz-meta-sha256` both matched.
- Mismatch: 6,912 different synthetic bytes (`b72d41b8d4da…`) sent with
  the control's digest signed as `x-amz-content-sha256`. **R2 returned
  HTTP 400; no object was created** (HEAD 404, the listing showed only the
  control).
- Both keys were deleted by exact key; the prefix was then empty.

Integrity therefore holds in two layers: R2 rejects the body, and the
publisher's `HashingReader` check and read-back would catch it too.

## Final cleanup

The final snapshot shows all four allowed prefixes holding **0 objects**.
The snapshot SHA-256 `d2db42a7…` is identical to the empty preflight before
Batch B. Recorded-key verification (`recorded-keys.json`, 51 keys):

| Source | Keys | Removed by |
|---|---|---|
| Batch B report run `f598…` | 19 | sweep apply (this section) |
| Batch B source `f598…` | 1 | P1-A expiry (rollback rehearsal) |
| Retest report run `d123…` | 14 | expiry deletion when turned on |
| Retest source `d123…` | 1 | normal expiry |
| Outage report run `52fa…` | 14 | expiry deletion (deletion on) |
| Payload probe | 2 (1 created) | deleted by exact key |

Recorded keys still present: none. Unrecorded keys present: none. The 34
objects left after the retest are all gone, removed by the product's sweep
and expiry paths. The final step needed no manual deletion.

## Findings

1. **A storage request can outlast the 60 s socket timeout.** The first
   read-back after the outage (started 08:14:12, right after publication)
   ran about 16 minutes. It then failed with `RemoteDisconnected` on a single
   HEAD. A rerun at 08:31 took 7 s and passed. Cause not established.
   `urlopen(timeout=60)` bounds each socket operation, not the whole
   request. That matters for the delete settle's assumption that 600 s is
   "well above the 60 s request timeout": the publisher holds the write
   barrier during its own PUTs, but a PUT abandoned by a dead process is
   bounded only by the server. Recommend reviewing this before Gate 5;
   nothing in this run was affected.
2. With deletion on, the first deletion pass (prefix emptied) logs
   nothing at INFO. Only the final removal logs "report prefix deleted".
   The timeline above comes from the records.
3. The `s3gate1r` container was recreated to turn deletion on, so its
   deletion-off log was not kept; the evidence for that phase is the record
   polls and `status` output above.

Every evidence file (96) and log was checked against the R2 values, the
three database passwords, the admin passwords and the review tokens: none
appears.

## Left in place

- Dev R2: nothing (all allowed prefixes empty).
- Docker volumes `s3gate1-*`, `s3gate1r-*`, `s3gate1o-*` (containers and
  networks removed) and images `s3gate1-p1b:*`.
- `/private/tmp/s3gate1/`, `/private/tmp/s3gate1r/`,
  `/private/tmp/s3gate1o/`, `/private/tmp/s3gate1x/`: scripts, evidence and
  logs, with `db.env` and `state.json` (synthetic passwords and review
  tokens, 0600). Every `gate1.env` was deleted.

## Decision

Gate 1 is complete on `af3d9f4`: end-to-end publication, the retest,
outage and recovery, expiry deletion under both settings with the 600 s
settle, sweep apply with the digest guard, the payload-hash probe and the
final cleanup all passed. One open item (finding 1) is recommended for
review before Gate 5. Gate 2 onwards (production key, production scan,
deploy, enabling settings) were not started. Stopped for approval.
