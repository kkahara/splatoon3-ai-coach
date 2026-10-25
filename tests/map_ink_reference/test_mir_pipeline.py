"""End-to-end synthetic run in a temporary git repository.

Exercises the sealing order (step 2a, labels before keys, gate before
criterion 4a before comparison, trigger with the Stage 1 results), the gate,
the post-sealing mapping, the blind-input reproduction check, the
descriptive stage-identity cross-check, the comparison and the report. All
media, manifests and calibrations are synthetic; pinned corpus files are
never touched (their pin entries point at synthetic files).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import cv2
import map_ink_reference.criterion_4a as c4a
import map_ink_reference.intro_latch as intro_latch
import map_ink_reference.pins as pins
import map_ink_reference.pipeline_blind as blind
import map_ink_reference.pipeline_unblinded as unblinded
import map_ink_reference.report as report
import pytest
from map_ink_reference.constants import CORRECTNESS_APPLICABLE, RECORDS_DIR, WORK_DIR
from map_ink_reference.errors import (
    MalformedInput,
    PinMismatch,
    ProcedureOrderError,
    ReproductionFailure,
    SealViolation,
)
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.points import duplicate_point_ids
from map_ink_reference.records import (
    SWATCHES,
    TOKEN_KEY,
    export_session_records,
    labels_name,
    queue_key_name,
    queue_name,
    team_identity_name,
)
from map_ink_reference.session import AnnotationSession
from map_ink_reference.verdicts import PASS, PRESERVED
from mir_helpers import Counter, commit, init_repo, ink_image, write_geometry
from mir_step2a import SYNTH_STAGE, SyntheticStep2a
from test_mir_session import COLOURS, find_pixel

from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig

TOOL = {"tool_commit": "synthetic"}
ORIGINAL_VERIFY_CODE_IDENTITY = intro_latch.verify_code_identity


class Run:
    """A synthetic Amendment 3 workspace with step 2a sealed."""

    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.repo = init_repo_with_readme(tmp / "repo")
        self.records = self.repo / RECORDS_DIR
        self.work = self.repo / WORK_DIR
        self.mp = monkeypatch
        geometry = write_geometry(tmp)
        monkeypatch.setattr(blind, "verify_geometry_pins", lambda repo: geometry)
        monkeypatch.setattr(unblinded, "verify_geometry_pins", lambda repo: geometry)
        monkeypatch.setattr(
            unblinded, "load_map_ink_config", lambda repo: MapInkAnalyzerConfig()
        )
        monkeypatch.setattr(report, "coverage", lambda repo: {})
        self.a1_hues: dict[str, tuple[float, float]] = {}
        self.sidecar_stage: dict[tuple[str, str], str] = {}
        monkeypatch.setattr(unblinded, "load_calibration", self._calibration)
        monkeypatch.setattr(unblinded, "load_identity_sidecar", self._sidecar)
        monkeypatch.setattr(c4a, "compute_criterion_4a", self._criterion_rows)
        self.step2a = SyntheticStep2a(self.repo, self.records, monkeypatch)
        self.step2a.seal()

    def _calibration(self, repo: Path, rid: str, which: str) -> dict[str, Any]:
        ally, opp = (
            (30.0, 120.0) if which == "v3" else self.a1_hues.get(rid, (30.0, 120.0))
        )
        return {
            "ally_h": ally,
            "opponent_h": opp,
            "separation_degrees": 90.0,
            "calibrated_at": 1.0,
        }

    def _sidecar(self, repo: Path, rid: str, which: str) -> tuple[dict, str]:
        stage = self.sidecar_stage.get((rid, which), SYNTH_STAGE)
        digest = hashlib.sha256(f"{rid} {which}".encode()).hexdigest()
        return {"stage_id": stage, "battle_mode_id": None}, digest

    def _criterion_rows(self, repo: Path) -> dict[str, dict[str, Any]]:
        row = c4a.evaluate_recording(
            {"team_color_calibration": {"calibrated_at": 1.0}}, [{"video_time": 2.0}]
        )
        return {
            rid: {
                **row,
                "match_identity_sha256": "0" * 64,
                "map_observations_sha256": "1" * 64,
            }
            for rid in c4a.ALL_RECORDINGS
        }

    def plan_and_extract(self) -> None:
        for r, rid in enumerate(CORRECTNESS_APPLICABLE):
            blind.write_plan(self.repo, self.records, rid, TOOL)
            plan = read_json(blind.plan_path(self.records, rid))
            frames = []
            for i, frame in enumerate(plan["frames"]):
                png = self.work / "frames" / rid / f"{frame['frame_id']}.png"
                png.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(png), ink_image(1000 * r + i))
                frames.append(
                    {
                        "frame_id": frame["frame_id"],
                        "png": png.name,
                        "png_sha256": sha256_file(png),
                        "source_pts": frame["source_pts"],
                    }
                )
            write_json(
                blind.extraction_path(self.records, rid),
                {"recording_id": rid, "frames": frames, "tool": TOOL},
            )

    def points(self, stage: int, recordings) -> None:
        blind.write_point_manifest(self.records, self.work, stage, list(recordings), TOOL)

    def commit_records(self, names, message: str) -> None:
        commit(self.repo, [self.records / n for n in names], message)

    def criterion_4a(self) -> None:
        c4a.write_criterion_4a(self.records, TOOL)
        self.commit_records([unblinded.CRITERION_4A], "step 5a")

    def annotate(self, stage: int, label: Any = "ink_A") -> AnnotationSession:
        counter = Counter(100 * stage)
        session = AnnotationSession(
            blind.session_inputs(self.records, self.work, stage),
            self.work / f"session_stage{stage}",
            seed_source=counter.seed,
            id_source=counter.new_id,
        )
        while (task := session.current_task())["kind"] != "complete":
            if task["kind"] == "swatch":
                rid = {t: r for r, t in session.state["tokens"].items()}[task["token"]]
                frame = session.inputs.recordings[rid].selected[0]
                picks = [
                    dict(frame_index=0, x=x, y=y)
                    for x, y in (find_pixel(frame.png_path, c) for c in COLOURS)
                ]
                session.submit_swatch(task["token"], picks)
            elif task["kind"] == "team":
                phase = "start" if session.state["phase"] == "team_start" else "end"
                left = session.state["team"][task["token"]][phase]["left"]
                session.submit_team(task["token"], "left" if left == "A" else "right")
            else:
                value = label(session, task) if callable(label) else label
                session.submit_label(task["item_id"], value)
        return session

    def seal_session(self, stage: int) -> None:
        export_session_records(
            self.work / f"session_stage{stage}", self.records, tool=TOOL
        )
        first = [labels_name(stage), team_identity_name(stage), queue_name(stage)]
        if stage == 1:
            first.append(SWATCHES)
        self.commit_records(first, f"stage {stage} session record")
        keys = [queue_key_name(stage)] + ([TOKEN_KEY] if stage == 1 else [])
        self.commit_records(keys, f"stage {stage} keys")


def init_repo_with_readme(path: Path) -> Path:
    repo = init_repo(path)
    (repo / "README").write_text("x")
    commit(repo, [repo / "README"], "init")
    return repo


def _manifest_names(stage: int) -> list[str]:
    return [f"points_stage{stage}.json", f"duplicates_stage{stage}.json"]


def _stage1(run: Run) -> None:
    run.plan_and_extract()
    run.points(1, CORRECTNESS_APPLICABLE)
    names = [f"frame_plans/{r}.json" for r in CORRECTNESS_APPLICABLE]
    names += [f"extraction/{r}.json" for r in CORRECTNESS_APPLICABLE]
    run.commit_records(names + _manifest_names(1), "stage 1 manifest")


def _through_gate(run: Run, label: Any = "ink_A") -> None:
    _stage1(run)
    run.annotate(1, label=label)
    run.seal_session(1)
    blind.run_gate(run.records, 1, TOOL)
    run.commit_records(["gate_stage1.json"], "gate 1")


def _verdicts(results: dict[str, Any]) -> dict[str, str]:
    return {r["recording_id"]: r["verdict"] for r in results["recordings"]}


def test_full_synthetic_run_reaches_pass(tmp_path: Path, monkeypatch) -> None:
    run = Run(tmp_path, monkeypatch)
    _stage1(run)
    manifest = read_json(blind.points_path(run.records, 1))
    blind_sha = sha256_file(run.records / "blind_input_manifest.json")
    assert manifest["blind_input_manifest_sha256"] == blind_sha
    assert {v["stage_id"] for v in manifest["stage_identities"].values()} == {SYNTH_STAGE}
    run.annotate(1)
    run.seal_session(1)
    gate = blind.run_gate(run.records, 1, TOOL)
    assert gate["passed"] and gate["denominator"] == 36
    with pytest.raises(SealViolation):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)
    run.commit_records(["gate_stage1.json"], "gate 1")
    with pytest.raises(SealViolation):  # step 5a not sealed yet
        unblinded.run_comparison(run.records, run.work, 1, TOOL)
    run.criterion_4a()
    results = unblinded.run_comparison(run.records, run.work, 1, TOOL)
    assert set(_verdicts(results).values()) == {PRESERVED}
    assert all(
        r["identity"]["holds"] and r["own_swatch"] == "A" for r in results["recordings"]
    )
    assert results["blind_input_reproduction"]["reproduced"] is True
    assert all(
        r["v3_agrees"] and r["a1_agrees"] for r in results["stage_identity_crosscheck"]
    )
    assert not (run.records / "stage2_trigger.json").exists()
    run.commit_records(["results_stage1.json"], "results 1")
    payload = report.build_report(run.records, TOOL)
    assert (payload["verdict_4a"], payload["verdict_4b"], payload["criterion_4"]) == (
        PASS,
        PASS,
        PASS,
    )
    assert payload["post_seal_stage_identity_discrepancies"] == []
    accuracy = payload["descriptive"]["pooled_accuracy_a1"]
    assert accuracy == payload["descriptive"]["pooled_accuracy_v3"]


def test_plan_requires_step2a_sealed(tmp_path: Path, monkeypatch) -> None:
    repo = init_repo_with_readme(tmp_path / "repo")
    records = repo / RECORDS_DIR
    step2a = SyntheticStep2a(repo, records, monkeypatch)
    with pytest.raises(SealViolation):
        blind.write_plan(repo, records, CORRECTNESS_APPLICABLE[0], TOOL)
    commit(repo, [step2a.write_source_manifest()], "source")
    with pytest.raises(SealViolation):
        blind.write_plan(repo, records, CORRECTNESS_APPLICABLE[0], TOOL)


def test_gate_requires_keys_committed_after_labels(tmp_path: Path, monkeypatch) -> None:
    run = Run(tmp_path, monkeypatch)
    _stage1(run)
    run.annotate(1)
    export_session_records(run.work / "session_stage1", run.records, tool=TOOL)
    run.commit_records(
        [
            labels_name(1),
            team_identity_name(1),
            queue_name(1),
            SWATCHES,
            queue_key_name(1),
            TOKEN_KEY,
        ],
        "labels and keys together",
    )
    with pytest.raises(ProcedureOrderError):
        blind.run_gate(run.records, 1, TOOL)


def test_unsealed_point_manifest_blocks_annotation(tmp_path: Path, monkeypatch) -> None:
    run = Run(tmp_path, monkeypatch)
    run.plan_and_extract()
    run.points(1, CORRECTNESS_APPLICABLE)
    with pytest.raises(SealViolation):
        blind.session_inputs(run.records, run.work, 1)


def test_tampered_duplicate_list_fails_closed(tmp_path: Path, monkeypatch) -> None:
    run = Run(tmp_path, monkeypatch)
    _stage1(run)
    dup_file = run.records / "duplicates_stage1.json"
    payload = read_json(dup_file)
    payload["duplicate_ids"] = payload["duplicate_ids"][1:]
    write_json(dup_file, payload)
    run.commit_records(["duplicates_stage1.json"], "tamper")
    with pytest.raises(Exception, match="frozen duplicate rule"):
        blind.session_inputs(run.records, run.work, 1)


def test_failed_gate_blocks_comparison_and_report_is_not_evaluable(
    tmp_path: Path, monkeypatch
) -> None:
    run = Run(tmp_path, monkeypatch)
    _stage1(run)
    run.annotate(1, label="cant_tell")
    run.seal_session(1)
    gate = blind.run_gate(run.records, 1, TOOL)
    assert not gate["passed"] and gate["denominator"] == 0
    run.commit_records(["gate_stage1.json"], "gate 1")
    with pytest.raises(SealViolation):  # 4a must be sealed before the report
        report.build_report(run.records, TOOL)
    run.criterion_4a()
    with pytest.raises(ProcedureOrderError):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)
    payload = report.build_report(run.records, TOOL)
    assert payload["verdict_4a"] == PASS
    assert payload["verdict_4b"] == "NOT_EVALUABLE"
    assert payload["criterion_4"] == "NOT_EVALUABLE"
    assert len(payload["descriptive"]["stage_identity_crosscheck"]) == 5


def _mixed_labels(rid: str):
    """ink_A on even frames (v3 better), not_ink on odd frames (A1 better)."""

    def label(session: AnnotationSession, task: dict[str, Any]) -> str:
        item = session.state["queue"][task["progress"][0]]
        point_id = session.state["point_refs"][item["point_ref"]]
        point_rid, frame_id, _ = point_id.split("|")
        if point_rid != rid:
            return "ink_A"
        return "ink_A" if int(frame_id[1:]) % 2 == 0 else "not_ink"

    return label


def test_stage2_flow_with_trigger_and_pooled_gate(tmp_path: Path, monkeypatch) -> None:
    run = Run(tmp_path, monkeypatch)
    rid = CORRECTNESS_APPLICABLE[0]
    run.a1_hues[rid] = (60.0, 120.0)  # differs from v3: pixels differ
    _through_gate(run, label=_mixed_labels(rid))
    run.criterion_4a()
    results = unblinded.run_comparison(run.records, run.work, 1, TOOL)
    by_rid = {r["recording_id"]: r for r in results["recordings"]}
    assert not by_rid[rid]["identity"]["holds"]
    assert by_rid[rid]["identity"]["differing"] > 0
    trigger = read_json(run.records / "stage2_trigger.json")
    assert trigger == {
        "stage1_comparison_sha256": sha256_file(run.records / "results_stage1.json"),
        "recordings": [rid],
    }
    assert by_rid[rid]["verdict"] == "INCONCLUSIVE"
    with pytest.raises(SealViolation):  # trigger must be sealed first
        run.points(2, [rid])
    run.commit_records(["results_stage1.json", "stage2_trigger.json"], "results 1")
    with pytest.raises(SealViolation):
        report.build_report(run.records, TOOL)  # Stage 2 gate not sealed yet
    with pytest.raises(MalformedInput):
        run.points(2, [CORRECTNESS_APPLICABLE[1]])
    with pytest.raises(MalformedInput):
        run.points(2, [rid, CORRECTNESS_APPLICABLE[1]])
    run.points(2, [rid])
    run.commit_records(_manifest_names(2), "stage 2 manifest")
    run.annotate(2, label=_mixed_labels(rid))
    run.seal_session(2)
    gate2 = blind.run_gate(run.records, 2, TOOL)
    assert gate2["pooled"] and gate2["denominator"] > 36
    run.commit_records(["gate_stage2.json"], "gate 2")
    final = unblinded.run_comparison(run.records, run.work, 2, TOOL)
    assert [r["recording_id"] for r in final["recordings"]] == [rid]
    assert set(final["recordings"][0]["frames"]) == {"stage1", "stage2"}
    run.commit_records(["results_stage2.json"], "results 2")
    payload = report.build_report(run.records, TOOL)
    assert payload["gates"]["stage2"]["pooled"]
    final_verdict = final["recordings"][0]["verdict"]
    expected = (
        PASS
        if final_verdict in (PRESERVED, "PRESERVED_IMPROVED")
        else ("FAIL" if final_verdict == "REGRESSED" else "NOT_EVALUABLE")
    )
    assert payload["verdict_4b"] == expected


def test_tampered_trigger_digest_stops_stage2(tmp_path: Path, monkeypatch) -> None:
    run = Run(tmp_path, monkeypatch)
    rid = CORRECTNESS_APPLICABLE[0]
    run.a1_hues[rid] = (60.0, 120.0)
    _through_gate(run, label=_mixed_labels(rid))
    run.criterion_4a()
    unblinded.run_comparison(run.records, run.work, 1, TOOL)
    trigger = read_json(run.records / "stage2_trigger.json")
    write_json(
        run.records / "stage2_trigger.json",
        {**trigger, "stage1_comparison_sha256": "0" * 64},
    )
    run.commit_records(["results_stage1.json", "stage2_trigger.json"], "results 1")
    with pytest.raises(SealViolation, match="another Stage 1 results"):
        report.build_report(run.records, TOOL)


def test_hues_differ_but_no_pixel_differs_is_identity(
    tmp_path: Path, monkeypatch
) -> None:
    """Amendment 4 ruling 3: hue equality is not a second necessary condition."""
    run = Run(tmp_path, monkeypatch)
    rid = CORRECTNESS_APPLICABLE[0]
    run.a1_hues[rid] = (31.0, 120.0)
    _through_gate(run)
    run.criterion_4a()
    results = unblinded.run_comparison(run.records, run.work, 1, TOOL)
    record = {r["recording_id"]: r for r in results["recordings"]}[rid]
    assert record["identity"]["hues_equal"] is False
    assert record["identity"]["differing"] == 0 and record["identity"]["total"] > 0
    assert record["identity"]["holds"] and record["identity"]["q_all"] == 0.0
    assert record["verdict"] == PRESERVED
    assert not (run.records / "stage2_trigger.json").exists()


@pytest.fixture
def sealed_to_5a(tmp_path: Path, monkeypatch) -> Run:
    run = Run(tmp_path, monkeypatch)
    _through_gate(run)
    run.criterion_4a()
    return run


def test_reproduced_stage_difference_is_a_stop(sealed_to_5a: Run, monkeypatch) -> None:
    run = sealed_to_5a
    real = intro_latch.derive_latch
    monkeypatch.setitem(
        pins.STAGE_GEOMETRY_PINS, "configs/stage_maps/another_stage/default.yaml", "0"
    )
    monkeypatch.setattr(
        intro_latch,
        "derive_latch",
        lambda manifest, deadline: {
            **real(manifest, deadline),
            "stage_id": "another_stage",
        },
    )
    with pytest.raises(ReproductionFailure, match="differs from the sealed"):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)
    assert not (run.records / "results_stage1.json").exists()


def test_reproduction_value_difference_is_reproduction_failure(
    sealed_to_5a: Run, monkeypatch
) -> None:
    run = sealed_to_5a
    real = intro_latch.derive_latch
    monkeypatch.setattr(
        intro_latch,
        "derive_latch",
        lambda manifest, deadline: {**real(manifest, deadline), "stage_latched_at": 99.0},
    )
    with pytest.raises(ReproductionFailure, match="differs from the sealed"):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)
    assert not (run.records / "results_stage1.json").exists()


def test_reproduction_that_cannot_execute_is_a_stop(
    sealed_to_5a: Run, monkeypatch
) -> None:
    run = sealed_to_5a

    def broken(repo):
        raise OSError("configuration unreadable")

    monkeypatch.setattr(intro_latch, "intro_deadline_seconds", broken)
    with pytest.raises(ReproductionFailure, match="cannot re-execute"):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)
    assert not (run.records / "results_stage1.json").exists()


def test_code_digest_mismatch_is_a_stop(sealed_to_5a: Run, monkeypatch) -> None:
    run = sealed_to_5a
    monkeypatch.setattr(
        intro_latch, "verify_code_identity", ORIGINAL_VERIFY_CODE_IDENTITY
    )
    with pytest.raises(PinMismatch):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)
    assert not (run.records / "results_stage1.json").exists()


def test_tampered_sealed_blind_input_is_a_stop(sealed_to_5a: Run) -> None:
    run = sealed_to_5a
    path = run.records / "blind_input_manifest.json"
    payload = read_json(path)
    payload["recordings"][0]["battle_mode_id"] = "rainmaker"
    write_json(path, payload)
    with pytest.raises(SealViolation):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)


def test_corpus_manifest_changed_after_seal_is_a_stop(sealed_to_5a: Run) -> None:
    run = sealed_to_5a
    rid = CORRECTNESS_APPLICABLE[2]
    path = run.repo / intro_latch.MEDIA_PINS[rid].vision_manifest_path
    path.write_text(path.read_text() + " ")
    with pytest.raises(PinMismatch):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)


def test_crosscheck_disagreement_is_descriptive_only(sealed_to_5a: Run) -> None:
    """Post-seal match_identity.json disagreement never alters results or STOPs."""
    run = sealed_to_5a
    baseline = unblinded.run_comparison(run.records, run.work, 1, TOOL)
    rid = CORRECTNESS_APPLICABLE[1]
    run.sidecar_stage[(rid, "v3")] = "another_stage"
    run.sidecar_stage[(CORRECTNESS_APPLICABLE[3], "a1")] = "third_stage"
    results = unblinded.run_comparison(run.records, run.work, 1, TOOL)
    assert results["recordings"] == baseline["recordings"]
    assert results["blind_input_reproduction"] == baseline["blind_input_reproduction"]
    rows = {r["recording_id"]: r for r in results["stage_identity_crosscheck"]}
    assert rows[rid]["v3_agrees"] is False and rows[rid]["a1_agrees"] is True
    assert rows[rid]["v3"]["stage_id"] == "another_stage"
    assert rows[rid]["blind_input"]["stage_id"] == SYNTH_STAGE
    assert rows[rid]["v3"]["sha256"] and rows[rid]["blind_input"]["manifest_sha256"]
    run.commit_records(["results_stage1.json"], "results 1")
    payload = report.build_report(run.records, TOOL)
    found = {r["recording_id"] for r in payload["post_seal_stage_identity_discrepancies"]}
    assert found == {rid, CORRECTNESS_APPLICABLE[3]}
    assert payload["verdict_4b"] == PASS and payload["criterion_4"] == PASS
    blind_payload = read_json(run.records / "blind_input_manifest.json")
    assert {e["stage_id"] for e in blind_payload["recordings"]} == {SYNTH_STAGE}


def test_point_manifest_from_another_blind_input_is_a_stop(
    tmp_path: Path, monkeypatch
) -> None:
    run = Run(tmp_path, monkeypatch)
    run.plan_and_extract()
    run.points(1, CORRECTNESS_APPLICABLE)
    manifest_file = blind.points_path(run.records, 1)
    manifest = read_json(manifest_file)
    manifest["blind_input_manifest_sha256"] = "0" * 64
    digest = write_json(manifest_file, manifest)
    dups = duplicate_point_ids([p["point_id"] for p in manifest["points"]], digest)
    write_json(
        run.records / "duplicates_stage1.json",
        {"stage": 1, "point_manifest_sha256": digest, "duplicate_ids": dups},
    )
    names = [f"frame_plans/{r}.json" for r in CORRECTNESS_APPLICABLE]
    names += [f"extraction/{r}.json" for r in CORRECTNESS_APPLICABLE]
    run.commit_records(names + _manifest_names(1), "stage 1 manifest")
    run.annotate(1)
    run.seal_session(1)
    blind.run_gate(run.records, 1, TOOL)
    run.commit_records(["gate_stage1.json"], "gate 1")
    run.criterion_4a()
    with pytest.raises(SealViolation, match="sealed blind-input"):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)


def test_stale_trigger_without_inconclusive_is_a_stop(sealed_to_5a: Run) -> None:
    run = sealed_to_5a
    write_json(
        run.records / "stage2_trigger.json",
        {"stage1_comparison_sha256": "0" * 64, "recordings": [CORRECTNESS_APPLICABLE[0]]},
    )
    with pytest.raises(MalformedInput, match="trigger already exists"):
        unblinded.run_comparison(run.records, run.work, 1, TOOL)
    assert not (run.records / "results_stage1.json").exists()
