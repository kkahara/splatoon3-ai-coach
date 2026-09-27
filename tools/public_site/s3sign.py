"""AWS Signature Version 4 for S3-compatible PUT, GET, HEAD, and DELETE."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import quote

_ALGO = "AWS4-HMAC-SHA256"
_PAYLOAD = "UNSIGNED-PAYLOAD"


def signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    """Derive the SigV4 signing key."""
    key = _hmac(("AWS4" + secret).encode("utf-8"), datestamp)
    key = _hmac(key, region)
    key = _hmac(key, service)
    return _hmac(key, "aws4_request")


def presign(
    *,
    method: str,
    host: str,
    canonical_uri: str,
    access_key: str,
    secret: str,
    region: str,
    service: str,
    now: datetime,
    expires: int,
    signed_headers: Mapping[str, str],
) -> str:
    """Return a presigned URL. ``signed_headers`` excludes ``host`` (added here)."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    credential = f"{access_key}/{scope}"
    headers = {"host": host, **{name.lower(): value for name, value in signed_headers.items()}}
    signed_names = ";".join(sorted(headers))
    params = {
        "X-Amz-Algorithm": _ALGO,
        "X-Amz-Credential": credential,
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": signed_names,
    }
    query = _canonical_query(params)
    canonical = _canonical_request(method, canonical_uri, query, headers, signed_names)
    signature = _signature(secret, datestamp, region, service, amz_date, scope, canonical)
    return f"https://{host}{canonical_uri}?{query}&X-Amz-Signature={signature}"


def authorization_headers(
    *,
    method: str,
    host: str,
    canonical_uri: str,
    access_key: str,
    secret: str,
    region: str,
    service: str,
    now: datetime,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Headers for one signed request made by the server, not the browser."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    headers = {
        "host": host,
        "x-amz-content-sha256": _PAYLOAD,
        "x-amz-date": amz_date,
    }
    for name, value in (extra_headers or {}).items():
        headers[name.lower()] = value
    signed_names = ";".join(sorted(headers))
    canonical = _canonical_request(method, canonical_uri, "", headers, signed_names)
    signature = _signature(secret, datestamp, region, service, amz_date, scope, canonical)
    credential = f"{access_key}/{scope}"
    headers["Authorization"] = (
        f"{_ALGO} Credential={credential}, SignedHeaders={signed_names}, Signature={signature}"
    )
    return headers


def _canonical_request(
    method: str,
    canonical_uri: str,
    query: str,
    headers: Mapping[str, str],
    signed_names: str,
) -> str:
    names = signed_names.split(";")
    block = "".join(f"{name}:{headers[name].strip()}\n" for name in names)
    return "\n".join([method, canonical_uri, query, block, signed_names, _PAYLOAD])


def _canonical_query(params: Mapping[str, str]) -> str:
    encoded = [
        (quote(key, safe="-_.~"), quote(value, safe="-_.~")) for key, value in params.items()
    ]
    encoded.sort()
    return "&".join(f"{key}={value}" for key, value in encoded)


def _signature(
    secret: str,
    datestamp: str,
    region: str,
    service: str,
    amz_date: str,
    scope: str,
    canonical: str,
) -> str:
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    to_sign = "\n".join([_ALGO, amz_date, scope, digest])
    key = signing_key(secret, datestamp, region, service)
    return hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
