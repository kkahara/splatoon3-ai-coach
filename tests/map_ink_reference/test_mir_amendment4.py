"""Amendment 4 conformance: blind-input derivation, pilot sources, identity,
criterion 4a and the Stage 2 trigger. Synthetic fixtures only; the real
repository is read only for production source/configuration digests."""

from __future__ import annotations

import json
from pathlib import Path

import map_ink_reference.criterion_4a as c4a
import map_ink_reference.intro_latch as intro_latch
import map_ink_reference.pins as pins
import map_ink_reference.pipeline_unblinded as unblinded
import pytest
from map_ink_reference.constants import (
    AMENDMENT4_COMMIT,
    CORRECTNESS_APPLICABLE,
    PREREGISTRATION_COMMIT,
    Q_ALL_UNDEFINED,
    RECORDS_DIR,
    SCOPE_CORPUS,
    SCOPE_PILOT,
)
from map_ink_reference.errors import (
    MalformedInput,
    PinMismatch,
    ProcedureOrderError,
    SealViolation,
)
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.pilot import (
    check_declared_source,
    corpus_exclusions,
    load_pilot_source_manifest,
    select_pilot,
    verify_source_video,
)
from map_ink_reference.stage_identity import (
    PILOT_TOP_KEYS,
    TOP_KEYS,
    load_corpus_blind_input,
    load_pilot_blind_input,
    step2a_paths,
    validate_blind_input,
)
from map_ink_reference.trigger import (
    require_stage2_recordings,
    trigger_payload,
    validate_trigger,
)
from map_ink_reference.verdicts import FAIL, NOT_EVALUABLE, PASS, PRESERVED
from mir_helpers import commit, init_repo, interval_specs, manifest_from_specs
from mir_step2a import SYNTH_STAGE, SyntheticStep2a, corpus_manifest, with_intro

REPO = Path(__file__).resolve().parents[2]


# ----- amendment4-intro-latch-v1 ------------------------------------------
def _manifest(readings) -> dict:
    return with_intro(manifest_from_specs(interval_specs([2] * 3)), readings)


def test_latch_replays_stored_readings_in_timestamp_order() -> None:
    manifest = _manifest([(3, "makomart", "splat_zones"), (1, "wahoo_world", None)])
    manifest["frame_results"].reverse()  # stored order must not matter
    latch = intro_latch.derive_latch(manifest, 90.0)
    times = {f["frame_id"]: f["timestamp"] for f in manifest["frame_results"]}
    assert latch == {
        "stage_id": "wahoo_world",
        "battle_mode_id": "splat_zones",
        "stage_latched_at": times["f00001"],
        "battle_mode_latched_at": times["f00003"],
        "intro_reading_count": 2,
    }


def test_latch_null_mode_and_no_readings() -> None:
    latch = intro_latch.derive_latch(_manifest([(0, "makomart", None)]), 90.0)
    assert (latch["battle_mode_id"], latch["battle_mode_latched_at"]) == (None, None)
    empty = intro_latch.derive_latch(_manifest([]), 90.0)
    assert empty["stage_id"] is None and empty["intro_reading_count"] == 0


def test_latch_uses_the_production_deadline() -> None:
    """The tracker closes after absorbing a reading at/after the deadline."""
    manifest = _manifest(
        [(0, None, "splat_zones"), (2, None, None), (4, "makomart", None)]
    )
    deadline = manifest["frame_results"][2]["timestamp"]
    closed = intro_latch.derive_latch(manifest, deadline)
    assert closed["stage_id"] is None and closed["battle_mode_id"] == "splat_zones"
    assert closed["intro_reading_count"] == 3
    assert intro_latch.derive_latch(manifest, 90.0)["stage_id"] == "makomart"


def test_latch_rejects_invalid_stored_readings() -> None:
    manifest = _manifest([(0, "makomart", None)])
    manifest["frame_results"][0]["detections"][-1]["reading"]["stage_template_score"] = 2
    with pytest.raises(MalformedInput):
        intro_latch.derive_latch(manifest, 90.0)


def test_real_production_code_and_config_match_the_sealed_pins() -> None:
    """Read-only: the three pinned source files and configs/default.yaml."""
    assert intro_latch.verify_code_identity(REPO) == pins.INTRO_LATCH_CODE_PINS
    assert intro_latch.intro_deadline_seconds(REPO) == 90.0
    assert AMENDMENT4_COMMIT == PREREGISTRATION_COMMIT


def test_code_identity_rejects_a_module_that_is_not_the_pinned_file() -> None:
    modules = dict(intro_latch.production_modules())
    modules["src/splatoon3_ai_coach/vision/models.py"] = json
    with pytest.raises(PinMismatch):
        intro_latch.verify_code_identity(REPO, modules)
    with pytest.raises(PinMismatch):
        intro_latch.verify_code_identity(REPO, {})


def test_real_geometry_pins() -> None:
    pins.require_pinned_geometry(REPO, "makomart", None)
    pins.require_pinned_geometry(REPO, "manta_maria", "splat_zones")
    with pytest.raises(PinMismatch):
        pins.require_pinned_geometry(REPO, "eeltail_alley", None)
    with pytest.raises(MalformedInput):
        pins.require_pinned_geometry(REPO, "../makomart", None)


# ----- step 2a writer and loader ------------------------------------------
@pytest.fixture
def step2a(tmp_path: Path, monkeypatch) -> SyntheticStep2a:
    repo = init_repo(tmp_path / "repo")
    (repo / "README").write_text("x")
    commit(repo, [repo / "README"], "init")
    return SyntheticStep2a(repo, repo / RECORDS_DIR, monkeypatch)


def test_step2a_schema_and_order(step2a: SyntheticStep2a) -> None:
    with pytest.raises(SealViolation):  # source manifest not sealed
        step2a.write_blind(SCOPE_PILOT)
    commit(step2a.repo, [step2a.write_source_manifest()], "source")
    with pytest.raises(SealViolation):  # pilot blind input not sealed
        step2a.write_blind(SCOPE_CORPUS)
    pilot = step2a.write_blind(SCOPE_PILOT)
    with pytest.raises(SealViolation):  # never regenerated
        step2a.write_blind(SCOPE_PILOT)
    commit(step2a.repo, [pilot], "pilot")
    corpus = step2a.write_blind(SCOPE_CORPUS)
    commit(step2a.repo, [corpus], "corpus")
    payload = read_json(corpus)
    assert set(payload) == TOP_KEYS and set(read_json(pilot)) == PILOT_TOP_KEYS
    assert payload["code_sha256"] == pins.INTRO_LATCH_CODE_PINS
    assert payload["config_sha256"] == pins.CONFIG_PIN[1]
    assert payload["amendment4_commit"] == AMENDMENT4_COMMIT
    assert len(payload["code_commit"]) == 40
    assert payload["python_version"] and payload["pydantic_version"]
    entry = payload["recordings"][0]
    assert entry["stage_id"] == SYNTH_STAGE and entry["stage_latched_at"] == 0.0
    assert entry["intro_reading_count"] == 1
    assert "video_path" not in entry
    blind = load_corpus_blind_input(step2a.repo, step2a.records)
    assert sorted(blind.identities) == sorted(CORRECTNESS_APPLICABLE)
    pilot_payload = load_pilot_blind_input(step2a.repo, step2a.records)
    assert pilot_payload["pilot_source_manifest_sha256"] == sha256_file(
        step2a_paths(step2a.records)[0]
    )


def test_step2a_records_committed_together_are_out_of_order(step2a, tmp_path) -> None:
    step2a.seal()
    other = init_repo(tmp_path / "other")
    copies = []
    for path in step2a_paths(step2a.records):
        copy = other / RECORDS_DIR / path.name
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(path.read_bytes())
        copies.append(copy)
    commit(other, copies[:2], "source and pilot together")
    commit(other, copies[2:], "corpus")
    with pytest.raises(ProcedureOrderError):
        load_corpus_blind_input(other, other / RECORDS_DIR)
    with pytest.raises(ProcedureOrderError):
        load_pilot_blind_input(other, other / RECORDS_DIR)


def test_unsealed_corpus_blind_input_is_refused(step2a) -> None:
    commit(step2a.repo, [step2a.write_source_manifest()], "source")
    commit(step2a.repo, [step2a.write_blind(SCOPE_PILOT)], "pilot")
    step2a.write_blind(SCOPE_CORPUS)
    with pytest.raises(SealViolation):
        load_corpus_blind_input(step2a.repo, step2a.records)


def test_correctness_recording_without_stage_is_a_stop(step2a) -> None:
    rid = CORRECTNESS_APPLICABLE[1]
    step2a.write_corpus_manifest(rid, corpus_manifest(rid, stage=None))
    with pytest.raises(MalformedInput, match="latches no stage_id"):
        intro_latch.corpus_entries(step2a.repo, 90.0)


def test_latched_stage_without_pinned_geometry_is_a_stop(step2a) -> None:
    rid = CORRECTNESS_APPLICABLE[1]
    step2a.write_corpus_manifest(rid, corpus_manifest(rid, stage="eeltail_alley"))
    with pytest.raises(PinMismatch, match="no pinned geometry"):
        intro_latch.corpus_entries(step2a.repo, 90.0)


def test_corpus_manifest_pin_mismatch_is_a_stop(step2a) -> None:
    rid = CORRECTNESS_APPLICABLE[0]
    path = step2a.repo / pins.MEDIA_PINS[rid].vision_manifest_path
    path.write_text(path.read_text() + " ")
    with pytest.raises(PinMismatch):
        intro_latch.corpus_entries(step2a.repo, 90.0)


def _sealed_payload(step2a) -> dict:
    step2a.seal()
    return read_json(step2a_paths(step2a.records)[2])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(extra=1),
        lambda p: p.pop("pydantic_version"),
        lambda p: p.update(rule="other-rule"),
        lambda p: p.update(scope=SCOPE_PILOT),
        lambda p: p.update(code_commit="abc"),
        lambda p: p["recordings"][0].update(hue=1.0),
        lambda p: p["recordings"][0].update(stage_latched_at=None),
        lambda p: p["recordings"].append(dict(p["recordings"][0])),
    ],
)
def test_blind_input_schema_is_exact(step2a, mutate) -> None:
    payload = _sealed_payload(step2a)
    mutate(payload)
    with pytest.raises(MalformedInput):
        validate_blind_input(payload, SCOPE_CORPUS)


def test_blind_input_with_other_code_digests_is_a_stop(step2a) -> None:
    payload = _sealed_payload(step2a)
    payload["code_sha256"]["src/splatoon3_ai_coach/vision/models.py"] = "0" * 64
    path = step2a_paths(step2a.records)[2]
    write_json(path, payload)
    commit(step2a.repo, [path], "tamper")
    with pytest.raises(PinMismatch):
        load_corpus_blind_input(step2a.repo, step2a.records)


def test_blind_input_entry_must_match_media_pins(step2a) -> None:
    payload = _sealed_payload(step2a)
    payload["recordings"][0]["video_sha256"] = "0" * 64
    path = step2a_paths(step2a.records)[2]
    write_json(path, payload)
    commit(step2a.repo, [path], "tamper")
    with pytest.raises(PinMismatch):
        load_corpus_blind_input(step2a.repo, step2a.records)


# ----- pilot-source manifest ------------------------------------------------
def test_pilot_source_manifest_validation(step2a, tmp_path: Path) -> None:
    good = step2a.sources[0]
    path = tmp_path / "sources.json"

    def load(entries, schema="amendment3-pilot-sources/v1"):
        path.write_text(json.dumps({"schema": schema, "entries": entries}))
        return load_pilot_source_manifest(path)

    assert load([good])[0].recording == "pilot-a"
    for bad in (
        [{**good, "authorization": "ok"}],
        [{**good, "extra": "x"}],
        [{k: v for k, v in good.items() if k != "declared_by"}],
        [{**good, "video_sha256": "XYZ"}],
        [good, good],
        [],
    ):
        with pytest.raises(MalformedInput):
            load(bad)
    with pytest.raises(MalformedInput):
        load([good], schema="other")


def _source(step2a, **changes):
    from map_ink_reference.pilot import PilotSource

    return PilotSource(**{**step2a.sources[0], **changes})


def test_declared_source_exclusions(step2a) -> None:
    exclusions = corpus_exclusions(step2a.repo)
    check_declared_source(step2a.repo, _source(step2a), exclusions)
    for name in ("_private", "alignment-v3-heldout", "review_validation"):
        (step2a.repo / "analysis" / name).mkdir(parents=True)
        with pytest.raises(MalformedInput, match="excluded"):
            check_declared_source(
                step2a.repo, _source(step2a, analysis_dir=f"analysis/{name}"), exclusions
            )
    with pytest.raises(MalformedInput, match="directly under"):
        check_declared_source(
            step2a.repo, _source(step2a, analysis_dir="other/pilot-a"), exclusions
        )
    corpus_identity = sorted(exclusions[0])[0]
    with pytest.raises(MalformedInput, match="corpus"):
        check_declared_source(
            step2a.repo, _source(step2a, video_identity=corpus_identity), exclusions
        )
    corpus_sha = sorted(exclusions[1])[0]
    with pytest.raises(MalformedInput, match="corpus"):
        check_declared_source(
            step2a.repo, _source(step2a, video_sha256=corpus_sha), exclusions
        )
    with pytest.raises(PinMismatch, match="video_identity"):
        check_declared_source(
            step2a.repo, _source(step2a, video_identity="e" * 64), exclusions
        )
    (step2a.repo / "analysis" / "pilot-a" / "capture_package.json").write_text("{}")
    with pytest.raises(MalformedInput, match="capture-package"):
        check_declared_source(step2a.repo, _source(step2a), exclusions)


def test_corpus_exclusions_cover_all_seven(step2a) -> None:
    identities, shas = corpus_exclusions(step2a.repo)
    assert len(identities) == 7 and len(shas) == len(CORRECTNESS_APPLICABLE)


def test_source_video_must_match_both_declared_identities(step2a) -> None:
    source = _source(step2a)
    verify_source_video(step2a.repo, source)
    with pytest.raises(PinMismatch):
        verify_source_video(step2a.repo, _source(step2a, video_sha256="0" * 64))
    with pytest.raises(PinMismatch):
        verify_source_video(step2a.repo, _source(step2a, video_identity="0" * 64))
    (step2a.repo / source.video_path).unlink()
    with pytest.raises(MalformedInput):
        verify_source_video(step2a.repo, source)


def test_pilot_selection_takes_two_smallest_eligible_identities(step2a) -> None:
    step2a.sources.append(step2a.pilot_source("pilot-d", 12, stage=None))
    step2a.seal()
    selection = select_pilot(step2a.repo, step2a.records)
    rows = {r["recording"]: r for r in selection["declared"]}
    assert not rows["pilot-c"]["eligible"]  # 5 intervals
    assert not rows["pilot-d"]["eligible"]  # no stage geometry
    expected = sorted(
        (s["video_identity"], s["analysis_dir"].split("/")[1]) for s in step2a.sources[:2]
    )
    assert selection["chosen"] == [name for _, name in expected]
    assert selection["pilot_source_manifest_sha256"] == sha256_file(
        step2a_paths(step2a.records)[0]
    )


def test_fewer_than_two_eligible_pilots_is_a_stop(step2a) -> None:
    step2a.sources[:] = [step2a.sources[0], step2a.sources[2]]
    step2a.seal()
    with pytest.raises(MalformedInput, match="only 1"):
        select_pilot(step2a.repo, step2a.records)


def test_excluded_declared_source_stops_before_any_derivation(step2a) -> None:
    corpus_sha = sorted(corpus_exclusions(step2a.repo)[1])[0]
    step2a.sources[1] = {**step2a.sources[1], "video_sha256": corpus_sha}
    commit(step2a.repo, [step2a.write_source_manifest()], "source")
    with pytest.raises(MalformedInput, match="corpus"):
        step2a.write_blind(SCOPE_PILOT)


def test_pilot_tools_never_search_the_filesystem() -> None:
    source = Path(intro_latch.__file__).with_name("pilot.py").read_text()
    for token in ("glob(", "rglob(", "iterdir(", "os.walk", "listdir", "scandir"):
        assert token not in source


# ----- identity rulings 3 and 4 ---------------------------------------------
class _Rec(unblinded._Recording):
    def __init__(self, hues_equal: bool) -> None:
        self.rid = "synthetic"
        self.plan = {"intervals": []}
        self.v3_payload = {"ally_h": 30.0, "opponent_h": 120.0}
        self.a1_payload = {"ally_h": 30.0 if hues_equal else 31.0, "opponent_h": 120.0}
        self.v3 = self.a1 = None


def _identity(monkeypatch, hues_equal: bool, differing: int, total: int) -> dict:
    monkeypatch.setattr(unblinded, "q_all", lambda *a, **k: (differing, total))
    return _Rec(hues_equal).identity(Path("."), None)


def test_identity_n_zero_is_undefined_not_a_defect(monkeypatch) -> None:
    for hues_equal in (True, False):
        ident = _identity(monkeypatch, hues_equal, 0, 0)
        assert ident["q_all"] == Q_ALL_UNDEFINED and ident["holds"] is False
    unblinded._check_undefined_identity("r", ident, NOT_EVALUABLE)
    with pytest.raises(MalformedInput):
        unblinded._check_undefined_identity("r", ident, PRESERVED)


def test_identity_holds_iff_no_pixel_differs(monkeypatch) -> None:
    assert _identity(monkeypatch, False, 0, 50)["holds"] is True
    assert _identity(monkeypatch, True, 0, 50)["holds"] is True
    different = _identity(monkeypatch, False, 2, 50)
    assert different["holds"] is False and different["q_all"] == pytest.approx(0.04)


def test_equal_hues_with_differing_pixels_is_a_stop(monkeypatch) -> None:
    with pytest.raises(SealViolation, match="tool defect"):
        _identity(monkeypatch, True, 1, 50)


# ----- criterion 4a ---------------------------------------------------------
def _cal(at):
    return {"team_color_calibration": None if at is None else {"calibrated_at": at}}


def test_criterion_4a_frozen_rule() -> None:
    assert c4a.evaluate_recording(_cal(None), [])["pass"] is True
    assert c4a.evaluate_recording(_cal(None), [{"video_time": 5.0}])["pass"] is False
    row = c4a.evaluate_recording(_cal(2.0), [{"video_time": 2.0}, {"video_time": 9.0}])
    assert row == {
        "calibration_present": True,
        "calibrated_at": 2.0,
        "entry_count": 2,
        "earliest_observation_time": 2.0,
        "pass": True,
    }
    assert c4a.evaluate_recording(_cal(2.0), [{"video_time": 1.9}])["pass"] is False
    assert c4a.evaluate_recording(_cal(2.0), [{"video_time": 1.9996}])["pass"] is True
    assert c4a.evaluate_recording(_cal(2.0), [{"video_time": 1.9994}])["pass"] is False
    assert c4a.evaluate_recording(_cal(2.0), [])["pass"] is True


@pytest.mark.parametrize(
    ("identity", "observations"),
    [
        ({}, []),
        ({"team_color_calibration": {}}, []),
        (_cal(1.0), {"entries": []}),
        (_cal(1.0), [{"time": 1.0}]),
        (_cal(1.0), [{"video_time": "1.0"}]),
        ({"team_color_calibration": {"calibrated_at": True}}, []),
    ],
)
def test_criterion_4a_unparsable_input_is_a_stop(identity, observations) -> None:
    with pytest.raises(MalformedInput):
        c4a.evaluate_recording(identity, observations)


def test_criterion_4a_verdict_needs_all_seven() -> None:
    rows = [{"pass": True}] * 7
    assert c4a.verdict(rows) == PASS
    assert c4a.verdict(rows[:6] + [{"pass": False}]) == FAIL
    with pytest.raises(MalformedInput):
        c4a.verdict(rows[:6])


def test_criterion_4a_pins_are_verified(tmp_path: Path) -> None:
    with pytest.raises(PinMismatch):
        c4a.compute_criterion_4a(tmp_path)


def test_criterion_4a_record_order_and_integrity(step2a, monkeypatch) -> None:
    repo, records = step2a.repo, step2a.records
    row = {**c4a.evaluate_recording(_cal(None), []), "match_identity_sha256": "0" * 64}
    monkeypatch.setattr(
        c4a,
        "compute_criterion_4a",
        lambda repo: {r: dict(row) for r in c4a.ALL_RECORDINGS},
    )
    with pytest.raises(SealViolation):  # gate record not sealed
        c4a.write_criterion_4a(records, {})
    gate = unblinded.gate_path(records, 1)
    write_json(gate, {"passed": True})
    commit(repo, [gate], "gate")
    payload = c4a.write_criterion_4a(records, {})
    assert payload["verdict_4a"] == PASS and set(payload["recordings"]) == set(
        c4a.ALL_RECORDINGS
    )
    with pytest.raises(SealViolation):  # never recomputed
        c4a.write_criterion_4a(records, {})
    with pytest.raises(SealViolation):  # not yet sealed
        c4a.read_sealed_criterion_4a(repo, records)
    path = unblinded.criterion_4a_path(records)
    commit(repo, [path], "5a")
    assert c4a.read_sealed_criterion_4a(repo, records) == PASS
    write_json(path, {**payload, "verdict_4a": FAIL})
    commit(repo, [path], "tamper")
    with pytest.raises(MalformedInput, match="inconsistent"):
        c4a.read_sealed_criterion_4a(repo, records)


def test_criterion_4a_after_stage1_results_is_out_of_order(step2a) -> None:
    records = step2a.records
    gate = unblinded.gate_path(records, 1)
    write_json(gate, {"passed": True})
    commit(step2a.repo, [gate], "gate")
    write_json(unblinded.results_path(records, 1), {})
    with pytest.raises(ProcedureOrderError):
        c4a.write_criterion_4a(records, {})


def test_criterion_4a_committed_with_gate_is_out_of_order(step2a) -> None:
    records = step2a.records
    gate, path = unblinded.gate_path(records, 1), unblinded.criterion_4a_path(records)
    write_json(gate, {"passed": True})
    write_json(path, {})
    commit(step2a.repo, [gate, path], "together")
    with pytest.raises(ProcedureOrderError):
        c4a.read_sealed_criterion_4a(step2a.repo, records)


# ----- Stage 2 trigger --------------------------------------------------------
def test_trigger_payload_is_exactly_digest_and_sorted_ids() -> None:
    ids = [CORRECTNESS_APPLICABLE[2], CORRECTNESS_APPLICABLE[0]]
    payload = trigger_payload("a" * 64, ids)
    assert payload == {"stage1_comparison_sha256": "a" * 64, "recordings": sorted(ids)}
    for bad in (
        {**payload, "verdicts": {}},
        {"recordings": sorted(ids)},
        {**payload, "recordings": list(reversed(sorted(ids)))},
        {**payload, "recordings": []},
        {**payload, "recordings": ["18-25-16"]},
        {**payload, "stage1_comparison_sha256": "A" * 64},
    ):
        with pytest.raises(MalformedInput):
            validate_trigger(bad)


def test_stage2_set_must_equal_the_sealed_trigger(step2a) -> None:
    rid = CORRECTNESS_APPLICABLE[1]
    path = step2a.records / "stage2_trigger.json"
    write_json(path, trigger_payload("a" * 64, [rid]))
    with pytest.raises(SealViolation):
        require_stage2_recordings(step2a.repo, step2a.records, [rid])
    commit(step2a.repo, [path], "6a")
    assert require_stage2_recordings(step2a.repo, step2a.records, [rid]) == [rid]
    for bad in (
        [],
        [rid, rid],
        [CORRECTNESS_APPLICABLE[0]],
        [rid, CORRECTNESS_APPLICABLE[0]],
    ):
        with pytest.raises(MalformedInput):
            require_stage2_recordings(step2a.repo, step2a.records, bad)
