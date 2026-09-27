"""Run the existing ``s3-coach analyze`` command with a concurrency cap."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from subprocess import Popen
from typing import Any

from vmv_site.experiments import execute_experiment
from vmv_site.models import JobError, JobRecord
from vmv_site.settings import PlatformSettings
from vmv_site.store import PlatformStore, new_id, utc_now

PopenFactory = Callable[..., Popen[str]]


class JobRunner:
    """Queue analyze subprocesses. Tests inject ``popen`` and skip the thread."""

    def __init__(
        self,
        store: PlatformStore,
        *,
        popen: PopenFactory = subprocess.Popen,
        start_thread: bool = True,
        llm_complete: Callable[[str, str], str] | None = None,
        llm_provider: str = "test",
        llm_model: str = "test-model",
    ) -> None:
        self.store = store
        self._popen = popen
        self._llm_complete = llm_complete
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._procs: dict[str, Popen[str]] = {}
        self._owned: set[str] = set()
        self._waiters: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.recover_orphans()
        if start_thread:
            self._thread = threading.Thread(target=self._loop, name="vmv-jobs", daemon=True)
            self._thread.start()

    def wake(self) -> None:
        """Ask the worker to look for queued jobs."""
        self._wake.set()

    def stop(self) -> None:
        """Stop the background loop."""
        self._stop.set()
        self._wake.set()

    def join_workers(self, timeout: float = 5.0) -> None:
        """Wait for analyze waiter threads. Tests use this after a fake process ends."""
        for thread in list(self._waiters):
            thread.join(timeout=timeout)

    def cancel(self, job_id: str) -> JobRecord:
        """Cancel a queued job or terminate a running analyze process."""
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status in {"completed", "failed", "cancelled"}:
            return job
        if job.status == "queued":
            job.status = "cancelled"
            job.phase = "cancelled"
            job.completed_at = utc_now()
            self.store.save_job(job)
            return job
        proc = self._procs.get(job_id)
        job.status = "cancelled"
        job.phase = "cancelled"
        job.completed_at = utc_now()
        self.store.save_job(job)
        if proc is not None and proc.poll() is None:
            proc.terminate()
        return job

    def recover_orphans(self) -> None:
        """Fail running jobs this process does not own.

        A restart does not resume them. The next pump starts a different
        queued job.
        """
        for job in self.store.list_jobs():
            if job.status != "running" or job.id in self._owned:
                continue
            self._interrupt_orphan(job)

    def _interrupt_orphan(self, job: JobRecord) -> None:
        prior = job.phase
        job.status = "failed"
        job.phase = "interrupted"
        job.completed_at = utc_now()
        job.error = JobError(message=_orphan_message(job.kind), phase=prior)
        self.store.save_job(job)

    def _own(self, job_id: str) -> None:
        self._owned.add(job_id)

    def pump_available(self) -> None:
        """Start queued jobs until the concurrency limit is reached."""
        with self._lock:
            while self._running_count() < self.store.settings.max_concurrent:
                job = self.store.next_queued()
                if job is None:
                    return
                self._start(job)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            if self._stop.is_set():
                return
            self.pump_available()

    def _running_count(self) -> int:
        return sum(1 for job in self.store.list_jobs() if job.status == "running")

    def _start(self, job: JobRecord) -> None:
        if job.kind == "coach":
            self._start_coach(job)
            return
        if job.kind == "coach-llm":
            self._start_prototype_llm(job)
            return
        if job.kind == "coach-experiment":
            self._start_experiment(job)
            return
        video = self.store.video_path(job.video_id)
        record = self.store.get_video(job.video_id)
        if video is None or record is None:
            self._fail(job, "stored video is missing", phase="queued")
            return
        run_id = new_id()
        out = self.store.settings.analysis_root / run_id
        out.mkdir(parents=True, exist_ok=True)
        self._own(job.id)
        job.status = "running"
        job.phase = "running: vision"
        job.started_at = utc_now()
        job.run_id = run_id
        self.store.save_job(job)
        log_path = self.store.log_path(job.id)
        log_handle = log_path.open("w", encoding="utf-8")
        command = analyze_command(self.store.settings, video, out, job.language)
        proc = self._popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._procs[job.id] = proc
        waiter = threading.Thread(
            target=self._wait,
            args=(job.id, proc, log_handle, record.original_filename),
            name=f"vmv-job-{job.id[:8]}",
            daemon=True,
        )
        self._waiters.append(waiter)
        waiter.start()

    def _wait(
        self,
        job_id: str,
        proc: Popen[str],
        log_handle: Any,
        filename: str,
    ) -> None:
        code = proc.wait()
        log_handle.close()
        with self._lock:
            self._procs.pop(job_id, None)
        job = self.store.get_job(job_id)
        if job is None or job.status == "cancelled":
            self.wake()
            return
        tail = _tail(self.store.log_path(job_id))
        manifest = self.store.settings.analysis_root / (job.run_id or "") / "vision_manifest.json"
        if code == 0 and job.run_id and manifest.is_file():
            video = self.store.get_video(job.video_id)
            if video is not None:
                self.store.write_link(job.run_id, job.video_id, job.id, filename)
            job.status = "completed"
            job.phase = "completed"
            job.completed_at = utc_now()
            job.error = None
        else:
            job.status = "failed"
            job.phase = "failed"
            job.completed_at = utc_now()
            message = "analysis process failed" if code != 0 else "vision_manifest.json was not written"
            job.error = JobError(
                message=message,
                phase="running: vision",
                exit_code=code,
                log_tail=tail,
            )
        self.store.save_job(job)
        self.wake()

    def _fail(self, job: JobRecord, message: str, *, phase: str, exit_code: int | None = None) -> None:
        job.status = "failed"
        job.phase = "failed"
        job.completed_at = utc_now()
        tail = _tail(self.store.log_path(job.id))
        job.error = JobError(message=message, phase=phase, exit_code=exit_code, log_tail=tail)
        self.store.save_job(job)

    def _start_coach(self, job: JobRecord) -> None:
        folder = self.store.settings.analysis_root / (job.run_id or "")
        if not job.run_id or not (folder / "vision_manifest.json").is_file():
            self._fail(job, "run not found", phase="queued")
            return
        self._own(job.id)
        job.status = "running"
        job.phase = "running: coach-inputs"
        job.started_at = utc_now()
        self.store.save_job(job)
        log_handle = self.store.log_path(job.id).open("w", encoding="utf-8")
        command = coach_command(self.store.settings, folder, "coach-inputs")
        proc = self._popen(command, stdout=log_handle, stderr=subprocess.STDOUT, text=True)
        self._procs[job.id] = proc
        self._spawn(job.id, self._wait_coach, job.id, proc, log_handle)

    def _wait_coach(self, job_id: str, proc: Popen[str], log_handle: Any) -> None:
        code = proc.wait()
        self._forget_proc(job_id)
        job = self._live_job(job_id)
        if job is None:
            log_handle.close()
            return
        log_handle.close()
        if code != 0:
            self._fail(job, "coach-inputs failed", phase="running: coach-inputs", exit_code=code)
            self.wake()
            return
        job.status = "completed"
        job.phase = "completed"
        job.completed_at = utc_now()
        job.error = None
        self.store.save_job(job)
        self.wake()

    def _start_prototype_llm(self, job: JobRecord) -> None:
        folder = self.store.settings.analysis_root / (job.run_id or "")
        index = folder / "coach_inputs" / "coaching_index.json"
        if not job.run_id or not index.is_file():
            self._fail(job, "run coach-inputs first", phase="queued")
            return
        self._own(job.id)
        job.status = "running"
        job.phase = "running: prototype-llm"
        job.started_at = utc_now()
        self.store.save_job(job)
        log_handle = self.store.log_path(job.id).open("w", encoding="utf-8")
        command = coach_command(
            self.store.settings, folder, "coach-prototype", extra=["--call-llm"]
        )
        proc = self._popen(command, stdout=log_handle, stderr=subprocess.STDOUT, text=True)
        self._procs[job.id] = proc
        self._spawn(job.id, self._wait_prototype_llm, job.id, proc, log_handle)

    def _wait_prototype_llm(self, job_id: str, proc: Popen[str], log_handle: Any) -> None:
        code = proc.wait()
        log_handle.close()
        self._forget_proc(job_id)
        current = self._live_job(job_id)
        if current is None:
            return
        if code != 0:
            self._fail(
                current,
                "coach-prototype failed",
                phase="running: prototype-llm",
                exit_code=code,
            )
        else:
            current.status = "completed"
            current.phase = "completed"
            current.completed_at = utc_now()
            current.error = None
            self.store.save_job(current)
        self.wake()

    def _start_experiment(self, job: JobRecord) -> None:
        self._own(job.id)
        job.status = "running"
        job.phase = "running: llm-experiment"
        job.started_at = utc_now()
        self.store.save_job(job)
        self._spawn(job.id, self._wait_experiment, job.id)

    def _wait_experiment(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None or not job.run_id or not job.scenario_id:
            return
        try:
            provider, model, complete = self._llm_binding()
            execute_experiment(
                self.store.settings,
                job.run_id,
                job.scenario_id,
                provider=provider,
                model=model,
                complete=complete,
            )
        except Exception as exc:  # noqa: BLE001 — job status is the user-facing result
            current = self._live_job(job_id)
            if current is not None:
                message = str(exc) or exc.__class__.__name__
                self._fail(current, message, phase="running: llm-experiment")
            self.wake()
            return
        current = self._live_job(job_id)
        if current is None:
            return
        current.status = "completed"
        current.phase = "completed"
        current.completed_at = utc_now()
        current.error = None
        self.store.save_job(current)
        self.wake()

    def _llm_binding(self) -> tuple[str, str, Callable[[str, str], str]]:
        if self._llm_complete is not None:
            return self._llm_provider, self._llm_model, self._llm_complete
        return resolve_production_llm(self.store.settings)

    def _spawn(self, job_id: str, target: Callable[..., None], *args: Any) -> None:
        waiter = threading.Thread(
            target=target,
            args=args,
            name=f"vmv-job-{job_id[:8]}",
            daemon=True,
        )
        self._waiters.append(waiter)
        waiter.start()

    def _forget_proc(self, job_id: str) -> None:
        with self._lock:
            self._procs.pop(job_id, None)

    def _live_job(self, job_id: str) -> JobRecord | None:
        job = self.store.get_job(job_id)
        if job is None or job.status == "cancelled":
            return None
        return job


def _orphan_message(kind: str) -> str:
    """Why a running job was closed on startup. It is not retried."""
    if kind == "coach-experiment":
        return "analysis site restarted while LLM experiment was running"
    if kind == "coach-llm":
        return "analysis site restarted while prototype LLM was running"
    if kind == "coach":
        return "analysis site restarted while coaching preparation was running"
    return "analysis site restarted while analysis was running"


def analyze_command(
    settings: PlatformSettings, video: Path, out: Path, language: str
) -> list[str]:
    """Arguments for the existing analyze CLI. Does not reimplement analysis."""
    import sys

    script = Path(sys.executable).with_name("s3-coach")
    launch = [str(script)] if script.is_file() else [sys.executable, "-c", _CLI_BOOT]
    return [
        *launch,
        "analyze",
        str(video),
        "--out",
        str(out),
        "--language",
        language,
        "--config",
        str(settings.config_path),
        "--debug-persist-cadence-frames",
    ]


_CLI_BOOT = "from splatoon3_ai_coach.cli.app import app; app()"


def coach_command(
    settings: PlatformSettings,
    analysis_dir: Path,
    subcommand: str,
    extra: list[str] | None = None,
) -> list[str]:
    """Arguments for coach-inputs or coach-prototype. Does not rebuild inputs."""
    import sys

    script = Path(sys.executable).with_name("s3-coach")
    launch = [str(script)] if script.is_file() else [sys.executable, "-c", _CLI_BOOT]
    return [
        *launch,
        subcommand,
        str(analysis_dir),
        "--config",
        str(settings.config_path),
        *(extra or []),
    ]


def resolve_production_llm(
    settings: PlatformSettings,
) -> tuple[str, str, Callable[[str, str], str]]:
    """Same provider resolution coach-prototype uses for a single run."""
    from splatoon3_ai_coach.coach.llm_client import normalize_coach_provider
    from splatoon3_ai_coach.coach.llm_runs import build_llm_runs
    from splatoon3_ai_coach.config import load_config
    from splatoon3_ai_coach.config.settings import CoachSettings

    app_config = load_config(settings.config_path)
    coach_settings = CoachSettings()
    provider_name = normalize_coach_provider(
        coach_settings.llm_provider or app_config.coach.provider
    )
    runs = build_llm_runs(
        provider_name=provider_name,
        coach_cfg=app_config.coach,
        settings=coach_settings,
        ollama_model=None,
        nvidia_model_override=None,
        also_baseline=False,
        baseline_model_override=None,
    )
    if not runs:
        raise RuntimeError("no LLM provider configured")
    run = runs[0]
    return provider_name, run.label, run.provider.complete


def _tail(path: Path, limit: int = 4000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]
