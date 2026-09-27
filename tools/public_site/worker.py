"""Run queued submissions through the existing s3-coach CLI.

This worker is not ``vmv_site.jobs.JobRunner``. A submission found in
``processing`` or ``validating`` at startup is failed and is not resumed.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from public_site.errors import ANALYSIS_FAILED, COACHING_FAILED, INTERRUPTED
from public_site.frames import extract_frames
from public_site.models import Submission
from public_site.service import SubmissionService
from public_site.settings import PublicSettings
from public_site.store import PublicStore, utc_now

Command = Callable[[list[str]], int]
_COACH_INPUTS = "coach_inputs"
_INDEX = "coaching_index.json"


def cli_launch() -> list[str]:
    """How to invoke s3-coach from this environment."""
    script = Path(sys.executable).with_name("s3-coach")
    if script.is_file():
        return [str(script)]
    boot = "from splatoon3_ai_coach.cli.app import app; app()"
    return [sys.executable, "-c", boot]


def analyze_argv(settings: PublicSettings, video: Path, out: Path, language: str) -> list[str]:
    """Arguments for ``s3-coach analyze``. Does not reimplement analysis."""
    return [
        *cli_launch(),
        "analyze",
        str(video),
        "--out",
        str(out),
        "--language",
        language,
        "--config",
        str(settings.config_path),
    ]


def coach_argv(
    settings: PublicSettings,
    analysis_dir: Path,
    subcommand: str,
    extra: list[str] | None = None,
) -> list[str]:
    """Arguments for coach-inputs or coach-prototype."""
    return [
        *cli_launch(),
        subcommand,
        str(analysis_dir),
        "--config",
        str(settings.config_path),
        *(extra or []),
    ]


def prototype_extra(settings: PublicSettings) -> list[str]:
    """coach-prototype flags. The provider name is not a secret; the API key stays in the environment."""
    extra = ["--call-llm"]
    if settings.llm_provider:
        extra.extend(["--provider", settings.llm_provider])
    return extra


def subprocess_run(argv: list[str]) -> int:
    """Run one CLI stage. The return code is the only result kept."""
    completed = subprocess.run(argv, check=False)
    return int(completed.returncode)


class PublicWorker:
    """One analysis at a time. The lease fields are an audit marker, not a resume cursor."""

    def __init__(
        self,
        settings: PublicSettings,
        store: PublicStore,
        service: SubmissionService,
        *,
        run_command: Command = subprocess_run,
        start_thread: bool = True,
    ) -> None:
        self.settings = settings
        self.store = store
        self.service = service
        self.worker_id = settings.worker_id
        self._run = run_command
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.recover()
        if start_thread:
            self._thread = threading.Thread(target=self._loop, name="public-submissions", daemon=True)
            self._thread.start()

    def wake(self) -> None:
        """Ask the loop to look for queued submissions."""
        self._wake.set()

    def stop(self) -> None:
        """Stop the background loop."""
        self._stop.set()
        self._wake.set()

    def recover(self) -> list[Submission]:
        """Fail in-flight work. Queued submissions stay queued."""
        failed = self.store.fail_in_flight(INTERRUPTED, utc_now())
        for submission in failed:
            self.service.note_terminal(submission, "failed")
        return failed

    def tick(self) -> bool:
        """Expire old work, then run at most one queued submission."""
        self.service.maintain(datetime.now(UTC))
        return self.pump()

    def pump(self) -> bool:
        """Start the oldest queued submission when a slot is free."""
        if self.store.count_status("processing") >= self.settings.max_concurrent:
            return False
        submission = self.store.oldest_queued()
        if submission is None:
            return False
        self._own(submission)
        self._execute(submission)
        return True

    def _own(self, submission: Submission) -> None:
        submission.status = "processing"
        submission.step = "analyzing"
        submission.worker_id = self.worker_id
        submission.processing_started_at = utc_now()
        submission.updated_at = submission.processing_started_at
        self.store.save_submission(submission)

    def _execute(self, submission: Submission) -> None:
        video = self.store.get_video(submission.id)
        if video is None or not video.object_key:
            self._fail(submission, ANALYSIS_FAILED)
            return
        analysis = self.settings.root / "analysis" / submission.id
        analysis.mkdir(parents=True, exist_ok=True)
        submission.analysis_dir = str(analysis)
        self.store.save_submission(submission)
        dest = analysis / Path(video.object_key).name
        try:
            path, temporary = self.service.storage.local_file(video.object_key, dest)
        except FileNotFoundError:
            self._fail(submission, ANALYSIS_FAILED)
            return
        try:
            self._stages(submission, analysis, path)
        finally:
            if temporary and path.is_file():
                path.unlink()

    def _stages(self, submission: Submission, analysis: Path, video: Path) -> None:
        if not self._stage(submission, "analyzing", analyze_argv(
            self.settings, video, analysis, submission.language
        )):
            self._fail(submission, ANALYSIS_FAILED)
            return
        submission.step = "coaching"
        submission.updated_at = utc_now()
        self.store.save_submission(submission)
        inputs = coach_argv(self.settings, analysis, "coach-inputs")
        if not self._stage(submission, "coaching", inputs):
            self._fail(submission, COACHING_FAILED)
            return
        prototype = coach_argv(
            self.settings,
            analysis,
            "coach-prototype",
            prototype_extra(self.settings),
        )
        index = analysis / _COACH_INPUTS / _INDEX
        if not self._stage(submission, "coaching", prototype) and not index.is_file():
            self._fail(submission, COACHING_FAILED)
            return
        if not index.is_file():
            self._fail(submission, COACHING_FAILED)
            return
        self._frames(submission, analysis, video)
        submission.status = "complete"
        submission.step = "ready"
        submission.error = None
        submission.updated_at = utc_now()
        self.store.save_submission(submission)
        self.service.note_terminal(submission, "ready")
        self.service.release_source_video(submission)

    def _frames(self, submission: Submission, analysis: Path, video: Path) -> None:
        """Copy stills while the source file is still on disk. Failure stays non-fatal."""
        try:
            extract_frames(analysis, video, submission_id=submission.id)
        except Exception:
            logger.exception("moment frame extract failed for submission {}", submission.id)

    def _stage(self, submission: Submission, name: str, argv: list[str]) -> bool:
        code = self._run(argv)
        logger.info("submission {} stage {} exit {}", submission.id, name, code)
        return code == 0

    def _fail(self, submission: Submission, detail: str) -> None:
        submission.status = "failed"
        submission.error = detail
        submission.updated_at = utc_now()
        self.store.save_submission(submission)
        self.service.note_terminal(submission, "failed")

    def _loop(self) -> None:
        while not self._stop.is_set():
            worked = False
            try:
                worked = self.tick()
            except Exception:
                logger.exception("public worker iteration failed")
            if not worked:
                self._wake.wait(0.5)
                self._wake.clear()
