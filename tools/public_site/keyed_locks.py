"""In-process locks keyed by an identifier, removed when no thread holds or waits."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class KeyedLocks:
    """One re-entrant lock per key. Different keys never block each other."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.RLock, int]] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        """Hold the lock for ``key`` for the duration of the block."""
        with self._guard:
            lock, users = self._locks.get(key, (threading.RLock(), 0))
            self._locks[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._guard:
                lock, users = self._locks[key]
                if users == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (lock, users - 1)

    def __len__(self) -> int:
        """Keys currently held or awaited."""
        with self._guard:
            return len(self._locks)
