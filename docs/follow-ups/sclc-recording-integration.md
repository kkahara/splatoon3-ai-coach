# SCLC recording integration (deferred)

Status: **deferred until SCLC is ready.** Nothing here is implemented or
scheduled. This note records the current recording workflow, why the web
capture station was removed, and what may help when SCLC integration starts.

## A. Current workflow

1. The operator records the Splatoon 3 replay with OBS on the capture Mac.
2. In the admin console, the operator opens **Recordings** and selects the job.
3. The upload panel (`UploadPanel` in
   `web/public/src/pages/AdminRecordings.jsx`) takes one MP4 or MOV file.
4. The browser asks for an upload grant
   (`POST /api/admin/recordings/{id}/upload`). `RecordingIntake.request_upload`
   checks the declared name, type and size (`_declared_file`,
   `max_video_bytes`), and `RecordingStore.grant_upload` opens or reuses the
   job's one upload attempt (state `recording`). The site returns a presigned
   PUT for the fixed key `submissions/<id>/source.mp4` or `.mov`. The operator's
   filename is never used as the key.
5. The browser PUTs the bytes to storage, then confirms
   (`POST /api/admin/recordings/{id}/upload/confirm`).
   `RecordingIntake.confirm_upload` checks the stored object with a HEAD, the
   attempt id, the media probe and the SHA-256. `verify_upload` moves the job to
   `uploading`, and `queue_for_analysis` queues the same submission.
6. The worker admits the job only when `RecordingIntake.admits` and
   `recording_publish.remote_agrees` agree with the verified fingerprint, and it
   refuses a downloaded copy that fails `downloaded_matches`. Analysis,
   coaching, the report handoff and the customer status are unchanged.

The backend (Postgres `review_code_acquisitions` plus its events) is the
source of truth for job and processing status. The admin Recordings page and
its routes exist only when `PUBLIC_REVIEW_CODES_ENABLED` is set. Production
does not set it yet.

## B. Removed: the web capture station

The capture station ran ffmpeg (avfoundation) inside the web server process
and wrote the take to a local scratch directory. That only works when the site
runs on the capture Mac. Production runs on the Hetzner server, and recording
happens on the local Mac, so the station could never serve production. Its
removal is an intentional architectural simplification, not an unfinished
feature. The manual OBS upload above is the production recording workflow.

Removed: `tools/public_site/recording_station.py`, the station routes
(`/api/admin/recording-station/devices`, `/api/admin/recordings/{id}/station*`),
`RecordingStore.begin_capture` / `release_capture`,
`recording_publish.publish_finalized`, the `StationPanel` UI, and the settings
`PUBLIC_RECORDING_STATION` and `PUBLIC_STATION_DIR`. Old `capture_started` and
`capture_discarded` audit events, if any, still display as plain history lines.

**Recovery reference.** The removed code is available in git at commit
`f4e1812` (the last commit that contains it). That commit is a historical
recovery reference only. It is not a dependency of anything, and it does not
mean SCLC should restore the station architecture.

## C. What exists, and how reusable it is

File and function names below are at `f4e1812` unless marked as current.

| Area | Implementation | Coupling | For SCLC |
|---|---|---|---|
| Device discovery | `parse_avfoundation_devices` (parses `ffmpeg -f avfoundation -list_devices true -i ""`); `RecordingStation.devices` | Pure function; needs the `ffmpeg` executable on the Mac | Directly reusable |
| Capture modes | `parse_capture_modes`, `discover_capture_modes`, `classify_probe` (probe at 1 fps so ffmpeg lists real modes); `select_capture_mode`, `_request_rate` (1080p, then 60 / 30 fps, within avfoundation's 0.01 fps slack) | Pure, except `classify_probe` raises the site's `RequestRejected` | Reusable; replace `RequestRejected` with SCLC's own errors |
| FFmpeg command | `_capture_argv`: video only (`:none`), `h264_videotoolbox`, 8 Mb/s, `yuv420p`; `ffmpeg` found on `PATH` | Pure | Adapt (no audio; settings were never characterized against the capture contract) |
| Start / stop | `RecordingStation.spawn` (own process group), `request_graceful_stop` (`q` on stdin, then SIGINT, never SIGKILL) | `request_graceful_stop` is pure; `start` also writes the job's database state | Reuse `request_graceful_stop`; do not carry the database coupling forward |
| Recording state | Per-job `recording.json` scratch record: `capturing`, `interrupted`, `finalized`, `upload_uncertain`, `verified`; `recover` and `_pid_owns` stop leftover ffmpeg on restart | Local files plus the web process's threads and locks | Adapt the state machine idea only |
| Duration and limits | Elapsed time from `started_at`; size warning at 650 MiB, stop at 700 MiB or 720 s; final duration from the site's media probe | Limits are local constants; the probe is `SubmissionService.probe` | Adapt; take limits from the upload contract (current: `PUBLIC_MAX_VIDEO_BYTES`, default 750 MiB) |
| Diagnostics | `OutputTail` (drains ffmpeg output), `diagnostic_text`, `explain_capture_failure`; device-missing and macOS camera-permission messages | Pure | Directly reusable |
| Local files | `<station_dir>/<submission_id>/source.mp4` plus `recording.json`; SHA-256 via `storage.file_sha256`; the file is deleted after publication | Paths came from the removed settings | Adapt to SCLC's own output layout |
| Upload | Station: `publish_finalized` stored the file server-side with `storage.put_file`. **Current:** `RecordingIntake.request_upload` / `confirm_upload` / `abandon_upload`, `UploadAttempt` | `publish_finalized` needed the server's own storage credentials, so it cannot run on the Mac | Do not carry `publish_finalized` forward; the current presigned grant / confirm pair is the candidate |
| Job association | The submission id is in the URL; `expected_version` guards each step; `grant_upload` and `verify_upload` own the state changes (current) | Database, admin session | Reuse through the API, not by direct database access |
| Error handling | Current: API rejections return `{"detail": ...}` with an HTTP status, sometimes with a `reason`; a failed verification is audited as `recording_upload_rejected`. The station's own `recording_conflict` reason was removed with it | Site models | Reuse the API's responses |

SCLC's existing code, `src/splatoon3_ai_coach/capture/` (capture packages and
capture contracts), imports nothing from `tools/public_site`. Keep it that way.

## D. Future integration contract (conceptual)

1. SCLC produces a completed recording file.
2. The production backend authorizes the submission and identifies the job.
3. The file is uploaded to storage (R2 in production) through the approved
   upload mechanism.
4. The backend records the association and makes the recording available to
   the existing worker workflow.
5. The worker processes the recording.
6. The client reads the authoritative job status and report availability from
   the backend.

To confirm when integration starts (none of this exists today):

- **Authentication.** How a non-browser client authenticates. The upload routes
  accept only an admin session cookie.
- **Job identity.** How SCLC learns which submission id to use.
- **Upload path.** Whether SCLC uses the existing grant / confirm routes as
  they are (one attempt per job, create-only presigned PUT, `expected_version`).
- **File format and limits.** MP4/MOV, the size limit, and whether a capture
  package or capture-contract result travels with the file.
- **Feature flag.** The routes require `PUBLIC_REVIEW_CODES_ENABLED`.
- **Presigned replay.** The interaction with the frozen presigned-replay debt
  (`docs/follow-ups/source-video-presigned-replay.md`).

## E. Deferred decisions

SCLC integration is deferred until SCLC is ready, and so is every question in
section D. The manual OBS upload stays available after SCLC is integrated.
