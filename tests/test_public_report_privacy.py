"""JPEG segment allowlist, NFKC name matching, short names and non-string IDs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from public_site.models import Submission
from public_site.report_bundle import known_values
from public_site.report_content import ContentFinding, KnownValues, scan_jpeg, scan_json
from report_bundle_fixture import lavfi_jpeg
from report_test_helpers import report_settings

LAVC = b"Lavc61.19.101\x00"


def _segment(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(body) + 2).to_bytes(2, "big") + body


def _ffmpeg(tmp_path: Path) -> bytes:
    still = tmp_path / "ffmpeg.jpg"
    lavfi_jpeg(still)
    return still.read_bytes()


def _opencv(progressive: bool) -> bytes:
    import cv2
    import numpy as np

    noise = np.random.default_rng(3).integers(0, 256, (48, 64, 3), dtype=np.uint8)
    params = [cv2.IMWRITE_JPEG_PROGRESSIVE, 1] if progressive else []
    ok, encoded = cv2.imencode(".jpg", noise, params)
    assert ok
    return encoded.tobytes()


def _app0_end(data: bytes) -> int:
    assert data[2:4] == b"\xff\xe0"
    return 4 + int.from_bytes(data[4:6], "big")


def _insert(data: bytes, segment: bytes) -> bytes:
    end = _app0_end(data)
    return data[:end] + segment + data[end:]


def _thumbnail(data: bytes) -> bytes:
    """Rewrite APP0 as a JFIF header carrying a 1x1 RGB thumbnail."""
    body = bytearray(data[6 : _app0_end(data)])
    body[12:14] = b"\x01\x01"
    return (
        data[:2] + _segment(0xE0, bytes(body) + b"\x00\x00\x00") + data[_app0_end(data) :]
    )


INJECTIONS = {
    "app2_icc": lambda d: _insert(d, _segment(0xE2, b"ICC_PROFILE\x00\x01\x01")),
    "app11": lambda d: _insert(d, _segment(0xEB, b"JP\x00\x00")),
    "app14_adobe": lambda d: _insert(
        d, _segment(0xEE, b"Adobe\x00d\x00\x00\x00\x00\x01")
    ),
    "jfif_thumbnail": _thumbnail,
    "jfxx": lambda d: _insert(d, _segment(0xE0, b"JFXX\x00\x10\xff\xd8\xff\xd9")),
    "second_com": lambda d: _insert(d, _segment(0xFE, LAVC) + _segment(0xFE, LAVC)),
    "non_lavc_com": lambda d: _insert(d, _segment(0xFE, b"Created by Kenji")),
    "lavc_lookalike_com": lambda d: _insert(d, _segment(0xFE, b"Lavc61.19.101 kenji")),
    "dac": lambda d: _insert(d, _segment(0xCC, b"\x00\x10")),
}
STILLS = {
    "ffmpeg": _ffmpeg,
    "opencv_baseline": lambda _tmp: _opencv(False),
    "opencv_progressive": lambda _tmp: _opencv(True),
}


@pytest.mark.parametrize("still", list(STILLS))
def test_real_encoder_stills_pass_the_allowlist(tmp_path: Path, still: str) -> None:
    scan_jpeg(STILLS[still](tmp_path), KnownValues())


@pytest.mark.parametrize("injection", list(INJECTIONS))
@pytest.mark.parametrize("still", list(STILLS))
def test_injected_segments_are_metadata(
    tmp_path: Path, still: str, injection: str
) -> None:
    data = STILLS[still](tmp_path)
    with pytest.raises(ContentFinding, match="jpeg_metadata"):
        scan_jpeg(INJECTIONS[injection](data), KnownValues())


def test_one_lavc_comment_is_allowed_in_an_opencv_still() -> None:
    scan_jpeg(_insert(_opencv(False), _segment(0xFE, LAVC)), KnownValues())
    scan_jpeg(_insert(_opencv(False), _segment(0xFE, LAVC[:-1])), KnownValues())


def test_ffmpeg_comment_is_the_only_comment(tmp_path: Path) -> None:
    data = _ffmpeg(tmp_path)
    assert data.count(b"\xff\xfe") == 1
    with pytest.raises(ContentFinding, match="jpeg_metadata"):
        scan_jpeg(_insert(data, _segment(0xFE, LAVC)), KnownValues())


def test_jpeg_without_app0_is_allowed_but_a_second_app0_is_not() -> None:
    data = _opencv(False)
    bare = data[:2] + data[_app0_end(data) :]
    scan_jpeg(bare, KnownValues())
    with pytest.raises(ContentFinding, match="jpeg_metadata"):
        scan_jpeg(_insert(data, data[2 : _app0_end(data)]), KnownValues())


def test_temporary_marker_is_metadata() -> None:
    data = _opencv(False)
    with pytest.raises(ContentFinding, match="jpeg_metadata"):
        scan_jpeg(_insert(data, b"\xff\x01"), KnownValues())


# Names: NFKC, short names, non-string IDs.


def _scan(known: KnownValues, payload: object) -> str | None:
    try:
        scan_json(json.dumps(payload).encode(), known)
    except ContentFinding as exc:
        return exc.rule
    return None


def test_full_width_and_half_width_names_match_each_other() -> None:
    half = KnownValues()
    half.add_name("known_value:display_name", "Kenji99")
    assert _scan(half, {"note": "ＫＥＮＪＩ９９ was here"}) == "known_value:display_name"
    full = KnownValues()
    full.add_name("known_value:display_name", "ＫＥＮＪＩ９９")
    assert _scan(full, {"note": "kenji99 was here"}) == "known_value:display_name"
    kana = KnownValues()
    kana.add_name("known_value:target_player_name", "ｲｶｽ")
    assert _scan(kana, {"name": "イカス"}) == "known_value:target_player_name"


def test_full_width_raw_bytes_still_match_their_own_form() -> None:
    known = KnownValues()
    known.add_personal("known_value:display_name", "ＫＥＮＪＩ９９")
    with pytest.raises(ContentFinding):
        known.check("ＫＥＮＪＩ９９".encode())


def test_short_names_match_only_whole_string_values() -> None:
    known = KnownValues()
    known.add_name("known_value:display_name", "Ika")
    assert _scan(known, {"player": "IKA"}) == "known_value:display_name"
    assert _scan(known, {"player": " i-k a "}) == "known_value:display_name"
    assert _scan(known, {"player": "ｉｋａ"}) == "known_value:display_name"
    assert _scan(known, {"note": "Ikachan splatted a squid"}) is None
    assert _scan(known, {"ika": 1}) is None
    assert _scan(known, {"note": "ika ika"}) is None


def test_single_character_name_is_exact_only() -> None:
    known = KnownValues()
    known.add_name("known_value:target_player_name", "A")
    assert _scan(known, {"slot": "a"}) == "known_value:target_player_name"
    assert _scan(known, {"mode": "area"}) is None


def _submission(**changes) -> Submission:
    values = {
        "id": "f" * 32,
        "access_token_hash": "a" * 64,
        "created_at": "2026-11-09T00:00:00Z",
        "updated_at": "2026-11-09T00:00:00Z",
        "expires_at": "2026-12-09T00:00:00Z",
        **changes,
    }
    return Submission(**values)


def test_short_display_name_and_acquisition_names_are_known(tmp_path: Path) -> None:
    settings = report_settings(tmp_path, None)
    known = known_values(
        settings,
        _submission(display_name="Mo"),
        None,
        None,
        {"target_player_name": "ｹﾝ", "confirmed_player_name": "Bo"},
    )
    assert _scan(known, {"a": "mo"}) == "known_value:display_name"
    assert _scan(known, {"a": "ケン"}) == "known_value:target_player_name"
    assert _scan(known, {"a": "BO"}) == "known_value:confirmed_player_name"
    assert _scan(known, {"a": "mobo is fine"}) is None


def test_acquisition_without_player_names_keeps_other_checks(tmp_path: Path) -> None:
    settings = report_settings(tmp_path, None)
    known = known_values(
        settings,
        None,
        None,
        None,
        {
            "review_code": "ABCD-EFGH-JKLM-NPQR",
            "target_player_name": None,
            "confirmed_player_name": None,
            "requested_email": "player@example.test",
            "handoff_reference": "handoff-ref-0001",
            "submission_snapshot": {"display_name": "Squidkid", "email": None},
        },
    )
    assert _scan(known, {"a": "ABCD-EFGH-JKLM-NPQR"}) == "known_value:review_code"
    assert _scan(known, {"a": "player@example.test"}) == "known_value:requested_email"
    assert _scan(known, {"a": "handoff-ref-0001"}) == "known_value:handoff_reference"
    assert _scan(known, {"a": "squidkid"}) == "known_value:display_name"
    assert _scan(known, {"note": "None"}) is None


def test_non_string_snapshot_user_id_is_known(tmp_path: Path) -> None:
    settings = report_settings(tmp_path, None)
    known = known_values(
        settings,
        None,
        None,
        None,
        {"submission_snapshot": {"user_id": 48151623, "display_name": None}},
    )
    assert _scan(known, {"note": "account 48151623"}) == "known_value:user_id"
    flags = known_values(
        settings, None, None, None, {"submission_snapshot": {"user_id": True}}
    )
    assert _scan(flags, {"note": "True"}) is None
