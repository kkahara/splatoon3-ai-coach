# Tower Control HUD observations

Enable the opt-in `tower_control` detector for development recordings. It
observes score pods and localized HUD messages independently. A score pod
retains its screen geometry, color evidence, digit scores, and a numeric value
when usable. Single-digit score reading and score-to-team association remain
unvalidated; messages do not alter score readings.

## Control messages and state

The English development templates recognize “We took the tower!”, “They took
the tower!”, “We lost the tower!”, and “They lost the tower!”. The Japanese
development template recognizes the operator-labeled opponent-loss cue
`ガチヤグラを もどした！`. A positive `TowerControlReading.control_cue` retains the cue type,
team association, and template score; its enclosing `DetectorResult` and
`VisionFrameResult` retain detector ID, frame ID, and video timestamp.
Localized template selection uses the configured vision language. Japanese
templates for other Tower-control messages are outside this slice.

`VisionManifest.tower_opponent_lost_observations` groups positive opponent-loss
samples separated by at most one video second. It retains each frame reference,
timestamp, detector evidence ID, and configured language. `first_video_time`
is the observation timestamp, not the exact gameplay-transition time. An
intervening different recognized Tower-control message ends the group. This groups nearby
observations, not a GameEvent or proof of a continuous display; long gaps can
split a display and two close displays can be grouped. Raw frame detections
remain available. Refusion rebuilds the grouped observations without decoding.

`VisionManifest.tower_took_observations` applies the same bounded grouping and
source-provenance contract to “We took the tower!” and “They took the tower!”.
Their semantic observations are language-neutral (`ally_took_tower` and
`opponent_took_tower`); each source sample retains its localized cue and
language. The first accepted positive is the observation time, not an exact
gameplay control-change time.

In an in-match Tower snapshot, `tower_control_state` is `ally_control`,
`opponent_control`, `returning`, or `unknown`. A grouped took cue sets the respective
team control; the ally-loss cue and grouped opponent-loss observation set
`returning`, per operator-supplied game semantics. The opponent-loss state
transition uses the group's first accepted observation time. Its state evidence
contains the grouped samples observed by that snapshot; later samples add
support without changing that time. State persists until another recognized cue, and
`tower_control_evidence` holds the direct cue and source that established the
current state. The cue timestamp is the observation time, not a claim of an
exact gameplay transition time. Messages can be observed over the map.

The English matcher is development-only. Its templates come from a small set
of recordings and its strict threshold misses some visible messages. Missing
a cue can leave the held state stale. There is no independent validation yet;
the current held state must not be presented as verified continuous control.

## Parked historical work

The active detector and state fusion do not use the squid marker, marker hue,
marker-position comparisons, checkpoint lane locations, or checkpoint state.
Legacy models and experimental helpers remain for artifact compatibility and
historical debugging; they are not consumed by the active pipeline. Raw
checkpoint-message observations remain separate, without checkpoint identity
or state inference. Checkpoint sequencing, progress estimation, events,
scenarios, and coaching are outside this slice.
