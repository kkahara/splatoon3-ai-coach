# Tower Control HUD development calibration

The checkpoint-position, squid-marker, and progress notes below are retained
as historical development evidence. They no longer describe the active Tower
factual-state pipeline. The current slice uses explicit control/loss messages;
see the final section for its source frames and limitations.

The supplied 1920×1080 screenshots are development references. Their red
rectangles record approximate human annotations and are not detector templates
or independent validation data.

Initial measured search regions:

- tower lane: normalized `[0.25, 0.101852, 0.75, 0.185185]`
- moving score pods: normalized `[0.234375, 0.171296, 0.765625, 0.268519]`

The detector uses the shared deterministic digit templates under
`calibration/templates`. Add clean marker or pod templates here only when a
development frame and crop provenance can be recorded alongside them.

The lane coordinate is screen geometry in this search region. It is not
gameplay distance, score distance, direction, or movement.

## Operator-labeled checkpoint development frames

`analysis/development/tower_control/makomart_112_5.png` and
`analysis/development/tower_control/makomart_113_5.png` are lossless HUD crops
(x=450–1469, y=105–204) from
`en-live-playerview-2026-10-11 18-18-00.mov`, at video times 112.5 s and
113.5 s respectively. They are development evidence, not independent
validation. The operator identified these as ally checkpoint 1: the first
shows an almost complete active progress ring and the second approximately
three quarters remaining. The icon inside is a sideways squid, not the digit 8.

The operator described the outer progress ring and the 2×2 checkpoint symbol
as distinct cues: the active checkpoint symbol can be obscured by the squid,
while a visible 2×2 symbol can describe progress on a later arrival. The ring
vanishing in this sequence alone does not establish that the checkpoint was
cleared. These two examples do not establish a complete progress scale or a
successful checkpoint-clear transition.

## Candidate progress and clear sequence

`analysis/development/tower_control/makomart_candidate_165_5.png` through
`analysis/development/tower_control/makomart_candidate_176_5.png`
are 1020×220 crops (x=450–1469, y=95–314) from the same recording, sampled
at the video times in their names. At 166.5–169.5 s the active ring shrinks;
the marker is subsequently seen beyond that location and the screen displays
“We're past checkpoint!” at 170.5 s. This is a candidate clear, not yet a
confirmed state transition. The orange main score reads 80, 80, 78 and 76
while the ring is present at 166.5–169.5 s. The operator corrected the earlier
score assumption: score is an independent observation and is not a criterion
for active progress or checkpoint clearing.

`analysis/development/tower_control/museum_symbol_a.png` and
`analysis/development/tower_control/museum_symbol_b.png` are native-resolution crops
from `museum_30.png` in `tests/fixtures/tower_control/`, from
`en-live-playerview-2026-10-11 22-20-42.mov` at 30 s. Their bounds within
the original video are x=562–606, y=132–176 and x=697–751, y=127–181,
respectively. The visual roles of these two appearances remain unconfirmed.

`tools/diagnostics/tower_ring_probe.py` compares colored angular occupancy
with 2×2 annular quadrant occupancy using a manually specified crop center
and hue; it does not detect the ring or emit gameplay facts. For the two
operator-labeled frames, center (612, 50) and OpenCV hue 134 give radial
occupancies 0.972 and 0.778, and respectively four and three quadrants above
the probe's development threshold. With center (612, 60) on the candidate
sequence, quadrant counts at 166.5–169.5 s are 3, 3, 2, 1; radial
occupancies are 0.639, 0.653, 0.458, 0.208. The early candidate frames are
not individually labeled and no threshold or method is independently
validated. Manual hue, center, fixed radius and background interference are
open limitations; these measurements are not exact progress percentages.

The English cue prototype uses `en/passed_checkpoint_template.png`, a binary
mask cropped from the phrase at x=690–1362, y=283–341 of the same recording
at 170.5 s. `analysis/development/tower_control/en/passed_development.png` and
`analysis/development/tower_control/en/at_checkpoint_development.png` are the message region x=300–1599,
y=250–379 at 170.5 s and 167.5 s, respectively. The first is also the
template source: this is a self-match, not an independent test. The second
is also the source of `en/at_checkpoint_template.png`, cropped at x=738–1319,
y=283–341. These are development self-matches. The opt-in Tower detector
reports English at/passed text candidates without attributing team or checkpoint;
no Japanese template has been calibrated. The operator confirmed that the
at-checkpoint message supports an uncleared state.

An exploratory run on the same recording detected the cue at 170.5, 171.0,
171.5 and 172.0 s and did not detect it at 166.5–170.0 s (the checked
pre-cue times were 166.5, 167.5, 168.5, 169.5 and 170.0 s). These neighboring
frames share the template's source sequence and are development evidence only;
the 0.93 threshold has not been independently assessed. Vision fusion accepts
the cue only with a prior and current matching team and configured checkpoint
location, using the same stage and a maximum two-second observation gap.
The at-checkpoint cue can establish uncleared with matching same-frame team
and configured position. Missing stage, location, team, cue, or the necessary
prior direct observation leaves the relevant state unknown. The cue
observation time is not an exact checkpoint-clear time.

The MakoMart crop shows approximate fixed symbol centers at source-frame
x=564, 659, 858, 1062, 1261, and 1355 pixels (1920-pixel source width).
The operator confirmed that the three locations to the right of center are
ally attack checkpoints 1–3 in outward order, and those to the left are the
opponent attack checkpoints 1–3 in outward order. These six locations are
configured for MakoMart as development calibration and have not been
independently validated across recordings.

A one-second development scan of 165.5–176.5 s detects ally checkpoint 1
as uncleared at 166.5 s and cleared at 170.5 s; later observations retain
cleared. This run used manually supplied team-color calibration (ally hue 10,
opponent hue 143 in OpenCV units), not a measured Ready-window calibration.
The marker hue sample was narrowed to avoid nearby colored HUD contamination;
its behavior on other footage remains to be evaluated.

The same transition was reproduced through snapshot fusion using the existing
MakoMart vision manifest, adding freshly detected Tower observations at the
manifest's half-second cadence from 165–172 s. The run was in memory and did
not rewrite the stored manifest. Its team-color calibration was again supplied
manually; it is a development check, not an end-to-end calibration validation.

Checkpoint-progress estimation is on hold. Its previous development crops,
notes and unregistered experimental code are retained for possible later work
and are not consumed by the detector or state fusion.

## Explicit Tower control/loss message development

All examples are English development evidence, not independent validation.
The stored message crops cover source pixels x=300–1599 and y=250–379.
Template masks are cropped and thresholded from these development frames:

- `en/control/ally_took.png`: `en-2026-07-05 18-02-24.mov`, 58.5 s;
  alternate `ally_took__hammer_76.png`: same recording, 76.5 s.
- `en/control/ally_lost.png`: same recording, 67.5 s; the map is visible.
- `en/control/opponent_took.png`: `en-live-playerview-2026-10-11 18-18-00.mov`,
  30.0 s; alternate `opponent_took__hammer.png`: Hammerhead recording, 103.5 s.
- `en/control/opponent_lost.png`: the MakoMart recording, 143.0 s.

The exact observed English opponent messages are “They took the tower!” and
“They lost the tower!”. Opponent-loss detection now uses localized English and
Japanese templates. A grouped opponent-loss observation now establishes
`returning` in the Tower control-state fuser. A
development scan of Hammerhead at
0.5-second samples from 55–105 s observed ally took at 58.5, ally lost at
66.5–67.5, ally took at 76.5, and opponent took at 103.5 s. These frames
overlap the template source material, so this is not independent validation.
The matcher failed on several nearby frames where text was visible, and a
missed message can leave held control state stale. Source cue time represents
the first accepted observation, not an exact gameplay-transition timestamp.

### Opponent-loss cue development, November 2, 2026

The Japanese `ja/control/opponent_lost.png` template and source crop are from
`ja-tower-2026-09-20 14-39-55.mov` at 186.5 s. The operator supplied the
interpretation of `ガチヤグラを もどした！`. A second Japanese development
positive is from `ja_2026-10-28 21-00-53.mov` at 79.5 s; nearby other Tower
messages, including `ガチヤグラを もどされた！` at 202.5 s, are negative
development examples. The English alternate opponent-loss masks come from
Museum at 55 s, Flounder at 85 s, and Urchin at 71 s from the recordings
listed in the checkpoint review. These templates use their own development
frames and are not independent validation.

At 0.5-second sampling, one or more positive frames were detected in six of
seven selected English displays and both selected Japanese displays. The
Urchin 223 s example, with other graphics overlapping the message, was missed.
A 55–105 s Hammerhead sample and selected Japanese nonmatching-message windows
produced no opponent-loss positives. The sample is small and selected, so
these observations are not accuracy estimates. Development sampling also
missed visible animation frames in the labeled 186 s Japanese window.
