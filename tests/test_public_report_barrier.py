"""The in-flight write barrier: no deletion races a publication attempt (R2).

A real publisher runs in a separate process. The fake S3 holds its first object
PUT on ``put_gate`` (the request is recorded before it blocks), so the parent
acts at a known point: the child has chosen its run, holds the barrier and is
mid-upload. Every step is ordered by that gate and by process exit, never by
sleeps.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fake_s3_helpers import FakeObject, FakeS3, fake_s3
from public_site.report_publications import PublicationStore
from public_site.report_sweep import ReportSweeper
from public_site.storage import ReportEraser
from public_site.store import PublicStore
from report_test_helpers import (
    Clock,
    complete_submission,
    drain,
    expire,
    r2_for,
    report_client,
    run_keys,
    settle,
)

TESTS = Path(__file__).resolve().parent
OLD = datetime.now(UTC) - timedelta(days=3)
STALE_RUN = "20261001T000000Z-0000000a"
PUBLISHER = """
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


def _puts(fake: FakeS3, submission_id: str) -> int:
    marker = f"/reports/{submission_id}/"
    return sum(1 for method, path in fake.requests if method == "PUT" and marker in path)


def _deletes(fake: FakeS3) -> int:
    return sum(1 for method, _path in fake.requests if method in {"DELETE", "POST"})


class _Child:
    """One publisher attempt in its own process, held at PUT number ``after + 1``
    (the first ``after`` objects are stored).
    """

    def __init__(
        self, tmp_path: Path, fake: FakeS3, submission_id: str, after: int = 2
    ) -> None:
        self.fake = fake
        self.after = after
        self.errors = tmp_path / "publisher.err"
        env = {k: v for k, v in os.environ.items() if not k.startswith("PUBLIC_")}
        env["PUBLIC_LOAD_DOTENV"] = "0"
        # Prefer the same ``splatoon3_ai_coach`` import as the parent pytest
        # process (editable install). Putting worktree ``src`` first desyncs
        # CoachingIndex schema from indexes the parent fixture just wrote.
        env["PYTHONPATH"] = os.pathsep.join(
            str(TESTS.parent / name) for name in ("tools", "tests")
        )
        fake.put_gate = threading.Event()
        fake.put_gate_after = after
        with self.errors.open("wb") as stderr:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    PUBLISHER,
                    str(tmp_path),
                    _fake_view(fake),
                    str(TESTS),
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )
        self._await_held_put(submission_id)

    def _await_held_put(self, submission_id: str) -> None:
        deadline = time.time() + 120
        while _puts(self.fake, submission_id) <= self.after:
            assert self.process.poll() is None, self.errors.read_text()[-2000:]
            assert time.time() < deadline, "publisher never reached the held PUT"
            time.sleep(0.01)

    def release(self) -> int:
        """Open the gate and wait for the attempt to finish."""
        assert self.fake.put_gate is not None
        self.fake.put_gate.set()
        code = self.process.wait(120)
        assert code == 0, self.errors.read_text()[-2000:]
        return code

    def kill(self) -> None:
        """SIGKILL while the PUT is held, then open the gate for the server."""
        os.kill(self.process.pid, signal.SIGKILL)
        self.process.wait(30)
        assert self.fake.put_gate is not None
        self.fake.put_gate.set()


def _sweeper(fake: FakeS3, root: Path) -> ReportSweeper:
    return ReportSweeper(
        eraser=ReportEraser(r2_for(fake)),
        store=PublicStore(root),
        publications=PublicationStore(root),
    )


def _queued(tmp_path: Path, fake: FakeS3, clock: Clock, **flags):
    client = report_client(tmp_path, fake, clock=clock, report_publish=True, **flags)
    _token, submission = complete_submission(client, tmp_path)
    return client, submission


def test_expiry_deletion_and_sweep_wait_for_an_in_flight_publication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    clock = Clock(datetime.now(UTC))
    with fake_s3() as fake:
        client, submission = _queued(tmp_path, fake, clock, report_expiry_delete=True)
        publisher = client.app.state.reports
        child = _Child(tmp_path, fake, submission.id)
        expire(client.app.state.store, submission.id)
        client.app.state.service.maintain(datetime.now(UTC))
        assert publisher.publications.get(submission.id).state == "delete_pending"
        puts_in_flight, deletes_before = _puts(fake, submission.id), _deletes(fake)

        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert (record.state, record.delete_attempts) == ("delete_pending", 0)
        assert record.next_attempt_at > clock.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        sweeper = _sweeper(fake, root)
        plan = sweeper.plan()
        assert [row.reason for row in plan.rows] == ["expired_submission"]
        result = sweeper.apply(plan.digest)
        assert (result.deleted, result.skipped_on_recheck) == (
            [],
            [f"reports/{submission.id}/"],
        )
        assert _deletes(fake) == deletes_before

        child.release()
        assert _puts(fake, submission.id) == puts_in_flight
        assert not any(
            path.endswith("bundle.json")
            for path in next(iter(run_keys(fake, submission.id).values()))
        )
        clock.advance(minutes=2)
        drain(publisher)
        assert run_keys(fake, submission.id) == {}
        settle(publisher)
        assert publisher.publications.get(submission.id) is None
        client.close()


def test_sweep_skips_a_submission_while_its_publication_is_in_flight(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    clock = Clock(datetime.now(UTC))
    with fake_s3() as fake:
        client, submission = _queued(tmp_path, fake, clock)
        stale = f"reports/{submission.id}/{STALE_RUN}/"
        fake.objects[f"{stale}coach_inputs/coaching_index.json"] = FakeObject(
            b"{}", {}, OLD
        )
        child = _Child(tmp_path, fake, submission.id)
        sweeper = _sweeper(fake, root)
        plan = sweeper.plan()
        assert [(row.prefix, row.reason) for row in plan.rows] == [
            (stale, "stale_partial_run")
        ]
        result = sweeper.apply(plan.digest)
        assert (result.deleted, result.skipped_on_recheck) == ([], [stale])
        assert STALE_RUN in run_keys(fake, submission.id)

        child.release()
        record = PublicationStore(root).get(submission.id)
        assert record.state == "verified"
        plan = sweeper.plan()
        assert sweeper.apply(plan.digest).deleted == [stale]
        assert list(run_keys(fake, submission.id)) == [record.run_id]
        client.close()


def test_an_invalidated_run_stops_uploading_with_local_files_intact(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime.now(UTC))
    with fake_s3() as fake:
        client, submission = _queued(tmp_path, fake, clock, report_expiry_delete=True)
        publisher = client.app.state.reports
        child = _Child(tmp_path, fake, submission.id)
        publisher.submission_expired(submission.id)
        assert Path(submission.analysis_dir).is_dir()
        puts_in_flight = _puts(fake, submission.id)
        child.release()
        assert _puts(fake, submission.id) == puts_in_flight
        record = publisher.publications.get(submission.id)
        assert (record.state, record.delete_attempts) == ("delete_pending", 0)
        client.close()


def test_a_killed_publisher_releases_the_barrier(tmp_path: Path) -> None:
    root = tmp_path / "public"
    clock = Clock(datetime.now(UTC))
    with fake_s3() as fake:
        client, submission = _queued(tmp_path, fake, clock)
        publications = PublicationStore(root)
        child = _Child(tmp_path, fake, submission.id)
        with publications.write_barrier(submission.id) as held:
            assert held is False
        child.kill()
        with publications.write_barrier(submission.id) as held:
            assert held is True
        client.close()
