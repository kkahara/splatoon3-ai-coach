"""Object storage. Production uploads go to R2. Local dev accepts the PUT itself."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.client import HTTPException, HTTPResponse
from pathlib import Path
from urllib.parse import quote, urlsplit

from public_site.models import PresignedUpload
from public_site.s3sign import authorization_headers, canonical_query, presign

OBJECT_PREFIXES = ("submissions/", "processing/", "reports/", "exports/")
SHA256_METADATA = "x-amz-meta-sha256"
ATTEMPT_METADATA = "x-amz-meta-attempt"
_GRANT = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1024 * 1024
_LIST_PAGE = 1000
_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def valid_key(key: str) -> bool:
    """True for ``<prefix>/<segment>/...`` with an allowed prefix and safe segments."""
    if not key.startswith(OBJECT_PREFIXES):
        return False
    parts = key.split("/")
    return len(parts) >= 2 and all(
        not part.startswith(".") and _SEGMENT.fullmatch(part) for part in parts
    )


def valid_prefix(prefix: str) -> bool:
    """True for an allowed top-level prefix or a valid key path ending in ``/``."""
    return prefix in OBJECT_PREFIXES or (prefix.endswith("/") and valid_key(prefix[:-1]))


def file_sha256(path: Path) -> str:
    """Hex SHA-256 of one file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def is_folder_marker(key: str) -> bool:
    """Zero-byte ``prefix/`` placeholders made by the R2 dashboard. Never data."""
    return key.endswith("/")


class ObjectStat:
    """Size, and the recorded SHA-256 when the object has one. The key is the one asked for.

    ``last_modified`` is filled by listings only. ``attempt`` is the upload
    attempt id an operator grant signed into the object, when it has one.
    """

    def __init__(
        self,
        key: str,
        size_bytes: int,
        sha256: str | None = None,
        last_modified: datetime | None = None,
        attempt: str | None = None,
    ) -> None:
        self.key = key
        self.size_bytes = size_bytes
        self.sha256 = sha256
        self.last_modified = last_modified
        self.attempt = attempt


class ObjectExists(Exception):
    """A conditional PUT found an object already at the key."""


def _attempt_headers(attempt: str | None) -> dict[str, str]:
    """Headers that make a PUT create-only and stamp it with its attempt id."""
    if attempt is None:
        return {}
    if not _GRANT.fullmatch(attempt):
        raise ValueError("invalid upload attempt")
    return {"If-None-Match": "*", ATTEMPT_METADATA: attempt}


class HashingReader:
    """A read-only file wrapper that hashes and counts exactly the bytes handed out."""

    def __init__(self, handle) -> None:
        self._handle = handle
        self._digest = hashlib.sha256()
        self.size_bytes = 0

    def read(self, size: int = -1) -> bytes:
        """Read like the wrapped file and account for every byte returned."""
        block = self._handle.read(size)
        self._digest.update(block)
        self.size_bytes += len(block)
        return block

    def hexdigest(self) -> str:
        """SHA-256 of everything read so far."""
        return self._digest.hexdigest()


def _check_sent(reader: HashingReader, digest: str, size: int) -> None:
    if reader.hexdigest() != digest or reader.size_bytes != size:
        raise OSError("storage put failed (checksum)")


def _hash_stream(handle) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    try:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
            total += len(block)
    except HTTPException:
        raise OSError("storage get failed (incomplete)") from None
    return total, digest.hexdigest()


class LocalStorage:
    """Filesystem objects and a dev-only upload grant. Disabled when using R2."""

    allows_dev_upload = True

    def __init__(self, root: Path, *, url_seconds: int = 900) -> None:
        self.root = root
        self.objects = root / "objects"
        self.grants = root / "upload_grants"
        self.url_seconds = url_seconds
        self.objects.mkdir(parents=True, exist_ok=True)
        self.grants.mkdir(parents=True, exist_ok=True)

    def presign_put(
        self,
        key: str,
        content_type: str,
        size_bytes: int,
        *,
        attempt: str | None = None,
        issued_at: datetime | None = None,
    ) -> PresignedUpload:
        """Issue a one-object PUT grant. The review token is not part of the URL.

        With ``attempt`` the grant is create-only and stamps the object with that
        id; the grant id is the attempt, so the same attempt yields the same URL.
        """
        import secrets

        extra = _attempt_headers(attempt)
        grant_id = attempt or secrets.token_urlsafe(24)
        expires = (issued_at or datetime.now(UTC)) + timedelta(seconds=self.url_seconds)
        payload = {
            "object_key": key,
            "size_bytes": size_bytes,
            "content_type": content_type,
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attempt": attempt,
        }
        (self.grants / f"{grant_id}.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return PresignedUpload(
            url=f"/api/dev-upload/{grant_id}",
            method="PUT",
            headers={"Content-Type": content_type, **extra},
            expires_at=payload["expires_at"],
        )

    def write_grant(self, grant_id: str, body: bytes) -> str:
        """Store bytes for a live grant. Returns the object key.

        An attempt grant never replaces an existing object (``ObjectExists``).
        """
        grant = self._grant(grant_id)
        if grant is None:
            raise KeyError(grant_id)
        if len(body) > int(grant["size_bytes"]):
            raise ValueError("upload too large")
        key = str(grant["object_key"])
        attempt = grant.get("attempt")
        path = self._path(key)
        if attempt and path is not None and path.is_file():
            raise ObjectExists(key)
        self._write(key, body)
        if attempt and path is not None:
            _attempt_sidecar(path).write_text(f"{attempt}\n", encoding="utf-8")
        return key

    def head(self, key: str) -> ObjectStat | None:
        """Return the stored size, or None when the object is absent."""
        path = self._path(key)
        if path is None or not path.is_file():
            return None
        return ObjectStat(
            key,
            path.stat().st_size,
            self._recorded_sha256(path),
            attempt=_read_sidecar(_attempt_sidecar(path)),
        )

    def local_file(self, key: str, dest: Path) -> tuple[Path, bool]:
        """Return a local path. The second value is True when ``dest`` is a copy."""
        path = self._path(key)
        if path is None or not path.is_file():
            raise FileNotFoundError(key)
        return path, False

    def put_file(
        self,
        key: str,
        source: Path,
        content_type: str,
        sha256: str | None = None,
        size_bytes: int | None = None,
    ) -> ObjectStat:
        """Copy ``source`` to ``key`` and record its SHA-256 beside it.

        The bytes read from ``source`` are hashed while copying; they must match
        ``sha256`` (and ``size_bytes`` when given).
        """
        path = self._path(key)
        if path is None:
            raise ValueError("invalid object key")
        digest = sha256 or file_sha256(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".{path.name}.part")
        with source.open("rb") as handle, partial.open("wb") as out:
            reader = HashingReader(handle)
            shutil.copyfileobj(reader, out, _CHUNK)
        try:
            _check_sent(reader, digest, reader.size_bytes if size_bytes is None else size_bytes)
            if file_sha256(partial) != digest:
                raise OSError("storage put failed (checksum)")
        except OSError:
            partial.unlink()
            raise
        partial.replace(path)
        _sha_sidecar(path).write_text(digest + "\n", encoding="utf-8")
        _attempt_sidecar(path).unlink(missing_ok=True)
        return ObjectStat(key, path.stat().st_size, digest)

    def read_sha256(self, key: str) -> tuple[int, str]:
        """Size and SHA-256 of the stored bytes. A missing object raises FileNotFoundError."""
        path = self._path(key)
        if path is None or not path.is_file():
            raise FileNotFoundError(key)
        with path.open("rb") as handle:
            return _hash_stream(handle)

    def list_objects(self, prefix: str) -> list[ObjectStat]:
        """Objects under ``prefix``, sorted by key. Folder markers are excluded."""
        if not valid_prefix(prefix):
            raise ValueError("invalid object prefix")
        found = []
        for path in sorted(self.objects.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            key = path.relative_to(self.objects).as_posix()
            if key.startswith(prefix):
                stat = path.stat()
                modified = datetime.fromtimestamp(stat.st_mtime, UTC)
                found.append(
                    ObjectStat(key, stat.st_size, self._recorded_sha256(path), modified)
                )
        return found

    def copy(self, source_key: str, dest_key: str) -> ObjectStat:
        """Copy one object to a new key. A missing source raises FileNotFoundError."""
        source = self._path(source_key)
        if source is None or not source.is_file():
            raise FileNotFoundError(source_key)
        return self.put_file(dest_key, source, "application/octet-stream")

    def delete(self, key: str) -> None:
        """Remove one object. Missing keys are ignored."""
        path = self._path(key)
        if path is not None and path.is_file():
            path.unlink()
            _sha_sidecar(path).unlink(missing_ok=True)
            _attempt_sidecar(path).unlink(missing_ok=True)

    def _recorded_sha256(self, path: Path) -> str | None:
        sidecar = _sha_sidecar(path)
        if not sidecar.is_file():
            return None
        value = sidecar.read_text(encoding="utf-8").strip()
        return value if _SHA256.fullmatch(value) else None

    def _grant(self, grant_id: str) -> dict[str, object] | None:
        if not _GRANT.fullmatch(grant_id):
            return None
        path = self.grants / f"{grant_id}.json"
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        expires = datetime.strptime(str(payload["expires_at"]), "%Y-%m-%dT%H:%M:%SZ")
        expires = expires.replace(tzinfo=UTC)
        if datetime.now(UTC) > expires:
            return None
        return payload

    def _write(self, key: str, body: bytes) -> None:
        path = self._path(key)
        if path is None:
            raise ValueError("invalid object key")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        _attempt_sidecar(path).unlink(missing_ok=True)

    def _path(self, key: str) -> Path | None:
        if not valid_key(key):
            return None
        path = (self.objects / key).resolve()
        root = self.objects.resolve()
        if path != root and root not in path.parents:
            return None
        return path


def _sha_sidecar(path: Path) -> Path:
    return path.with_name(f".{path.name}.sha256")


def _attempt_sidecar(path: Path) -> Path:
    return path.with_name(f".{path.name}.attempt")


def _read_sidecar(path: Path) -> str | None:
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value if _GRANT.fullmatch(value) else None


class R2Storage:
    """Cloudflare R2 via the S3 API. The application never receives video bytes.

    Request errors carry only the operation and HTTP status, never a URL,
    header, or credential.
    """

    allows_dev_upload = False

    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        url_seconds: int = 900,
        cache_dir: Path | None = None,
        endpoint: str | None = None,
    ) -> None:
        self.account_id = account_id
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket = bucket
        self.url_seconds = url_seconds
        self.cache_dir = cache_dir or Path("/tmp")
        parts = urlsplit(endpoint or f"https://{account_id}.r2.cloudflarestorage.com")
        self.scheme = parts.scheme
        self.host = parts.netloc

    def __repr__(self) -> str:
        return f"R2Storage(bucket={self.bucket!r}, host={self.host!r})"

    def presign_put(
        self,
        key: str,
        content_type: str,
        size_bytes: int,
        *,
        attempt: str | None = None,
        issued_at: datetime | None = None,
    ) -> PresignedUpload:
        """Presign a PUT for this key only.

        With ``attempt`` the PUT is create-only (``If-None-Match: *``) and stamps
        the object with that id. The same ``issued_at`` gives the same URL.
        """
        now = issued_at or datetime.now(UTC)
        expires_at = (now + timedelta(seconds=self.url_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        extra = _attempt_headers(attempt)
        url = presign(
            method="PUT",
            host=self.host,
            canonical_uri=self._uri(key),
            access_key=self.access_key_id,
            secret=self.secret_access_key,
            region="auto",
            service="s3",
            now=now,
            expires=self.url_seconds,
            signed_headers={
                "content-type": content_type,
                "content-length": str(size_bytes),
                **extra,
            },
            scheme=self.scheme,
        )
        return PresignedUpload(
            url=url,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "Content-Length": str(size_bytes),
                **extra,
            },
            expires_at=expires_at,
        )

    def head(self, key: str) -> ObjectStat | None:
        """HEAD the object. A missing object returns None."""
        status, headers, _body = self._request("HEAD", self._uri(key))
        if status == 404:
            return None
        if status != 200:
            raise OSError(f"storage head failed ({status})")
        length = int(_header(headers, "content-length") or "0")
        recorded = _header(headers, SHA256_METADATA)
        sha256 = recorded if recorded and _SHA256.fullmatch(recorded) else None
        stamped = _header(headers, ATTEMPT_METADATA)
        attempt = stamped if stamped and _GRANT.fullmatch(stamped) else None
        return ObjectStat(key, length, sha256, attempt=attempt)

    def local_file(self, key: str, dest: Path) -> tuple[Path, bool]:
        """Download the object to ``dest``. The caller deletes that copy."""
        self.download(key, dest)
        return dest, True

    def download(self, key: str, dest: Path) -> int:
        """Stream the object to ``dest`` without holding it in memory. Returns bytes written.

        Each call writes its own partial file, so concurrent downloads to one
        ``dest`` never share or remove each other's partial data.

        Raises:
            FileNotFoundError: The object does not exist.
            OSError: Any other storage, network, or length failure. ``dest`` is untouched.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            dir=dest.parent, prefix=f".{dest.name}.", suffix=".part"
        )
        partial = Path(name)
        try:
            with (
                os.fdopen(fd, "wb") as out,
                self._stream("GET", self._uri(key)) as response,
            ):
                expected = _header(dict(response.headers), "content-length")
                try:
                    shutil.copyfileobj(response, out, _CHUNK)
                except HTTPException:
                    raise OSError("storage get failed (incomplete)") from None
            written = partial.stat().st_size
            if expected is not None and int(expected) != written:
                raise OSError("storage get failed (length)")
            partial.replace(dest)
            return written
        finally:
            partial.unlink(missing_ok=True)

    def put_file(
        self,
        key: str,
        source: Path,
        content_type: str,
        sha256: str | None = None,
        size_bytes: int | None = None,
    ) -> ObjectStat:
        """Upload ``source`` as ``key``. The store verifies the signed SHA-256 of the body.

        The digest is also stored as ``x-amz-meta-sha256`` so HEAD can verify later.
        The bytes actually sent are hashed and counted; they must equal ``sha256``
        and the declared size, or the put fails with ``checksum``.
        """
        uri = self._uri(key)
        digest = sha256 or file_sha256(source)
        size = source.stat().st_size if size_bytes is None else size_bytes
        extra = {
            "content-type": content_type,
            "content-length": str(size),
            SHA256_METADATA: digest,
        }
        with source.open("rb") as handle:
            reader = HashingReader(handle)
            status, _headers, _body = self._request(
                "PUT", uri, extra_headers=extra, body=reader, payload_sha256=digest
            )
        if status != 200:
            raise OSError(f"storage put failed ({status})")
        _check_sent(reader, digest, size)
        return ObjectStat(key, size, digest)

    def read_sha256(self, key: str) -> tuple[int, str]:
        """Stream the object and return its size and SHA-256. Nothing is written to disk.

        Raises:
            FileNotFoundError: The object does not exist.
            OSError: Any other storage, network, or length failure.
        """
        with self._stream("GET", self._uri(key)) as response:
            expected = _header(dict(response.headers), "content-length")
            size, digest = _hash_stream(response)
        if expected is not None and int(expected) != size:
            raise OSError("storage get failed (length)")
        return size, digest

    def list_objects(self, prefix: str) -> list[ObjectStat]:
        """Objects under ``prefix``, in key order, across every page. Folder markers excluded."""
        if not valid_prefix(prefix):
            raise ValueError("invalid object prefix")
        found: list[ObjectStat] = []
        token: str | None = None
        while True:
            query = {"list-type": "2", "prefix": prefix, "max-keys": str(_LIST_PAGE)}
            if token:
                query["continuation-token"] = token
            status, _headers, body = self._request("GET", f"/{self.bucket}", query=query)
            if status != 200 or body is None:
                raise OSError(f"storage list failed ({status})")
            page, token = _parse_list(body)
            found.extend(item for item in page if not is_folder_marker(item.key))
            if not token:
                return found

    def copy(self, source_key: str, dest_key: str) -> ObjectStat:
        """Server-side copy, keeping metadata. A missing source raises FileNotFoundError."""
        source = self._uri(source_key)
        status, _headers, body = self._request(
            "PUT", self._uri(dest_key), extra_headers={"x-amz-copy-source": source}
        )
        if status == 404:
            raise FileNotFoundError(source_key)
        if status != 200 or body is None or b"<Error>" in body:
            raise OSError(f"storage copy failed ({status})")
        stat = self.head(dest_key)
        if stat is None:
            raise OSError("storage copy failed (missing)")
        return stat

    def delete(self, key: str) -> None:
        """Delete the object. A missing object is success."""
        status, _headers, _body = self._request("DELETE", self._uri(key))
        if status not in {200, 204, 404}:
            raise OSError(f"storage delete failed ({status})")

    def _request(
        self,
        method: str,
        uri: str,
        *,
        query: Mapping[str, str] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        body=None,
        payload_sha256: str | None = None,
    ) -> tuple[int, dict[str, str], bytes | None]:
        try:
            with self._stream(
                method,
                uri,
                query=query,
                extra_headers=extra_headers,
                body=body,
                payload_sha256=payload_sha256,
            ) as response:
                try:
                    payload = response.read() if method != "HEAD" else b""
                except HTTPException:
                    raise OSError(f"storage {method.lower()} failed (incomplete)") from None
                return response.status, dict(response.headers), payload
        except FileNotFoundError:
            return 404, {}, None
        except _StatusError as exc:
            return exc.status, {}, None

    @contextmanager
    def _stream(
        self,
        method: str,
        uri: str,
        *,
        query: Mapping[str, str] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        body=None,
        payload_sha256: str | None = None,
    ) -> Iterator[HTTPResponse]:
        headers = authorization_headers(
            method=method,
            host=self.host,
            canonical_uri=uri,
            access_key=self.access_key_id,
            secret=self.secret_access_key,
            region="auto",
            service="s3",
            now=datetime.now(UTC),
            extra_headers=extra_headers,
            query=query,
            payload_sha256=payload_sha256,
        )
        suffix = f"?{canonical_query(query)}" if query else ""
        request = urllib.request.Request(
            f"{self.scheme}://{self.host}{uri}{suffix}", data=body, method=method
        )
        for name, value in headers.items():
            if name == "host":
                continue
            request.add_header(name, value)
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 404:
                raise FileNotFoundError(uri.rsplit("/", 1)[-1]) from None
            raise _StatusError(exc.code) from None
        except (urllib.error.URLError, OSError, HTTPException) as exc:
            raise OSError(f"storage {method.lower()} failed (network: {type(exc).__name__})") from None
        with response:
            yield response

    def _uri(self, key: str) -> str:
        if not valid_key(key):
            raise ValueError("invalid object key")
        encoded = quote(key, safe="/-_.~")
        return f"/{self.bucket}/{encoded}"


class _StatusError(OSError):
    """A non-404 HTTP error status from the store. Carries only the code."""

    def __init__(self, status: int) -> None:
        super().__init__(f"storage request failed ({status})")
        self.status = status


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _parse_list(body: bytes) -> tuple[list[ObjectStat], str | None]:
    """One ListObjectsV2 page and its continuation token (None on the last page)."""
    root = ET.fromstring(body)
    items = [
        ObjectStat(
            item.findtext(f"{_S3_NS}Key") or "",
            int(item.findtext(f"{_S3_NS}Size") or "0"),
            last_modified=_parse_time(item.findtext(f"{_S3_NS}LastModified")),
        )
        for item in root.iter(f"{_S3_NS}Contents")
    ]
    truncated = (root.findtext(f"{_S3_NS}IsTruncated") or "").lower() == "true"
    token = root.findtext(f"{_S3_NS}NextContinuationToken") if truncated else None
    return items, token or None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


REPORTS_PREFIX = "reports/"
Storage = LocalStorage | R2Storage


def _report_key(key: str) -> str:
    if not key.startswith(REPORTS_PREFIX) or not valid_key(key):
        raise ValueError("invalid report key")
    return key


def _report_prefix(prefix: str) -> str:
    if not prefix.startswith(REPORTS_PREFIX) or not valid_prefix(prefix):
        raise ValueError("invalid report prefix")
    return prefix


class ReportWriter:
    """Publishing capability: HEAD, PUT, read-back and LIST under ``reports/``.

    It has no delete and no copy. The publisher only ever receives this view.
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    @property
    def bucket(self) -> str:
        """Bucket name for operator output (never a URL or credential)."""
        return str(getattr(self._storage, "bucket", "local"))

    def head(self, key: str) -> ObjectStat | None:
        """HEAD one report object."""
        return self._storage.head(_report_key(key))

    def put_file(
        self, key: str, source: Path, content_type: str, sha256: str, size_bytes: int
    ) -> ObjectStat:
        """Upload one report object; the bytes sent must match ``sha256`` and size."""
        return self._storage.put_file(
            _report_key(key), source, content_type, sha256, size_bytes
        )

    def read_sha256(self, key: str) -> tuple[int, str]:
        """Read one report object back and hash it."""
        return self._storage.read_sha256(_report_key(key))

    def list_objects(self, prefix: str) -> list[ObjectStat]:
        """List report objects under ``prefix``."""
        return self._storage.list_objects(_report_prefix(prefix))


class ReportLister:
    """Read-only listing under ``reports/`` for status output. No GET, PUT or DELETE."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    @property
    def bucket(self) -> str:
        """Bucket name for operator output."""
        return str(getattr(self._storage, "bucket", "local"))

    def list_objects(self, prefix: str) -> list[ObjectStat]:
        """List report objects under ``prefix``."""
        return self._storage.list_objects(_report_prefix(prefix))


class ReportEraser:
    """Deleting capability for expiry deletion and the sweep: LIST and DELETE under
    ``reports/`` only. It cannot write or read object contents.
    """

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    @property
    def bucket(self) -> str:
        """Bucket name for operator output."""
        return str(getattr(self._storage, "bucket", "local"))

    def list_objects(self, prefix: str) -> list[ObjectStat]:
        """List report objects under ``prefix``."""
        return self._storage.list_objects(_report_prefix(prefix))

    def delete(self, key: str) -> None:
        """Delete one report object. A missing object is success."""
        self._storage.delete(_report_key(key))
