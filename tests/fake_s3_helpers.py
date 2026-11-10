"""A local S3-compatible stub for storage tests. No real R2 is ever contacted.

The server verifies SigV4 header and presigned-query signatures with its own
implementation (not ``public_site.s3sign``), and checks signed payload hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from xml.sax.saxutils import escape

UNSIGNED = "UNSIGNED-PAYLOAD"


@dataclass
class FakeObject:
    """One stored object."""

    body: bytes
    metadata: dict[str, str] = field(default_factory=dict)
    modified: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FakeS3:
    """State shared with the request handler."""

    access_key: str
    secret: str
    bucket: str
    objects: dict[str, FakeObject] = field(default_factory=dict)
    fail_next: list[int] = field(default_factory=list)
    truncate_next_get: bool = False
    requests: list[tuple[str, str]] = field(default_factory=list)
    endpoint: str = ""
    fail_always: int | None = None
    put_delay_seconds: float = 0.0
    put_gate: threading.Event | None = None
    verify_payload: bool = True


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _sign(secret: str, datestamp: str, to_sign: str) -> str:
    key = ("AWS4" + secret).encode()
    for part in (datestamp, "auto", "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()


def _query_string(pairs: list[tuple[str, str]]) -> str:
    encoded = sorted((quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in pairs)
    return "&".join(f"{k}={v}" for k, v in encoded)


def _expected_signature(
    state: FakeS3,
    method: str,
    path: str,
    pairs: list[tuple[str, str]],
    headers: dict[str, str],
    signed: str,
    payload: str,
    amz_date: str,
) -> str:
    block = "".join(
        f"{name}:{headers.get(name, '').strip()}\n" for name in signed.split(";")
    )
    canonical = "\n".join([method, path, _query_string(pairs), block, signed, payload])
    scope = f"{amz_date[:8]}/auto/s3/aws4_request"
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ]
    )
    return _sign(state.secret, amz_date[:8], to_sign)


class _Handler(BaseHTTPRequestHandler):
    state: FakeS3
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_GET(self) -> None:
        self._handle("GET")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        parts = urlsplit(self.path)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        self.state.requests.append((method, parts.path))
        length = int(self.headers.get("content-length") or "0")
        body = self.rfile.read(length) if method == "PUT" and length else b""
        if not self._authorized(method, parts.path, pairs, body):
            return
        if self.state.fail_next:
            self._error(self.state.fail_next.pop(0), "InjectedFailure")
            return
        if self.state.fail_always is not None:
            self._error(self.state.fail_always, "InjectedFailure")
            return
        if method == "PUT":
            if self.state.put_gate is not None:
                self.state.put_gate.wait(30)
            if self.state.put_delay_seconds:
                time.sleep(self.state.put_delay_seconds)
        bucket, _, key = unquote(parts.path).lstrip("/").partition("/")
        if bucket != self.state.bucket:
            self._error(404, "NoSuchBucket")
            return
        if not key:
            self._list(dict(pairs))
            return
        getattr(self, f"_{method.lower()}")(key, body)

    def _authorized(self, method: str, path: str, pairs, body: bytes) -> bool:
        headers = {name.lower(): value for name, value in self.headers.items()}
        query = dict(pairs)
        if "X-Amz-Signature" in query:
            unsigned = [(k, v) for k, v in pairs if k != "X-Amz-Signature"]
            signed = query["X-Amz-SignedHeaders"]
            amz_date = query["X-Amz-Date"]
            access = query["X-Amz-Credential"].split("/")[0]
            given, payload = query["X-Amz-Signature"], UNSIGNED
        else:
            auth = headers.get("authorization", "")
            try:
                fields = dict(
                    item.strip().split("=", 1)
                    for item in auth.split(" ", 1)[1].split(",")
                )
            except (IndexError, ValueError):
                self._error(403, "AccessDenied")
                return False
            unsigned, signed = pairs, fields["SignedHeaders"]
            amz_date = headers.get("x-amz-date", "")
            access = fields["Credential"].split("/")[0]
            given, payload = fields["Signature"], headers.get("x-amz-content-sha256", "")
        expected = _expected_signature(
            self.state, method, path, unsigned, headers, signed, payload, amz_date
        )
        if access != self.state.access_key or not hmac.compare_digest(given, expected):
            self._error(403, "SignatureDoesNotMatch")
            return False
        if (
            self.state.verify_payload
            and payload not in {UNSIGNED, ""}
            and hashlib.sha256(body).hexdigest() != payload
        ):
            self._error(400, "XAmzContentSHA256Mismatch")
            return False
        return True

    def _head(self, key: str, _body: bytes) -> None:
        found = self.state.objects.get(key)
        if found is None:
            self._error(404, "NoSuchKey", head=True)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(found.body)))
        for name, value in found.metadata.items():
            self.send_header(name, value)
        self.end_headers()

    def _get(self, key: str, _body: bytes) -> None:
        found = self.state.objects.get(key)
        if found is None:
            self._error(404, "NoSuchKey")
            return
        body = found.body
        if self.state.truncate_next_get:
            self.state.truncate_next_get = False
            body = body[: len(body) // 2]
        self.send_response(200)
        self.send_header("Content-Length", str(len(found.body)))
        self.end_headers()
        self.wfile.write(body)
        if len(body) != len(found.body):
            self.close_connection = True

    def _put(self, key: str, body: bytes) -> None:
        source = self.headers.get("x-amz-copy-source")
        if source is not None:
            src_bucket, _, src_key = unquote(source).lstrip("/").partition("/")
            found = (
                self.state.objects.get(src_key)
                if src_bucket == self.state.bucket
                else None
            )
            if found is None:
                self._error(404, "NoSuchKey")
                return
            self.state.objects[key] = FakeObject(found.body, dict(found.metadata))
            self._xml(200, '<CopyObjectResult><ETag>"x"</ETag></CopyObjectResult>')
            return
        metadata = {
            name.lower(): value
            for name, value in self.headers.items()
            if name.lower().startswith("x-amz-meta-")
        }
        self.state.objects[key] = FakeObject(body, metadata)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _delete(self, key: str, _body: bytes) -> None:
        self.state.objects.pop(key, None)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _list(self, query: dict[str, str]) -> None:
        prefix = query.get("prefix", "")
        page = int(query.get("max-keys", "1000"))
        keys = sorted(key for key in self.state.objects if key.startswith(prefix))
        start = int(query.get("continuation-token") or "0")
        chunk = keys[start : start + page]
        truncated = start + page < len(keys)
        items = "".join(
            f"<Contents><Key>{escape(key)}</Key>"
            f"<Size>{len(self.state.objects[key].body)}</Size>"
            f"<LastModified>{_iso(self.state.objects[key].modified)}</LastModified>"
            "</Contents>"
            for key in chunk
        )
        token = (
            f"<NextContinuationToken>{start + page}</NextContinuationToken>"
            if truncated
            else ""
        )
        self._xml(
            200,
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"<KeyCount>{len(chunk)}</KeyCount>"
            f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>"
            f"{token}{items}</ListBucketResult>",
        )

    def _xml(self, status: int, text: str) -> None:
        payload = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, code: str, *, head: bool = False) -> None:
        if head or self.command == "HEAD":
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._xml(status, f"<Error><Code>{code}</Code></Error>")


@contextmanager
def fake_s3(
    *,
    access_key: str = "AKFAKE",
    secret: str = "fake-secret",
    bucket: str = "test-bucket",
) -> Iterator[FakeS3]:
    """Run a fake S3 server on 127.0.0.1 for the duration of the block."""
    state = FakeS3(access_key=access_key, secret=secret, bucket=bucket)
    handler = type("Handler", (_Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    state.endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
