"""Per-IP and per-account submission limits. Stored keys are hashes, not addresses."""

from __future__ import annotations

from datetime import datetime, timedelta

from public_site.store import PublicStore, parse_utc


class RateLimiter:
    """Allow a fixed number of successful creates per key per window."""

    def __init__(self, store: PublicStore, *, limit: int, window_seconds: int = 3600) -> None:
        self.store = store
        self.limit = limit
        self.window = timedelta(seconds=window_seconds)

    def allow(self, ip_hash: str, now: datetime) -> bool:
        """Record one attempt when the hash is under the default limit."""
        return self.allow_all([(ip_hash, self.limit)], now) is None

    def allow_all(self, checks: list[tuple[str, int]], now: datetime) -> str | None:
        """Record one attempt on every key, or none when any key is at its limit.

        Returns ``None`` on success, otherwise the first key that was full.
        """
        payload = self.store.read_rate()
        recent = {key: _recent(payload.get(key, []), now, self.window) for key, _ in checks}
        full = next((key for key, limit in checks if len(recent[key]) >= limit), None)
        for key, _ in checks:
            if full is None:
                recent[key].append(now)
            payload[key] = [_stamp(item) for item in recent[key]]
        self.store.write_rate(_drop_empty(payload))
        return full


def _recent(stamps: list[str], now: datetime, window: timedelta) -> list[datetime]:
    kept: list[datetime] = []
    for stamp in stamps:
        parsed = parse_utc(stamp)
        if now - parsed < window:
            kept.append(parsed)
    return kept


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _drop_empty(payload: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: value for key, value in payload.items() if value}
