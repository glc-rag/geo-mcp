from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_sec: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            cutoff = now - window_sec
            self._hits[key] = [t for t in bucket if t >= cutoff]
            if len(self._hits[key]) >= limit:
                return False
            self._hits[key].append(now)
            return True


limiter = SlidingWindowLimiter()
