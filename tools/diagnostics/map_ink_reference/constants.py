"""Frozen Amendment 3 constants (literal values from the sealed text).

None of these values is read from production configuration. Changing any of
them requires a new preregistration amendment.
"""

from __future__ import annotations

from fractions import Fraction

AMENDMENT3_COMMIT = "228e03774c2d5e9737f35698ea42cc11a0937a87"
# Amendment 4 (clarifications of Amendment 3); the preregistration file as
# sealed by this commit is the normative text the tooling implements.
AMENDMENT4_COMMIT = "570f188c3a2322c20065b095d740a6ad2538c57a"
PREREGISTRATION_COMMIT = AMENDMENT4_COMMIT
PREREGISTRATION_PATH = (
    "analysis/review_validation/alignment/v4/DEVELOPMENT_PREREGISTRATION.md"
)
PREREGISTRATION_SHA256 = (
    "4ffeeaf899185d84beea1e97c59b0baa46798cb7390cf294fae3343804e757e1"
)

# Committed records and gitignored working artifacts.
RECORDS_DIR = "analysis/review_validation/alignment/v4/amendment3"
WORK_DIR = "analysis/_amendment3"

# Amendment 4 step 2a records (committed in this order).
PILOT_SOURCE_MANIFEST = "pilot_source_manifest.json"
PILOT_BLIND_INPUT_MANIFEST = "pilot_blind_input_manifest.json"
BLIND_INPUT_MANIFEST = "blind_input_manifest.json"

# Amendment 4 schemas, rule identifiers and literals.
BLIND_INPUT_SCHEMA = "amendment3-blind-input/v1"
PILOT_SOURCE_SCHEMA = "amendment3-pilot-sources/v1"
INTRO_LATCH_RULE = "amendment4-intro-latch-v1"
SCOPE_CORPUS = "corpus"
SCOPE_PILOT = "pilot"
PILOT_AUTHORIZATION = "development/pilot media; not evaluation, held-out or corpus media"
Q_ALL_UNDEFINED = "undefined"

# Frame population and selection.
CADENCE_STEP = Fraction(1, 2)
STAGE_FRAME_COUNT = 8

# Point sampling.
POINTS_PER_FRAME = 9
MIN_POINT_SPACING_PX = 32
POINT_SEED_PREFIX = "amendment3-points|"
DUPLICATE_SEED_PREFIX = "amendment3-dups|"
# Duplicate rate 0.10 is applied as an exact integer ceiling: ceil(N / 10).
DUPLICATE_RATE_DENOMINATOR = 10
DUPLICATE_MIN_GAP = 50

# Production candidate gate for the point domain (literal sealed values).
DOMAIN_S_MIN = 70
DOMAIN_V_MIN = 40

# Frozen swatch-quality requirement of the reference procedure.
SWATCH_S_MIN = 70
SWATCH_V_MIN = 40
SWATCH_DISPLAY_SIZE = 9

# Display (crop size and zoom may change after the pilot within these limits).
DEFAULT_CROP_SIZE = 96
MIN_CROP_SIZE = 64
MAX_CROP_SIZE = 160
DEFAULT_ZOOM = 4
PAD_GREY_BGR = (128, 128, 128)

# Vocabulary.
LABELS = ("ink_A", "ink_B", "not_ink", "cant_tell")
CANT_TELL = "cant_tell"
SWATCH_IDS = ("A", "B")
TEAM_ANSWERS = ("A", "B", "cant_tell")

# Judgeability.
MIN_JUDGEABLE_POINTS_PER_FRAME = 6
MIN_JUDGEABLE_FRAMES_STAGE1 = 6
MIN_STAGE2_ADDED_FRAMES = 2
MIN_COMBINED_FRAMES = 6
MIN_EVALUABLE_RECORDINGS = 4

# Duplicate admissibility gate.
GATE_CAP = 0.10
GATE_CONFIDENCE = 0.95

# Per-look significance levels.
ALPHA_REGRESSION_PER_LOOK = 0.005
ALPHA_IMPROVEMENT_PER_LOOK = 0.025

# Corpus.
CORRECTNESS_APPLICABLE = (
    "18-18-00",
    "20-33-31",
    "22-31-53",
    "22-37-47",
    "22-57-30",
)
COVERAGE_ONLY = ("22-20-42", "18-25-16")

STAGE_1 = 1
STAGE_2 = 2
PURPOSE_EVALUATION = "evaluation"
PURPOSE_PILOT = "pilot"
