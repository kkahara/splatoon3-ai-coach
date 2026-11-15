"""Bundle allowlist, ``bundle.json`` v1, and the publish-download round trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fake_s3_helpers import fake_s3
from public_site.clips import has_clip
from public_site.frames import has_frame, ranked_entries
from public_site.report_bundle import BundleError, prepare_bundle, select_files
from public_site.report_content import KnownValues
from public_site.results import load_result
from report_bundle_fixture import add_media, write_report_fixture
from report_test_helpers import complete_submission, drain, report_client, run_keys

SID = "c" * 32
EXPECTED = {
    "vision_manifest.json",
    "match_identity.json",
    "coach_inputs/coaching_index.json",
    "coach_inputs/death_episode_0.5.coach_input.json",
    "coach_inputs/death_episode_0.5.coaching.json",
    "coach_inputs/death_episode_0.5.llm_view.json",
    "coach_prototype/coaching_index.json",
    "coach_prototype/death_episode_0.5.llm_view.json",
    "coach_prototype/death_episode_0.5.model.flags.json",
    "coach_prototype/death_episode_0.5.model.output.json",
    "public_clips/death_episode_0.5.mp4",
    "public_frames/death_episode_0.5.jpg",
}


@pytest.fixture
def analysis(tmp_path: Path) -> Path:
    folder = tmp_path / SID
    write_report_fixture(folder)
    add_media(folder)
    for name in (
        "scenarios.json",
        "scenario_contexts.json",
        "map_observations.json",
        "game_language.json",
        "summary.md",
        ".hidden.json",
    ):
        (folder / name).write_text("{}", encoding="utf-8")
    (folder / "localized").mkdir()
    (folder / "localized" / "coaching.ja.json").write_text("{}", encoding="utf-8")
    (folder / "debug_snapshots").mkdir()
    (folder / "debug_snapshots" / "x.json").write_text("{}", encoding="utf-8")
    (folder / "public_clips" / ".death_episode_0.5.mp4.part").write_bytes(b"x")
    return folder


def _prepare(analysis: Path, outcome: str | None = "coached"):
    return prepare_bundle(
        analysis,
        submission_id=SID,
        run_id="20261109T120000Z-0000000d",
        outcome=outcome,
        created_at="2026-11-09T12:00:00Z",
        known=KnownValues(),
        staging=None,
    )


def test_allowlist_is_exactly_what_serving_reads(analysis: Path) -> None:
    assert set(select_files(analysis, "coached")) == EXPECTED


def test_symlinked_files_are_never_selected(analysis: Path, tmp_path: Path) -> None:
    secret = tmp_path / "outside.json"
    secret.write_text("{}", encoding="utf-8")
    (analysis / "coach_inputs" / "extra.coaching.json").symlink_to(secret)
    assert "coach_inputs/extra.coaching.json" not in select_files(analysis, "coached")


def test_bundle_records_hashes_transforms_and_missing_media(analysis: Path) -> None:
    (analysis / "public_clips" / "death_episode_0.5.mp4").unlink()
    prepared = _prepare(analysis)
    bundle = prepared.bundle
    assert bundle.missing_media == ["public_clips/death_episode_0.5.mp4"]
    assert bundle.outcome == "coached"
    assert bundle.producer.git_sha == "abc123"
    assert bundle.producer.evidence_version is not None
    assert bundle.total_bytes == sum(f.published_size for f in bundle.files)
    assert [f.path for f in bundle.files] == sorted(f.path for f in bundle.files)


def test_required_files(analysis: Path) -> None:
    (analysis / "coach_inputs" / "coaching_index.json").unlink()
    with pytest.raises(BundleError, match="local_missing"):
        _prepare(analysis)
    assert _prepare(analysis, outcome="no_deaths").bundle.outcome == "no_deaths"
    (analysis / "vision_manifest.json").unlink()
    with pytest.raises(BundleError, match="local_missing"):
        _prepare(analysis, outcome="no_deaths")


def test_round_trip_serves_identically(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(
            tmp_path, fake, death_times=(0.5, 1.5), report_publish=True
        )
        _token, submission = complete_submission(client, tmp_path)
        drain(client.app.state.reports)
        record = client.app.state.reports.publications.get(submission.id)
        assert record.state == "verified"
        local = Path(submission.analysis_dir)
        restored = tmp_path / "restored"
        from report_test_helpers import attempt_prefix_for

        prefix = attempt_prefix_for(fake, submission.id, record.run_id)
        for rest in run_keys(fake, submission.id)[record.run_id]:
            relative = rest.split("/", 1)[1] if rest.startswith("attempt-") else rest
            target = restored / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fake.objects[f"reports/{submission.id}/{record.run_id}/{rest}"].body)
    bundle = json.loads((restored / "bundle.json").read_text())
    assert bundle["missing_media"] == []
    assert load_result(restored) == load_result(local)
    assert load_result(restored).stale is False
    for entry in ranked_entries(local):
        assert (
            has_frame(restored, entry.safe_id) == has_frame(local, entry.safe_id) is True
        )
        assert has_clip(restored, entry.safe_id) == has_clip(local, entry.safe_id) is True
        clip = f"public_clips/{entry.safe_id}.mp4"
        assert (restored / clip).read_bytes() == (local / clip).read_bytes()
