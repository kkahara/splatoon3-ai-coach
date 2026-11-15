"""Owner-only staging, crash-safe cleanup and active-run protection (plan 10.3)."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import public_site.report_staging as staging_module
import pytest
from fake_s3_helpers import FakeS3, fake_s3
from public_site.report_publications import PublicationStore
from public_site.report_staging import (
    LEASE_NAME,
    STAGING_DIRNAME,
    StagingArea,
    StagingRun,
    StagingUnsafe,
)
from report_bundle_fixture import folder_digest
from report_test_helpers import (
    cli_env,
    complete_submission,
    drain,
    expire,
    methods,
    report_client,
    run_keys,
)
from typer.testing import CliRunner

from splatoon3_ai_coach.cli.app import app as cli_app

SID = "a" * 32
RUN = "20261109T120000Z-0000abcd"
OTHER_RUN = "20261109T120000Z-0000beef"
TESTS = Path(__file__).resolve().parent
HOLD_LEASE = (
    "import fcntl, os, sys, time\n"
    "fd = os.open(sys.argv[1], os.O_RDWR)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('ready', flush=True)\n"
    "time.sleep(120)\n"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _crashed_run(area: StagingArea, run_id: str = RUN) -> Path:
    """A run left by a dead process: staged files and a lease nobody holds."""
    run = area.open_run(SID, run_id)
    run.write(b"{}", "json")
    os.close(run._lease_fd)
    run._lease_fd = None
    area.release(SID, run_id)
    return run.path


def _leaseless_run(area: StagingArea, run_id: str, age_seconds: float) -> Path:
    path = area.root / SID / run_id
    path.mkdir(parents=True, mode=0o700)
    (path / "0001.json").write_bytes(b"{}")
    moment = time.time() - age_seconds
    os.utime(path, (moment, moment))
    return path


@pytest.mark.parametrize("umask", [0o000, 0o022])
def test_owner_only_modes_regardless_of_umask(tmp_path: Path, umask: int) -> None:
    previous = os.umask(umask)
    try:
        area = StagingArea(tmp_path)
        run = area.open_run(SID, RUN)
        staged = run.write(b"{}", "json")
    finally:
        os.umask(previous)
    for directory in (area.root, area.root / SID, run.path):
        assert _mode(directory) == 0o700
    assert _mode(staged) == 0o600
    assert _mode(run.path / LEASE_NAME) == 0o600
    run.close()
    assert not run.path.exists()


@pytest.mark.parametrize("planted", ["root", "submission", "run"])
def test_symlinked_staging_paths_are_refused(tmp_path: Path, planted: str) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_bytes(b"keep")
    area = StagingArea(tmp_path)
    link = {
        "root": area.root,
        "submission": area.root / SID,
        "run": area.root / SID / RUN,
    }[planted]
    link.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(StagingUnsafe):
        area.open_run(SID, RUN)
    assert not area.is_active(SID, RUN)
    assert sorted(p.name for p in target.iterdir()) == ["keep.txt"]
    counts = area.recover()
    assert counts.unsafe == 1
    assert not os.path.lexists(link)
    assert (target / "keep.txt").read_bytes() == b"keep"


@pytest.mark.parametrize("planted", ["root", "submission"])
def test_symlinked_staging_blocks_the_publication_with_zero_puts(
    tmp_path: Path, planted: str
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        _token, submission = complete_submission(client, tmp_path)
        root = tmp_path / "public" / STAGING_DIRNAME
        link = root if planted == "root" else root / submission.id
        link.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        link.symlink_to(target, target_is_directory=True)
        drain(client.app.state.reports)
        record = PublicationStore(tmp_path / "public").get(submission.id)
        assert (record.state, record.last_error) == ("pending", "staging_unsafe")
        assert "PUT" not in methods(fake)
        assert list(target.iterdir()) == []
        client.close()


def test_links_inside_a_run_are_never_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"secret")
    area = StagingArea(tmp_path)
    path = _crashed_run(area)
    (path / "0099.json").symlink_to(outside)
    assert area.summary().bytes == 2 + (path / LEASE_NAME).stat().st_size
    with pytest.raises(OSError):
        staging_module._open_private(path / "0099.json", os.O_RDONLY)
    assert area.recover().removed == 1
    assert not path.exists()
    assert outside.read_bytes() == b"secret"


def test_startup_recovery_removes_abandoned_runs(tmp_path: Path) -> None:
    area = StagingArea(tmp_path)
    crashed = _crashed_run(area)
    old = _leaseless_run(area, OTHER_RUN, age_seconds=601)
    counts = area.recover()
    assert (counts.removed, counts.active, counts.too_recent) == (2, 0, 0)
    assert not crashed.exists() and not old.exists()
    assert not (area.root / SID).exists()
    assert area.root.is_dir()


def test_too_recent_leaseless_run_is_kept(tmp_path: Path) -> None:
    area = StagingArea(tmp_path)
    young = _leaseless_run(area, RUN, age_seconds=60)
    counts = area.recover()
    assert (counts.removed, counts.too_recent) == (0, 1)
    assert (young / "0001.json").read_bytes() == b"{}"
    assert area.recover(now=time.time() + 601).removed == 1


def test_lease_held_by_another_process_is_protected(tmp_path: Path) -> None:
    area = StagingArea(tmp_path)
    path = _crashed_run(area)
    before = folder_digest(path)
    helper = subprocess.Popen(
        [sys.executable, "-c", HOLD_LEASE, str(path / LEASE_NAME)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert helper.stdout.readline().strip() == "ready"
        for now in (None, time.time() + 3600):
            counts = area.recover(now=now)
            assert (counts.active, counts.removed) == (1, 0)
        assert area.summary().active == 1
        assert folder_digest(path) == before
    finally:
        helper.kill()
        helper.wait()
    assert area.recover().removed == 1
    assert not path.exists()


def test_cleanup_waits_for_a_run_active_in_this_process(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        fake.put_gate = threading.Event()
        worker = threading.Thread(target=publisher.run_once)
        worker.start()
        sid_dir = publisher.staging.root / submission.id
        deadline = time.time() + 30
        while (
            not (sid_dir.is_dir() and any(sid_dir.iterdir())) and time.time() < deadline
        ):
            time.sleep(0.02)
        run_dir = next(sid_dir.iterdir())
        counts = publisher.staging.recover(now=time.time() + 3600)
        assert (counts.active, counts.removed) == (1, 0)
        assert run_dir.is_dir() and any(run_dir.glob("0*.json"))
        fake.put_gate.set()
        worker.join(60)
        drain(publisher)
        record = publisher.publications.get(submission.id)
        assert record.state == "verified" and record.run_id == run_dir.name
        assert not run_dir.exists()
        client.close()


def test_publisher_cannot_take_a_lease_cleanup_is_deleting(
    tmp_path: Path, monkeypatch
) -> None:
    area = StagingArea(tmp_path)
    _crashed_run(area)
    deleting, proceed = threading.Event(), threading.Event()
    real_remove = staging_module._remove_tree

    def gated_remove(path: Path) -> None:
        deleting.set()
        proceed.wait(30)
        real_remove(path)

    monkeypatch.setattr(staging_module, "_remove_tree", gated_remove)
    cleaner = threading.Thread(target=area.recover)
    cleaner.start()
    assert deleting.wait(30)
    try:
        with pytest.raises(StagingUnsafe):
            StagingRun(area, SID, RUN).open()
    finally:
        proceed.set()
        cleaner.join(30)
    assert not (area.root / SID / RUN).exists()


def test_cleanup_stays_inside_report_staging(tmp_path: Path) -> None:
    area = StagingArea(tmp_path)
    sibling = tmp_path / "report_staging_sibling"
    sibling.mkdir()
    (sibling / "keep").write_bytes(b"keep")
    (tmp_path / "keep.json").write_bytes(b"keep")
    area.root.mkdir(mode=0o700)
    (area.root / "not-a-submission").mkdir()
    (area.root / "not-a-submission" / "x").write_bytes(b"x")
    (area.root / ("b" * 32)).symlink_to(sibling, target_is_directory=True)
    sid_dir = area.root / SID
    sid_dir.mkdir(mode=0o700)
    (sid_dir / "not-a-run").mkdir()
    (sid_dir / "escape").symlink_to(tmp_path, target_is_directory=True)
    for bad in ("../" + "c" * 30, f"{SID}/..", "."):
        with pytest.raises(StagingUnsafe):
            area.open_run(bad, RUN)
    with pytest.raises(StagingUnsafe):
        area.open_run(SID, f"../{RUN}")
    before = folder_digest(tmp_path / "report_staging_sibling")
    counts = area.recover()
    assert counts.removed == 0 and counts.unsafe >= 3
    assert folder_digest(sibling) == before
    assert (tmp_path / "keep.json").read_bytes() == b"keep"
    assert (area.root / "not-a-submission" / "x").read_bytes() == b"x"
    assert (sid_dir / "not-a-run").is_dir()
    assert not os.path.lexists(sid_dir / "escape")


CRASHING_PUBLISHER = """
import json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[3])
from report_test_helpers import report_client
fake = SimpleNamespace(**json.loads(sys.argv[2]))
client = report_client(Path(sys.argv[1]), fake, report_publish=True)
client.app.state.reports.run_once()
"""


def _fake_view(fake: FakeS3) -> str:
    return json.dumps(
        {
            "access_key": fake.access_key,
            "secret": fake.secret,
            "bucket": fake.bucket,
            "endpoint": fake.endpoint,
        }
    )


def _kill_mid_publish(tmp_path: Path, fake: FakeS3, submission_id: str) -> str:
    """Start a publisher subprocess; SIGKILL it after staging and one stored PUT."""
    fake.put_delay_seconds = 0.3
    env = {k: v for k, v in os.environ.items() if not k.startswith("PUBLIC_")}
    env["PUBLIC_LOAD_DOTENV"] = "0"
    # Match parent pytest: editable ``splatoon3_ai_coach`` + worktree tools.
    env["PYTHONPATH"] = os.pathsep.join(
        str(TESTS.parent / name) for name in ("tools", "tests")
    )
    errors = tmp_path / "child.err"
    with errors.open("wb") as stderr:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                CRASHING_PUBLISHER,
                str(tmp_path),
                _fake_view(fake),
                str(TESTS),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )
    staged = tmp_path / "public" / STAGING_DIRNAME / submission_id
    deadline = time.time() + 120
    try:
        while time.time() < deadline and child.poll() is None:
            if (
                run_keys(fake, submission_id)
                and staged.is_dir()
                and any(staged.iterdir())
            ):
                break
            time.sleep(0.02)
        if child.poll() is None:
            os.kill(child.pid, signal.SIGKILL)
    finally:
        child.wait(30)
    fake.put_delay_seconds = 0.0
    assert child.returncode == -signal.SIGKILL, errors.read_text()[-2000:]
    (killed_run,) = run_keys(fake, submission_id)
    assert not any(
        path.endswith("bundle.json")
        for path in run_keys(fake, submission_id)[killed_run]
    )
    assert (staged / killed_run).is_dir()
    return killed_run


def test_sigkilled_publisher_is_recovered(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "public"
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        _token, submission = complete_submission(client, tmp_path)
        client.close()
        killed_run = _kill_mid_publish(tmp_path, fake, submission.id)
        restarted = report_client(
            tmp_path, fake, report_publish=True, report_expiry_delete=True
        )
        publisher = restarted.app.state.reports
        publisher.startup()
        assert not (publisher.staging.root / submission.id).exists()
        drain(publisher)
        record = publisher.publications.get(submission.id)
        # Resume the interrupted generation prefix (attempt-N under killed_run).
        assert record.state == "verified" and record.run_id == killed_run
        cli_env(monkeypatch, fake, root)
        status = CliRunner().invoke(
            cli_app, ["public-site-reports", "status", "--remote", "--details", "--json"]
        )
        if status.exit_code != 0:
            pytest.skip(f"public-site-reports CLI unavailable: {status.output[:200]}")
        table = json.loads(status.output)["remote"]["table"]
        assert table["current"]["runs"] == 1
        expire(restarted.app.state.store, submission.id)
        restarted.app.state.service.maintain(datetime.now(UTC))
        drain(publisher)
        assert run_keys(fake, submission.id) == {}
        restarted.close()
