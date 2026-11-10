"""Rolling back to frozen P1-A, then redeploying P1-B, recovers expired reports.

The frozen code runs in a subprocess from a temporary detached worktree of the
``p1a-review-code-frozen`` tag (removed in teardown; the tag is never changed).
The release checklist requires ``pytest -m rollback -rs`` to show passed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_s3_helpers import FakeObject, FakeS3, fake_s3
from public_site.report_publications import PublicationStore
from public_site.report_sweep import ReportSweeper, SweepAborted
from public_site.storage import ReportEraser
from public_site.store import PublicStore
from report_bundle_fixture import folder_digest
from report_test_helpers import (
    cli_env,
    complete_submission,
    drain,
    methods,
    r2_for,
    report_client,
    run_keys,
    settle,
)
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.app import app as cli_app

pytestmark = pytest.mark.rollback

TAG = "p1a-review-code-frozen"
REPO = Path(__file__).resolve().parents[1]
ABSENT = "d" * 32
P1A_EXPIRE = """
import json, sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
import public_site, splatoon3_ai_coach
from public_site.app import create_app
from public_site.settings import PublicSettings
from public_site.store import PublicStore
from splatoon3_ai_coach.config.paths import default_config_path

root, submission_id = Path(sys.argv[1]), sys.argv[2]
settings = PublicSettings(
    root=root, config_path=default_config_path(), static_dir=root / "no-dist",
    dev_mode=False, worker_id="p1a-rollback", public_base_url="http://coach.example",
    storage="local",
)
store = PublicStore(root)
app = create_app(settings, store=store, verifier=lambda _t: True,
                 run_command=lambda _argv: 0, start_worker=False)
submission = store.get(submission_id)
past = datetime.now(UTC) - timedelta(days=1)
submission.expires_at = past.strftime("%Y-%m-%dT%H:%M:%SZ")
store.save_submission(submission)
app.state.service.maintain(datetime.now(UTC))
print(json.dumps({"public_site": public_site.__file__,
                  "package": splatoon3_ai_coach.__file__}))
"""


@pytest.fixture(scope="module")
def p1a_worktree(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A detached checkout of the frozen tag, or a skip with the reason."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    found = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", f"{TAG}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode != 0:
        pytest.skip(f"tag {TAG} is not available in this clone")
    path = tmp_path_factory.mktemp("p1a") / "worktree"
    subprocess.run(
        ["git", "-C", str(REPO), "worktree", "add", "--detach", str(path), TAG],
        capture_output=True,
        check=True,
    )
    try:
        yield path
    finally:
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "remove", "--force", str(path)],
            capture_output=True,
            check=False,
        )


def _run_p1a_expiry(worktree: Path, root: Path, submission_id: str) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PUBLIC_", "PYTHONPATH"))
    }
    env.update(
        PYTHONPATH=f"{worktree / 'src'}{os.pathsep}{worktree / 'tools'}",
        PUBLIC_STORAGE="local",
        PUBLIC_LOAD_DOTENV="0",
    )
    done = subprocess.run(
        [sys.executable, "-c", P1A_EXPIRE, str(root), submission_id],
        capture_output=True,
        text=True,
        env=env,
        cwd=worktree,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    modules = json.loads(done.stdout.strip().splitlines()[-1])
    for path in modules.values():
        assert Path(path).resolve().is_relative_to(worktree.resolve())


def _digest_or_none(path: Path) -> str | None:
    return folder_digest(path) if path.exists() else None


def _published_then_rolled_back(tmp_path: Path, fake: FakeS3, worktree: Path):
    """P1-B publishes A and B; frozen P1-A then expires A on the same PUBLIC_ROOT."""
    root = tmp_path / "public"
    client = report_client(tmp_path, fake, report_publish=True)
    _ta, a = complete_submission(client, tmp_path)
    _tb, b = complete_submission(client, tmp_path)
    drain(client.app.state.reports)
    publications = PublicationStore(root)
    assert publications.get(a.id).state == publications.get(b.id).state == "verified"
    client.close()
    fake.objects[f"reports/{ABSENT}/20261101T000000Z-0000cccc/bundle.json"] = FakeObject(
        b"x"
    )
    store = PublicStore(root)
    before = {
        "records": _digest_or_none(root / "report_publications"),
        "staging": _digest_or_none(root / "report_staging"),
        "b": store.get(b.id).model_dump(),
        "b_analysis": folder_digest(Path(b.analysis_dir)),
    }
    requests = len(fake.requests)
    _run_p1a_expiry(worktree, root, a.id)
    assert len(fake.requests) == requests
    assert store.get(a.id).status == "expired"
    assert not Path(a.analysis_dir).exists()
    assert _digest_or_none(root / "report_publications") == before["records"]
    assert _digest_or_none(root / "report_staging") == before["staging"]
    assert publications.get(a.id).state == "verified"
    assert store.get(b.id).model_dump() == before["b"]
    assert folder_digest(Path(b.analysis_dir)) == before["b_analysis"]
    return a, b, before


def test_p1a_expiry_is_recovered_by_p1b_sweep(
    tmp_path: Path, monkeypatch, p1a_worktree: Path
) -> None:
    root = tmp_path / "public"
    with fake_s3() as fake:
        a, b, _before = _published_then_rolled_back(tmp_path, fake, p1a_worktree)
        b_objects = {k: v.body for k, v in fake.objects.items() if f"/{b.id}/" in k}
        redeployed = report_client(tmp_path, fake)
        assert redeployed.app.state.reports is None
        redeployed.app.state.service.maintain(datetime.now(UTC))
        redeployed.close()
        assert "DELETE" not in methods(fake)
        cli_env(monkeypatch, fake, root)
        runner = CliRunner()
        dry = runner.invoke(cli_app, ["public-site-reports", "sweep", "--json"])
        assert dry.exit_code == 0, dry.output
        plan = json.loads(dry.output)
        a_keys = [k for k in fake.objects if k.startswith(f"reports/{a.id}/")]
        assert [
            (r["prefix"], r["reason"], r["object_count"], r["bytes"])
            for r in plan["rows"]
        ] == [
            (
                f"reports/{a.id}/",
                "expired_submission",
                len(a_keys),
                sum(len(fake.objects[k].body) for k in a_keys),
            )
        ]
        skips = {(s["prefix"], s["reason"]) for s in plan["skips"]}
        record_b = PublicationStore(root).get(b.id)
        assert (f"reports/{b.id}/{record_b.run_id}/", "current_run") in skips
        assert (f"reports/{ABSENT}/", "projection_absent") in skips
        stale = runner.invoke(
            cli_app, ["public-site-reports", "sweep", "--apply", "--confirm", "0" * 64]
        )
        assert stale.exit_code == 2
        assert "DELETE" not in methods(fake)
        applied = runner.invoke(
            cli_app,
            ["public-site-reports", "sweep", "--apply", "--confirm", plan["digest"]],
        )
        assert applied.exit_code == 0, applied.output
        assert run_keys(fake, a.id) == {}
        assert PublicationStore(root).get(a.id) is None
        assert PublicationStore(root).get(b.id) == record_b
        assert {
            k: v.body for k, v in fake.objects.items() if f"/{b.id}/" in k
        } == b_objects


def test_p1a_expiry_is_recovered_by_expiry_delete_startup(
    tmp_path: Path, p1a_worktree: Path
) -> None:
    root = tmp_path / "public"
    with fake_s3() as fake:
        a, b, _before = _published_then_rolled_back(tmp_path, fake, p1a_worktree)
        record_b = PublicationStore(root).get(b.id)
        b_objects = {k: v.body for k, v in fake.objects.items() if f"/{b.id}/" in k}
        redeployed = report_client(tmp_path, fake, report_expiry_delete=True)
        publisher = redeployed.app.state.reports
        publisher.startup()
        assert PublicationStore(root).get(a.id).state == "delete_pending"
        drain(publisher)
        settle(publisher)
        redeployed.close()
        assert run_keys(fake, a.id) == {}
        assert PublicationStore(root).get(a.id) is None
        assert PublicationStore(root).get(b.id) == record_b
        assert {
            k: v.body for k, v in fake.objects.items() if f"/{b.id}/" in k
        } == b_objects
        deletes = [p for m, p in fake.requests if m == "DELETE"]
        assert deletes and all(f"/reports/{a.id}/" in p for p in deletes)


def test_allow_large_with_zero_threshold_still_skips_current_and_absent(
    tmp_path: Path, p1a_worktree: Path
) -> None:
    root = tmp_path / "public"
    with fake_s3() as fake:
        a, b, _before = _published_then_rolled_back(tmp_path, fake, p1a_worktree)
        sweeper = ReportSweeper(
            eraser=ReportEraser(r2_for(fake)),
            store=PublicStore(root),
            publications=PublicationStore(root),
            threshold_min=0,
            threshold_fraction=0.0,
        )
        plan = sweeper.plan()
        with pytest.raises(SweepAborted, match="threshold"):
            sweeper.apply(plan.digest)
        result = sweeper.apply(plan.digest, allow_large=True)
        assert result.deleted == [f"reports/{a.id}/"]
        assert run_keys(fake, b.id) != {}
        assert f"reports/{ABSENT}/20261101T000000Z-0000cccc/bundle.json" in fake.objects
