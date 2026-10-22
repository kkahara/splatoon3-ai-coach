# Capture packages and capture contracts

Status: **DRAFT**. `timeline-capture-v1` is not frozen. Its thresholds are
provisional until about five controlled manual captures have been recorded
and reviewed (see `docs/capture/OPERATOR_CHECKLIST.md`).

## Purpose

`capture/` is the acquisition boundary. It records how a match recording was
obtained and whether it satisfies a versioned capture contract. It produces
no gameplay evidence: evidence still comes from the existing pipelines run on
the stored artifacts (`s3-coach analyze`, `s3-coach review
timeline-video-import`), which are unchanged.

Dependency direction:

```text
media, vision, review  ->  capture          (capture reuses them)
capture                -/-> analysis, coach (never imported in either direction)
```

`analysis/`, `coach/`, `review/`, `vision/`, `media/` and `alignment/` never
import `capture/` (`tests/test_capture_boundary.py`).

## Source types and aliases

| `SourceType` | Meaning | Existing names (not renamed) |
|---|---|---|
| `review_code` | Recording made by replaying a Nintendo replay (review) code on the Switch | public site `InputType="review_code"`, `SourceKind="nintendo_review_code"` |
| `uploaded_video` | A gameplay video supplied by a user | public site video upload |

`SourceType` is where the recording came from. `media.video_source.VideoSource`
(`screen_capture` / `review` / `hand_capture`) describes how a gameplay video
looks to the analyzer. The two are orthogonal and neither replaces the other.

Review codes are stored normalized as `XXXX-XXXX-XXXX-XXXX` (16 upper-case
letters/digits), the same format the public site accepts.

## Acquisition methods

`upload`, `manual` and `sclc`. A `review_code` package is `manual` or `sclc`;
an `uploaded_video` package is `upload`. **Manual and automated (SCLC)
acquisition satisfy the same contract.** The method is recorded for
provenance only. An automated capture passes only if its stored artifact
passes the same gate a manual one does.

## Package layout

```text
<package_dir>/
  capture_package.json            # CapturePackage (schema_version 1)
  artifacts/
    timeline.mov                  # byte-identical copy, read-only
    top_down_1x.mov               # optional, experimental
  quality/
    timeline-capture-v1/
      result.json                 # QualityGateResult (never edited)
      samples.json                # every adapter sample of the gate decode
      retained_samples/           # JPEGs the adapter retained
```

## Artifact rules

- An artifact is copied, its content identity (`media.video_identity`,
  sha256 of size + bytes) is compared with the source, and only then is it
  renamed into place and made read-only. The source file is only read.
- No trimming, re-encoding or remuxing anywhere in this package.
- Declared stream properties are probed at ingest
  (`media.video.probe_video_stream`): container, major brand, encoder tag,
  codec, pixel format, size, `avg_frame_rate`, `r_frame_rate`, time base,
  declared frame count, duration, audio codecs.
- One artifact per kind: `timeline`, `gameplay`, and one `top_down_<rate>`
  per declared playback rate. Duplicates are refused.

| Kind | Allowed in | Status | Contract |
|---|---|---|---|
| `timeline` | `review_code` | required | `timeline-capture-v1` (draft) |
| `top_down` | `review_code` | experimental | none (collection only) |
| `gameplay` | `uploaded_video` | required | none yet |

`top_down` artifacts carry the declared playback setting
(`playback_rate_declared`, e.g. `1x`). `effective_playback_rate` stays
`null` until it is measured from the recording; it is never assumed from the
declared setting. There is no top-down computer vision and no top-down
contract.

## Contract versioning

- `contract_status: draft` means the meaning of checks and thresholds may
  still change. Every result records contract name, contract status,
  package version and git SHA.
- Freezing requires the controlled-capture study to be reviewed. After a
  freeze, any change in meaning is a new contract (`timeline-capture-v2`);
  a frozen contract's checks never change.
- A stored `result.json` is never edited. Re-running needs `--replace` and
  rewrites the whole gate directory.

## `timeline-capture-v1` (DRAFT)

The gate decodes the timeline artifact once. Frames pass unchanged into the
existing `review.video_adapter.adapt_frames` (same classification as
`review timeline-video-import`). On the way, frame timestamps and
pixel-identical repeats are recorded and the existing `MatchIntroDetector`
runs at ~2 fps until identity latches or 90 s pass. Neither detector nor
adapter is tuned for this gate.

Statuses: `pass`, `fail`, `unknown`, `not_run`. Detector evidence is used
positively only: something not observed is `unknown`, never `fail`.

### Automatic checks

| Check | Layer | Pass | Fail | Unknown |
|---|---|---|---|---|
| `video_decodable` | technical | all frames decode | decode error or no frames | |
| `artifact_hash_verified` | provenance | stored bytes match ingest identity | mismatch | |
| `resolution_1920x1080` | technical | 1920x1080 | anything else | |
| `frame_rate_60` | technical | declared avg rate in [59, 61] | < 50 or > 70 | anything between |
| `container_codec` | technical | `.mov`/`.mp4` and `h264`/`hevc` | otherwise | |
| `duration_envelope` | technical | ≤ 180 s | | > 180 s or no duration |
| `intro_identity_observed` | content | mode and stage latched before the timeline opens | | not observed |
| `timeline_entry_observed` | content | ≥ 1 s of non-timeline frames before the first timeline frame | starts inside the timeline, or no timeline | |
| `timeline_ui_observed` | content | ≥ 1 plausible clock reading | none | |
| `left_endpoint_observed` | content | a pre-GO label or a reading ≥ regulation − 1 | regulation known, no such reading | regulation length unknown |
| `right_endpoint_observed` | content | the furthest-right reading is ≤ 0:01 | no readings | furthest right is overtime, or a regulation value > 0:01 (knockout or short sweep) |
| `return_left_endpoint_observed` | content | a left-edge reading after the furthest-right reading | none | regulation length unknown |

Regulation length comes from the intro mode (`turf_war` 180 s, ranked modes
300 s), otherwise 300 s if any 4:59+ reading exists, otherwise unknown.

The right endpoint never fails automatically. In overtime the end of
overtime is not identifiable from labels; a knockout ends with time left.
Both are confirmed by the operator.

Provisional values (all in `capture/timeline_gate.py`): fps window [59, 61],
clearly-invalid bounds 50 / 70, duration envelope 180 s, pre-timeline lead
1 s, left-edge tolerance 1 s, right-edge tolerance 1 s. The procedure itself
requires 1920x1080 at 60 fps through the known-good Cam Link 4K / OBS path;
the gate records the actual `avg_frame_rate`, `r_frame_rate`, time base and
measured timestamp rate so intended and measured values stay distinct.

### Manual checks (`not_run` until confirmed)

| Check | Layer | Operator confirms |
|---|---|---|
| `capture_path_unmodified` | provenance | OBS recording of the Cam Link 4K path; not trimmed, re-encoded or remuxed |
| `acquisition_profile_1080p60` | technical | OBS base/output 1920x1080 at 60 fps; Cam Link source at 60 fps |
| `replay_matches_declared_code` | provenance | the replay shown is the one loaded from the declared code |

### Verdict

- `fail` if any required check fails (automatic, or a confirmed manual one).
- `pass` only when every required check passes.
- `incomplete` otherwise.

`s3-coach capture confirm` records an attestation (pass/fail plus a
mandatory note) only for checks that are `unknown` or `not_run`. An
attestation never overrides an automatic pass or fail; the latest
attestation for a check wins. The package stores both the automatic verdict
and the verdict with confirmations.

### Observations (never gate a draft contract)

- **Continuity:** missing regulation seconds (count, ranges, longest run) on
  the outbound pass `[left_at, turn_at]`, on the return pass, in the union of
  both, and over the whole recording; sweep speed in clock seconds per video
  second; overtime seconds seen and missing per pass.
- **Overtime:** whether labels appear, count, maximum, first time, per-pass
  maxima, lowest regulation reading.
- **Frames:** decoded count, measured fps, maximum frame interval, intervals
  over 1.5x nominal, pixel-identical repeat fractions (whole, before the
  timeline, during each sweep).
- **Samples:** adapter status counts and implausible clock readings.

Pixel-identical repeats mean duplicated source frames only where the picture
is known to move (during sweeps); static content decodes identically too.
