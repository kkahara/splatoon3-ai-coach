# Objective inspection

The React scenario Evidence section displays `objective` directly from the
explicitly exported `scenario_contexts.json` payload. The shared viewer loader
passes this dictionary through without reconstructing state from readings or
round-tripping through `ScenarioContext.model_dump()` (which excludes it).

All four modes are supported: Splat Zones, Tower Control, Rainmaker and Clam
Blitz. The panel labels actual sample times, provided observed/held qualities,
state-establishing observation times, and persisted endpoint progress results.
Clam counts/penalties are fresh observations; null penalties remain unknown.
Rainmaker checkpoint unknown never becomes uncleared. Tower progress remains
unavailable. Missing objective artifacts are explicitly unavailable.

Supporting evidence resolves by exact detector ID or source-frame ID against
the existing observation payload. The navigation action clears gallery filters
and selects that record, then seeks the existing video player. A missing exact
record leaves the image selection empty and identifies the referenced video
time; no nearby image is substituted. Sample frames and state-establishing
frames have separate labels. Decoder PTS/time-base and available cue/template/
language metadata remain inspectable. Image paths returned by the run API must
resolve inside the run's existing media boundary.

The panel needs neither saved coaching nor an LLM call. It does not change
coaching inputs, claims, ranking, prompts, detectors, fusion, or scenario
construction. It does not regenerate artifacts. Refusion/regeneration can only
use detector readings already recorded; absent readings require detection.

React VMV is the current supported Analysis Tool interface and the target for
scenario-review UX work. The former standalone HTML renderer was retired;
`vision_manifest_viewer` now supplies shared loading, view-model, and media
helpers rather than a competing presentation. New detector reading shapes are
recognized by the shared gallery classifier without introducing objective-state
inference.

Verification: Python viewer/API tests, `npm test` under `web/vmv` for component
rendering and evidence resolution, and `npm run build` for the React bundle.
