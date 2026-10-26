"""Synthetic Amendment 7 workspace: no study media, manifests or records.

A temporary repository holds synthetic frozen tooling, a synthetic
preregistration commit (C7), a synthetic repair commit, synthetic production
pin files, the synthetic step 2a records and decodable synthetic pilot videos
(lossless FFV1 written by the test). Checkpoints of the pilot chain are built
once per module and copied per test.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import cv2
import map_ink_reference
import map_ink_reference.cli as cli
import map_ink_reference.execution as execution
import map_ink_reference.intro_latch as intro_latch
import map_ink_reference.pilot_pipeline as pilot_pipeline
import map_ink_reference.pins as pins
import map_ink_reference.seal as seal
import pytest
from map_ink_reference.constants import (
    CORRECTNESS_APPLICABLE,
    COVERAGE_ONLY,
    PILOT_AUTHORIZATION,
    PREREGISTRATION_PATH,
    RECORDS_DIR,
)
from map_ink_reference.io_utils import sha256_file
from map_ink_reference.pilot_addendum import write_addendum
from map_ink_reference.pilot_pipeline import (
    G3_NAMES,
    G4_NAMES,
    GATE,
    PilotScope,
    export,
    extract_plans,
    gate,
    pilot_groups,
    sealed_chosen,
    selection_path,
    session_dir,
    session_inputs,
    write_plans,
    write_points,
    write_selection,
)
from map_ink_reference.session import AnnotationSession
from mir_helpers import (
    FRAME_H,
    FRAME_W,
    Counter,
    commit,
    git,
    init_repo,
    ink_image,
    interval_specs,
    manifest_from_specs,
    write_geometry,
)
from mir_step2a import SYNTH_STAGE, SyntheticStep2a, with_intro
from test_mir_session import COLOURS, find_pixel

from splatoon3_ai_coach.media.video import VideoLoader
from splatoon3_ai_coach.media.video_identity import video_identity

TOOL_REL = "tools/diagnostics/map_ink_reference"
TEST_REL = "tests/map_ink_reference"
CONSTANTS_REL = f"{TOOL_REL}/constants.py"
SESSION_REL = f"{TOOL_REL}/session.py"
A1_REL = "src/synthetic_a1.py"
TOOL = {"tool_commit": "synthetic"}
PACKAGE_DIR = Path(map_ink_reference.__file__).parent


def constants_source(
    c7: str | None, digest: str | None, crop: Any = 96, zoom: Any = 4
) -> str:
    """Synthetic ``constants.py``; ``c7=None`` is the pre-repair chain."""
    entries = ['    ("amendment6", "' + "6" * 40 + '", "' + "e" * 64 + '"),']
    if c7 is not None:
        entries.append(f'    ("amendment7", "{c7}", "{digest}"),')
    head = ['    ("amendment4", "' + "4" * 40 + '", "' + "f" * 64 + '"),']
    head.append('    ("amendment5", "' + "5" * 40 + '", "' + "d" * 64 + '"),')
    return "\n".join(
        [
            '"""Synthetic constants."""',
            "",
            "PREREGISTRATION_CHAIN = (",
            *head,
            *entries,
            ")",
            f"DEFAULT_CROP_SIZE = {crop}",
            "MIN_CROP_SIZE = 64",
            "MAX_CROP_SIZE = 160",
            f"DEFAULT_ZOOM = {zoom}",
            "",
        ]
    )


def write_video(path: Path, count: int, seed: int) -> None:
    """Lossless synthetic video at the 0.5 s cadence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"FFV1"), 2, (FRAME_W, FRAME_H)
    )
    for i in range(count):
        writer.write(ink_image(seed + i))
    writer.release()


class DecodableStep2a(SyntheticStep2a):
    """Step 2a fixtures whose pilot sources are decodable synthetic videos."""

    def pilot_source(
        self, name: str, intervals: int, stage: str | None = SYNTH_STAGE
    ) -> dict[str, str]:
        specs = interval_specs([2] * intervals)
        video = self.repo / "videos" / f"{name}.avi"
        write_video(video, len(specs), seed=1000 * (ord(name[-1]) - ord("a")))
        manifest = manifest_from_specs(specs, video_identity=video_identity(video))
        with VideoLoader(video) as loader:
            decoded = list(loader.frames())
        assert len(decoded) == len(specs)
        for frame, item in zip(manifest["frame_results"], decoded, strict=True):
            frame.update(
                source_pts=item.source_pts,
                source_time_base_num=item.source_time_base_num,
                source_time_base_den=item.source_time_base_den,
                source_frame_index=item.frame_index,
            )
        path = self.repo / "analysis" / name / "vision_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(with_intro(manifest, [(0, stage, None)])))
        return {
            "analysis_dir": f"analysis/{name}",
            "video_path": f"videos/{name}.avi",
            "video_sha256": sha256_file(video),
            "video_identity": video_identity(video),
            "authorization": PILOT_AUTHORIZATION,
            "declared_by": "synthetic operator",
        }


@dataclass(frozen=True)
class Workspace:
    """A synthetic repository at one point of the Amendment 7 chain."""

    repo: Path
    c7: str
    digest: str
    geometry: Path
    media: dict
    coverage: dict
    production: dict
    a1: dict

    @property
    def scope(self) -> PilotScope:
        return PilotScope.from_repo(self.repo)

    @property
    def records(self) -> Path:
        return self.repo / RECORDS_DIR

    def patch(self, mp: pytest.MonkeyPatch) -> None:
        """Point every pin and identity table at the synthetic repository."""
        mp.setattr(seal, "PREREGISTRATION_CHAIN", (("amendment7", self.c7, self.digest),))
        mp.setattr(execution, "A1_IMPLEMENTATION_PINS", dict(self.a1))
        mp.setattr(execution, "PRODUCTION_PINS", dict(self.production))
        mp.setattr(
            intro_latch,
            "verify_code_identity",
            lambda repo, modules=None: dict(pins.INTRO_LATCH_CODE_PINS),
        )
        mp.setattr(intro_latch, "intro_deadline_seconds", lambda repo: 90.0)
        mp.setitem(
            pins.STAGE_GEOMETRY_PINS,
            f"{pins.GEOMETRY_DIR}/{SYNTH_STAGE}/default.yaml",
            "0",
        )
        for rid, pin in self.media.items():
            mp.setitem(pins.MEDIA_PINS, rid, pin)
        for rid, digest in self.coverage.items():
            mp.setitem(pins.COVERAGE_MANIFEST_PINS, rid, digest)
        mp.setattr(pilot_pipeline, "verify_geometry_pins", lambda repo: self.geometry)
        mp.setattr(cli, "_repo", lambda: self.repo)

    def copy_to(self, dest: Path) -> Workspace:
        """An independent copy of the repository (git history included)."""
        shutil.copytree(self.repo, dest / "repo", symlinks=True)
        return replace(self, repo=dest / "repo")

    def commit(self, paths: Sequence[Path], message: str) -> str:
        return commit(self.repo, paths, message)

    def head(self) -> str:
        return git(self.repo, "rev-parse", "HEAD").strip()

    def chosen(self) -> list[str]:
        return sealed_chosen(self.scope)

    def run_g1(self) -> str:
        write_selection(self.scope, TOOL)
        return self.commit([selection_path(self.scope)], "G1 pilot selection")

    def run_g2(self) -> str:
        write_plans(self.scope, TOOL)
        extract_plans(self.scope, TOOL)
        write_points(self.scope, TOOL)
        return self.commit(pilot_groups(self.scope, self.chosen())[1], "G2 pilot")

    def annotate(self, label: str = "ink_A") -> AnnotationSession:
        counter = Counter(500)
        session = AnnotationSession(
            session_inputs(self.scope),
            session_dir(self.scope),
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
                session.submit_label(task["item_id"], label)
        return session

    def run_g3_g4(self) -> tuple[str, str]:
        export(self.scope, TOOL)
        g3 = self.commit([self.scope.records / n for n in G3_NAMES], "G3 pilot labels")
        g4 = self.commit([self.scope.records / n for n in G4_NAMES], "G4 pilot keys")
        return g3, g4

    def run_g5(self) -> str:
        gate(self.scope, TOOL)
        return self.commit([self.scope.records / GATE], "G5 pilot gate")

    def run_g6(self, changes: Sequence[str] = (), interface: str | None = None) -> str:
        path = write_addendum(
            self.scope, changes=list(changes), interface_commit=interface
        )
        return self.commit([path], "G6 pilot addendum")

    def write_tool(self, rel: str, text: str) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frozen_tooling(repo: Path) -> list[Path]:
    """The synthetic pre-C7 tool tree (the files an interface commit may touch)."""
    files = {
        CONSTANTS_REL: constants_source(None, None),
        SESSION_REL: (PACKAGE_DIR / "session.py").read_text(),
        f"{TOOL_REL}/render.py": '"""Synthetic render."""\n',
        f"{TOOL_REL}/server.py": '"""Synthetic server."""\n',
        f"{TOOL_REL}/annotate.html": "<html></html>\n",
        f"{TOOL_REL}/plan.py": '"""Synthetic frozen module."""\n',
        f"{TEST_REL}/test_existing.py": '"""Synthetic existing test."""\n',
    }
    paths = []
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        paths.append(path)
    return paths


def build_repaired(root: Path, mp: pytest.MonkeyPatch) -> Workspace:
    """Frozen tooling, pins, C7, the repair and sealed step 2a."""
    repo = init_repo(root / "repo")
    production, paths = {}, []
    for rel in execution.PRODUCTION_PINS:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"synthetic {rel}\n".encode())
        production[rel] = _sha(path)
        paths.append(path)
    a1 = repo / A1_REL
    a1.write_text("synthetic a1\n")
    commit(repo, [*paths, a1, *_frozen_tooling(repo)], "frozen tooling and pins")
    prereg = repo / PREREGISTRATION_PATH
    prereg.parent.mkdir(parents=True, exist_ok=True)
    prereg.write_text("# synthetic preregistration through Amendment 7\n")
    c7 = commit(repo, [prereg], "C7")
    digest = _sha(prereg)
    constants = repo / CONSTANTS_REL
    constants.write_text(constants_source(c7, digest))
    repair_test = repo / TEST_REL / "test_repair.py"
    repair_test.write_text('"""Synthetic repair test."""\n')
    commit(repo, [constants, repair_test], "repair")
    geometry = write_geometry(root)
    DecodableStep2a(repo, repo / RECORDS_DIR, mp).seal()
    workspace = Workspace(
        repo=repo,
        c7=c7,
        digest=digest,
        geometry=geometry,
        media={rid: pins.MEDIA_PINS[rid] for rid in CORRECTNESS_APPLICABLE},
        coverage={rid: pins.COVERAGE_MANIFEST_PINS[rid] for rid in COVERAGE_ONLY},
        production=production,
        a1={A1_REL: _sha(a1)},
    )
    workspace.patch(mp)
    return workspace


def build_checkpoints(root: Path) -> dict[str, Workspace]:
    """Copies of the synthetic repository after each step of the pilot chain."""
    import map_ink_reference.gate as gate_module

    mp = pytest.MonkeyPatch()
    saved: dict[str, Workspace] = {}
    try:
        ws = build_repaired(root / "build", mp)

        def save(name: str, workspace: Workspace) -> None:
            saved[name] = workspace.copy_to(root / name)

        save("repaired", ws)
        ws.run_g1()
        save("g1", ws)
        ws.run_g2()
        save("g2", ws)
        ws.annotate()
        save("annotated", ws)
        ws.run_g3_g4()
        save("g4", ws)
        failing = ws.copy_to(root / "fail_build")
        failing.run_g5()
        save("g5_fail", failing)
        mp.setattr(gate_module, "GATE_CAP", 0.5)
        ws.run_g5()
        save("g5_pass", ws)
    finally:
        mp.undo()
    return saved
