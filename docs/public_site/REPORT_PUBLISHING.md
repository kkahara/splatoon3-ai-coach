# Report publishing to R2 (P1-B B2)

Contract for write-through publishing of completed reports to object storage.
Release procedure and gates: `PHASE1B_B2_RELEASE.md`.

## Boundaries

- **Local storage is the only thing that serves results.** Nothing in
  `results.py`, `frames.py`, `history.py`, `localize.py`, the serving routes,
  `models.Submission`, telemetry or the database changes. `PUBLIC_STORAGE`
  stays `local`. A failing or absent R2 never changes what users see.
- **Runs are immutable.** An object is written once and never overwritten. A
  changed report is a new run.
- **No database migration, no telemetry change.** Publication state lives in
  separate local records under `PUBLIC_ROOT/report_publications/` (Pydantic
  projections drop unknown fields, so it cannot live on the projection).
- **All report network work runs on one dedicated background thread**, never
  inside `maintain()` or the job thread.
- **The local analysis folder is never modified.**
- Historical reports (completed before B2) are not published by B2; they are
  B3 (sanitized copies, with their own approval).

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

`reports/<submission_id>/<run_id>/<path>`

- `run_id` is `YYYYMMDDTHHMMSSZ-<8 hex>` (UTC time of the run plus randomness).
- `<path>` is relative to the analysis folder and passes B1's key rules.
- A run is **committed** when its `bundle.json` exists. `bundle.json` is
  written last, after every other object has been read back and verified.

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
- `producer`: `pipeline_version`, `git_sha`, `evidence_version`
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
and the sweep).

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
  under `reports/<id>/` (all runs), and the record is removed only when a
  following LIST is empty.
- Hooks never raise into the worker or the request; failures log the exception
  type only.

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
- **Expiry deletion:** 1 min, 5 min, 15 min, then hourly, with no give-up.
- One submission is processed at a time.

## Content policy (version 1)

Runs on the published bytes before the first PUT. A finding blocks the record
(`content_policy:<rule>`) and logs the rule name only.

- **JSON:** non-null forbidden keys (`victim_name`, `ocr_text`, `email`,
  `display_name`, `user_id`, `access_token`, `access_token_hash`,
  `review_code`, `original_filename`, `analysis_dir`, `ip`); absolute paths; a
  strict email pattern. Keys are scanned as well as values.
- **Known values (raw bytes of every file):** the submission's email, display
  name, user ID, original filename and its stem (8 characters or more), token
  hash, review code, every configured secret of 8 characters or more, and the
  original `video_identity` and `analysis_id`.
- **MP4 (ffprobe):** format tags within `major_brand`, `minor_version`,
  `compatible_brands`, `encoder`; stream tags within `handler_name`,
  `vendor_id`, `language`, `encoder`; handler names `VideoHandler` and
  `SoundHandler`; only video and audio streams; no chapters. A file ffprobe
  cannot read is `mp4_unverifiable`.
- **JPEG:** no APP1 (EXIF/XMP) or APP13 segment; a COM segment only when it is
  exactly ffmpeg's encoder comment (`Lavc<version>`).

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
  the per-submission lock just before deletion; a deletion counts only when the
  following LIST is empty. Results: `deleted`, `failed`, `skipped_on_recheck`.
- **Threshold:** abort above max(5, 10% of prefixes). `--allow-large` lifts only
  this limit; every other rule still applies.
- **Automatic sweep** (`SWEEP=1`, daily): the same planner and recheck, never
  `--allow-large`.

## Operator CLI (`s3-coach public-site-reports`)

| Command | Effect |
|---|---|
| `status [--remote] [--details] [--json]` | Local: record counts by state, blocked rules, overdue `delete_pending`, superseded runs, settings (on/off), staging summary. `--remote` (LIST only): runs, objects and bytes for current, superseded and partial runs, expired submissions still holding objects, other keys, and a total row; `--details` adds one row per submission. |
| `scan [--analysis-root DIR] [--inventory FILE] [--write-inventory FILE] [--json]` | Read-only, local only: runs the allowlist, transforms, consistency checks and content policy over every local analysis without staging or uploading. Prints counts by rule and, with `--inventory`, key paths that are new or unseen relative to the inventory (key names, never values). |
| `sweep [--apply --confirm DIGEST] [--allow-large] [--json]` | Dry run by default; see Sweep. |
| `publish --retry-blocked ID` | Moves one `blocked` record back to `pending` (local record only). |

Remote commands need R2 credentials but none of the three settings.

## Logging and privacy

Logs and records carry submission IDs, run IDs, counts, error codes and rule
names only: never credentials, signatures, endpoints, host paths, object
contents, fingerprints, pseudonyms, filenames, emails or other personal values.

## Tests

`tests/test_public_report_{bundle,transform,content,publisher,sweep,retention,rollback,staging}.py`,
additions to `tests/test_public_storage.py` and
`tests/test_public_storage_logging.py`, the synthetic fixture
`tests/report_bundle_fixture.py` and the key-path inventory
`tests/fixtures/report_bundle/key_inventory.json`. All storage tests use the
in-process fake S3 only. The rollback test is marked `rollback`.
