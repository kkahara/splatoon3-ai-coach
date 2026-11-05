"""Local blinded rating service. Reads only explicit files inside blind/."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

MEASURES = (
    "usefulness_1to5",
    "actionability_1to5",
    "specificity_1to5",
    "redundancy_1to5",
    "evidence_supported_yn",
    "opportunity_lost_yn",
)
FIELDS = [f"{side}_{name}" for side in "AB" for name in MEASURES]
COLUMNS = ["blind_id", *FIELDS, "preferred_A_B_tie", "notes"]
REQUIRED = [*FIELDS, "preferred_A_B_tie"]


class Pending(BaseModel):
    """A question needing evidence; never a completed rating."""

    model_config = ConfigDict(extra="forbid")
    blind_id: str = Field(pattern=r"^R[1-9]$")
    field: str
    reason: str = Field(default="", max_length=4000)


class ReviewState(BaseModel):
    """Sidecar independent of the canonical analysis CSV."""

    model_config = ConfigDict(extra="forbid")
    pending: list[Pending] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)


class Edit(BaseModel):
    """Optimistic, explicit field edit from one browser."""

    model_config = ConfigDict(extra="forbid")
    revision: str
    blind_id: str
    field: str
    value: str = Field(max_length=20000)
    kind: Literal["rating", "pending", "reason"] = "rating"


def atomic_write(path: Path, data: bytes) -> None:
    """Flush a sibling temporary file before atomically replacing the destination."""
    fd, name = tempfile.mkstemp(prefix=".review-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_value(field: str, value: str) -> None:
    """Validate partial ratings with the existing scales (including legacy yes/no)."""
    if field not in COLUMNS[1:]:
        raise ValueError("Unknown question")
    if not value or field == "notes":
        return
    allowed = {"A", "B", "tie"}
    if field.endswith("_yn"):
        allowed = {"y", "n", "yes", "no"}
    elif field.endswith("_1to5"):
        allowed = {"1", "2", "3", "4", "5"}
    if value not in allowed:
        raise ValueError("Invalid rating value")


class ReviewStore:
    """Allowlisted packet reader and serialized, conflict-checked rating writer."""

    def __init__(self, blind: Path) -> None:
        self.blind = blind.resolve()
        self.lock = threading.Lock()
        self.sheet = self._file("rating_sheet.csv")
        self.sidecar = self._file("review_ui_state.json")
        media_file = self.blind.parent / "review_ui_media.json"
        self.media = json.loads(media_file.read_text()) if media_file.is_file() else {}
        self.instructions = self._file("INSTRUCTIONS.md").read_text(encoding="utf-8")
        self.reports = {
            f"R{i}": {
                s: self._file(f"R{i}/{s}.md").read_text(encoding="utf-8") for s in "AB"
            }
            for i in range(1, 10)
        }
        self.snapshot()

    def _file(self, relative: str) -> Path:
        path = self.blind / relative
        if path.resolve() != path.absolute():
            raise ValueError("Symlinks are not allowed in review inputs")
        return path

    def _load(self) -> tuple[list[dict[str, str]], ReviewState, str]:
        raw = self.sheet.read_bytes()
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        rows = list(reader)
        if reader.fieldnames != COLUMNS or [r["blind_id"] for r in rows] != list(
            self.reports
        ):
            raise ValueError("Unexpected rating schema, pair order or duplicate rows")
        for row in rows:
            if set(row) != set(COLUMNS) or any(v is None for v in row.values()):
                raise ValueError("Malformed rating row")
            for field in COLUMNS[1:]:
                validate_value(field, row[field])
        state_raw = self.sidecar.read_bytes() if self.sidecar.exists() else b"{}"
        state = ReviewState.model_validate_json(state_raw)
        if any(p.field not in REQUIRED for p in state.pending):
            raise ValueError("Unknown pending question")
        revision = hashlib.sha256(raw + b"\0" + state_raw).hexdigest()
        return rows, state, revision

    def snapshot(self) -> dict[str, Any]:
        """Return only blinded packet text, ratings and pending questions."""
        with self.lock:
            rows, state, revision = self._load()
            return dict(
                rows=rows,
                pending=state.model_dump()["pending"],
                reasons=state.reasons,
                media=self.media_summary(),
                revision=revision,
                reports=self.reports,
                instructions=self.instructions,
                queue=self.evidence_queue(),
            )

    def media_summary(self) -> dict[str, Any]:
        """Expose neutral recording availability and stored moment timestamps only."""
        result = {}
        for bid in self.reports:
            entry = self.media.get(bid, {})
            path = Path(entry["video"]) if entry.get("video") else None
            available = bool(
                path
                and path.is_file()
                and path.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}
            )
            images = {
                title: f"/image/{bid}/{index}"
                for index, (title, filename) in enumerate(entry.get("images", {}).items())
                if Path(filename).is_file()
            }
            result[bid] = {
                "available": available,
                "moments": entry.get("moments", {}),
                "images": images,
            }
        return result

    def evidence_queue(self) -> list[dict[str, Any]]:
        """List missing recordings, timestamps and optional saved screenshots."""
        media = self.media_summary()
        queue = []
        for bid, reports in self.reports.items():
            titles = dict.fromkeys(re.findall(r"^### (Death — .+)$", reports["A"], re.M))
            for title in titles:
                if not media[bid]["available"] or title not in media[bid]["moments"]:
                    queue.append(
                        dict(
                            blind_id=bid,
                            moment=title,
                            available=False,
                            needed="Gameplay video"
                            if not media[bid]["available"]
                            else "Verified moment timestamp",
                            reason="Recording not found or unsupported."
                            if not media[bid]["available"]
                            else "No unambiguous stored timestamp for this moment.",
                        )
                    )
                if title not in media[bid]["images"]:
                    queue.append(
                        dict(
                            blind_id=bid,
                            moment=title,
                            available=False,
                            needed="Saved screenshot (optional)",
                            reason="No saved evidence frame was found for this moment.",
                        )
                    )
        return queue

    def edit(self, edit: Edit) -> None:
        """Apply only an explicit edit; reject stale tabs without losing any rating."""
        with self.lock:
            rows, state, revision = self._load()
            if edit.revision != revision:
                raise ValueError("Another tab changed the sheet. Reload before editing.")
            if edit.blind_id not in self.reports:
                raise ValueError("Unknown pair")
            if edit.kind == "reason":
                if edit.field != "reason" or edit.value not in {
                    "",
                    "specific",
                    "repetition",
                    "missing",
                    "evidence",
                    "other",
                }:
                    raise ValueError("Invalid reason")
                state.reasons[edit.blind_id] = edit.value
                atomic_write(self.sidecar, state.model_dump_json(indent=2).encode())
                return
            if edit.kind == "pending":
                if edit.field not in REQUIRED:
                    raise ValueError("Unknown question")
                state.pending = [
                    p
                    for p in state.pending
                    if (p.blind_id, p.field) != (edit.blind_id, edit.field)
                ]
                if edit.value:
                    state.pending.append(
                        Pending(
                            blind_id=edit.blind_id, field=edit.field, reason=edit.value
                        )
                    )
                atomic_write(self.sidecar, state.model_dump_json(indent=2).encode())
                return
            validate_value(edit.field, edit.value)
            row = next(r for r in rows if r["blind_id"] == edit.blind_id)
            if row[edit.field] == edit.value:
                return
            row[edit.field] = edit.value
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            backup = self._file("rating_sheet.pre_ui.csv")
            if not backup.exists():
                with backup.open("xb") as stream:
                    stream.write(self.sheet.read_bytes())
            atomic_write(self.sheet, output.getvalue().encode("utf-8"))


def serve(blind: Path, port: int = 8766) -> None:
    """Serve only the evaluator on loopback, with a per-launch write token."""
    store = ReviewStore(blind)
    token = secrets.token_urlsafe(32)
    asset_dir = Path(__file__).with_name("coaching_diversity_review_assets")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # Never log packet text, filenames or user notes.

        def reply(self, status: int, body: bytes, mime: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def video(self, bid: str) -> None:
            if bid not in store.reports or not store.media_summary()[bid]["available"]:
                self.reply(404, b"Not found", "text/plain")
                return
            path = Path(store.media[bid]["video"])
            size = path.stat().st_size
            from vision_manifest_viewer.server import parse_byte_range

            header = self.headers.get("Range")
            selected = parse_byte_range(header, size)
            if header and selected is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            start, end = selected or (0, size - 1)
            self.send_response(206 if selected else 200)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if selected:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                try:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_GET(self) -> None:
            image_match = re.fullmatch(r"/image/(R[1-9])/(\d+)", self.path)
            if image_match:
                bid, index = image_match.groups()
                images = list(store.media.get(bid, {}).get("images", {}).values())
                if int(index) >= len(images) or not Path(images[int(index)]).is_file():
                    self.reply(404, b"Not found", "text/plain")
                    return
                self.reply(200, Path(images[int(index)]).read_bytes(), "image/jpeg")
            elif re.fullmatch(r"/media/R[1-9]", self.path):
                self.video(self.path.rsplit("/", 1)[1])
            elif self.path == "/api":
                data = store.snapshot() | {"token": token}
                self.reply(
                    200,
                    json.dumps(data, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            elif self.path in ("/", "/app.js", "/style.css"):
                name = {"/": "index.html"}.get(self.path, self.path[1:])
                mime = {
                    "/": "text/html",
                    "/app.js": "text/javascript",
                    "/style.css": "text/css",
                }[self.path]
                self.reply(200, (asset_dir / name).read_bytes(), mime + "; charset=utf-8")
            else:
                self.reply(404, b"Not found", "text/plain")

        def do_POST(self) -> None:
            if self.path != "/api" or self.headers.get("X-Review-Token") != token:
                self.reply(403, b"Forbidden", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100000:
                    raise ValueError("Invalid request size")
                store.edit(Edit.model_validate_json(self.rfile.read(length)))
                self.reply(200, b"{}", "application/json")
            except (ValueError, OSError):
                self.reply(
                    409,
                    b"Edit could not be saved. Reload to check for "
                    b"conflicting changes; your unsaved edit is still visible.",
                    "text/plain",
                )

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    logger.warning(
        "Coaching Report Comparison: http://127.0.0.1:{}", server.server_address[1]
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
