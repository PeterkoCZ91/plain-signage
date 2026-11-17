"""Generic abuse-protection helpers: per-key sliding window and a small LRU cache with TTL."""
import threading
import time
from collections import OrderedDict


class RateLimiter:
    """Sliding time window per key (e.g. IP). Memory is bounded by an LRU cap on keys.

    The clock is injectable for tests. Has its own lock, thread-safe.
    """

    def __init__(self, limit, window, clock=time.monotonic, max_keys=4096):
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self._clock = clock
        self._hits = OrderedDict()          # key -> list of timestamps (ascending)
        self._mu = threading.Lock()

    def __len__(self):
        with self._mu:
            return len(self._hits)

    def _fresh(self, key, now):
        hits = self._hits.get(key)
        if hits is None:
            return None
        cut = now - self.window
        while hits and hits[0] <= cut:
            hits.pop(0)
        if not hits:
            del self._hits[key]
            return None
        return hits

    def _retry(self, key, now):
        hits = self._fresh(key, now)
        if hits is None or len(hits) < self.limit:
            return 0
        return max(1, int(hits[-self.limit] + self.window - now + 0.999))

    def retry_after(self, key):
        """Seconds until released (0 = free). Consumes nothing."""
        with self._mu:
            return self._retry(key, self._clock())

    def hit(self, key):
        """Record one event (without checking the limit)."""
        with self._mu:
            now = self._clock()
            hits = self._fresh(key, now)
            if hits is None:
                hits = self._hits[key] = []
            hits.append(now)
            self._hits.move_to_end(key)
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)

    def acquire(self, key):
        """Check + consume: 0 = allowed and recorded, otherwise seconds until released."""
        with self._mu:
            now = self._clock()
            wait = self._retry(key, now)
            if wait:
                return wait
        self.hit(key)
        return 0

    def clear(self):
        with self._mu:
            self._hits.clear()


class TtlCache:
    """Small in-memory LRU cache with TTL (injectable clock)."""

    def __init__(self, ttl, max_items, clock=time.monotonic):
        self.ttl, self.max_items, self._clock = ttl, max_items, clock
        self._d = OrderedDict()
        self._mu = threading.Lock()

    def __len__(self):
        with self._mu:
            return len(self._d)

    def get(self, key):
        with self._mu:
            item = self._d.get(key)
            if item is None:
                return None
            if item[0] <= self._clock():
                del self._d[key]
                return None
            self._d.move_to_end(key)
            return item[1]

    def put(self, key, value):
        with self._mu:
            self._d[key] = (self._clock() + self.ttl, value)
            self._d.move_to_end(key)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def pop(self, key):
        with self._mu:
            self._d.pop(key, None)

    def clear(self):
        with self._mu:
            self._d.clear()
