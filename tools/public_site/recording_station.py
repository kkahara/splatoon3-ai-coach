"""Operator capture from a local ffmpeg process.

This is the temporary acquisition tool. It produces one finalized file and
then calls the shared publisher. It does not talk to the analysis worker.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from public_site.errors import RequestRejected
from public_site.recording_publish import publish_finalized
from public_site.storage import file_sha256

_MIB = 1024 * 1024
WARN_BYTES = 650 * _MIB
STOP_BYTES = 700 * _MIB
MAX_SECONDS = 720
TAKE_PHASES = frozenset({"capturing", "interrupted", "finalized", "upload_uncertain"})
_DEVICE_LINE = re.compile(r"\[(\d+)\] (.+)$")
_MODE_LINE = re.compile(r"(\d+)x(\d+)@\[(\d+(?:\.\d+)?) (\d+(?:\.\d+)?)\]fps")
_LOG_PREFIX = re.compile(r"^\[[^\]]+ @ 0x[0-9a-f]+\]\s*")
_PERMISSION = re.compile(r"not authori[sz]ed|permission|access.*denied", re.IGNORECASE)
_TAIL_LINES = 40
_MESSAGE_CHARS = 500
FPS_TOLERANCE = 0.5
# avfoundation accepts a requested rate within 0.01 fps of a mode's range.
_AVF_RATE_SLACK = 0.01
PREFERRED_SIZE = (1920, 1080)
PREFERRED_RATES = (60.0, 30.0)
DEVICE_MISSING = (
    "The capture device {name!r} was not found. Reconnect it, then refresh devices."
)
DEVICE_DENIED = (
    "macOS did not allow camera access for the site. Allow it under System Settings, "
    "Privacy & Security, Camera, for the app that runs the site, then restart the site."
)


@dataclass(frozen=True)
class CaptureMode:
    """One video mode a capture device reports: frame size and its frame-rate range."""

    width: int
    height: int
    min_fps: float
    max_fps: float

    def label(self, rate: str) -> str:
        """Operator-facing text such as ``1920x1080 at 60 fps``."""
        return f"{self.width}x{self.height} at {rate} fps"


def parse_capture_modes(text: str) -> list[CaptureMode]:
    """The distinct modes in ffmpeg's avfoundation "Supported modes" list, in order."""
    modes: list[CaptureMode] = []
    for match in _MODE_LINE.finditer(text):
        width, height, low, high = match.groups()
        mode = CaptureMode(int(width), int(height), float(low), float(high))
        if mode not in modes:
            modes.append(mode)
    return modes


def diagnostic_text(lines: list[str]) -> str:
    """The last meaningful ffmpeg lines, without log prefixes, capped for display."""
    kept = [
        _LOG_PREFIX.sub("", line).strip()
        for line in lines
        if line.strip() and "Last message repeated" not in line
    ]
    return " · ".join(kept[-3:])[:_MESSAGE_CHARS]


def _probe_argv(device_name: str) -> list[str]:
    """Ask for 1 fps, which capture devices refuse, so ffmpeg lists the real modes."""
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info",
        "-f", "avfoundation", "-framerate", "1", "-i", f"{device_name}:none",
        "-frames:v", "1", "-f", "null", "-",
    ]


def classify_probe(device_name: str, text: str) -> list[CaptureMode]:
    """Modes from a probe's output, or a rejection that says what went wrong."""
    modes = parse_capture_modes(text)
    if modes:
        return modes
    lines = text.splitlines()
    if "Video device not found" in text:
        raise RequestRejected(409, DEVICE_MISSING.format(name=device_name))
    if _PERMISSION.search(text):
        raise RequestRejected(409, DEVICE_DENIED)
    detail = diagnostic_text(lines)
    if "Supported modes" in text or "not supported by the device" in text:
        raise RequestRejected(409, f"The capture device reported no usable video mode. {detail}")
    raise RequestRejected(503, f"The capture device could not be checked. {detail}".strip())


def discover_capture_modes(device_name: str, run=subprocess.run) -> list[CaptureMode]:
    """Probe one device and return the modes it reports. Nothing is recorded."""
    try:
        completed = run(
            _probe_argv(device_name), capture_output=True, text=True, check=False, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RequestRejected(503, "The capture device could not be checked.") from exc
    return classify_probe(device_name, f"{completed.stderr or ''}\n{completed.stdout or ''}")


def select_capture_mode(modes: list[CaptureMode]) -> tuple[CaptureMode, str]:
    """Prefer 1920x1080 (else the largest frame), then 60, then 30, then the fastest rate.

    Returns the mode and the ``-framerate`` value to request: the nominal rate
    when the device accepts it, otherwise the device's own nearest rate.
    """
    if not modes:
        raise ValueError("no capture modes")
    sized = [mode for mode in modes if (mode.width, mode.height) == PREFERRED_SIZE]
    if not sized:
        area = max(mode.width * mode.height for mode in modes)
        sized = [mode for mode in modes if mode.width * mode.height == area]
    for target in PREFERRED_RATES:
        for mode in sized:
            if mode.min_fps - FPS_TOLERANCE <= target <= mode.max_fps + FPS_TOLERANCE:
                return mode, _request_rate(mode, target)
    fastest = max(sized, key=lambda mode: mode.max_fps)
    return fastest, _request_rate(fastest, fastest.max_fps)


def _request_rate(mode: CaptureMode, target: float) -> str:
    if mode.min_fps - _AVF_RATE_SLACK <= target <= mode.max_fps + _AVF_RATE_SLACK:
        value = target
    else:
        value = min(max(target, mode.min_fps), mode.max_fps)
    return f"{value:.6f}".rstrip("0").rstrip(".")


class OutputTail:
    """Read a child's output as it arrives, keeping only the last lines.

    Draining keeps a full pipe from stalling ffmpeg during a long capture.
    """

    def __init__(self, stream, limit: int = _TAIL_LINES) -> None:
        self._lines: deque[str] = deque(maxlen=limit)
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def _drain(self, stream) -> None:
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._lines.append(line)
        except (OSError, ValueError):
            return

    def lines(self, timeout: float = 2.0) -> list[str]:
        """The kept lines, after waiting briefly for the stream to close."""
        self._thread.join(timeout)
        return list(self._lines)


def explain_capture_failure(lines: list[str]) -> str:
    """Operator text for a capture that ended with an error."""
    detail = diagnostic_text(lines)
    if any("Video device not found" in line for line in lines):
        return (
            "The capture device disappeared before recording started. "
            f"Reconnect it and start again. ({detail})"
        )[:_MESSAGE_CHARS]
    return detail


def parse_avfoundation_devices(text: str) -> list[str]:
    """Video device names between the video and audio sections of ffmpeg's list."""
    names: list[str] = []
    video = False
    for line in text.splitlines():
        if "AVFoundation video devices" in line:
            video = True
            continue
        if "AVFoundation audio devices" in line:
            break
        if not video:
            continue
        match = _DEVICE_LINE.search(line)
        if match:
            names.append(match.group(2).strip())
    return names


def command_owns(command: str, output: Path) -> bool:
    """True when this command is ffmpeg writing this exact file."""
    return "ffmpeg" in command and str(output) in command


def request_graceful_stop(proc: subprocess.Popen, timeout: float = 10) -> None:
    """Ask ffmpeg to finish the file. SIGINT is the same quit if stdin is gone.

    This never sends SIGKILL. A hard kill can leave an unreadable MP4.
    """
    if proc.poll() is not None:
        return
    _write_quit(proc)
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        _interrupt(proc)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg is still running after SIGINT pid={}", proc.pid)


def _write_quit(proc: subprocess.Popen) -> None:
    stdin = proc.stdin
    if stdin is None or stdin.closed:
        return
    try:
        stdin.write(b"q\n")
        stdin.flush()
    except OSError:
        return


def _interrupt(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except OSError:
        try:
            proc.send_signal(signal.SIGINT)
        except OSError:
            return


class RecordingStation:
    """One capture at a time, with the scratch file kept until publication."""

    def __init__(self, recordings, directory: Path) -> None:
        self.recordings = recordings
        self.directory = directory
        self.warn_bytes = WARN_BYTES
        self.stop_bytes = STOP_BYTES
        self.max_seconds = MAX_SECONDS
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._finished: dict[str, threading.Event] = {}
        self._tails: dict[str, OutputTail] = {}
        self._busy: set[str] = set()
        self.discover = discover_capture_modes
        self.directory.mkdir(parents=True, exist_ok=True)
        self.recover()

    def devices(self) -> list[str]:
        """Names ffmpeg can open. Indexes are not returned."""
        return parse_avfoundation_devices(self._device_text())

    def start(self, submission_id: str, actor_id: str, device_name: str, version: int) -> dict:
        """Begin one capture. A second start while this job is recording does nothing new."""
        name = device_name.strip()
        self._require_device(name)
        mode, rate = select_capture_mode(self.discover(name))
        output = self._media(submission_id)
        with self._lock:
            current = self._load(submission_id)
            if current and current["phase"] == "capturing":
                return self._view(current)
            if current and current["phase"] != "verified":
                raise RequestRejected(409, "Discard or upload the recording already on disk.")
            if any(item["phase"] == "capturing" for item in self._all()):
                raise RequestRejected(409, "Another recording is already running.")
            if output.exists():
                raise RequestRejected(409, "Discard or upload the recording already on disk.")
        new_version = self.recordings.database.begin_capture(submission_id, actor_id, version)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = self._open(name, output, mode, rate)
        except RequestRejected:
            self.recordings.database.release_capture(submission_id, actor_id, new_version)
            raise
        record = _blank(submission_id, name, output, proc.pid, new_version)
        record["capture_mode"] = mode.label(rate)
        self._save(record)
        done = threading.Event()
        with self._lock:
            self._procs[submission_id] = proc
            self._finished[submission_id] = done
            if proc.stdout is not None:
                self._tails[submission_id] = OutputTail(proc.stdout)
        threading.Thread(
            target=self._supervise,
            args=(submission_id, proc, output, done),
            name=f"capture-{submission_id[:8]}",
            daemon=True,
        ).start()
        return self._view(record)

    def stop(self, submission_id: str) -> dict:
        """Finish the open capture and leave a probeable file when ffmpeg exits cleanly."""
        with self._lock:
            proc = self._procs.get(submission_id)
            done = self._finished.get(submission_id)
        if proc is None or done is None:
            return self.status(submission_id)
        request_graceful_stop(proc)
        done.wait(timeout=30)
        return self.status(submission_id)

    def accept(self, submission_id: str) -> dict:
        """Keep an interrupted file when it probes cleanly. This does not upload it."""
        record = self._require(submission_id)
        if record["phase"] != "interrupted":
            raise RequestRejected(409, "Only an interrupted recording can be accepted.")
        facts = self._probe(self._media(submission_id))
        digest = file_sha256(self._media(submission_id))
        record.update(
            phase="finalized",
            sha256=digest,
            size_bytes=self._media(submission_id).stat().st_size,
            message=f"Accepted interrupted recording ({facts.duration_seconds:.0f}s).",
        )
        self._save(record)
        return self._view(record)

    def discard(self, submission_id: str, actor_id: str, version: int) -> dict:
        """Delete the scratch file only when no stored object needs a decision."""
        self._stop_if_running(submission_id)
        if self._object_present(submission_id):
            raise RequestRejected(
                409,
                "Resolve the stored recording before discarding this take.",
                "recording_conflict",
            )
        self._delete_scratch(submission_id)
        self.recordings.database.release_capture(submission_id, actor_id, version)
        return {"phase": "absent", "message": "Recording discarded."}

    def upload(self, submission_id: str, actor_id: str, version: int) -> dict:
        """Publish the finalized file. A second call waits on the same attempt."""
        with self._lock:
            if submission_id in self._busy:
                return self.status(submission_id)
            self._busy.add(submission_id)
        try:
            return self._upload(submission_id, actor_id, version)
        finally:
            with self._lock:
                self._busy.discard(submission_id)

    def status(self, submission_id: str) -> dict:
        """Scratch status. A live phase is reported only while its process still owns the file."""
        record = self._load(submission_id)
        if record is None:
            return {"phase": "absent", "message": ""}
        if record["phase"] == "capturing" and not self._still_running(record):
            record["phase"] = "interrupted"
            record["message"] = "The recording process is not running."
            self._save(record)
        return self._view(record)

    def active_takes(self) -> dict[str, dict]:
        """Queue summary of every take that is running or awaiting a decision."""
        takes = {}
        for record in self._all():
            view = self.status(record["submission_id"])
            if view["phase"] in TAKE_PHASES:
                takes[record["submission_id"]] = {
                    key: view[key] for key in ("phase", "elapsed_seconds", "capture_mode", "size_warning")
                }
        return takes

    def recover(self) -> None:
        """Stop leftover ffmpeg processes and keep every unfinished file."""
        for record in self._all():
            if record["phase"] != "capturing":
                continue
            self._reap(record)
            record["phase"] = "interrupted"
            record["message"] = "Recording was interrupted. Accept it or discard it."
            self._save(record)

    def _upload(self, submission_id: str, actor_id: str, version: int) -> dict:
        record = self._require(submission_id)
        if record["phase"] not in {"finalized", "upload_uncertain"} or not record.get("sha256"):
            raise RequestRejected(409, "Finalize the recording before uploading it.")
        record["phase"] = "upload_uncertain"
        self._save(record)
        try:
            publish_finalized(
                self.recordings,
                submission_id,
                actor_id,
                self._media(submission_id),
                record["sha256"],
                version,
            )
        except RequestRejected as exc:
            record["message"] = exc.detail
            record["phase"] = (
                "upload_uncertain" if exc.reason == "recording_conflict" else "finalized"
            )
            self._save(record)
            raise
        self._media(submission_id).unlink(missing_ok=True)
        record["phase"] = "verified"
        record["message"] = "Published and queued for analysis."
        self._save(record)
        return self._view(record)

    def _supervise(
        self,
        submission_id: str,
        proc: subprocess.Popen,
        output: Path,
        done: threading.Event,
    ) -> None:
        started = datetime.now(UTC)
        try:
            self._watch(submission_id, proc, output, started)
            request_graceful_stop(proc)
            self._finalize(submission_id, proc)
        finally:
            with self._lock:
                self._procs.pop(submission_id, None)
            done.set()

    def _watch(
        self,
        submission_id: str,
        proc: subprocess.Popen,
        output: Path,
        started: datetime,
    ) -> None:
        while proc.poll() is None:
            size = output.stat().st_size if output.is_file() else 0
            elapsed = (datetime.now(UTC) - started).total_seconds()
            record = self._load(submission_id)
            if record and size >= self.warn_bytes and not record.get("size_warning"):
                record["size_warning"] = True
                record["message"] = "Recording is near 650 MiB and will stop at 700 MiB."
                self._save(record)
            if size >= self.stop_bytes or elapsed >= self.max_seconds:
                if record:
                    record["size_stop"] = size >= self.stop_bytes
                    self._save(record)
                return
            _sleep_until(proc, 0.25)

    def _finalize(self, submission_id: str, proc: subprocess.Popen) -> None:
        record = self._load(submission_id)
        if record is None or record["phase"] != "capturing":
            return
        media = self._media(submission_id)
        with self._lock:
            tail = self._tails.pop(submission_id, None)
        lines = tail.lines() if tail is not None else []
        if proc.returncode == 0 and media.is_file():
            self._finalize_clean(record, media)
            return
        self._finalize_interrupted(record, media, explain_capture_failure(lines))

    def _finalize_clean(self, record: dict, media: Path) -> None:
        try:
            facts = self._probe(media)
        except RequestRejected as exc:
            record.update(phase="interrupted", acceptable=False, message=exc.detail)
            self._save(record)
            return
        record.update(
            phase="finalized",
            acceptable=False,
            sha256=file_sha256(media),
            size_bytes=media.stat().st_size,
            message=_final_message(record, facts.duration_seconds),
        )
        self._save(record)

    def _finalize_interrupted(self, record: dict, media: Path, error: str) -> None:
        acceptable = False
        message = error or "Recording stopped before a valid file."
        if media.is_file():
            try:
                self._probe(media)
            except RequestRejected as exc:
                message = error or exc.detail
            else:
                acceptable = True
        record.update(phase="interrupted", acceptable=acceptable, message=message)
        self._save(record)

    def _open(
        self, device_name: str, output: Path, mode: CaptureMode, rate: str
    ) -> subprocess.Popen:
        try:
            return self.spawn(_capture_argv(device_name, output, mode, rate), output)
        except OSError as exc:
            raise RequestRejected(503, "The capture process could not be started.") from exc

    def spawn(self, argv: list[str], _output: Path) -> subprocess.Popen:
        """Start ffmpeg in its own process group so a later startup can stop it.

        Output and errors share one pipe, drained by ``OutputTail``.
        """
        return subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def _device_text(self) -> str:
        completed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
            check=False,
        )
        return f"{completed.stderr or ''}\n{completed.stdout or ''}"

    def _require_device(self, name: str) -> None:
        if not name or "\n" in name or '"' in name:
            raise RequestRejected(400, "Choose a capture device.")
        found = [item for item in self.devices() if item == name]
        if len(found) != 1:
            raise RequestRejected(409, "The capture device is not available.")

    def _running(self, submission_id: str) -> tuple[subprocess.Popen, threading.Event]:
        with self._lock:
            proc = self._procs.get(submission_id)
            done = self._finished.get(submission_id)
        if proc is None or done is None:
            raise RequestRejected(409, "No recording is running.")
        return proc, done

    def _stop_if_running(self, submission_id: str) -> None:
        with self._lock:
            proc = self._procs.get(submission_id)
            done = self._finished.get(submission_id)
        if proc is None or done is None:
            return
        request_graceful_stop(proc)
        done.wait(timeout=30)

    def _still_running(self, record: dict) -> bool:
        with self._lock:
            if record["submission_id"] in self._procs:
                return True
        return _pid_owns(int(record.get("pid") or 0), Path(record["output"]))

    def _reap(self, record: dict) -> None:
        pid = int(record.get("pid") or 0)
        output = Path(record["output"])
        if not _pid_owns(pid, output):
            return
        try:
            os.killpg(pid, signal.SIGINT)
        except OSError:
            return
        _wait_pid(pid)

    def _probe(self, path: Path):
        from public_site.media_probe import ProbeRejected

        try:
            return self.recordings.service.probe(path)
        except ProbeRejected as exc:
            raise RequestRejected(400, exc.detail) from None

    def _object_present(self, submission_id: str) -> bool:
        from public_site.recording_intake import intake_key

        try:
            return self.recordings.service.storage.head(intake_key(submission_id)) is not None
        except OSError:
            return True

    def _require(self, submission_id: str) -> dict:
        record = self._load(submission_id)
        if record is None:
            raise RequestRejected(404, "No local recording for this job.")
        return record

    def _media(self, submission_id: str) -> Path:
        return self.directory / submission_id / "source.mp4"

    def _record_path(self, submission_id: str) -> Path:
        return self.directory / submission_id / "recording.json"

    def _load(self, submission_id: str) -> dict | None:
        path = self._record_path(submission_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _all(self) -> list[dict]:
        found = []
        for path in self.directory.glob("*/recording.json"):
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                logger.warning("unreadable recording record {}", path.parent.name)
        return found

    def _save(self, record: dict) -> None:
        path = self._record_path(record["submission_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _delete_scratch(self, submission_id: str) -> None:
        self._media(submission_id).unlink(missing_ok=True)
        self._record_path(submission_id).unlink(missing_ok=True)

    def _view(self, record: dict) -> dict:
        started = record.get("started_at")
        elapsed = None
        if started:
            elapsed = max(0, (datetime.now(UTC) - _stamp(started)).total_seconds())
        return {
            "phase": record["phase"],
            "device_name": record.get("device_name"),
            "elapsed_seconds": elapsed,
            "size_bytes": record.get("size_bytes"),
            "size_warning": bool(record.get("size_warning")),
            "acceptable": bool(record.get("acceptable")),
            "sha256": record.get("sha256"),
            "message": record.get("message") or "",
            "version": record.get("version"),
            "capture_mode": record.get("capture_mode"),
        }


def _capture_argv(device_name: str, output: Path, mode: CaptureMode, rate: str) -> list[str]:
    """Video only (``:none``) at the device's own mode; the encoder output is yuv420p."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "warning",
        "-f",
        "avfoundation",
        "-framerate",
        rate,
        "-video_size",
        f"{mode.width}x{mode.height}",
        "-i",
        f"{device_name}:none",
        "-c:v",
        "h264_videotoolbox",
        "-b:v",
        "8M",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]


def _blank(submission_id: str, device: str, output: Path, pid: int, version: int) -> dict:
    return {
        "submission_id": submission_id,
        "device_name": device,
        "output": str(output),
        "phase": "capturing",
        "pid": pid,
        "version": version,
        "sha256": None,
        "size_bytes": None,
        "size_warning": False,
        "size_stop": False,
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "message": "録画中は、リプレイの初期視点を変更しないでください。",
    }


def _final_message(record: dict, duration: float) -> str:
    if record.get("size_stop"):
        return f"Stopped at the 700 MiB limit ({duration:.0f}s). The file is ready to upload."
    return f"Recording ready ({duration:.0f}s)."


def _sleep_until(proc: subprocess.Popen, seconds: float) -> None:
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return


def _pid_owns(pid: int, output: Path) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="], text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return command_owns(command, output)


def _wait_pid(pid: int) -> None:
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        threading.Event().wait(0.25)


def _stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
