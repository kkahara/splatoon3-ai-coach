"""Content policy: planted values block publication; real shapes pass."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from fake_s3_helpers import fake_s3
from public_site.models import Submission, VideoInput
from public_site.report_bundle import BundleError, key_paths, known_values, prepare_bundle
from public_site.report_content import (
    ContentFinding,
    KnownValues,
    scan_jpeg,
    scan_json,
    scan_mp4,
)
from report_bundle_fixture import (
    IDENTIFIER_LEAVES,
    INVENTORY,
    REPRESENTATIVE,
    add_media,
    lavfi_clip,
    lavfi_jpeg,
    write_report_fixture,
)
from report_test_helpers import complete_submission, methods, report_client

SID = "b" * 32


def _settings_like(**secrets):
    class _S:
        pass

    holder = _S()
    for name in (
        "r2_access_key_id",
        "r2_secret_access_key",
        "smtp_password",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "voucher_hmac_secret",
        "turnstile_secret",
        "database_url",
    ):
        setattr(holder, name, secrets.get(name, ""))
    return holder


def _known() -> KnownValues:
    submission = Submission(
        id=SID,
        access_token_hash="h" * 64,
        email="player.one@example.org",
        display_name="InklingAce",
        user_id="user-7f3a9c",
        created_at="2026-11-09T00:00:00Z",
        updated_at="2026-11-09T00:00:00Z",
        expires_at="2026-12-09T00:00:00Z",
    )
    video = VideoInput(
        submission_id=SID,
        object_key="submissions/x/source.mp4",
        original_filename="Switch_Capture_20261109.mp4",
        size_bytes=1,
        content_type="video/mp4",
    )
    settings = _settings_like(r2_secret_access_key="r2-secret-abcdefgh")
    return known_values(
        settings, submission, video, None, {"review_code": "RVW-CODE-1234"}
    )


@pytest.fixture
def analysis(tmp_path: Path) -> Path:
    folder = tmp_path / SID
    write_report_fixture(folder)
    add_media(folder)
    return folder


def _prepare(analysis: Path, known: KnownValues | None = None):
    return prepare_bundle(
        analysis,
        submission_id=SID,
        run_id="20261109T120000Z-0000000c",
        outcome="coached",
        created_at="2026-11-09T12:00:00Z",
        known=known or _known(),
        staging=None,
    )


def test_clean_fixture_passes(analysis: Path) -> None:
    prepared = _prepare(analysis)
    paths = {item.path for item in prepared.files}
    assert "coach_inputs/coach_inputs_meta.json" not in paths
    assert not any(p.endswith((".jsonl", ".raw.txt")) for p in paths)


def _rewrite(path: Path, change) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


PLANTS = [
    (
        "vision_manifest.json",
        "forbidden_key:victim_name",
        lambda p: p["frame_results"][0]["detections"][0]["reading"].update(
            victim_name="OtherPlayer"
        ),
    ),
    (
        "vision_manifest.json",
        "forbidden_key:ocr_text",
        lambda p: p["frame_results"][0]["detections"][1]["reading"].update(
            ocr_text="Splatted by OtherPlayer"
        ),
    ),
    (
        "match_identity.json",
        "forbidden_key:email",
        lambda p: p.update(email="someone@example.com"),
    ),
    (
        "coach_inputs/death_episode_0.5.coaching.json",
        "email_address",
        lambda p: p.update(note="contact me at coach.person@mailhost.net"),
    ),
    (
        "coach_inputs/death_episode_0.5.llm_view.json",
        "absolute_path",
        lambda p: p.update(note="/home/runner/work/analysis"),
    ),
    (
        "coach_inputs/death_episode_0.5.coach_input.json",
        "absolute_path",
        lambda p: p.update(path="C:\\Users\\player\\video.mp4"),
    ),
    (
        "coach_prototype/death_episode_0.5.model.output.json",
        "known_value:display_name",
        lambda p: p.update(assessment="Nice push, InklingAce."),
    ),
    (
        "coach_prototype/death_episode_0.5.model.flags.json",
        "known_value:user_id",
        lambda p: p.update(note="user-7f3a9c"),
    ),
    (
        "coach_prototype/death_episode_0.5.llm_view.json",
        "known_value:original_filename",
        lambda p: p.update(note="Switch_Capture_20261109"),
    ),
    (
        "coach_inputs/coaching_index.json",
        "known_value:review_code",
        lambda p: p.update(note="RVW-CODE-1234"),
    ),
    (
        "coach_prototype/coaching_index.json",
        "secret:r2_secret_access_key",
        lambda p: p.update(note="r2-secret-abcdefgh"),
    ),
    (
        "coach_inputs/death_episode_0.5.coaching.json",
        "forbidden_key:access_token",
        lambda p: p.update(access_token="tok"),
    ),
]


@pytest.mark.parametrize(("path", "rule", "change"), PLANTS, ids=[p[1] for p in PLANTS])
def test_planted_value_blocks_with_its_rule(analysis: Path, path, rule, change) -> None:
    _rewrite(analysis / path, change)
    with pytest.raises(BundleError) as caught:
        _prepare(analysis)
    assert (caught.value.kind, caught.value.rule) == ("content_policy", rule)


def test_planted_value_in_a_real_completion_sends_zero_puts(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        _token, submission = complete_submission(client, tmp_path)
        _rewrite(
            Path(submission.analysis_dir) / "match_identity.json",
            lambda p: p.update(display_name="Planted"),
        )
        publisher = client.app.state.reports
        while publisher.run_once():
            pass
        record = publisher.publications.get(submission.id)
        assert (record.state, record.blocked_rule) == (
            "blocked",
            "forbidden_key:display_name",
        )
        assert "PUT" not in methods(fake)


def test_jpeg_metadata_is_blocked_but_the_encoder_comment_is_allowed(
    tmp_path: Path,
) -> None:
    still = tmp_path / "still.jpg"
    lavfi_jpeg(still)
    data = still.read_bytes()
    scan_jpeg(data, KnownValues())
    exif = data[:2] + b"\xff\xe1\x00\x08Exif\x00\x00" + data[2:]
    with pytest.raises(ContentFinding, match="jpeg_metadata"):
        scan_jpeg(exif, KnownValues())
    comment = data[:2] + b"\xff\xfe\x00\x0bmy house" + data[2:]
    with pytest.raises(ContentFinding, match="jpeg_metadata"):
        scan_jpeg(comment, KnownValues())
    iptc = data[:2] + b"\xff\xed\x00\x04ab" + data[2:]
    with pytest.raises(ContentFinding, match="jpeg_metadata"):
        scan_jpeg(iptc, KnownValues())


def _progressive_jpeg() -> bytes:
    import cv2
    import numpy as np

    noise = np.random.default_rng(7).integers(0, 256, (48, 64, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", noise, [cv2.IMWRITE_JPEG_PROGRESSIVE, 1])
    assert ok
    return encoded.tobytes()


def _scan_starts(data: bytes) -> list[int]:
    """SOS offsets (``FF DA`` never occurs inside entropy-coded data)."""
    return [m.start() for m in re.finditer(rb"\xff\xda", data)]


BETWEEN_SCANS = [
    (b"\xff\xe1\x00\x08Exif\x00\x00", "jpeg_metadata"),
    (b"\xff\xed\x00\x04ab", "jpeg_metadata"),
    (b"\xff\xfe\x00\x0amy house", "jpeg_metadata"),
    (b"\xff\xfe\x00\x0eLavc61.3.100", None),
]


@pytest.mark.parametrize(
    ("segment", "rule"), BETWEEN_SCANS, ids=["app1", "app13", "comment", "encoder"]
)
def test_progressive_jpeg_segments_between_scans_are_inspected(segment, rule) -> None:
    data = _progressive_jpeg()
    starts = _scan_starts(data)
    assert len(starts) >= 2
    scan_jpeg(data, KnownValues())
    for start in starts[1:]:
        planted = data[:start] + segment + data[start:]
        if rule is None:
            scan_jpeg(planted, KnownValues())
            continue
        with pytest.raises(ContentFinding, match=rule):
            scan_jpeg(planted, KnownValues())


def test_jpeg_metadata_after_the_last_scan_is_blocked(tmp_path: Path) -> None:
    still = tmp_path / "still.jpg"
    lavfi_jpeg(still)
    data = still.read_bytes()
    assert data.endswith(b"\xff\xd9")
    before_eoi = data[:-2] + b"\xff\xe1\x00\x08Exif\x00\x00" + data[-2:]
    with pytest.raises(ContentFinding, match="jpeg_metadata"):
        scan_jpeg(before_eoi, KnownValues())


@pytest.mark.parametrize(
    "damage",
    [
        lambda d: d[:-2],
        lambda d: d + b"Exif\x00\x00trailing",
        lambda d: d[:-2] + b"\xff\xd8" + d[-2:],
        lambda d: d[: _scan_starts(d)[1] + 3],
    ],
    ids=["no_eoi", "after_eoi", "second_soi", "truncated_segment"],
)
def test_malformed_progressive_jpeg_is_invalid(damage) -> None:
    with pytest.raises(ContentFinding, match="jpeg_invalid"):
        scan_jpeg(damage(_progressive_jpeg()), KnownValues())


def test_mp4_tags_chapters_and_extra_streams_are_blocked(tmp_path: Path) -> None:
    clean = tmp_path / "clean.mp4"
    lavfi_clip(clean)
    scan_mp4(clean, clean.read_bytes(), KnownValues())
    tagged = tmp_path / "tagged.mp4"
    _remux(
        clean,
        tagged,
        ["-metadata", "title=Living room", "-metadata", "location=+35.6+139.6/"],
    )
    with pytest.raises(ContentFinding, match="mp4_format_tags"):
        scan_mp4(tagged, tagged.read_bytes(), KnownValues())
    handler = tmp_path / "handler.mp4"
    _remux(clean, handler, ["-metadata:s:v:0", "handler_name=Kenji iPhone"])
    with pytest.raises(ContentFinding, match="mp4_stream_tags"):
        scan_mp4(handler, handler.read_bytes(), KnownValues())


def _remux(source: Path, dest: Path, extra: list[str]) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-c",
            "copy",
            "-movflags",
            "use_metadata_tags",
            *extra,
            str(dest),
        ],
        check=True,
    )


def test_strict_email_rule_ignores_hashes_and_detector_versions() -> None:
    harmless = {
        "id": f"{'a' * 64}:result:f1:splat:splat@1.2.3+9c9e9e:{'b' * 16}",
        "version": "player_count@2.0.0",
        "fingerprint": f"{'e3b0c442' * 4}@{'27ae41e4' * 4}",
    }
    scan_json(json.dumps(harmless).encode(), KnownValues())
    with pytest.raises(ContentFinding, match="email_address"):
        scan_json(
            json.dumps({"x": "reach a.b@coach-mail.io today"}).encode(), KnownValues()
        )


def test_short_filename_stem_is_not_a_canary() -> None:
    video = VideoInput(
        submission_id=SID,
        object_key="k",
        original_filename="sample.mp4",
        size_bytes=1,
        content_type="video/mp4",
    )
    known = known_values(_settings_like(), None, video)
    rules = [(rule, token) for rule, token in known.tokens]
    assert ("known_value:original_filename", b"sample.mp4") in rules
    assert ("known_value:original_filename", b"sample") not in rules


def test_fixture_covers_every_identifier_path_in_the_inventory(analysis: Path) -> None:
    from public_site.report_ops import category_of, normalize_key_path

    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    fixture: dict[str, set[str]] = {}
    for path in sorted(analysis.rglob("*.json")):
        relative = path.relative_to(analysis).as_posix()
        category = category_of(relative)
        if category is None:
            continue
        found = key_paths(json.loads(path.read_text(encoding="utf-8")))
        fixture.setdefault(category, set()).update(normalize_key_path(p) for p in found)
    missing = {}
    for category, paths in inventory.items():
        required = {
            p
            for p in paths
            if p.rsplit(".", 1)[-1].replace("[]", "") in IDENTIFIER_LEAVES
        } | REPRESENTATIVE.get(category, set())
        gap = required - fixture.get(category, set())
        if gap:
            missing[category] = sorted(gap)
    assert missing == {}


def test_inventory_holds_key_names_only() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    segment = re.compile(r"^(?:[a-z][a-z0-9_]*|<key>)?(\[\])*$")
    for paths in inventory.values():
        for path in paths:
            assert all(segment.fullmatch(part) for part in path.lstrip(".").split(".")), (
                path
            )


def test_bundle_json_has_no_identity_or_personal_fields(analysis: Path) -> None:
    prepared = _prepare(analysis)
    body = json.loads(prepared.bundle_bytes)
    names = key_paths(body)
    assert not any(
        leaf in path
        for path in names
        for leaf in ("analysis_id", "video_identity", "email")
    )
    assert body["format"] == "s3coach.report_bundle.v1"
    assert body["content_policy_version"] == 1
