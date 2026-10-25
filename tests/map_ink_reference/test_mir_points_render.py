"""Point seeds and spacing, duplicate rule, queue placement, display rendering."""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest
from map_ink_reference.constants import DUPLICATE_MIN_GAP, PAD_GREY_BGR
from map_ink_reference.domain import candidate_gate, point_domain
from map_ink_reference.errors import MalformedInput, PinMismatch
from map_ink_reference.io_utils import sha256_file
from map_ink_reference.points import (
    StageFrame,
    draw_points,
    duplicate_count,
    duplicate_point_ids,
    load_verified_png,
    point_seed,
    sample_stage_points,
)
from map_ink_reference.queueing import build_queue, duplicate_gaps
from map_ink_reference.render import (
    DisplaySettings,
    padded_crop,
    point_view,
    swatch_view,
)
from map_ink_reference.stage_identity import StageIdentity
from mir_helpers import Counter, ink_image, write_geometry


def test_point_seed_is_first_8_bytes_big_endian() -> None:
    digest = hashlib.sha256(b"amendment3-points|vid|1234").digest()
    assert point_seed("vid", 1234) == int.from_bytes(digest[:8], "big")


def test_draw_points_respects_spacing_and_count() -> None:
    domain = np.ones((240, 320), dtype=bool)
    points = draw_points(domain, 42)
    assert len(points) == 9
    for i, (x, y) in enumerate(points):
        for ax, ay in points[:i]:
            assert (x - ax) ** 2 + (y - ay) ** 2 >= 32 * 32
    assert draw_points(domain, 42) == points
    assert draw_points(domain, 43) != points


def test_draw_points_follows_pcg64_permutation_order() -> None:
    domain = np.zeros((100, 100), dtype=bool)
    domain[10, 10] = domain[90, 90] = True
    order = np.random.Generator(np.random.PCG64(5)).permutation(np.flatnonzero(domain))
    expected = [tuple(reversed(divmod(int(i), 100))) for i in order]
    assert draw_points(domain, 5) == expected


def test_draw_points_returns_fewer_when_domain_is_small() -> None:
    domain = np.zeros((50, 50), dtype=bool)
    domain[0, 0] = domain[0, 10] = True
    assert len(draw_points(domain, 1)) == 1


def test_candidate_gate_uses_literal_s_v() -> None:
    image = np.zeros((1, 3, 3), dtype=np.uint8)
    image[0, 0] = cv2.cvtColor(np.uint8([[[0, 70, 40]]]), cv2.COLOR_HSV2BGR)[0, 0]
    image[0, 1] = cv2.cvtColor(np.uint8([[[0, 69, 200]]]), cv2.COLOR_HSV2BGR)[0, 0]
    image[0, 2] = cv2.cvtColor(np.uint8([[[0, 200, 39]]]), cv2.COLOR_HSV2BGR)[0, 0]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    expected = (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 40)
    assert np.array_equal(candidate_gate(image), expected)


def test_point_domain_intersects_sample_mask(tmp_path: Path) -> None:
    geometry = write_geometry(tmp_path)
    image = ink_image(1)
    domain = point_domain(image, geometry, StageIdentity("synth", None))
    assert np.array_equal(domain, candidate_gate(image))
    with pytest.raises(MalformedInput):
        point_domain(image, geometry, StageIdentity("missing", None))


def test_sample_stage_points_uses_verified_pngs(tmp_path: Path) -> None:
    geometry = write_geometry(tmp_path)
    png = tmp_path / "f.png"
    cv2.imwrite(str(png), ink_image(2))
    frame = StageFrame("r", "f1", 500, png, sha256_file(png))
    kwargs = dict(
        video_identities={"r": "vid"},
        stage_identities={"r": StageIdentity("synth", None)},
        geometry_dir=geometry,
    )
    points = sample_stage_points([frame], **kwargs)
    assert [p["point_id"] for p in points] == [f"r|f1|{k}" for k in range(9)]
    image = cv2.imread(str(png))
    domain = point_domain(image, geometry, StageIdentity("synth", None))
    assert all(domain[p["y"], p["x"]] for p in points)
    bad = StageFrame("r", "f1", 500, png, "0" * 64)
    with pytest.raises(PinMismatch):
        sample_stage_points([bad], **kwargs)
    with pytest.raises(PinMismatch):
        load_verified_png(tmp_path / "missing.png", "0" * 64)


@pytest.mark.parametrize(
    ("n", "count"), [(0, 0), (1, 1), (10, 1), (11, 2), (30, 3), (360, 36), (361, 37)]
)
def test_duplicate_count_is_exact_ceiling(n: int, count: int) -> None:
    assert duplicate_count(n) == count


def test_duplicate_ids_seeded_by_manifest_hash() -> None:
    ids = [f"p{i}" for i in range(360)]
    first = duplicate_point_ids(ids, "a" * 64)
    assert len(first) == 36 and len(set(first)) == 36
    assert duplicate_point_ids(ids, "a" * 64) == first
    assert duplicate_point_ids(ids, "b" * 64) != first
    seed = int.from_bytes(
        hashlib.sha256(("amendment3-dups|" + "a" * 64).encode()).digest()[:8], "big"
    )
    rng = np.random.Generator(np.random.PCG64(seed))
    assert first == [ids[int(i)] for i in rng.choice(360, size=36, replace=False)]


def test_queue_places_duplicates_at_least_50_later_or_at_end() -> None:
    ids = [f"p{i}" for i in range(120)]
    dups = ids[:12]
    counter = Counter()
    queue, key = build_queue(ids, dups, 99, counter.new_id)
    assert len(queue) == 132
    gaps = duplicate_gaps(queue)
    assert len(gaps) == 12
    for ref, gap in gaps.items():
        repeat = next(
            i for i, it in enumerate(queue) if it["point_ref"] == ref and it["copy"] == 1
        )
        assert gap >= DUPLICATE_MIN_GAP or all(it["copy"] == 1 for it in queue[repeat:])
    assert sorted(key.values()) == sorted(ids)
    assert all(set(item) == {"point_ref", "copy", "item_id"} for item in queue)


def test_short_queue_puts_duplicates_at_end() -> None:
    ids = [f"p{i}" for i in range(10)]
    queue, _ = build_queue(ids, ids[:1], 3, Counter().new_id)
    assert queue[-1]["copy"] == 1


def test_queue_drops_unpresentable_duplicates() -> None:
    queue, key = build_queue(["a", "b"], ["c", "a"], 1, Counter().new_id)
    assert [key[i["point_ref"]] for i in queue if i["copy"] == 1] == ["a"]


def test_crop_padding_and_centre_pixel_untouched() -> None:
    image = ink_image(3)
    crop = padded_crop(image, 0, 0, 96)
    assert np.array_equal(crop[48, 48], image[0, 0])
    assert tuple(crop[0, 0]) == PAD_GREY_BGR
    settings = DisplaySettings()
    view = point_view(image, 100, 100, settings)
    c = 48 * settings.zoom
    block = view[c : c + settings.zoom, c : c + settings.zoom]
    assert (block == image[100, 100]).all()
    ring = view[
        c - 2 * settings.zoom + 1 : c + 3 * settings.zoom - 1,
        c - 2 * settings.zoom + 1 : c + 3 * settings.zoom - 1,
    ]
    zoomed = np.repeat(np.repeat(padded_crop(image, 100, 100, 96), 4, 0), 4, 1)
    expected = zoomed[c - 7 : c + 11, c - 7 : c + 11]
    assert np.array_equal(ring, expected)


def test_swatch_view_is_9x9_zoomed() -> None:
    image = ink_image(4)
    view = swatch_view(image, 5, 5, 4)
    assert view.shape == (36, 36, 3)
    assert np.array_equal(view[16:20, 16:20], np.tile(image[5, 5], (4, 4, 1)))


@pytest.mark.parametrize("size", [63, 161])
def test_display_settings_enforce_frozen_limits(size: int) -> None:
    with pytest.raises(MalformedInput):
        DisplaySettings(crop_size=size)
