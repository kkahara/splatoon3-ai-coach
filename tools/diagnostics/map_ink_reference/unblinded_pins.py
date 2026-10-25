"""Calibration-bearing pins (comparison and report stages only)."""

from __future__ import annotations

from dataclasses import dataclass

from map_ink_reference.pins import v3_dir

A1_DIR_TEMPLATE = "analysis/_a1_amendment2_dev/{rid}"
IDENTITY_FILENAME = "match_identity.json"


def a1_dir(rid: str) -> str:
    """Repo-relative A1 development-run analysis directory."""
    return A1_DIR_TEMPLATE.format(rid=rid)


@dataclass(frozen=True)
class CalibrationPins:
    """Identity sidecar pins for one recording."""

    recording_id: str
    v3_identity_sha256: str
    a1_identity_sha256: str

    @property
    def v3_identity_path(self) -> str:
        """Repo-relative v3-replay identity sidecar path."""
        return f"{v3_dir(self.recording_id)}/{IDENTITY_FILENAME}"

    @property
    def a1_identity_path(self) -> str:
        """Repo-relative A1 development-run identity sidecar path."""
        return f"{a1_dir(self.recording_id)}/{IDENTITY_FILENAME}"


CALIBRATION_PINS: dict[str, CalibrationPins] = {
    pin.recording_id: pin
    for pin in (
        CalibrationPins(
            "18-18-00",
            "ccb17a920c6bf010def1ede8b64dcb99782d0cc4b9d52b5111a4177c42ee4e57",
            "deb3c7dbd65039a48f3b636f29823b019b40b1d59cbcac932705510ec7818193",
        ),
        CalibrationPins(
            "20-33-31",
            "0ed0de2707ee19644d1c684e1e1a75b5a52168da524db349856ab0888eea0323",
            "d13486ec81bcf4b16dfd0e6d986bbbe8c40238f90b3a96a0253e1f88866ee27d",
        ),
        CalibrationPins(
            "22-31-53",
            "fee58b967c0f8e914793c0c7d58cf7979d38bcafd4dc52354544d5bf6acf1bf4",
            "6adb291f4b77cdd0770387baeefc230b1d87f47656a446b44e4e8f29c196e8c8",
        ),
        CalibrationPins(
            "22-37-47",
            "9b8fdd4fd963c2ee162f0e383cc1f001fcf867fd7bad214e7b4577c5fb3467f2",
            "37a853e9e81bdb7d28563412e5915f2354ff2f97fbc5357cd6863ceab7bb853e",
        ),
        CalibrationPins(
            "22-57-30",
            "a30a6da97053b719ab7e1dc1e220cc9d33f50b60927ba1d50731ab4cb1c0c028",
            "ad47138732baa35df2ec5afa80c47258f4b9e1baa8b04dac686d91efabd6c3d3",
        ),
    )
}

# Coverage-only recordings: identity sidecars (their media must never be
# opened; only criterion 4a and the report read these files).
COVERAGE_PINS: dict[str, CalibrationPins] = {
    pin.recording_id: pin
    for pin in (
        CalibrationPins(
            "18-25-16",
            "27c3687ddd6ec2f808f2078a5e751b0bd3609917f718e74c7e5d469ada575a3c",
            "f3b9a1c15285ddf5d000e7de04d2d835fd01aaea0800d8c0791cb06a48ef157f",
        ),
        CalibrationPins(
            "22-20-42",
            "82c11f9b9b907296cfb4398b91df5672b219d7cb70af0e2f458fbbeff65a40c7",
            "3b2b1ff2b5a5fad77f8c9b9f33cdda6e773659e4125d7607d59cdb68e769da5c",
        ),
    )
}


def identity_pins(rid: str) -> CalibrationPins:
    """Identity sidecar pins for any of the seven recordings."""
    return CALIBRATION_PINS.get(rid) or COVERAGE_PINS[rid]


# Amendment 4: pinned ``map_observations.json`` (rid → SHA-256).
OBSERVATIONS_FILENAME = "map_observations.json"
A1_OBSERVATION_PINS: dict[str, str] = {
    "18-18-00": "eb58f65c4fde629682bc382fba3723eb5de7b6049722fde33a079e4233c04045",
    "20-33-31": "779dd9f05dfb02e1df3dbcd8aaf8100fd9c627a8faf76a84d7708fe01af37353",
    "22-31-53": "bc647070c5ced03afe75e98457e170f87e1d64ce293e20697bc9a837128d39b3",
    "22-37-47": "add5223303f9beeb94e0f666c856cba569a928748119aacfdadb17477451bb46",
    "22-57-30": "4aaffcdc6ece0ebdeb17146f95d10300bd99b0791cf65b3a443b57036c12b363",
    "18-25-16": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
    "22-20-42": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
}
V3_OBSERVATION_PINS: dict[str, str] = {
    "18-18-00": "d770b3dc4bf9357a2ce1b9b0ede673e2b3fabbffa5b870f62236077e200a7df2",
    "20-33-31": "6df10e0e1bacba78db7f495b44c1215c5db620f5284c9a08e7fbe337f7a3f133",
    "22-31-53": "bc647070c5ced03afe75e98457e170f87e1d64ce293e20697bc9a837128d39b3",
    "22-37-47": "ccba8e6a40b57641c65015f8ff4d475c04ed8d184d8ca9a6a273b91ddec637aa",
    "22-57-30": "5dd58be6d9df9efc96538d1b96a9d581f63033254c0c600ff0c4792fb2db1698",
    "18-25-16": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
    "22-20-42": "0a442f25aff9dd80dc9b85d12d84d683e216bdbbcf3ab4793e61c835a9cf1e32",
}


def a1_observations_path(rid: str) -> str:
    """Repo-relative A1 development-run observations path."""
    return f"{a1_dir(rid)}/{OBSERVATIONS_FILENAME}"


def v3_observations_path(rid: str) -> str:
    """Repo-relative v3-replay observations path."""
    return f"{v3_dir(rid)}/{OBSERVATIONS_FILENAME}"


# A1 implementation digests at drafting (hashed only, for sealing step 1).
A1_IMPLEMENTATION_PINS: dict[str, str] = {
    "src/splatoon3_ai_coach/vision/calibration_window.py": (
        "1ec3929969b073adefd2f11923d88fb06cd716009dbdd0d098eed3649457ebef"
    ),
    "src/splatoon3_ai_coach/vision/match_side_effects.py": (
        "41b158549eb37bf8b7f084a7ced7ab9da781afd9b6d79abebfe1a95f9c98db08"
    ),
    "src/splatoon3_ai_coach/vision/team_color_calibration.py": (
        "ab87d07df313044e5d0194b4d0452c0ceca01df2da66643a478d59a18f28ea4d"
    ),
    "src/splatoon3_ai_coach/vision/cadence_scan.py": (
        "e34adc361a8bd81b224d3fe8be39231f136425be97f516d6b245ee4a680402fd"
    ),
}

MAP_INK_CODE_PIN = (
    "src/splatoon3_ai_coach/vision/map_ink.py",
    "50f0dba3e0e004dc17096cff6d5825a496cd917d03517de00c08402150070ab3",
)
