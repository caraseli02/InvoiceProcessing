"""Unit tests for in-memory extract cache behavior."""

from invproc.extract_cache import InMemoryExtractCache


def test_extract_cache_hit_and_miss() -> None:
    """Basic set/get behavior returns payload for known keys."""
    cache = InMemoryExtractCache(ttl_sec=60, max_entries=2)
    payload = {"supplier": "A"}

    assert cache.get("k1") is None
    cache.set("k1", payload)
    assert cache.get("k1") == payload


def test_extract_cache_ttl_expiry(monkeypatch) -> None:
    """Expired entries should return miss and be removed."""
    now = [1000.0]

    def fake_time() -> float:
        return now[0]

    monkeypatch.setattr("invproc.extract_cache.time.time", fake_time)

    cache = InMemoryExtractCache(ttl_sec=10, max_entries=5)
    cache.set("k1", {"supplier": "A"})
    assert cache.get("k1") == {"supplier": "A"}

    now[0] = 1011.0
    assert cache.get("k1") is None


def test_extract_cache_lru_eviction() -> None:
    """Least recently used key should be evicted when capacity exceeded."""
    cache = InMemoryExtractCache(ttl_sec=60, max_entries=2)
    cache.set("k1", {"v": 1})
    cache.set("k2", {"v": 2})
    # Touch k1 so k2 becomes LRU.
    assert cache.get("k1") == {"v": 1}
    cache.set("k3", {"v": 3})

    assert cache.get("k2") is None
    assert cache.get("k1") == {"v": 1}
    assert cache.get("k3") == {"v": 3}


def test_extract_cache_configure_prunes_over_capacity() -> None:
    """Reducing max_entries via configure should prune oldest entries."""
    cache = InMemoryExtractCache(ttl_sec=60, max_entries=3)
    cache.set("k1", {"v": 1})
    cache.set("k2", {"v": 2})
    cache.set("k3", {"v": 3})

    cache.configure(ttl_sec=60, max_entries=1)

    assert cache.get("k1") is None
    assert cache.get("k2") is None
    assert cache.get("k3") == {"v": 3}
