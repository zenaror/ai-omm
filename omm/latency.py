"""Fixed, bounded latency buckets. Bounds are not exact percentiles."""
from math import ceil

BOUNDS_MS = (1, 5, 20, 100, 500, 1000, 5000, 30000, 120000)

def bucket_index(elapsed_ms):
    return next((i for i, bound in enumerate(BOUNDS_MS) if elapsed_ms <= bound), len(BOUNDS_MS))

def distribution(counts):
    total = sum(counts)
    def upper(fraction):
        threshold, cumulative = ceil(total * fraction), 0
        for i, count in enumerate(counts):
            cumulative += count
            if total and cumulative >= threshold:
                return BOUNDS_MS[i] if i < len(BOUNDS_MS) else None
        return None
    return {"observed_calls": total, "buckets": list(counts),
            "p50_upper_ms": upper(.5), "p95_upper_ms": upper(.95)}
