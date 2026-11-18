# Report publishing to R2 (P1-B B2)

Contract for write-through publishing of completed reports to object storage.
Release procedure and gates: `PHASE1B_B2_RELEASE.md`.

## Boundaries

- **PostgreSQL is authoritative once a report is `verified`.**
  `published_reports` holds the coaching result projection, match identity,
  and localization cache; `published_assets` holds object keys (never
  presigned URLs). Guest token / session ownership is checked **before** any
  PostgreSQL or filesystem media resolution.
- **Increment 6 media delivery.** Moment `/frame`, `/clip`, and `/thumbnail`
  routes return `302 Found` to a fresh presigned R2 GET URL
  (`Cache-Control: private, no-store`, `Vary: Cookie, Accept`). FastAPI never
  proxies media bytes. Browsers send unsigned `Range` requests **to the
  presigned R2 URL** for seeking (`206 Partial Content`). Explicit
  `Accept: application/json` returns `{url, expires_at, content_type}` only.
  The Review UI retries a failed load at most once (`?refresh=1`).
- **Local fallback by publication state only:**
  - `verified` → PostgreSQL asset lookup only (missing asset → 404).
  - `pending` / `publishing` → local `analysis_dir` only if still present.
  - `blocked` / `delete_pending` / `deleted` → always 404 (no local fallback).
  - No publication row / publishing off → local fallback when analysis exists
    (historical / `PUBLIC_STORAGE=local` compatibility).
- **Runs are immutable.** An object is written once and never overwritten. A
  changed report is a new generation under `attempt-<n>/`. Replacement
  attempts keep the prior verified generation readable until
  `commit_verified` switches `active_run_id`.
- Local `report_publications/` records remain a same-machine fast path;
  fencing and authority live in PostgreSQL (`003_published_report_assets.sql`).
- **All report network work runs on one dedicated background thread**, never
  inside `maintain()` or the job thread (except synchronous `publish_now`
  after `.pipeline_ready.json`).
- **The local analysis folder is temporary** after verified publication
  (`local_cleanup_state`). Historical reports not yet migrated still use
  local disk until the migration CLI (B3 / Milestone 3).

## Settings: three independent opt-ins, all off by default

| Setting | Allows | R2 operations |
|---|---|---|
| `PUBLIC_REPORT_PUBLISH` | Publishing new completed reports | PUT, HEAD, GET, LIST. Never DELETE. |
| `PUBLIC_REPORT_EXPIRY_DELETE` | Deleting `reports/<id>/` when that submission expires | LIST, DELETE under that prefix |
| `PUBLIC_REPORT_SWEEP` | The automatic daily sweep | LIST, DELETE, behind the sweep rules |

- Only `0` or `1` is accepted (absent means `0`). Anything else, or a `1`
  without complete R2 credentials, stops startup (`create_app`, `public-site
  serve` and `public-site-reports`). Messages name the setting, never a value.
- **Credentials alone authorize nothing.** With all three at `0` no report
  storage client is built, no publisher thread starts, no staging directory is
  created and zero report R2 requests are sent.
- **Capabilities are structural.** The publisher only holds `ReportWriter`
  (`head`, `put_file`, `read_sha256`, `list_objects`; no `delete`, no `copy`).
  Expiry deletion holds `ReportEraser` (LIST and DELETE under `reports/`) and is
  built only when `PUBLIC_REPORT_EXPIRY_DELETE=1`; the automatic sweep likewise
  only when `PUBLIC_REPORT_SWEEP=1`. Every view rejects keys outside
  `reports/`.
- **Retention gap guard:** `PUBLISH=1` with `EXPIRY_DELETE=0` logs a startup
  WARNING. Expiry still marks the record `delete_pending`, so the backlog is
  kept and visible in `status` as overdue. Enable both together.
- The operator `sweep --apply` needs credentials, not `PUBLIC_REPORT_SWEEP`,
  and always requires the plan digest.

## Keys and run IDs

Legacy (pre-fencing) layout: `reports/<submission_id>/<run_id>/<path>`

**Generation-fenced layout (Increment 5):**

`reports/<submission_id>/<run_id>/attempt-<attempt_generation>/<path>`

- `run_id` is `YYYYMMDDTHHMMSSZ-<8 hex>` (UTC time of the run plus randomness).
- `attempt_generation` is a monotonic PostgreSQL fencing token per submission.
  Each publication attempt writes **only** under its own generation prefix.
  A stale worker that loses its lease can finish a late PUT into its abandoned
  prefix, but cannot overwrite a newer generation or publish its manifest as
  authoritative. PostgreSQL `commit_verified` stores `bundle_key` and asset
  keys for the verified generation only.
- Ordinary transient retries while the lease is still valid **resume the same
  generation** (same prefix). A new generation is allocated only when acquiring
  a fresh lease after expiry/fence-out, `retry_blocked`, or a replacement run.
- Abandoned generation prefixes are reconciled and deleted with the same
  upload-window settle, bounded request deadlines, and empty-LIST settle rules
  as expiry deletion. Sweep never deletes the active generation's retained keys.
- `<path>` is relative to the analysis folder and passes B1's key rules.
- A generation is **committed** when its full inventory (every artifact plus
  `bundle.json`) verifies by size and SHA-256. `bundle.json` is written last,
  after the local re-hash barrier.

## What is published (allowlist)

Only what serving reads; the round-trip test proves it is sufficient.

- `vision_manifest.json` (required; sanitized copy)
- `match_identity.json`
- `coach_inputs/coaching_index.json` (required when the outcome is `coached`),
  `*.coaching.json`, `*.coach_input.json`, `*.llm_view.json`
- `coach_prototype/coaching_index.json`, `*.output.json`, `*.flags.json`,
  `*.revalidation.json`, `*.llm_view.json` (serving reads the prototype copy
  first and falls back to `coach_inputs/`)
- `public_clips/*.mp4`, `public_frames/*.jpg`

Everything else is excluded, including `coach_inputs_meta.json` (absolute host
path), `llm_metrics.jsonl` (provider error text), `game_language.json`, the
source video, `probe/`, partial and hidden files, symlinks, `localized/`,
`*.raw.txt`, prompts, `summary.md`, `claim_eval.md`, `*.vmv.*.txt`, `debug_*/`,
`scenarios*`, `scenario_contexts.json`, `map_observations.json` and the viewer
HTML. Missing clips or stills do not block a run; they are listed in
`missing_media`.

## Sanitized copies (transforms)

`video_identity` is a fingerprint of the uploaded file, and `analysis_id` is
derived from it (and embedded in frame IDs, result IDs, evidence IDs and the
coaching indexes). Neither leaves the host:

- **`redact_video_identity_v1`** (`vision_manifest.json`): both occurrences of
  the 64-hex `video_identity` become the sentinel `redacted:v1`. The schema
  still validates; the sentinel does not pretend to be a hash.
- **`pseudonymize_analysis_id_v1`** (every published JSON file): every
  occurrence of the 64-hex `analysis_id` becomes one pseudonym per run,
  `secrets.token_hex(32)`. **No mapping is stored anywhere.** The audit link to
  the local analysis is `submission_id` plus each file's `local_sha256`.

Transforms replace exact byte tokens; JSON is never re-serialized, so every
other byte is unchanged. Each run then checks, and fails closed with `blocked`
(`transform_failed:<rule>`) on any violation:

- the manifest contained both tokens;
- no original `video_identity` or `analysis_id` byte remains in any published
  file;
- every transformed file still parses, and the manifest validates as
  `VisionManifest`;
- every reference agrees: `analysis.analysis_id`, both coaching indexes, every
  `coach_input.json`, the prefix of every frame and detection result ID (read
  with `parse_result_id`) and every `state_snapshots[].evidence_ids[]` carry
  the run's pseudonym, never the original and never a second value.

A report whose local coaching was built from a different analysis (stale)
cannot be made consistent and is blocked, not published.

Because the pseudonym exists only in memory and in the published bytes, a run
interrupted by a restart or crash cannot be resumed: the next attempt is a new
run, and the uploaded objects become a partial run (see Sweep). Retries within
one process reuse the run.

## `bundle.json` (`s3coach.report_bundle.v1`)

- `format`, `submission_id`, `run_id`, `created_at`, `outcome`
- `producer`: `pipeline_version`, `git_sha`, `evidence_version`. `git_sha`
  is the full commit an image was built from (the Dockerfile records
  `GIT_SHA` in `src/splatoon3_ai_coach/BUILD_COMMIT`), a short SHA when run
  from a git checkout, and `null` when neither is available.
- `missing_media`, `content_policy_version`, `total_bytes`
- `files[]`: `path`, `local_size`, `local_sha256`,
  `published_size`, `published_sha256`, `transforms` (empty when published
  as-is)

It never contains `analysis_id`, `video_identity`, personal fields or host
paths, and it passes the same content scanner as every other file.

## Integrity

1. Each local file is read once. Those bytes give `local_sha256`; the
   transform runs on the same bytes and gives the published bytes and
   `published_sha256`. The scanner runs on the published bytes.
2. `put_file` wraps the source in `HashingReader`, which hashes and counts
   exactly the bytes sent. They must equal `published_sha256` and the size, or
   the attempt fails with `checksum`. The payload SHA-256 is also signed
   (`x-amz-content-sha256`) and stored as `x-amz-meta-sha256`.
3. Every object is read back with `read_sha256` (a streamed GET, nothing
   written to disk); length and digest must match.
4. Before commit, under the per-submission lock: the record and projection are
   re-checked (an expired submission aborts the commit), every local file is
   re-hashed against `local_sha256` and every staged file against
   `published_sha256` (`local_changed` on any difference). Then `bundle.json`
   is put and read back, and the record becomes `verified`.
5. Resume: an existing object counts only when HEAD and read-back both match;
   anything else is `conflict`. Objects are never overwritten.

## Records and lifecycle

`PUBLIC_ROOT/report_publications/<submission_id>.json` (atomic writes; one
lock per submission, a thread lock plus `flock` on
`report_publications/.locks/<id>.lock`, shared by publishing, expiry deletion
and the sweep). Publishing holds it only for short record changes, never during
an upload: the bundle PUT runs with the write barrier held but not the record
lock, between a re-check before it and a re-check after it, and only the second
re-check marks the run `verified`. Expiry deletion holds it for its LIST and
DELETE requests.

### In-flight write barrier

A second per-submission `flock`, `report_publications/.writers/<id>.lock`,
serializes every writer and eraser of `reports/<id>/` across threads and
processes:

- A publication attempt holds it from choosing its run until the attempt ends
  (commit, failure or invalidation).
- Expiry deletion holds it while it deletes and verifies the prefix empty.
- The sweep holds it while it re-checks and deletes one row.
- It is taken non-blocking: a busy barrier never waits. A publication attempt
  or an expiry deletion that finds it busy is deferred 60 s without counting an
  attempt or changing `last_error`; a sweep row that finds it busy is
  `skipped_on_recheck`. So no deletion is declared complete while a writer can
  still add objects under the prefix.
- Lock order is always barrier, then record lock.
- The kernel releases it when the holding process dies, so a crash never
  leaves it held; crash recovery (staging leases, startup recovery) is
  unchanged.
- It carries no capability: the writer still cannot delete and the eraser
  still cannot put.

Before each object, a publication attempt re-reads its record and submission.
If the record is no longer `pending` for this run (expired, re-queued, removed)
or the submission is no longer `complete`, the attempt stops uploading,
closes its staging and records nothing (not a failure). At most the one object
PUT already in progress completes, while the attempt still holds the barrier.

| State | Meaning |
|---|---|
| `pending` | Waiting for (or retrying) a publication run |
| `verified` | `run_id` is committed and every object was verified |
| `blocked` | Content policy or transform failure; no automatic retry |
| `delete_pending` | The submission expired; its prefix must be deleted |

- **Completion:** `PublicWorker._complete` creates or resets a `pending` record
  and wakes the publisher (only when publishing is on).
- **Media filled later:** when `_fill_frames` adds clips or stills, a
  `verified` record goes back to `pending` and the next run gets a new
  `run_id`. The previous run is kept in `superseded_run_ids`; the publisher
  never deletes it.
- **Expiry:** `_expire_one` marks the record `delete_pending` (local only). At
  startup, records of already-expired submissions are moved to
  `delete_pending`. With `EXPIRY_DELETE=1` the eraser deletes every object
  under `reports/<id>/` (all runs). See **Delete settle** below for when the
  record is removed.
- **Invalidated after the bundle PUT:** if the record or submission changes
  while the bundle is uploading, the run is not marked `verified`. An expired
  submission's prefix (bundle included) goes with expiry deletion; a committed
  run that a newer run replaces is later a sweep `superseded_run`.
- Hooks never raise into the worker or the request; failures log the exception
  type only.

### Delete settle (late PUTs)

A PUT abandoned by an earlier attempt (for example after a client timeout) can
still land after the prefix was emptied. So an empty prefix is not final at
once:

1. The pass that leaves the prefix empty keeps the record `delete_pending` and
   sets `delete_empty_at`.
2. The record is re-listed every `DELETE_RECHECK_SECONDS` (60 s). A non-empty
   LIST deletes again and restarts the settle time (`delete_empty_at` = now).
3. The record (and its canary) is removed only by an empty LIST at least
   `DELETE_SETTLE_SECONDS` (600 s) after `delete_empty_at`.

The settle is not a proven bound. The 60 s storage timeout limits each socket
operation, not the whole request, and Gate 1 observed one request lasting
about 16 minutes. Expiry deletion stays disabled in production until this is
resolved (`PHASE1B_B2_RELEASE.md`, "Disabled in this deployment").

A failed deletion clears `delete_empty_at`. The sweep's own apply step still
removes an `expired_submission` record after one empty LIST (its rows are at
least 24 h old).

## Retry policy

- **Publishing:** the first attempt is immediate; backoff 1 min, 5 min, 15 min,
  1 h, 6 h, then every 24 h, with no give-up while the submission is complete
  and its report exists. From the sixth consecutive failure, one ERROR per
  submission per day; earlier failures are WARNINGs.
- **Error vocabulary (closed):** `network`, `status_4xx`, `status_5xx`,
  `checksum`, `conflict`, `local_changed`, `local_missing`, `invalid_key`,
  `content_policy`, `transform_failed`, `staging_unsafe`.
  - `checksum`, `conflict`, `local_changed` and `staging_unsafe` start a new
    run on the next attempt.
  - `content_policy` and `transform_failed` set `blocked`; the operator
    retries with `public-site-reports publish --retry-blocked <id>`.
- **Unexpected exceptions** during an attempt (preparation, staging, upload,
  commit) are mapped into the closed vocabulary and back off like any other
  failure: a missing local file is `local_missing`, anything else
  `local_changed`. Either closes the run and the next attempt starts a new run.
  The WARNING carries the exception type name only, never its message.
- **Unknown canary values:** when any value group could not be retrieved (see
  Known values below) the attempt fails closed with `network` and backs off;
  nothing is uploaded.
- **Expiry deletion:** 1 min, 5 min, 15 min, then hourly, with no give-up. An
  unexpected exception while deleting is `network`.
- **Per-record isolation:** a pass (`run_once`) takes the due records, most
  overdue first (`delete_pending` before `pending`). An exception escaping one
  record's attempt (for example `ELOOP` opening its write barrier) backs off
  that record only (`local_missing` or `local_changed`, its normal publish or
  delete backoff, a type-only WARNING), and the pass continues with the next
  due record. The pass stops after the first attempt that completes (success
  or a normally recorded failure). Each record is tried at most once per pass
  and at most `MAX_RECORDS_PER_PASS` (50) per pass. One submission is
  processed at a time.
- **Publisher thread:** `startup()` runs inside the loop's error handling: if it
  fails it is retried after the 60 s pause, and no record is processed until it
  has succeeded. After any exception that escapes a cycle (including a failure
  to record a per-record backoff) the thread pauses 60 s and ignores wake-ups
  during the pause, so no error can turn into an immediate retry loop. Only
  shutdown interrupts the pause.

## Content policy (version 1)

Runs on the published bytes before the first PUT. A finding blocks the record
(`content_policy:<rule>`) and logs the rule name only.

- **JSON:** non-null forbidden keys (`victim_name`, `ocr_text`, `email`,
  `display_name`, `user_id`, `access_token`, `access_token_hash`,
  `review_code`, `original_filename`, `analysis_dir`, `ip`); absolute paths; a
  strict email pattern. Keys are scanned as well as values.
- **Known values:** the submission's email, display name, user ID (a numeric
  ID is checked as text), original
  filename and its stem (8 characters or more), token hash; the review-code
  acquisition's `review_code`, `target_player_name`, `confirmed_player_name`,
  `requested_email`, `customer_message`, `handoff_reference` and the
  `submission_snapshot` email, display name, user ID and token hash; every
  configured secret of 8 characters or more; and the original `video_identity`
  and `analysis_id`.
  - Matched in the raw bytes of every file, and in JSON-escaped form (so a
    non-ASCII name written as `\uXXXX` escapes is still found).
  - In JSON files, also matched against every decoded key and string value,
    after Unicode NFKC normalization (full-width and half-width forms match),
    case-insensitively and ignoring whitespace and hyphens. Raw-byte tokens
    cover both the value as given and its NFKC form.
  - Names (display names, `target_player_name`, `confirmed_player_name`)
    shorter than 4 characters are matched only as an entire decoded JSON
    string value (after NFKC, case-folding and removing whitespace and
    hyphens), never as a substring and never against keys.
  - New review-code requests no longer store `target_player_name` or
    `confirmed_player_name` (see `REVIEW_CODE_RECORDING.md`). Both stay in the
    known values because rows created before that policy may hold them; a
    missing value adds no matcher and changes no other check.
  - The review-code canary (below) adds an HMAC matcher.
  - One function (`report_known.submission_known_values`) builds them for both
    the publisher and `scan`. Acquisition values come from the app's
    `recordings` store, or, when review codes are disabled, from the same
    read-only `RecordingStore.get` query on `PUBLIC_DATABASE_URL`.
  - A value group that cannot be retrieved is reported by name:
    `submission_values` when there is no submission projection, and
    `acquisition_values` when the lookup fails for any submission. A
    review-code submission must have an acquisition row, so it is also
    `acquisition_values` when there is no lookup or no row. A database
    without the acquisition table counts as no row.
  - An upload without an acquisition row is fully checked.
  - The publisher fails closed on any unchecked group; `scan` reports it and
    never counts the analysis as clean.
- **MP4 (ffprobe):** format tags within `major_brand`, `minor_version`,
  `compatible_brands`, `encoder`; stream tags within `handler_name`,
  `vendor_id`, `language`, `encoder`; handler names `VideoHandler` and
  `SoundHandler`; only video and audio streams; no chapters. A file ffprobe
  cannot read is `mp4_unverifiable`.
- **JPEG:** the whole marker structure is walked from SOI to EOI, through
  every scan's entropy-coded data (stuffed `FF 00`, restart markers, fill
  bytes), so segments between progressive scans are inspected too. Segment
  allowlist, anywhere in the file:
  - structural markers: SOFn, DHT, DQT, DRI, SOS, RSTn, DNL;
  - at most one APP0, which must be a plain JFIF header: `JFIF\0`, the fixed
    14-byte body and a 0×0 thumbnail (JFXX and thumbnails are rejected);
  - at most one COM, which must fullmatch ffmpeg's encoder signature
    `Lavc<major>.<minor>.<micro>` (1-3, 1-4, 1-4 digits) with an optional single
    trailing NUL.

  Everything else (APP1-APP15, a second APP0 or COM, any other comment, DAC,
  TEM, unknown markers) is `jpeg_metadata`. A truncated or malformed segment, a
  missing EOI, a second SOI or any byte after EOI is `jpeg_invalid`. The real
  ffmpeg stills (65 in the dev analyses) and OpenCV baseline, progressive and
  restart-interval stills contain only these segments.

## Review-code canary

A review-code submission's code leaves the database at recording handoff
(`confirm_recording_handoff` sets `review_code = NULL`), but its report is
published later. The canary keeps the code detectable without keeping it.

### Design

- **Storage** (`report_canary.py`), owner-only (directory `0700`, files
  `0600`):
  - `PUBLIC_ROOT/report_canary/key.json`: version, a 32-byte random key,
    `key_id` (a truncated SHA-256 of a fixed label plus the key; it never
    reveals the key) and `created_at`, the **canary epoch**;
  - `PUBLIC_ROOT/report_canary/<submission_id>.json`: `key_id`, the
    HMAC-SHA-256 of the code compacted to 16 uppercase characters, and
    `created_at`;
  - `PUBLIC_ROOT/report_canary/<submission_id>.pending.json`: a `correct_code`
    HMAC not yet finalized;
  - `PUBLIC_ROOT/report_canary.epoch.json`: the key's `key_id` and epoch,
    written beside the directory and never deleted (see Key situations).
- **Capture:** `RecordingService.create` writes the record durably after
  validation and **before** the acquisition row is inserted. If it cannot be
  written the submission is refused (503); nothing is skipped silently. An
  orphan record from a failed insert has no projection and is removed after
  24 h. The app loads (or, on a fresh install, creates) the key at startup, so
  the epoch precedes every captured submission.
- **Matcher** (`report_content.CodeMatcher`): runs of ASCII letters/digits,
  with whitespace and hyphens allowed between characters, are compacted,
  uppercased and HMACed over every 16-character window with a precomputed key
  state and compared with `compare_digest`. Windows and whole runs already
  found not to match are remembered (bounded), so repeated identifiers are
  hashed once. It runs on the raw bytes of every file (MP4 and JPEG before
  their structural checks) and on every decoded JSON key and string (after
  NFKC, so full-width codes match). A decoded string that JSON writes verbatim
  (ASCII, no control character, quote or backslash) in a file without `\u`
  escapes is already covered by the raw-byte pass and is not matched twice;
  escaped whitespace (`\n`, `\t`) and `\u` escapes always get the decoded
  pass. The rule is `known_value:review_code`.
- **Time budget:** at most 1 s added per analysis. Measured on the 28 dev
  analyses (571 published files, 152 MB): scan 13.3 s without a canary, 17.8 s
  with one per analysis (+0.16 s per analysis).
- **Check** (`report_known.submission_known_values`, shared by the publisher and
  `scan`):
  - a record, when present, is always used;
  - a submission created at or after the epoch must have a record and a key
    that load with a matching `key_id`;
  - a submission created before the epoch has no record: this is the
    documented pre-canary limitation; it is still checked against the
    database `review_code` while the row holds it;
  - otherwise the value group `review_code_canary` is unchecked: the
    publisher fails closed (`network`, backoff, no PUT) and `scan` reports it.
- **Never logged or published:** the key, HMACs and codes. Logs carry the
  exception type and submission ID only. `CanaryKey`'s repr hides the key.
- **Retention:** a canary is kept until expiry deletion removes the
  submission's publication record (after the delete settle). While any report
  setting is on, the publisher's startup and hourly maintenance remove
  canaries of expired submissions that have no
  publication record (this covers P1-A rollback periods, when P1-A expires
  submissions without touching `report_canary/`), stale temporary files
  (older than 10 minutes) and orphans (no projection, older than 24 h). The
  canary is independent of the database row, so it survives handoff.

### Crash safety

- **Atomic writes:** a temporary file in the same directory
  (`O_CREAT|O_EXCL|O_NOFOLLOW`, `0600`), `fsync`, then `rename` over the target;
  the directory is fsynced after every create, replace or unlink. A crash
  leaves either the old or the new file; a leftover temporary file is ignored
  by readers and removed after 10 minutes.
- **Symlink and ownership checks:** `report_canary/` must be a real directory
  owned by this uid with mode `0700`, and `PUBLIC_ROOT` must not be a
  symlinked path. Every read uses `O_NOFOLLOW` and checks type, owner and mode
  `0600` with `fstat`. Any violation makes the canary unavailable, never clean.
- **Key creation:** under `flock(LOCK_EX)` on `report_canary/.key.lock`, only
  when no key, no record, no pending file and no epoch marker exist; the key
  is written to a temporary file, fsynced and `os.link`ed to `key.json`, which
  fails if a key already exists. Concurrent starts end with exactly one key.
  An existing key is never replaced or regenerated.

### `correct_code`

1. Write `<id>.pending.json` durably with the new HMAC. If that fails the action
   is refused (503) and nothing changes.
2. Run the database update. If it fails (stale version, wrong state, error),
   the pending file is removed.
3. Rename the pending file over `<id>.json`. If this fails after the update,
   the action returns 500 ("publication stays blocked until repaired") and logs
   the exception type only.

A pending file is **never** a valid canary: any submission with a pending file
(or an interrupted pending write) is unchecked. At startup the app classifies
every pending file against the database code (`CanaryStore.pending_states`)
and acts only on two states:

| State | Meaning | Startup repair |
|---|---|---|
| `promotable` | The database code's HMAC equals the pending HMAC | Renamed over the record |
| `stale` | The database still holds the recorded code (the update never happened) | Pending file removed |
| `no_code` | The code is already NULL (handoff) | None: stays blocked |
| `mismatch` | Neither HMAC matches the database code | None: stays blocked |
| `multiple` | More than one pending entry for the submission | None: stays blocked |
| `unreadable` | Key or pending file unusable | None: stays blocked |
| `lookup_failed` / `unverified` | The database could not be read / no database lookup | None: stays blocked |
| `repair_failed` | Promotion or removal failed | None: retried at the next start |

**Operator procedure for a blocked pending file.** Run `public-site-reports
canary-status` (it verifies against `PUBLIC_DATABASE_URL`, SELECT only).
`pending_promotable` or `pending_stale` resolve at the next app start. For any
other state, confirm the submission's current code with the customer or the
audit trail; if the row still holds a code, re-apply `correct_code` with that
code (the new pending file replaces the old one and is promoted). If the code
is already NULL, the canary cannot be re-derived: keep the report unpublished
(the record stays `pending` with `network` backoff) and close the
acquisition, or remove the pending file only after reviewing the report for
the code by hand. Never copy a pending file over a record by hand.

### Key situations

| Situation | Behavior |
|---|---|
| Key and canary present | Normal checking; a planted code is `known_value:review_code` |
| Key missing, unreadable, corrupt, wrong owner or mode, symlinked, or a different `key_id` | Fail closed: captures are refused, post-epoch review-code publications back off with `network`, `scan` reports `review_code_canary`, `canary-status` reports counts and exits 1. The key is **never** regenerated, including after a restart |
| Whole `report_canary/` lost | Same: the epoch marker beside it forbids a new key, so older submissions cannot silently become "pre-epoch" |
| Key restored from backup | Checking resumes once owner and mode are correct (`0700` directory, `0600` files); a wrong mode or owner is rejected until corrected |

### Backup and restore

Back up with the `PUBLIC_ROOT` volume backup, and keep the archive with the
host's volume backups (it contains the HMAC key: same protection as the
volume). In the container (`PUBLIC_ROOT=/data/public`):

```bash
# Backup: report_canary/ and the epoch marker, modes preserved.
docker compose exec -T app tar -C /data/public -cpf - \
  report_canary report_canary.epoch.json > report-canary-$(date -u +%Y%m%dT%H%M%SZ).tar

# Restore (app stopped or idle), then check owner and mode.
docker compose exec -T app tar -C /data/public -xpf - < report-canary-<stamp>.tar
docker compose exec -T app stat -c '%a %U %n' /data/public/report_canary \
  /data/public/report_canary/key.json /data/public/report_canary.epoch.json
# expected: 700 for the directory, 600 for the files, owned by the app user

# Confirm.
docker compose exec -T app s3-coach public-site-reports canary-status
```

A backup taken before newer captures restores the key (so checking resumes
for every submission) but not those newer records: they show up in
`canary-status` as `post_epoch_without_canary` and stay unchecked until their
acquisition is reviewed. The key never needs to be rotated for a restore.

### `canary-status`

Counts only: `directory_safe`, `key_present`, `key_valid`, `epoch_marker`,
`records`, `pending` plus `pending_<state>`, `post_epoch_without_canary` and
`unchecked_review_code_submissions`. Never a code, HMAC, key or path. Exit code
1 when any review-code submission is unchecked, any correction is pending, the
directory is unsafe, or a key or epoch marker exists without a valid key.


## Staging (`PUBLIC_ROOT/report_staging/<id>/<run_id>/`)

Transformed bytes are staged before upload; files published as-is upload from
the analysis folder.

- **Owner-only:** directories `0700`, staged files and the lease `0600`, set
  explicitly (independent of the umask). Every component must be a real
  directory owned by this uid; files are opened with `O_NOFOLLOW`. Any
  violation is `staging_unsafe`: nothing is uploaded.
- **Lease:** each run holds `flock(LOCK_EX)` on `<run_id>/.lease` for its whole
  life. The lease holds only `run_id` and `created_at`. The kernel releases the
  lock when the process exits, so a held lock proves the run is active.
- **Cleanup** (on commit or abort for the run's own directory, at startup
  before any record is processed, and hourly) removes a run directory only
  when it is not active in this process, and either its lease lock can be
  taken (and is held while deleting) or, without a lease, it is older than
  10 minutes. It never follows symlinks (a symlink is unlinked, its target
  untouched) and never touches anything outside `report_staging/`. It logs
  counts only.
- **Requirement:** `flock` is advisory and needs a local filesystem.
  `PUBLIC_ROOT` must be a local volume (it is a local Docker volume), never a
  network filesystem.

## Sweep

Classifies every prefix under `reports/`:

| Reason | Eligible when |
|---|---|
| `expired_submission` | The projection exists and is `expired`: deletes `reports/<id>/` |
| `superseded_run` | A record with a current run exists, the run is committed, not the current run, and older than 24 h |
| `stale_partial_run` | No `bundle.json`, not the current or in-progress run, older than 24 h |

Never eligible (reported as skips): `projection_absent`,
`complete_without_record`, `current_run`, `run_too_recent`, `processing`,
`record_pending` (the run a pending record is attempting), `invalid_prefix`.

- **Dry run** (default): header (mode, bucket name, time, threshold, limit),
  one row per proposed deletion (`submission_id`, `prefix`, `reason`,
  `object_count`, `bytes`, `projection_status`, `record_state`,
  `newest_object_age_seconds`), skips, totals and the **plan digest**
  (SHA-256 of the canonical rows, ages excluded). Never credentials, URLs,
  signatures or object contents.
- **Apply:** `--apply --confirm <digest>` recomputes the plan and aborts (exit
  code 2, nothing deleted) if the digest differs. Each row is re-checked under
  the write barrier and the per-submission lock just before deletion. A
  submission whose publication attempt holds the barrier is
  `skipped_on_recheck`. A deletion counts only when the following LIST is
  empty. Results: `deleted`, `failed`, `skipped_on_recheck`.
- **Threshold:** abort above max(5, 10% of prefixes). `--allow-large` lifts only
  this limit; every other rule still applies.
- **Automatic sweep** (`SWEEP=1`, daily): the same planner and recheck, never
  `--allow-large`.

## Operator CLI (`s3-coach public-site-reports`)

| Command | Effect |
|---|---|
| `status [--remote] [--details] [--json]` | Local: record counts by state, blocked rules, overdue `delete_pending`, superseded runs, settings (on/off), staging summary. `--remote` (LIST only): runs, objects and bytes for current, superseded and partial runs, unrecorded runs (no local projection or no publication record; the sweep skips them as `projection_absent` / `complete_without_record`, so they need an operator decision), expired submissions still holding objects, other keys, and a total row; `--details` adds one row per submission. |
| `scan [--analysis-root DIR] [--inventory FILE] [--write-inventory FILE] [--json]` | Read-only, no R2: runs the allowlist, transforms, consistency checks and content policy over every local analysis, with the publisher's known values, without staging or uploading. Acquisition values are read with one SELECT per analysis from `PUBLIC_DATABASE_URL` when set. Prints counts by rule; `analyses_unchecked` and `unchecked` (counts per value group that could not be retrieved); and, with `--inventory`, key paths that are new or unseen relative to the inventory (key names, never values). `analyses_clean` counts only analyses with no finding **and** every check performed. |
| `sweep [--apply --confirm DIGEST] [--allow-large] [--json]` | Dry run by default; see Sweep. |
| `publish --retry-blocked ID` | Moves one `blocked` record back to `pending` (local record only). |
| `canary-status [--json]` | Review-code canary counts only (see Review-code canary). Exit code 1 when attention is needed. |

Remote commands need R2 credentials but none of the three settings.

## Logging and privacy

Logs and records carry submission IDs, run IDs, counts, error codes and rule
names only: never credentials, signatures, endpoints, host paths, object
contents, fingerprints, pseudonyms, filenames, emails or other personal values.

## Tests

`tests/test_public_report_{bundle,transform,content,publisher,sweep,retention,rollback,staging,parity,barrier,backoff,canary,canary_db,privacy,lifecycle}.py`,
additions to `tests/test_public_storage.py` and
`tests/test_public_storage_logging.py`, the synthetic fixture
`tests/report_bundle_fixture.py` and the key-path inventory
`tests/fixtures/report_bundle/key_inventory.json`. All storage tests use the
in-process fake S3 only. The rollback test is marked `rollback`. The barrier
tests run a real publisher in a separate process, held at a chosen object PUT
by the fake S3's `put_gate` / `put_gate_after`, and order every step by that
gate and by process exit.
