"""Per-IP submission limits. The stored key is a hash, not the address."""

from __future__ import annotations

from datetime import datetime, timedelta

from public_site.store import PublicStore, parse_utc


class RateLimiter:
    """Allow a fixed number of successful creates per hash per hour."""

    def __init__(self, store: PublicStore, *, limit: int, window_seconds: int = 3600) -> None:
        self.store = store
        self.limit = limit
        self.window = timedelta(seconds=window_seconds)

    def allow(self, ip_hash: str, now: datetime) -> bool:
        """Record one attempt when the hash is under the limit."""
        payload = self.store.read_rate()
        kept = _recent(payload.get(ip_hash, []), now, self.window)
        if len(kept) >= self.limit:
            payload[ip_hash] = [_stamp(item) for item in kept]
            self.store.write_rate(_drop_empty(payload))
            return False
        kept.append(now)
        payload[ip_hash] = [_stamp(item) for item in kept]
        self.store.write_rate(payload)
        return True


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
