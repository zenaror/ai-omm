import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from omm.errors import error_report
from omm.usage import UsageRecorder
from omm.latency import BOUNDS_MS, distribution
from omm.timing import PhaseTimings

class LatencyDiagnosticsTests(unittest.TestCase):
    def test_new_histograms_preserve_unknown_history_and_scope(self):
        with tempfile.TemporaryDirectory() as root:
            usage = UsageRecorder(Path(root))
            usage._record('read_source', 'demo', 1, 1, 20, False)
            with sqlite3.connect(usage.path) as db:
                db.execute('DELETE FROM usage_latency_daily')
            for elapsed in [1, 5, 20, 100, 500, 1000, 5000, 30000, 120000, 130000]:
                usage._record('read_source', 'demo', 1, 1, elapsed, False)
            usage._record('read_source', 'other', 1, 1, 1, False)
            report = usage.report(scope='demo')
            self.assertEqual(report['totals']['calls'], 11)
            row = report['latency_rows'][0]
            self.assertEqual(row['observed_calls'], 10)
            self.assertEqual(row['buckets'], [1] * 10)
            self.assertEqual(row['p50_upper_ms'], 500)
            self.assertIsNone(row['p95_upper_ms'])
            with sqlite3.connect(usage.path) as db:
                db.execute("UPDATE usage_latency_daily SET day='2000-01-01'")
            usage._record('new', 'demo', 1, 1, 1, False)
            self.assertEqual(len(usage.report()['latency_rows']), 1)

    def test_error_guidance_is_specific_but_never_echoes_rejected_values(self):
        for operation, error, hint in [
            ('read_source', ValueError('SECRET'), '80'),
            ('read_source_batch', ValueError('SECRET'), '32768'),
            ('read_source', FileNotFoundError('SECRET'), 'search_sources'),
            ('get_role', KeyError('SECRET'), 'list_roles')]:
            report = error_report(error, operation)
            self.assertIn(hint, report['error']['recovery'])
            self.assertNotIn('SECRET', json.dumps(report))
        timings = PhaseTimings()
        with timings.measure('fixture'):
            pass
        row = timings.report()['rows'][0]
        self.assertEqual(sum(row['buckets']), row['calls'])
