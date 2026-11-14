from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from omm.service import OMM
from omm.models import MemoryRecord
from omm import retrieval


class IncrementalSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.o = OMM(self.root)
        self.o.init()
        self.sources = self.root / 'sources/demo'
        self.sources.mkdir(parents=True)
        for i in range(40):
            (self.sources / f'{i}.md').write_text(f'# Original{i}\n\nMarker{i} original proof.\n')
        self.o.rebuild()

    def test_changed_new_removed_and_unchanged_have_atomic_current_results(self):
        (self.sources / '0.md').write_text('# Changed\n\nNewSemanticProof\n')
        (self.sources / '1.md').unlink()
        (self.sources / 'new.md').write_text('# Added\n\nAdditionalProof\n')
        with patch('omm.retrieval.read_source_chunks', wraps=retrieval.read_source_chunks) as read:
            self.o.rebuild()
        self.assertEqual({p.name for p in read.call_args.kwargs['paths']}, {'0.md', 'new.md'})
        self.assertEqual(len(self.o.search_sources('Marker2')), 1)
        self.assertFalse(self.o.search_sources('Marker0'))
        self.assertFalse(self.o.search_sources('Marker1'))
        self.assertEqual(len(self.o.search_sources('NewSemanticProof')), 1)
        with patch('omm.retrieval.read_source_chunks', wraps=retrieval.read_source_chunks) as read:
            self.o.rebuild()
        self.assertEqual(read.call_args.kwargs['paths'], [])

    def test_failed_read_preserves_previous_index_and_semantic_tables(self):
        index = self.root / '.omm/index.sqlite3'
        with sqlite3.connect(index) as db:
            db.execute('CREATE TABLE semantic_checkpoint_fixture(value TEXT)')
            db.execute("INSERT INTO semantic_checkpoint_fixture VALUES ('retained')")
        before = self.o.retriever.indexed_fingerprint()
        (self.sources / '2.md').write_text('# Changed\n\nAnotherProof\n')
        with patch('omm.retrieval.read_source_chunks', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                self.o.rebuild()
        self.assertEqual(self.o.retriever.indexed_fingerprint(), before)
        with sqlite3.connect(index) as db:
            self.assertEqual(db.execute('SELECT value FROM semantic_checkpoint_fixture').fetchone()[0], 'retained')
        self.o.rebuild()
        self.assertFalse(self.o.search_sources('Marker2'))

    def test_external_record_edit_and_legacy_manifest_seed(self):
        r = MemoryRecord(kind='fact', title='ExternalRecord', content='ExternalNeedle', source='fixture')
        self.o.store.append(r)
        self.o._ensure_index_current(force=True)
        self.assertEqual(len(self.o.search('ExternalNeedle')), 1)
        with sqlite3.connect(self.root / '.omm/index.sqlite3') as db:
            db.execute('DROP TABLE source_manifest')
        self.o.rebuild()
        self.assertEqual(len(self.o.search_sources('Marker3')), 1)

    def test_scoped_snapshot_uses_the_same_version_as_individual_checks(self):
        self.o.store.append(MemoryRecord(kind='fact', title='Scoped', content='Scoped record', source='fixture', scope='demo'))
        versions = self.o._scope_fingerprints(['demo', 'global', 'other'])
        for scope, version in versions.items():
            self.assertEqual(version, self.o._canonical_fingerprint([scope]))
