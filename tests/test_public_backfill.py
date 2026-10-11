"""Retained-artifact backfill does not open expired submissions."""

from __future__ import annotations

from pathlib import Path

from public_site.backfill import backfill_retained
from public_site.models import Submission
from public_site.store import PublicStore


class _Writer:
    def __init__(self) -> None:
        self.ids: list[str] = []
        self.coaching: list[bool] = []

    def update_artifacts(self, submission_id: str, **kwargs) -> bool:
        self.ids.append(submission_id)
        self.coaching.append(kwargs.get("record_coaching") is True)
        return True


def test_backfill_skips_expired_and_missing_directories(tmp_path: Path) -> None:
    store = PublicStore(tmp_path / "public")
    retained = tmp_path / "retained"
    retained.mkdir()
    expired_dir = tmp_path / "expired"
    expired_dir.mkdir()
    (expired_dir / "vision_manifest.json").write_text("{}", encoding="utf-8")
    store.save_submission(_submission("a" * 32, "complete", str(retained)))
    store.save_submission(_submission("b" * 32, "expired", str(expired_dir)))
    store.save_submission(_submission("c" * 32, "complete", str(tmp_path / "missing")))
    writer = _Writer()

    counts = backfill_retained(store, writer)

    assert counts == {"updated": 1, "skipped_expired": 1, "skipped_missing": 1}
    assert writer.ids == ["a" * 32]
    assert writer.coaching == [True]
    assert not (expired_dir / "vision_manifest.json").read_text(encoding="utf-8") == ""


def _submission(submission_id: str, status: str, analysis_dir: str) -> Submission:
    return Submission(
        id=submission_id,
        access_token_hash="d" * 64,
        status=status,  # type: ignore[arg-type]
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        expires_at="2026-02-01T00:00:00Z",
        analysis_dir=analysis_dir,
    )
