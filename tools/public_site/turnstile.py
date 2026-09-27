"""Cloudflare Turnstile. The widget token is checked before any record is written."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from public_site.settings import PublicSettings

_SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile(token: str, secret: str, remote_ip: str | None = None) -> bool:
    """Return whether Cloudflare accepted this token. Network errors are rejection."""
    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(_SITEVERIFY, data=data)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False
    return bool(body.get("success"))


def build_verifier(settings: PublicSettings) -> Callable[[str], bool]:
    """Production verifies with Cloudflare. Dev mode accepts only the token ``dev``."""
    secret = settings.turnstile_secret
    if secret:
        def verify(token: str) -> bool:
            return verify_turnstile(token, secret)

        return verify
    if settings.dev_mode:
        def verify(token: str) -> bool:
            return token == "dev"

        return verify
    raise RuntimeError("PUBLIC_TURNSTILE_SECRET is required")
