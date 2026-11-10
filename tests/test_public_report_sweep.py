"""Sweep plan / apply safety rules and the status output."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fake_s3_helpers import FakeObject, FakeS3, fake_s3
from public_site.report_publications import PublicationStore, ReportPublication
from public_site.report_sweep import ReportSweeper, SweepAborted, remote_status
from public_site.storage import ReportEraser, ReportLister
from public_site.store import PublicStore
from report_test_helpers import (
    cli_env,
    complete_submission,
    drain,
    methods,
    r2_for,
    report_client,
    run_keys,
)
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.app import app as cli_app

OLD = datetime.now(UTC) - timedelta(days=3)
ABSENT = "d" * 32


def _age_everything(fake: FakeS3) -> None:
    for item in fake.objects.values():
        item.modified = OLD


def _plant(fake: FakeS3, key: str, body: bytes = b"x") -> None:
    fake.objects[key] = FakeObject(body, {}, OLD)


def _sweeper(fake: FakeS3, root: Path, **kwargs) -> ReportSweeper:
    return ReportSweeper(
        eraser=ReportEraser(r2_for(fake)),
        store=PublicStore(root),
        publications=PublicationStore(root),
        **kwargs,
    )


@pytest.fixture
def world(tmp_path: Path):
    """Two published submissions plus planted prefixes, all older than 24 h."""
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports
        _ta, a = complete_submission(client, tmp_path)
        _tb, b = complete_submission(client, tmp_path)
        drain(publisher)
        yield fake, client, a, b
        client.close()


def test_plan_classifies_every_prefix(world, tmp_path: Path) -> None:
    fake, client, a, b = world
    root = tmp_path / "public"
    store = client.app.state.store
    publications = PublicationStore(root)
    record_b = publications.get(b.id)
    superseded = f"reports/{b.id}/20261101T000000Z-0000aaaa/"
    _plant(fake, superseded + "vision_manifest.json")
    _plant(fake, superseded + "bundle.json")
    _plant(fake, f"reports/{b.id}/20261102T000000Z-0000bbbb/vision_manifest.json")
    _plant(fake, f"reports/{ABSENT}/20261101T000000Z-0000cccc/bundle.json")
    _plant(fake, "reports/not-a-submission/file.json")
    _age_everything(fake)
    recent = f"reports/{b.id}/20261103T000000Z-0000dddd/vision_manifest.json"
    fake.objects[recent] = FakeObject(b"y")
    expired = store.get(a.id)
    expired.status = "expired"
    store.save_submission(expired)
    record_b.superseded_run_ids = ["20261101T000000Z-0000aaaa"]
    publications.save(record_b)
    plan = _sweeper(fake, root).plan()
    rows = {(row.prefix, row.reason) for row in plan.rows}
    assert rows == {
        (f"reports/{a.id}/", "expired_submission"),
        (superseded, "superseded_run"),
        (f"reports/{b.id}/20261102T000000Z-0000bbbb/", "stale_partial_run"),
    }
    skips = {(skip.prefix, skip.reason) for skip in plan.skips}
    assert (f"reports/{b.id}/{record_b.run_id}/", "current_run") in skips
    assert (f"reports/{b.id}/20261103T000000Z-0000dddd/", "run_too_recent") in skips
    assert (f"reports/{ABSENT}/", "projection_absent") in skips
    assert ("reports/not-a-submission/", "invalid_prefix") in skips
    expired_row = next(r for r in plan.rows if r.reason == "expired_submission")
    assert expired_row.object_count == len(
        run_keys(fake, a.id)[a_run(publications, a.id)]
    )
    assert "DELETE" not in methods(fake)


def a_run(publications: PublicationStore, submission_id: str) -> str:
    return publications.get(submission_id).run_id


def test_processing_complete_without_record_and_pending_are_skipped(
    world, tmp_path
) -> None:
    fake, client, a, b = world
    root = tmp_path / "public"
    store = client.app.state.store
    publications = PublicationStore(root)
    processing = store.get(a.id)
    processing.status = "processing"
    store.save_submission(processing)
    publications.remove(b.id)
    _age_everything(fake)
    plan = _sweeper(fake, root).plan()
    assert plan.rows == []
    reasons = {skip.reason for skip in plan.skips}
    assert {"processing", "complete_without_record"} <= reasons
    publications.save(
        ReportPublication(
            submission_id=b.id, state="pending", attempt_run_id=run_keys_one(fake, b.id)
        )
    )
    plan = _sweeper(fake, root).plan()
    assert plan.rows == []
    assert "record_pending" in {skip.reason for skip in plan.skips}


def run_keys_one(fake: FakeS3, submission_id: str) -> str:
    return next(iter(run_keys(fake, submission_id)))


def test_apply_needs_the_digest_and_rechecks_each_row(world, tmp_path: Path) -> None:
    fake, client, a, b = world
    root = tmp_path / "public"
    store = client.app.state.store
    for submission in (a, b):
        expired = store.get(submission.id)
        expired.status = "expired"
        store.save_submission(expired)
    sweeper = _sweeper(fake, root)
    plan = sweeper.plan()
    assert len(plan.rows) == 2
    with pytest.raises(SweepAborted, match="digest_mismatch"):
        sweeper.apply("0" * 64)
    _plant(fake, f"reports/{b.id}/20261101T000000Z-0000eeee/extra.json")
    with pytest.raises(SweepAborted, match="digest_mismatch"):
        sweeper.apply(plan.digest)
    del fake.objects[f"reports/{b.id}/20261101T000000Z-0000eeee/extra.json"]
    plan = sweeper.plan()
    original_row = sweeper._apply_row

    def revive_b_before_its_recheck(row):
        if row.submission_id == b.id:
            revived = store.get(b.id)
            revived.status = "complete"
            store.save_submission(revived)
        return original_row(row)

    sweeper._apply_row = revive_b_before_its_recheck
    result = sweeper.apply(plan.digest)
    assert result.deleted == [f"reports/{a.id}/"]
    assert result.skipped_on_recheck == [f"reports/{b.id}/"]
    assert run_keys(fake, a.id) == {}
    assert run_keys(fake, b.id) != {}
    assert PublicationStore(root).get(a.id) is None
    deletes = [path for method, path in fake.requests if method == "DELETE"]
    assert deletes and all(f"/reports/{a.id}/" in path for path in deletes)


def test_threshold_and_allow_large_lifts_only_the_threshold(
    world, tmp_path: Path
) -> None:
    fake, client, a, b = world
    root = tmp_path / "public"
    store = client.app.state.store
    expired = store.get(a.id)
    expired.status = "expired"
    store.save_submission(expired)
    _plant(fake, f"reports/{ABSENT}/20261101T000000Z-0000cccc/bundle.json")
    recent = f"reports/{b.id}/20261103T000000Z-0000dddd/vision_manifest.json"
    fake.objects[recent] = FakeObject(b"y")
    processing_id = _processing_prefix(fake, store)
    sweeper = _sweeper(fake, root, threshold_min=0, threshold_fraction=0.0)
    plan = sweeper.plan()
    assert plan.limit == 0 and plan.exceeds_threshold
    with pytest.raises(SweepAborted, match="threshold"):
        sweeper.apply(plan.digest)
    result = sweeper.apply(plan.digest, allow_large=True)
    assert result.deleted == [f"reports/{a.id}/"]
    assert run_keys(fake, b.id) != {}
    assert f"reports/{ABSENT}/20261101T000000Z-0000cccc/bundle.json" in fake.objects
    assert recent in fake.objects
    assert any(key.startswith(f"reports/{processing_id}/") for key in fake.objects)


def _processing_prefix(fake: FakeS3, store: PublicStore) -> str:
    submission = store.list_submissions()[0].model_copy()
    submission.id = "e" * 32
    submission.status = "processing"
    store.save_submission(submission)
    _plant(fake, f"reports/{submission.id}/20261101T000000Z-0000ffff/bundle.json")
    return submission.id


def test_automatic_sweep_never_lifts_the_threshold(world, tmp_path: Path) -> None:
    fake, client, a, _b = world
    store = client.app.state.store
    expired = store.get(a.id)
    expired.status = "expired"
    store.save_submission(expired)
    sweeper = _sweeper(fake, tmp_path / "public", threshold_min=0, threshold_fraction=0.0)
    assert sweeper.run_automatic() is None
    assert run_keys(fake, a.id) != {}


def test_remote_status_totals_add_up(world, tmp_path: Path) -> None:
    fake, client, a, b = world
    root = tmp_path / "public"
    _plant(fake, f"reports/{b.id}/20261102T000000Z-0000bbbb/vision_manifest.json")
    payload = remote_status(
        lister=ReportLister(r2_for(fake)),
        store=client.app.state.store,
        publications=PublicationStore(root),
        now=datetime.now(UTC),
        details=True,
    )
    table = payload["table"]
    assert table["current"]["runs"] == 2
    assert table["partial"]["runs"] == 1
    for key in ("runs", "objects", "bytes"):
        assert table["total"][key] == sum(
            table[name][key]
            for name in ("current", "superseded", "partial", "expired_holding", "other")
        )
    assert table["total"]["objects"] == len(fake.objects)
    assert {row["submission_id"] for row in payload["submissions"]} == {a.id, b.id}
    assert set(methods(fake)) <= {"GET", "HEAD", "PUT"}


def test_cli_sweep_dry_run_then_apply(world, tmp_path: Path, monkeypatch) -> None:
    fake, client, a, b = world
    store = client.app.state.store
    expired = store.get(a.id)
    expired.status = "expired"
    store.save_submission(expired)
    cli_env(monkeypatch, fake, tmp_path / "public")
    runner = CliRunner()
    dry = runner.invoke(cli_app, ["public-site-reports", "sweep", "--json"])
    assert dry.exit_code == 0, dry.output
    plan = json.loads(dry.output)
    assert [row["prefix"] for row in plan["rows"]] == [f"reports/{a.id}/"]
    for leak in (fake.secret, fake.endpoint, "Signature", "http"):
        assert leak not in dry.output
    missing = runner.invoke(cli_app, ["public-site-reports", "sweep", "--apply"])
    assert missing.exit_code != 0
    stale = runner.invoke(
        cli_app, ["public-site-reports", "sweep", "--apply", "--confirm", "0" * 64]
    )
    assert stale.exit_code == 2
    assert run_keys(fake, a.id) != {}
    applied = runner.invoke(
        cli_app, ["public-site-reports", "sweep", "--apply", "--confirm", plan["digest"]]
    )
    assert applied.exit_code == 0, applied.output
    assert run_keys(fake, a.id) == {}
    assert run_keys(fake, b.id) != {}
    status = runner.invoke(
        cli_app, ["public-site-reports", "status", "--remote", "--details", "--json"]
    )
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["local"]["settings"]["PUBLIC_REPORT_SWEEP"] == "off"
    assert payload["remote"]["table"]["current"]["runs"] == 1
    for leak in (fake.secret, fake.endpoint):
        assert leak not in status.output


def test_cli_status_local_needs_no_credentials(tmp_path: Path, monkeypatch) -> None:
    for name in (
        "PUBLIC_R2_ACCOUNT_ID",
        "PUBLIC_R2_ACCESS_KEY_ID",
        "PUBLIC_R2_SECRET_ACCESS_KEY",
        "PUBLIC_R2_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PUBLIC_ROOT", str(tmp_path))
    result = CliRunner().invoke(cli_app, ["public-site-reports", "status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["local"]["records"]["verified"] == 0
    remote = CliRunner().invoke(cli_app, ["public-site-reports", "status", "--remote"])
    assert remote.exit_code != 0
