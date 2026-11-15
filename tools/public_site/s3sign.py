"""AWS Signature Version 4 for S3-compatible PUT, GET, HEAD, DELETE, list, and copy."""

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
    scheme: str = "https",
    extra_query: Mapping[str, str] | None = None,
) -> str:
    """Return a presigned URL. ``signed_headers`` excludes ``host`` (added here).

    ``extra_query`` values (for example ``response-content-type``) are included in
    the canonical query string. Unsigned browser headers such as ``Range`` are
    intentionally omitted from ``signed_headers`` so seeking still works.
    """
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
    if extra_query:
        params.update(extra_query)
    query = _canonical_query(params)
    canonical = _canonical_request(method, canonical_uri, query, headers, signed_names, _PAYLOAD)
    signature = _signature(secret, datestamp, region, service, amz_date, scope, canonical)
    return f"{scheme}://{host}{canonical_uri}?{query}&X-Amz-Signature={signature}"


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
    query: Mapping[str, str] | None = None,
    payload_sha256: str | None = None,
) -> dict[str, str]:
    """Headers for one signed request made by the server, not the browser.

    ``query`` must be the exact parameters sent (see ``canonical_query``).
    ``payload_sha256`` is the hex digest of the body; the store rejects a body
    that does not match it. Without it the payload is unsigned.
    """
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    payload = payload_sha256 or _PAYLOAD
    headers = {
        "host": host,
        "x-amz-content-sha256": payload,
        "x-amz-date": amz_date,
    }
    for name, value in (extra_headers or {}).items():
        headers[name.lower()] = value
    signed_names = ";".join(sorted(headers))
    canonical = _canonical_request(
        method, canonical_uri, canonical_query(query or {}), headers, signed_names, payload
    )
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
    payload: str,
) -> str:
    names = signed_names.split(";")
    block = "".join(f"{name}:{headers[name].strip()}\n" for name in names)
    return "\n".join([method, canonical_uri, query, block, signed_names, payload])


def canonical_query(params: Mapping[str, str]) -> str:
    """SigV4 query string: RFC 3986 encoded, sorted. Also the string to send."""
    return _canonical_query(params)


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
