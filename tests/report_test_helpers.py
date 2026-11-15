"""Shared setup for report-publishing tests. Only the in-process fake S3 is used."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fake_s3_helpers import FakeS3
from fastapi.testclient import TestClient
from public_site.app import create_app
from public_site.models import Submission
from public_site.report_publisher import ReportPublisher
from public_site.storage import R2Storage
from public_site.store import PublicStore
from public_site.tokens import hash_token
from report_bundle_fixture import fixture_command
from test_public_site import _create, _probe_ok, _put, _sample_video, _settings


def r2_for(fake: FakeS3) -> R2Storage:
    """An R2 client pointed at the fake server."""
    return R2Storage(
        account_id="acct",
        access_key_id=fake.access_key,
        secret_access_key=fake.secret,
        bucket=fake.bucket,
        endpoint=fake.endpoint,
    )


def report_settings(tmp_path: Path, fake: FakeS3 | None, **overrides):
    """Public settings with fake R2 credentials and the given report flags."""
    values = {
        "max_video_bytes": 10_000_000,
        "dev_mode": False,
        "turnstile_secret": "turnstile-secret-value",
        "smtp_password": "smtp-password-value",
    }
    if fake is not None:
        values.update(
            r2_account_id="acct",
            r2_access_key_id=fake.access_key,
            r2_secret_access_key=fake.secret,
            r2_bucket=fake.bucket,
        )
    values.update(overrides)
    return _settings(tmp_path, **values)


@dataclass
class Clock:
    """A settable clock for publisher backoff and sweep ages."""

    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        """Move time forward."""
        self.now = self.now + timedelta(**delta)


def report_client(
    tmp_path: Path,
    fake: FakeS3 | None,
    *,
    death_times: tuple[float, ...] = (0.5,),
    run_command: Callable[[list[str]], int] | None = None,
    clock: Clock | None = None,
    **overrides,
) -> TestClient:
    """An app whose analyze step writes the synthetic report fixture."""
    settings = report_settings(tmp_path, fake, **overrides)
    store = PublicStore(settings.root)
    app = create_app(
        settings,
        store=store,
        verifier=lambda token: token == "ok",
        probe=_probe_ok,
        run_command=run_command or fixture_command(death_times),
        start_worker=False,
        report_storage=None if fake is None else r2_for(fake),
    )
    if clock is not None and app.state.reports is not None:
        app.state.reports._clock = clock
    client = TestClient(app)
    client.app = app
    return client


def complete_submission(client: TestClient, tmp_path: Path) -> tuple[str, Submission]:
    """Upload a lavfi video and run the worker to completion."""
    video = _sample_video(tmp_path / f"upload-{len(list(tmp_path.glob('upload-*')))}.mp4")
    created = _create(client, size_bytes=len(video), filename="ranked-replay-2026.mp4")
    token = _put(client, created, content=video)
    assert client.post(f"/api/submissions/{token}/uploaded").status_code == 200
    assert client.app.state.worker.pump() is True
    client.app.state.worker.join()
    store: PublicStore = client.app.state.store
    submission = store.get_by_hash(hash_token(token))
    assert submission is not None
    return token, submission


def drain(publisher: ReportPublisher, limit: int = 20) -> int:
    """Run the publisher synchronously until nothing is due."""
    count = 0
    while count < limit and publisher.run_once():
        count += 1
    return count


def settle(publisher: ReportPublisher) -> int:
    """Move the publisher's clock past the expiry-deletion settle time and drain.

    A record emptied by expiry deletion is forgotten only after a second empty
    LIST at least ``DELETE_SETTLE_SECONDS`` later.
    """
    from public_site.report_publisher import DELETE_SETTLE_SECONDS

    seconds = DELETE_SETTLE_SECONDS + 1
    base = publisher._clock
    if isinstance(base, Clock):
        base.advance(seconds=seconds)
    else:
        publisher._clock = lambda: base() + timedelta(seconds=seconds)
    return drain(publisher)


def run_keys(fake: FakeS3, submission_id: str) -> dict[str, list[str]]:
    """Object keys under ``reports/<id>/`` grouped by run ID.

    Paths are relative to the run prefix and include the generation segment
    (``attempt-<n>/...``) when present.
    """
    runs: dict[str, list[str]] = {}
    prefix = f"reports/{submission_id}/"
    for key in sorted(fake.objects):
        if key.startswith(prefix):
            run_id, _, rest = key[len(prefix) :].partition("/")
            runs.setdefault(run_id, []).append(rest)
    return runs


def attempt_prefix_for(fake: FakeS3, submission_id: str, run_id: str) -> str:
    """Full object-key prefix for the (only) generation under one run."""
    rest = run_keys(fake, submission_id).get(run_id) or []
    for path in rest:
        if path.startswith("attempt-") and "/" in path:
            gen, _, _ = path.partition("/")
            return f"reports/{submission_id}/{run_id}/{gen}/"
    return f"reports/{submission_id}/{run_id}/"


def methods(fake: FakeS3) -> list[str]:
    """HTTP methods the fake received, in order."""
    return [method for method, _path in fake.requests]


def cli_env(monkeypatch, fake: FakeS3, root: Path) -> None:
    """Point ``public-site-reports`` at ``root`` and the fake bucket, settings off."""
    import public_site.report_ops as ops

    for name in (
        "PUBLIC_REPORT_PUBLISH",
        "PUBLIC_REPORT_EXPIRY_DELETE",
        "PUBLIC_REPORT_SWEEP",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PUBLIC_ROOT", str(root))
    monkeypatch.setenv("PUBLIC_R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("PUBLIC_R2_ACCESS_KEY_ID", fake.access_key)
    monkeypatch.setenv("PUBLIC_R2_SECRET_ACCESS_KEY", fake.secret)
    monkeypatch.setenv("PUBLIC_R2_BUCKET", fake.bucket)
    monkeypatch.setattr(ops, "open_report_storage", lambda _settings: r2_for(fake))


def expire(store: PublicStore, submission_id: str) -> None:
    """Move ``expires_at`` into the past (the next ``maintain`` expires it)."""
    submission = store.get(submission_id)
    assert submission is not None
    past = datetime.now(UTC) - timedelta(days=1)
    submission.expires_at = past.strftime("%Y-%m-%dT%H:%M:%SZ")
    store.save_submission(submission)
