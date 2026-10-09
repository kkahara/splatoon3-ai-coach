"""Event-lane row-template detection, cursor provenance and output split."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.review.cursor import detect_cursor
from splatoon3_ai_coach.review.event_lane import (
    SPECIAL_TEMPLATE_FILES,
    deduplicate_events,
    extract_lane_events,
    kit_consistency_warnings,
    observe_lane,
)
from splatoon3_ai_coach.review.lane_glyphs import ROW_SLOTS, load_lane_templates
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    CursorObservation,
    ReviewTimelineSample,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "calibration" / "templates" / "review" / "specials"
FIXTURES = ROOT / "tests" / "fixtures" / "review"
WIDTH, HEIGHT = 1920, 1080
AXIS = AxisCalibrationEvidence(
    slope=-300.0,
    intercept=300.0,
    point_count=10,
    mean_absolute_error_seconds=0.0,
    max_absolute_error_seconds=0.0,
)


def _config(**update: object) -> ReviewTimelineConfig:
    return ReviewTimelineConfig(special_template_dir=TEMPLATES, **update)


def _template(template_id: str) -> np.ndarray:
    image = cv2.imread(str(TEMPLATES / SPECIAL_TEMPLATE_FILES[template_id]))
    assert image is not None
    return image


def _blank_frame(config: ReviewTimelineConfig) -> np.ndarray:
    image = np.full((HEIGHT, WIDTH, 3), 30, dtype=np.uint8)
    x1, y1, x2, y2 = _lane(config)
    image[y1:y2, x1:x2] = (64, 58, 56)
    return image


def _lane(config: ReviewTimelineConfig) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = config.event_lane_roi
    return int(x1 * WIDTH), int(y1 * HEIGHT), int(x2 * WIDTH), int(y2 * HEIGHT)


def _paste(
    image: np.ndarray,
    config: ReviewTimelineConfig,
    template_id: str,
    row_id: int,
    center_x: int,
    *,
    glyph: np.ndarray | None = None,
) -> tuple[int, int]:
    """Paste a template centered on a row; returns its (left, right) x."""
    glyph = _template(template_id) if glyph is None else glyph
    _, y1, _, y2 = _lane(config)
    center_y = y1 + config.event_lane_row_centers[row_id] * (y2 - y1)
    height, width = glyph.shape[:2]
    top = int(round(center_y - height / 2))
    left = int(center_x - width // 2)
    image[top : top + height, left : left + width] = glyph
    return left, left + width


def _draw_cursor(image: np.ndarray, x: int) -> None:
    image[:, x - 1 : x + 2] = 255


def _sample(sample_id: str, cursor_x: float | None, index: int) -> ReviewTimelineSample:
    return ReviewTimelineSample(
        sample_id=sample_id,
        image_path=f"{sample_id}.png",
        image_sha256="0" * 64,
        capture_index=index,
        clock_confidence=1.0,
        cursor=CursorObservation(
            x=None if cursor_x is None else int(cursor_x),
            x_norm=None if cursor_x is None else cursor_x / WIDTH,
            confidence=1.0,
        ),
    )


def _labels(image: np.ndarray, config: ReviewTimelineConfig, cursor_x=None) -> list:
    return [
        (o.candidate.slot_id, o.label, o.candidate.center_x)
        for o in observe_lane(image, "s", cursor_x, config)
    ]


def test_template_files_match_mapping_and_all_load() -> None:
    mapping = yaml.safe_load((TEMPLATES / "mapping.yaml").read_text(encoding="utf-8"))
    assert mapping == SPECIAL_TEMPLATE_FILES
    assert len(SPECIAL_TEMPLATE_FILES) == 20
    loaded = load_lane_templates(TEMPLATES, 1080, "whiteness", (1.0,))
    assert {t.template_id for t in loaded} == set(SPECIAL_TEMPLATE_FILES)


def test_each_row_center_maps_to_its_slot() -> None:
    config = _config()
    image = _blank_frame(config)
    ids = ["killer_wail_5.1", "booyah_bomb", "tacticooler", "triple_splashdown"] * 2
    for row_id, template_id in enumerate(ids):
        _paste(image, config, template_id, row_id, 300 + row_id * 150)
    found = sorted(_labels(image, config), key=lambda item: item[2])
    assert [slot for slot, _, _ in found] == list(ROW_SLOTS)
    assert [label for _, label, _ in found] == ids


@pytest.mark.parametrize("representation", ["whiteness", "grayscale", "hsv_value"])
def test_both_team_tints_classify_the_same_special(representation: str) -> None:
    config = _config(event_glyph_representation=representation)
    image = _blank_frame(config)
    glyph = _template("tacticooler")
    swapped = glyph[..., ::-1].copy()
    _paste(image, config, "tacticooler", 0, 400)
    _paste(image, config, "tacticooler", 4, 800, glyph=swapped)
    labels = {slot: label for slot, label, _ in _labels(image, config)}
    assert labels["top_1"] == "tacticooler"
    if representation == "whiteness":
        assert labels["bottom_4"] == "tacticooler"


def test_scaled_glyph_matches_with_scale_grid() -> None:
    config = _config(special_template_scales=(0.95, 1.0, 1.05))
    image = _blank_frame(config)
    big = cv2.resize(_template("big_bubbler"), None, fx=1.05, fy=1.05)
    _paste(image, config, "big_bubbler", 2, 600, glyph=big)
    [observation] = observe_lane(image, "s", None, config)
    assert observation.label == "big_bubbler"
    assert observation.score.scale == pytest.approx(1.05)


def test_adjacent_glyphs_on_one_row_stay_separate() -> None:
    config = _config()
    image = _blank_frame(config)
    _, right = _paste(image, config, "ink_vac", 1, 500)
    width = _template("wave_breaker").shape[1]
    _paste(image, config, "wave_breaker", 1, right + 2 + width // 2)
    labels = [label for _, label, _ in sorted(_labels(image, config), key=lambda i: i[2])]
    assert labels == ["ink_vac", "wave_breaker"]


def test_two_rows_at_the_same_x_stay_separate_events() -> None:
    config = _config()
    image = _blank_frame(config)
    _paste(image, config, "ink_storm", 0, 700)
    _paste(image, config, "ink_storm", 2, 700)
    markers, specials = extract_lane_events(
        {"a": image}, [_sample("a", None, 0)], AXIS, config
    )
    assert sorted(e.slot_id for e in specials) == ["top_1", "top_3"]
    assert markers == []


@pytest.mark.parametrize("anchor", ["center", "left", "right"])
def test_timestamp_uses_configured_anchor(anchor: str) -> None:
    config = _config(event_timestamp_anchor=anchor)
    image = _blank_frame(config)
    left, right = _paste(image, config, "inkjet", 3, 900)
    _, [event] = extract_lane_events({"a": image}, [_sample("a", None, 0)], AXIS, config)
    x = {"left": left, "right": right, "center": (left + right) / 2}[anchor]
    assert event.timestamp_anchor == anchor
    assert event.elapsed_estimate == pytest.approx(AXIS.elapsed_at(x / WIDTH), abs=0.2)


def test_deduplicate_merges_supporting_samples() -> None:
    config = _config()
    image = _blank_frame(config)
    _paste(image, config, "crab_tank", 5, 1000)
    _, [event] = extract_lane_events({"a": image}, [_sample("a", None, 0)], AXIS, config)
    twin = event.model_copy(
        update={"x_norm": event.x_norm + 0.001, "supporting_sample_ids": ["b"]}
    )
    [merged] = deduplicate_events([event, twin], config)
    assert merged.supporting_sample_ids == ["a", "b"]


def test_low_margin_is_retained_as_unknown() -> None:
    config = _config(special_match_margin=0.99, death_match_margin=0.99)
    image = _blank_frame(config)
    _paste(image, config, "zipcaster", 6, 700)
    [(_, label, _)] = _labels(image, config)
    assert label == "unknown"


def test_kit_conflict_warns_without_rewriting_events() -> None:
    config = _config()
    image = _blank_frame(config)
    _paste(image, config, "trizooka", 7, 500)
    _paste(image, config, "ultra_stamp", 7, 1200)
    _, specials = extract_lane_events({"a": image}, [_sample("a", None, 0)], AXIS, config)
    assert sorted(e.special_id for e in specials) == ["trizooka", "ultra_stamp"]
    assert kit_consistency_warnings(specials) == ["special_slot_conflict:bottom_1"]


def test_cursor_absent_and_adjacent_keep_the_glyph() -> None:
    config = _config()
    image = _blank_frame(config)
    left, right = _paste(image, config, "reefslider", 4, 800)
    half = config.cursor_exclusion_half_width * WIDTH
    adjacent = int(right + half + 3)
    _draw_cursor(image, adjacent)
    assert [label for _, label, _ in _labels(image, config)] == ["reefslider"]
    assert [label for _, label, _ in _labels(image, config, adjacent)] == ["reefslider"]


@pytest.mark.parametrize("offset", ["partial", "direct"])
def test_cursor_over_glyph_suppresses_that_frame(offset: str) -> None:
    config = _config()
    image = _blank_frame(config)
    left, right = _paste(image, config, "reefslider", 4, 800)
    cursor = right - 2 if offset == "partial" else (left + right) // 2
    _draw_cursor(image, cursor)
    assert _labels(image, config, cursor) == []


def test_cursor_over_blank_row_creates_nothing() -> None:
    config = _config()
    image = _blank_frame(config)
    _draw_cursor(image, 900)
    assert _labels(image, config, 900) == []


def test_obscured_frame_recovers_from_a_clear_frame() -> None:
    config = _config()
    clear = _blank_frame(config)
    left, right = _paste(clear, config, "super_chump", 2, 800)
    covered = clear.copy()
    cursor = (left + right) // 2
    _draw_cursor(covered, cursor)
    samples = [_sample("covered", cursor, 0), _sample("clear", 1500, 1)]
    markers, [event] = extract_lane_events(
        {"covered": covered, "clear": clear}, samples, AXIS, config
    )
    assert event.special_id == "super_chump"
    assert event.supporting_sample_ids == ["clear"]
    assert event.cursor_obscured_sample_ids == ["covered"]
    assert markers == []


def test_all_obscured_stays_unavailable() -> None:
    config = _config()
    image = _blank_frame(config)
    left, right = _paste(image, config, "super_chump", 2, 800)
    cursor = (left + right) // 2
    _draw_cursor(image, cursor)
    samples = [_sample("a", cursor, 0), _sample("b", cursor + 1, 1)]
    assert extract_lane_events({"a": image, "b": image}, samples, AXIS, config) == (
        [],
        [],
    )


def test_death_best_glyph_is_a_marker_never_a_special() -> None:
    config = _config(event_glyph_representation="grayscale")
    image = _blank_frame(config)
    _paste(image, config, "death", 1, 900)
    markers, specials = extract_lane_events(
        {"a": image}, [_sample("a", None, 0)], AXIS, config
    )
    assert specials == []
    [marker] = markers
    assert marker.kind == "death"
    assert marker.player_slot_id == "top_2"
    assert marker.supporting_sample_ids == ["a"]


def test_matching_never_modifies_image_or_templates() -> None:
    config = _config()
    image = _blank_frame(config)
    _paste(image, config, "kraken_royale", 3, 600)
    templates = load_lane_templates(TEMPLATES, HEIGHT, "whiteness", (1.0,))
    before_image = image.copy()
    before_templates = [t.glyph.copy() for t in templates]
    observe_lane(image, "s", 600, config)
    assert np.array_equal(image, before_image)
    assert all(
        np.array_equal(t.glyph, b)
        for t, b in zip(templates, before_templates, strict=True)
    )


CORPUS = FIXTURES / "lane_corpus"
STACKED_DEATHS = [
    ("21-23-30/sample-000246.jpg", 1224, "top_3", 549),
    ("21-23-30/sample-000246.jpg", 1224, "top_4", 1011),
    ("21-41-07/sample-000011.jpg", 152, "top_2", 978),
]


def _deaths_near(observations: list, slot: str, x: float) -> list:
    return [
        o
        for o in observations
        if o.label == "death"
        and o.candidate.slot_id == slot
        and abs(o.candidate.center_x - x) <= 8
    ]


@pytest.mark.parametrize(("name", "cursor_x", "slot", "x"), STACKED_DEATHS)
def test_echo_rule_keeps_deaths_that_pass_acceptance(
    name: str, cursor_x: int, slot: str, x: int
) -> None:
    image = cv2.imread(str(CORPUS / name))
    assert image is not None
    legacy = observe_lane(
        image, name, cursor_x, _config(event_echo_keep_accepted_deaths=False)
    )
    assert not _deaths_near(legacy, slot, x)
    assert _deaths_near(observe_lane(image, name, cursor_x, _config()), slot, x)


def test_echo_rule_still_suppresses_neighbor_specials() -> None:
    image = cv2.imread(str(CORPUS / "21-23-30/sample-000246.jpg"))
    assert image is not None
    labels = {
        (o.candidate.slot_id, round(o.candidate.center_x / 8)): o.label
        for o in observe_lane(image, "s", 1224, _config())
    }
    legacy = {
        (o.candidate.slot_id, round(o.candidate.center_x / 8)): o.label
        for o in observe_lane(
            image, "s", 1224, _config(event_echo_keep_accepted_deaths=False)
        )
    }
    added = {k: v for k, v in labels.items() if k not in legacy}
    assert added and set(added.values()) == {"death"}


FIXTURE_KITS = {
    "timeline_21-49-20.jpg": {
        "top_1": "killer_wail_5.1",
        "top_2": "booyah_bomb",
        "top_3": "tacticooler",
        "top_4": "triple_splashdown",
        "bottom_4": "ultra_stamp",
        "bottom_3": "wave_breaker",
        "bottom_2": "ink_storm",
        "bottom_1": "triple_inkstrike",
    },
    "timeline_21-56-52.jpg": {
        "top_1": "booyah_bomb",
        "top_2": "killer_wail_5.1",
        "top_3": "triple_splashdown",
        "top_4": "big_bubbler",
        "bottom_4": "big_bubbler",
        "bottom_3": "triple_inkstrike",
        "bottom_2": "crab_tank",
    },
}


@pytest.mark.parametrize("name", sorted(FIXTURE_KITS))
def test_real_frames_only_report_each_slots_own_special(name: str) -> None:
    config = _config()
    image = cv2.imread(str(FIXTURES / name))
    assert image is not None
    observations = observe_lane(image, name, detect_cursor(image, config).x, config)
    specials = [o for o in observations if o.label not in {"unknown", "death"}]
    kit = FIXTURE_KITS[name]
    assert {o.candidate.slot_id for o in specials} >= set(kit) - {"bottom_2"}
    for observation in specials:
        assert observation.label == kit[observation.candidate.slot_id]
