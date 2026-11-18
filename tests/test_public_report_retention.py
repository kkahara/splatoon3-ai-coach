"""Superseded runs stay until expiry, then the whole prefix goes (plan section 10.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import public_site.worker as worker_module
from fake_s3_helpers import FakeS3, fake_s3
from public_site.report_publications import PublicationStore
from public_site.report_sweep import ReportSweeper
from public_site.storage import ReportEraser
from report_test_helpers import (
    cli_env,
    complete_submission,
    drain,
    expire,
    methods,
    r2_for,
    report_client,
    run_keys,
    settle,
)
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.app import app as cli_app

FLAGS = {"report_publish": True, "report_expiry_delete": True}


def _run_prefix(fake: FakeS3, submission_id: str, run_id: str) -> str:
    from report_test_helpers import attempt_prefix_for

    return attempt_prefix_for(fake, submission_id, run_id)


def _published_analysis_id(fake: FakeS3, submission_id: str, run_id: str) -> str:
    key = _run_prefix(fake, submission_id, run_id) + "coach_inputs/coaching_index.json"
    return json.loads(fake.objects[key].body)["analysis_id"]


def _bundle(fake: FakeS3, submission_id: str, run_id: str) -> dict:
    key = _run_prefix(fake, submission_id, run_id) + "bundle.json"
    return json.loads(fake.objects[key].body)


def _two_runs(tmp_path: Path, fake: FakeS3, monkeypatch, **flags):
    """S publishes run A without its clip, then run B after the view repairs it."""
    real_extract = worker_module.extract_clips
    skip = {"on": True}

    def extract_unless_skipped(*args, **kwargs):
        if not skip["on"]:
            real_extract(*args, **kwargs)

    monkeypatch.setattr(worker_module, "extract_clips", extract_unless_skipped)
    client = report_client(tmp_path, fake, **flags)
    publisher = client.app.state.reports
    token_s, s = complete_submission(client, tmp_path)
    skip["on"] = False
    _token_t, t = complete_submission(client, tmp_path)
    drain(publisher)
    publications = publisher.publications
    run_a = publications.get(s.id).run_id
    assert publications.get(s.id).state == "verified"
    assert _bundle(fake, s.id, run_a)["missing_media"] == [
        "public_clips/death_episode_0.5.mp4"
    ]
    assert client.app.state.store.get_video(s.id).object_key
    assert client.get(f"/api/submissions/{token_s}").status_code == 200
    assert publications.get(s.id).state == "pending"
    drain(publisher)
    record = publications.get(s.id)
    run_b = record.run_id
    assert record.state == "verified" and run_b != run_a
    assert record.superseded_run_ids == [run_a]
    assert _bundle(fake, s.id, run_b)["missing_media"] == []
    assert _published_analysis_id(fake, s.id, run_a) != _published_analysis_id(
        fake, s.id, run_b
    )
    assert set(run_keys(fake, s.id)) == {run_a, run_b}
    assert any(
        path.endswith("bundle.json") for path in run_keys(fake, s.id)[run_a]
    )
    assert "DELETE" not in methods(fake)
    return client, s, t, run_a, run_b


def _snapshot_t(fake: FakeS3, root: Path, t_id: str) -> tuple[dict, bytes]:
    storage = r2_for(fake)
    objects = {
        key: storage.read_sha256(key)
        for key in sorted(fake.objects)
        if key.startswith(f"reports/{t_id}/")
    }
    return objects, PublicationStore(root).path(t_id).read_bytes()


def _status(monkeypatch, fake: FakeS3, root: Path) -> dict:
    cli_env(monkeypatch, fake, root)
    result = CliRunner().invoke(
        cli_app, ["public-site-reports", "status", "--remote", "--details", "--json"]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_superseded_runs_live_until_expiry_then_whole_prefix_goes(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "public"
    with fake_s3() as fake:
        client, s, t, run_a, _run_b = _two_runs(tmp_path, fake, monkeypatch, **FLAGS)
        a_bytes = sum(
            len(fake.objects[f"reports/{s.id}/{run_a}/{rest}"].body)
            for rest in run_keys(fake, s.id)[run_a]
        )
        status = _status(monkeypatch, fake, root)
        row = next(
            r for r in status["remote"]["submissions"] if r["submission_id"] == s.id
        )
        assert (row["current_runs"], row["superseded_runs"]) == (1, 1)
        assert row["superseded_bytes"] == a_bytes
        table = status["remote"]["table"]
        assert table["total"]["bytes"] == sum(len(o.body) for o in fake.objects.values())
        sweeper = ReportSweeper(
            eraser=ReportEraser(r2_for(fake)),
            store=client.app.state.store,
            publications=PublicationStore(root),
            clock=lambda: datetime.now(UTC) + timedelta(hours=25),
        )
        plan = sweeper.plan()
        assert [(r.prefix, r.reason) for r in plan.rows] == [
            (f"reports/{s.id}/{run_a}/", "superseded_run")
        ]
        assert "DELETE" not in methods(fake)
        before_t, record_t = _snapshot_t(fake, root, t.id)
        seen = len(fake.requests)
        expire(client.app.state.store, s.id)
        client.app.state.service.maintain(datetime.now(UTC))
        assert PublicationStore(root).get(s.id).state == "delete_pending"
        drain(client.app.state.reports)
        assert run_keys(fake, s.id) == {}
        settle(client.app.state.reports)
        assert PublicationStore(root).get(s.id) is None
        later = fake.requests[seen:]
        deletes = [path for method, path in later if method == "DELETE"]
        assert deletes and all(f"/reports/{s.id}/" in path for path in deletes)
        assert not [path for _m, path in later if f"/reports/{t.id}/" in path]
        assert _snapshot_t(fake, root, t.id) == (before_t, record_t)
        after = _status(monkeypatch, fake, root)
        assert s.id not in {r["submission_id"] for r in after["remote"]["submissions"]}
        client.close()


def test_expiry_delete_off_keeps_runs_until_enabled(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "public"
    with fake_s3() as fake:
        client, s, t, run_a, run_b = _two_runs(
            tmp_path, fake, monkeypatch, report_publish=True
        )
        before_t, record_t = _snapshot_t(fake, root, t.id)
        expire(client.app.state.store, s.id)
        client.app.state.service.maintain(datetime.now(UTC))
        drain(client.app.state.reports)
        assert PublicationStore(root).get(s.id).state == "delete_pending"
        assert set(run_keys(fake, s.id)) == {run_a, run_b}
        assert "DELETE" not in methods(fake)
        status = _status(monkeypatch, fake, root)
        assert status["local"]["delete_pending_overdue"] == 1
        assert status["remote"]["table"]["expired_holding"]["submissions"] == 1
        client.close()
        restarted = report_client(tmp_path, fake, **FLAGS)
        restarted.app.state.reports.startup()
        drain(restarted.app.state.reports)
        assert run_keys(fake, s.id) == {}
        settle(restarted.app.state.reports)
        assert PublicationStore(root).get(s.id) is None
        assert _snapshot_t(fake, root, t.id) == (before_t, record_t)
        restarted.close()
