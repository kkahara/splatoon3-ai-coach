# SCLC recording integration (deferred)

Status: **deferred until SCLC is ready.** Nothing here is implemented or
scheduled.

SCLC is a separate, standalone recording system. Later, this app will instruct
SCLC to record a review-code replay, and SCLC will deliver the finished file
back to the app. This note records what the app already provides for that
handoff and the questions to settle before building it.

## A. What SCLC must record

The recording rules are in `docs/public_site/REVIEW_CODE_RECORDING.md`
("Operator recording"). In short:

1. Open the submitted Battle Memory replay (the job's review code).
2. Leave the replay on its opening perspective and keep it for the whole
   recording. The coached player is whoever the replay opens on; nothing
   selects, names or confirms a player.
3. Deliver one MP4 or MOV file per job.

## B. The app side today

The backend (Postgres `review_code_acquisitions` plus
`review_code_acquisition_events`) is the source of truth for job state. The
routes below exist only when `PUBLIC_REVIEW_CODES_ENABLED` is set. Production
sets it.

**Job states** (`AcquisitionState` in `tools/public_site/recording_models.py`):
`pending_payment`, `awaiting_recording`, `recording`, `uploading`, `ready`,
`recording_failed`, `expired`, `closed`. A job needs recording when it is
`awaiting_recording` (paid or voucher-redeemed, contact verified).

**Routes** (`tools/public_site/recording_routes.py`; all require an admin
session cookie today):

| Route | Purpose |
|---|---|
| `GET /api/admin/recordings` | Queue: jobs needing an operator, plus unpaid requests |
| `GET /api/admin/recordings/{id}` | Job detail: state, version, review code, upload attempt, history |
| `POST /api/admin/recordings/{id}/upload` | Upload grant. Body `RecordingUploadRequest`: `expected_version`, `filename`, `content_type`, `size_bytes` |
| `POST /api/admin/recordings/{id}/upload/confirm` | Verify the stored file and queue analysis. Body: `expected_version` (the version returned by the grant) |
| `POST /api/admin/recordings/{id}/upload/cancel` | Abandon the live attempt. Body: `expected_version` |
| `POST /api/admin/recordings/{id}/actions` | Operator actions such as `recording_failed`, `retry_recording`, `retry_analysis` |

**Upload semantics** (`RecordingIntake`, `RecordingStore.grant_upload`):

- The grant checks the declared type and size (MP4/MOV, at most
  `PUBLIC_MAX_VIDEO_BYTES`, default 750 MiB), moves the job to `recording`,
  and returns `{upload, version, attempt}`. `upload` is a one-object PUT for the
  fixed key `submissions/<id>/source.mp4` or `.mov`; the client's filename is
  never the key.
- A job has one live attempt. A repeated grant returns the same attempt and
  URL. A new attempt cannot start before the attempt's `replace_after`
  (link expiry plus 15 minutes).
- The PUT is create-only and stamped with the attempt id.
- Confirm checks the stored object (HEAD, attempt id, media probe, SHA-256),
  moves the job to `uploading`, and queues the same submission for analysis.
  A rejected file is audited as `recording_upload_rejected`.
- Every state change quotes `expected_version`; a stale version is refused.
- The PUT target depends on the storage backend. With `PUBLIC_STORAGE=local`
  (production today) it is the site's own `PUT /api/dev-upload/{grant}`. With
  `r2` it is a presigned R2 URL.

**After upload:** the worker admits the job only when
`RecordingIntake.admits` and `recording_publish.remote_agrees` match the
verified fingerprint, and refuses a download that fails `downloaded_matches`.
Analysis, coaching, the report and the customer status page then run
unchanged.

Rejections return `{"detail": ...}` with an HTTP status, sometimes with a
`reason`.

## C. Integration shape (conceptual)

1. The app tells SCLC to record a job: at least the submission id and the
   review code.
2. SCLC records the replay as in section A.
3. SCLC asks for an upload grant, PUTs the file, and confirms, through the
   routes in section B or their successor.
4. The worker processes the recording.
5. Job and report status are read from the app's backend, never kept as a
   second copy in SCLC.

The manual upload on the admin recording page stays available after SCLC is
integrated.

## D. Questions to settle when integration starts

- **Instruction channel.** How the app sends SCLC a job (push, or SCLC polls the
  queue), and how SCLC reports progress or failure back (for example the
  existing `recording_failed` action).
- **Authentication.** How a non-browser client authenticates. Every route above
  accepts only an admin session cookie.
- **Job identity and claiming.** How SCLC claims one job so two recorders or an
  operator do not record the same job.
- **Upload path.** Whether SCLC uses the grant / confirm / cancel routes as they
  are (one attempt per job, create-only PUT, `expected_version`,
  `replace_after`).
- **File format and limits.** MP4/MOV, the 750 MiB cap, and whether a capture
  package or capture-contract result travels with the file
  (`src/splatoon3_ai_coach/capture/CAPTURE_CONTRACT.md`).
- **Storage mode.** Production uses local storage today, so uploads go through
  the site. If source video moves to R2, the presigned-replay debt applies
  (`docs/follow-ups/source-video-presigned-replay.md`).

## E. Reference: the removed web capture station

An experimental capture station (ffmpeg inside the web server) was removed on
2026-11-12. It could only run on the capture Mac, never in production. Its code
is at commit `f4e1812`, kept as a historical reference only: it is not a
dependency, and SCLC should not restore that architecture. Pieces that may still
help SCLC's own capture code, all in `tools/public_site/recording_station.py` at
that commit:

- `parse_avfoundation_devices`: lists capture devices from
  `ffmpeg -f avfoundation -list_devices true -i ""`.
- `discover_capture_modes`, `select_capture_mode`: probe the real modes and
  prefer 1920x1080, then 60, then 30 fps.
- `request_graceful_stop`: stop ffmpeg with `q` on stdin, then SIGINT, never
  SIGKILL, so the MP4 is finalized.
- `OutputTail`, `explain_capture_failure`: bounded ffmpeg diagnostics,
  missing-device and macOS permission messages.

Keep the boundary: `src/splatoon3_ai_coach/capture/` imports nothing from
`tools/public_site`.
