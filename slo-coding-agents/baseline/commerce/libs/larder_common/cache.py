"""
A small TTL + LRU cache (SB 2021-11).

Written for the recommendations prototype and the "cache catalog lookups in
pricing" ticket (PRICE-212). Neither shipped, so nothing uses this. Kept
because it has tests somewhere (it doesn't).
"""
import collections
import threading
import time


class TTLCache(object):
    def __init__(self, maxsize=1024, ttl=60.0, clock=time.monotonic):
        self.maxsize = maxsize
        self.ttl = ttl
        self._clock = clock
        self._data = collections.OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key, default=None):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return default
            value, expires = item
            if expires < self._clock():
                del self._data[key]
                self.misses += 1
                return default
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key, value, ttl=None):
        with self._lock:
            self._data[key] = (value, self._clock() + (ttl if ttl is not None else self.ttl))
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def get_or_set(self, key, fn, ttl=None):
        sentinel = object()
        v = self.get(key, sentinel)
        if v is sentinel:
            v = fn()
            self.set(key, v, ttl)
        return v

    def clear(self):
        with self._lock:
            self._data.clear()

    def __len__(self):
        return len(self._data)


def cached(cache, key_fn=None):
    def deco(fn):
        def wrapper(*a, **kw):
            key = key_fn(*a, **kw) if key_fn else (a, tuple(sorted(kw.items())))
            return cache.get_or_set(key, lambda: fn(*a, **kw))

        wrapper.__wrapped__ = fn
        wrapper.cache = cache
        return wrapper

    return deco
