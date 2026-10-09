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
from time import perf_counter

from loguru import logger

from public_site.errors import ANALYSIS_FAILED, COACHING_FAILED, INTERRUPTED
from public_site.frames import extract_frames
from public_site.models import CoachingOutcome, Submission
from public_site.service import SubmissionService
from public_site.settings import PublicSettings
from public_site.store import PublicStore, utc_now
from public_site.telemetry import CoachingOutcome as TelemetryOutcome
from public_site.telemetry import (
    Diagnostic,
    Stage,
    best_effort,
    classify_error,
)
from splatoon3_ai_coach.cli.coach_inputs import NO_COACHING_UNITS_EXIT_CODE
from splatoon3_ai_coach.coach.artifacts import coaching_index_path

Command = Callable[[list[str]], int]


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
        self.telemetry = service.telemetry
        self.worker_id = settings.worker_id
        self._run = run_command
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stage_seconds: dict[str, dict[str, float]] = {}
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
            self._capture_terminal(submission)
            if self.telemetry is not None:
                best_effort(
                    "interrupted",
                    self.telemetry.interrupted,
                    submission.id,
                )
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
        self._stage_seconds[submission.id] = {}
        if self.telemetry is not None:
            best_effort("started", self.telemetry.started, submission.id)

    def _execute(self, submission: Submission) -> None:
        video = self.store.get_video(submission.id)
        if video is None or not video.object_key:
            self._fail(
                submission,
                ANALYSIS_FAILED,
                stage=Stage.ANALYZE,
                diagnostic=Diagnostic.SOURCE_MISSING,
            )
            return
        analysis = self.settings.root / "analysis" / submission.id
        analysis.mkdir(parents=True, exist_ok=True)
        submission.analysis_dir = str(analysis)
        self.store.save_submission(submission)
        dest = analysis / Path(video.object_key).name
        try:
            path, temporary = self.service.storage.local_file(video.object_key, dest)
        except FileNotFoundError:
            self._fail(
                submission,
                ANALYSIS_FAILED,
                stage=Stage.ANALYZE,
                diagnostic=Diagnostic.SOURCE_MISSING,
            )
            return
        try:
            self._stages(submission, analysis, path)
        finally:
            if temporary and path.is_file():
                path.unlink()

    def _stages(self, submission: Submission, analysis: Path, video: Path) -> None:
        if self._stage(submission, Stage.ANALYZE, analyze_argv(
            self.settings, video, analysis, submission.language
        )):
            self._fail(submission, ANALYSIS_FAILED, stage=Stage.ANALYZE)
            return
        self._capture_trace(submission, analysis)
        submission.step = "coaching"
        submission.updated_at = utc_now()
        self.store.save_submission(submission)
        inputs = coach_argv(self.settings, analysis, "coach-inputs")
        code = self._stage(submission, Stage.COACH_INPUTS, inputs)
        if code == NO_COACHING_UNITS_EXIT_CODE:
            self._capture_trace(submission, analysis)
            self._complete(submission, "no_deaths")
            return
        if code:
            self._fail(submission, COACHING_FAILED, stage=Stage.COACH_INPUTS)
            return
        self._capture_trace(submission, analysis)
        prototype = coach_argv(
            self.settings,
            analysis,
            "coach-prototype",
            prototype_extra(self.settings),
        )
        index = coaching_index_path(analysis)
        if (
            self._stage(submission, Stage.COACH_PROTOTYPE, prototype)
            and not index.is_file()
        ):
            self._fail(submission, COACHING_FAILED, stage=Stage.COACH_PROTOTYPE)
            return
        if not index.is_file():
            self._fail(
                submission,
                COACHING_FAILED,
                stage=Stage.COACH_PROTOTYPE,
                diagnostic=Diagnostic.MISSING_ARTIFACT,
            )
            return
        self._capture_trace(submission, analysis)
        self._frames(submission, analysis, video)
        self._capture_trace(submission, analysis)
        self._complete(submission, "coached")

    def _complete(self, submission: Submission, outcome: CoachingOutcome) -> None:
        """Finish successfully. ``no_deaths`` is a valid empty result, not a failure."""
        submission.status = "complete"
        submission.step = "ready"
        submission.error = None
        submission.outcome = outcome
        submission.updated_at = utc_now()
        self.store.save_submission(submission)
        if self.telemetry is not None:
            best_effort(
                "completed",
                self.telemetry.completed,
                submission.id,
                outcome=TelemetryOutcome(outcome),
            )
        notice = "no_moments" if outcome == "no_deaths" else "ready"
        self.service.note_terminal(submission, notice)
        self.service.release_source_video(submission)
        self._stage_seconds.pop(submission.id, None)

    def _frames(self, submission: Submission, analysis: Path, video: Path) -> None:
        """Copy stills while the source file is still on disk. Failure stays non-fatal."""
        started = perf_counter()
        if self.telemetry is not None:
            best_effort(
                "frames_started",
                self.telemetry.stage_started,
                submission.id,
                Stage.FRAMES,
            )
        exit_code = 0
        try:
            extract_frames(analysis, video, submission_id=submission.id)
        except Exception:
            exit_code = 1
            logger.exception("moment frame extract failed for submission {}", submission.id)
        finally:
            seconds = perf_counter() - started
            self._stage_seconds.setdefault(submission.id, {})[Stage.FRAMES.value] = seconds
            if self.telemetry is not None:
                best_effort(
                    "frames_finished",
                    self.telemetry.stage_finished,
                    submission.id,
                    Stage.FRAMES,
                    exit_code=exit_code,
                    seconds=seconds,
                )

    def _stage(self, submission: Submission, stage: Stage, argv: list[str]) -> int:
        """Run one CLI stage and return its exit code (0 on success)."""
        started = perf_counter()
        if self.telemetry is not None:
            best_effort(
                "stage_started",
                self.telemetry.stage_started,
                submission.id,
                stage,
            )
        code = self._run(argv)
        seconds = perf_counter() - started
        self._stage_seconds.setdefault(submission.id, {})[stage.value] = seconds
        if self.telemetry is not None:
            best_effort(
                "stage_finished",
                self.telemetry.stage_finished,
                submission.id,
                stage,
                exit_code=code,
                seconds=seconds,
            )
        logger.info("submission {} stage {} exit {}", submission.id, stage.value, code)
        return code

    def _fail(
        self,
        submission: Submission,
        detail: str,
        *,
        stage: Stage | None,
        diagnostic: Diagnostic | None = None,
    ) -> None:
        submission.status = "failed"
        submission.error = detail
        submission.updated_at = utc_now()
        self.store.save_submission(submission)
        self._capture_terminal(submission)
        if self.telemetry is not None:
            code, default_diagnostic = classify_error(detail)
            best_effort(
                "failed",
                self.telemetry.failed,
                submission.id,
                stage=stage,
                error_code=code,
                diagnostic=diagnostic or default_diagnostic,
            )
        self.service.note_terminal(submission, "failed")
        self._stage_seconds.pop(submission.id, None)

    def _capture_trace(self, submission: Submission, analysis: Path) -> None:
        """Read safe artifact summaries without making coaching depend on telemetry."""
        if self.telemetry is None:
            return
        best_effort(
            "artifact_trace",
            self._write_trace,
            submission.id,
            analysis,
        )

    def _capture_terminal(self, submission: Submission) -> None:
        """Save the best trace that already exists before a terminal status."""
        analysis = self._analysis_dir(submission)
        if analysis is not None:
            self._capture_trace(submission, analysis)

    def _analysis_dir(self, submission: Submission) -> Path | None:
        if submission.analysis_dir:
            path = Path(submission.analysis_dir)
            return path if path.is_dir() else None
        candidate = self.settings.root / "analysis" / submission.id
        return candidate if candidate.is_dir() else None

    def _write_trace(self, submission_id: str, analysis: Path) -> None:
        from public_site.job_trace import (
            TopLevelTiming,
            read_job_trace,
            read_llm_calls,
            scalar_provenance,
        )
        from splatoon3_ai_coach.coach.artifacts import coach_prototype_dir

        top_level = TopLevelTiming(**self._stage_seconds.get(submission_id, {}))
        trace = read_job_trace(analysis, top_level=top_level)
        calls = read_llm_calls(coach_prototype_dir(analysis) / "llm_metrics.jsonl")
        self.telemetry.update_artifacts(
            submission_id,
            stage_seconds=trace.timing,
            llm=trace.llm_metrics,
            provenance=trace.operational_provenance(),
            scalar=scalar_provenance(trace),
            llm_calls=calls,
        )

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
