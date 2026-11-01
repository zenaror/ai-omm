"""Small process-local timing counters; no arguments, text or identifiers."""
from contextlib import contextmanager
from functools import wraps
import os
from threading import Lock
from time import perf_counter


class PhaseTimings:
    def __init__(self):
        self.enabled = os.getenv("OMM_USAGE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on", "sim"}
        self._lock = Lock()
        self._rows = {}

    @contextmanager
    def measure(self, name):
        if not self.enabled:
            yield
            return
        started = perf_counter()
        failed = True
        try:
            yield
            failed = False
        finally:
            elapsed = (perf_counter() - started) * 1000
            with self._lock:
                row = self._rows.setdefault(name, {"calls": 0, "failures": 0, "total_ms": 0., "max_ms": 0.})
                row["calls"] += 1
                row["failures"] += int(failed)
                row["total_ms"] += elapsed
                row["max_ms"] = max(row["max_ms"], elapsed)

    def report(self):
        with self._lock:
            rows = [{"phase": name, **{k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()},
                     "mean_ms": round(row["total_ms"] / row["calls"], 2)} for name, row in sorted(self._rows.items())]
        return {"enabled": self.enabled, "lifetime": "since_process_start", "scope": "all_projects_in_this_process",
                "measurement": "Etapas podem se sobrepor; não some seus tempos. Não mede rede nem tokens. Reiniciar zera estes contadores.",
                "rows": rows}


def timed_phase(name):
    def decorate(function):
        @wraps(function)
        def measured(self, *args, **kwargs):
            with self.timings.measure(name):
                return function(self, *args, **kwargs)
        return measured
    return decorate
