"""Local annotation UI (blind): stdlib HTTP server bound to 127.0.0.1.

The browser receives only ``AnnotationSession.current_task()`` views and PNG
bytes behind opaque handles. Submissions go straight to the session, which
enforces ordering, vocabulary and finality. No route returns session state,
the answer record, earlier team-identity answers or the A/B mapping
(Amendment 4); a submission is acknowledged only with ``{"ok": true}``.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from map_ink_reference.errors import ReferenceProcedureError
from map_ink_reference.session import AnnotationSession

HOST = "127.0.0.1"
_MAX_BODY = 64 * 1024

PAGE_PATH = Path(__file__).with_name("annotate.html")


def make_handler(session: AnnotationSession) -> type[BaseHTTPRequestHandler]:
    """Request handler bound to one session (no other state is reachable)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug(fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _guard(self, action: Any) -> None:
            try:
                action()
            except (ReferenceProcedureError, KeyError, ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})

        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, PAGE_PATH.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/task":
                return self._guard(lambda: self._json(200, session.current_task()))
            if url.path.startswith("/api/image/"):
                handle = url.path.rsplit("/", 1)[-1]
                return self._guard(
                    lambda: self._send(200, session.image(handle), "image/png")
                )
            if url.path == "/api/loupe":
                q = {k: int(v[0]) for k, v in parse_qs(url.query).items()}
                return self._guard(
                    lambda: self._json(200, session.loupe(q["frame"], q["x"], q["y"]))
                )
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 (stdlib name)
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                return self._json(413, {"error": "request too large"})
            self._guard(
                lambda: self._post(urlparse(self.path).path, self.rfile.read(length))
            )

        def _post(self, path: str, raw: bytes) -> None:
            body = json.loads(raw or b"{}")
            if path == "/api/swatch":
                session.submit_swatch(body["token"], body["picks"])
            elif path == "/api/team":
                session.submit_team(body["token"], body["choice"])
            elif path == "/api/label":
                session.submit_label(body["item_id"], body["label"])
            else:
                return self._json(404, {"error": "not found"})
            self._json(200, {"ok": True})

    return Handler


def serve(session: AnnotationSession, port: int = 8765) -> None:
    """Serve the session on 127.0.0.1 (single-threaded) until interrupted."""
    server = HTTPServer((HOST, port), make_handler(session))
    logger.info("annotation UI on http://{}:{}/", HOST, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
