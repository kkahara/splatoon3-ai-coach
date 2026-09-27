"""Filesystem store for public submissions. Separate from ``analysis/_platform``."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from public_site.models import Submission, VideoInput

_ID = re.compile(r"^[0-9a-f]{32}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
UNFINISHED = frozenset({"received", "validating", "queued", "processing"})
IN_FLIGHT = frozenset({"validating", "processing"})


def utc_now() -> str:
    """UTC timestamp stored on records."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    """Parse a timestamp written by ``utc_now``."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def add_days(stamp: str, days: int) -> str:
    """Return ``stamp`` plus ``days`` as a UTC timestamp."""
    return (parse_utc(stamp) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("invalid submission id")
    return value


class PublicStore:
    """JSON records keyed by submission id. Token lookup uses the hash only."""

    def __init__(self, root: Path) -> None:
        self.root = root
        for name in ("submissions", "videos", "by_token"):
            (root / name).mkdir(parents=True, exist_ok=True)

    def save_submission(self, submission: Submission) -> None:
        """Write one internal submission record."""
        _require_id(submission.id)
        path = self.root / "submissions" / f"{submission.id}.json"
        path.write_text(submission.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def save_video(self, video: VideoInput) -> None:
        """Write the video input bound to a submission."""
        _require_id(video.submission_id)
        path = self.root / "videos" / f"{video.submission_id}.json"
        path.write_text(video.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def remember_token(self, submission_id: str, token_hash: str) -> None:
        """Point a token hash at a submission id. The raw token is not written."""
        _require_id(submission_id)
        if not _HASH.fullmatch(token_hash):
            raise ValueError("invalid token hash")
        (self.root / "by_token" / token_hash).write_text(submission_id + "\n", encoding="utf-8")

    def get(self, submission_id: str) -> Submission | None:
        """Load a submission by id, or None when the id is unknown."""
        if not _ID.fullmatch(submission_id):
            return None
        path = self.root / "submissions" / f"{submission_id}.json"
        if not path.is_file():
            return None
        return Submission.model_validate_json(path.read_text(encoding="utf-8"))

    def get_video(self, submission_id: str) -> VideoInput | None:
        """Load the video input for a submission."""
        if not _ID.fullmatch(submission_id):
            return None
        path = self.root / "videos" / f"{submission_id}.json"
        if not path.is_file():
            return None
        return VideoInput.model_validate_json(path.read_text(encoding="utf-8"))

    def get_by_hash(self, token_hash: str) -> Submission | None:
        """Resolve a token hash to a submission."""
        if not _HASH.fullmatch(token_hash):
            return None
        pointer = self.root / "by_token" / token_hash
        if not pointer.is_file():
            return None
        return self.get(pointer.read_text(encoding="utf-8").strip())

    def list_submissions(self) -> list[Submission]:
        """Every internal submission record. Not exposed by the API."""
        records: list[Submission] = []
        for path in sorted((self.root / "submissions").glob("*.json")):
            records.append(Submission.model_validate_json(path.read_text(encoding="utf-8")))
        return records

    def unfinished_count(self) -> int:
        """Submissions that still occupy a queue slot."""
        return sum(1 for item in self.list_submissions() if item.status in UNFINISHED)

    def oldest_queued(self) -> Submission | None:
        """The earliest queued submission, or None when the queue is empty."""
        queued = [item for item in self.list_submissions() if item.status == "queued"]
        if not queued:
            return None
        return min(queued, key=lambda item: item.created_at)

    def count_status(self, status: str) -> int:
        """How many submissions currently have ``status``."""
        return sum(1 for item in self.list_submissions() if item.status == status)

    def fail_in_flight(self, message: str, now: str) -> list[Submission]:
        """Mark validating and processing submissions failed. Does not requeue."""
        failed: list[Submission] = []
        for item in self.list_submissions():
            if item.status not in IN_FLIGHT:
                continue
            item.status = "failed"
            item.error = message
            item.updated_at = now
            self.save_submission(item)
            failed.append(item)
        return failed

    def rate_path(self) -> Path:
        """JSON file of IP hashes and recent timestamps."""
        return self.root / "rate_limits.json"

    def read_rate(self) -> dict[str, list[str]]:
        """Load the rate-limit file. Missing file means no recent submissions."""
        path = self.rate_path()
        if not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): [str(item) for item in value] for key, value in payload.items()}

    def write_rate(self, payload: dict[str, list[str]]) -> None:
        """Replace the rate-limit file. Values are timestamps, keys are hashes."""
        self.rate_path().write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
