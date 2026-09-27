"""Object storage. Production uploads go to R2. Local dev accepts the PUT itself."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from public_site.models import PresignedUpload
from public_site.s3sign import authorization_headers, presign

_GRANT = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class ObjectStat:
    """Size of one stored object. The key is the one the server asked for."""

    def __init__(self, key: str, size_bytes: int) -> None:
        self.key = key
        self.size_bytes = size_bytes


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

    def presign_put(self, key: str, content_type: str, size_bytes: int) -> PresignedUpload:
        """Issue a one-object PUT grant. The review token is not part of the URL."""
        import secrets

        grant_id = secrets.token_urlsafe(24)
        expires = datetime.now(UTC) + timedelta(seconds=self.url_seconds)
        payload = {
            "object_key": key,
            "size_bytes": size_bytes,
            "content_type": content_type,
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        (self.grants / f"{grant_id}.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return PresignedUpload(
            url=f"/api/dev-upload/{grant_id}",
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=payload["expires_at"],
        )

    def write_grant(self, grant_id: str, body: bytes) -> str:
        """Store bytes for a live grant. Returns the object key."""
        grant = self._grant(grant_id)
        if grant is None:
            raise KeyError(grant_id)
        if len(body) > int(grant["size_bytes"]):
            raise ValueError("upload too large")
        self._write(str(grant["object_key"]), body)
        return str(grant["object_key"])

    def head(self, key: str) -> ObjectStat | None:
        """Return the stored size, or None when the object is absent."""
        path = self._path(key)
        if path is None or not path.is_file():
            return None
        return ObjectStat(key, path.stat().st_size)

    def local_file(self, key: str, dest: Path) -> tuple[Path, bool]:
        """Return a local path. The second value is True when ``dest`` is a copy."""
        path = self._path(key)
        if path is None or not path.is_file():
            raise FileNotFoundError(key)
        return path, False

    def delete(self, key: str) -> None:
        """Remove one object. Missing keys are ignored."""
        path = self._path(key)
        if path is not None and path.is_file():
            path.unlink()

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

    def _path(self, key: str) -> Path | None:
        if not key.startswith("submissions/") or ".." in key.split("/"):
            return None
        path = (self.objects / key).resolve()
        root = self.objects.resolve()
        if path != root and root not in path.parents:
            return None
        return path


class R2Storage:
    """Cloudflare R2 via the S3 API. The application never receives video bytes."""

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
    ) -> None:
        self.account_id = account_id
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket = bucket
        self.url_seconds = url_seconds
        self.cache_dir = cache_dir or Path("/tmp")
        self.host = f"{account_id}.r2.cloudflarestorage.com"

    def presign_put(self, key: str, content_type: str, size_bytes: int) -> PresignedUpload:
        """Presign a PUT for this key only."""
        now = datetime.now(UTC)
        expires_at = (now + timedelta(seconds=self.url_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
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
            },
        )
        return PresignedUpload(
            url=url,
            method="PUT",
            headers={"Content-Type": content_type, "Content-Length": str(size_bytes)},
            expires_at=expires_at,
        )

    def head(self, key: str) -> ObjectStat | None:
        """HEAD the object. A missing object returns None."""
        status, headers, _body = self._request("HEAD", key)
        if status == 404:
            return None
        if status != 200:
            raise OSError(f"storage head failed ({status})")
        length = int(headers.get("Content-Length") or headers.get("content-length") or "0")
        return ObjectStat(key, length)

    def local_file(self, key: str, dest: Path) -> tuple[Path, bool]:
        """Download the object to ``dest``. The caller deletes that copy."""
        status, _headers, body = self._request("GET", key)
        if status != 200 or body is None:
            raise FileNotFoundError(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest, True

    def delete(self, key: str) -> None:
        """Delete the object. A missing object is success."""
        status, _headers, _body = self._request("DELETE", key)
        if status not in {200, 204, 404}:
            raise OSError(f"storage delete failed ({status})")

    def _request(self, method: str, key: str) -> tuple[int, dict[str, str], bytes | None]:
        uri = self._uri(key)
        headers = authorization_headers(
            method=method,
            host=self.host,
            canonical_uri=uri,
            access_key=self.access_key_id,
            secret=self.secret_access_key,
            region="auto",
            service="s3",
            now=datetime.now(UTC),
        )
        request = urllib.request.Request(f"https://{self.host}{uri}", method=method)
        for name, value in headers.items():
            if name == "host":
                continue
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read() if method != "HEAD" else b""
                return response.status, dict(response.headers), payload
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), None

    def _uri(self, key: str) -> str:
        if not key.startswith("submissions/") or ".." in key.split("/"):
            raise ValueError("invalid object key")
        encoded = quote(key, safe="/-_.~")
        return f"/{self.bucket}/{encoded}"
