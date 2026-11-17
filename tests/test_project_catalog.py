import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from PIL import Image

from omm.errors import OMMError
from omm.models import MemoryRecord
from omm.projects import ProjectCatalog
from omm.service import OMM
from omm.web_server import dashboard_data


class ProjectCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {'OMM_SEMANTIC_ENABLED': 'false'})
        env.start(); self.addCleanup(env.stop)
        self.omm = OMM(self.root); self.omm.init()
        self.path = self.root / 'memory/projects.json'
        self.aliases = {'legacy': 'canonical', 'owner/repo': 'canonical',
                        '/old/local/repo': 'canonical'}
        self.catalog()

    def catalog(self, **changes):
        data = {'schema_version': 1, 'projects': {'canonical': {'display_name': 'Example'},
                'experiment': {'category': 'experiment'}}, 'aliases': self.aliases}
        data.update(changes)
        self.path.write_text(json.dumps(data))

    def record(self, scope, title):
        return MemoryRecord(kind='observation', title=title, content='Fixture facts', source='test', scope=scope)

    def test_existing_histories_and_bytes_stay_intact_and_queries_include_aliases(self):
        old = self.record('legacy', 'Oldneedle')
        self.omm.store.append(old)
        p = self.root / 'sources/legacy/old.md'; p.parent.mkdir(parents=True)
        p.write_text('# Legacy\nOldSourceNeedle')
        before = p.read_bytes(), self.omm.store.records_path.read_bytes()
        self.omm.rebuild()
        for scope in ['canonical', 'legacy', 'owner/repo', '/old/local/repo']:
            self.assertEqual(self.omm.search('Oldneedle', scopes=[scope])[0].id, old.id)
            self.assertEqual(self.omm.search_sources('OldSourceNeedle', scopes=[scope])[0].path,
                             'sources/legacy/old.md')
        self.assertEqual(before, (p.read_bytes(), self.omm.store.records_path.read_bytes()))
        self.assertEqual(self.omm.search('Oldneedle', scopes=['different']), [])

    def test_new_writes_use_canonical_scope_and_reject_unknown_paths(self):
        r = self.record('owner/repo', 'Newneedle'); self.omm.remember(r)
        self.assertEqual(self.omm.get_record(r.id).scope, 'canonical')
        source = self.omm.import_source('/old/local/repo', 'new.md', '# New source')
        self.assertEqual(source['source'], 'sources/canonical/new.md')
        proposal = self.omm.propose_memory(self.record('legacy', 'Proposal'))
        self.assertEqual(self.omm.proposals()[0]['record']['scope'], 'canonical')
        self.catalog(aliases={**self.aliases, '/old path/repo': 'canonical'})
        spaced = self.record('/old path/repo', 'Spaced alias')
        self.omm.remember(spaced)
        self.assertEqual(self.omm.get_record(spaced.id).scope, 'canonical')
        with self.assertRaises(OMMError):
            self.omm.import_source('/unregistered/path', 'bad.md', '# Bad')
        self.assertFalse((self.root/'sources/legacy/new.md').exists())

    def test_current_handoff_wins_and_aliases_share_cas_protection(self):
        self.omm.store.append_handoff({'scope':'legacy','summary':'Historical','status':'active',
                                      'updated_at':'2099-01-01T00:00:00+00:00'})
        initial = self.omm.get_handoff('legacy')
        self.assertEqual(initial['state']['summary'], 'Historical')
        self.omm.handoff('active','Canonical',[],[],[],'test',scope='owner/repo',
                         expected_sha256=initial['sha256'])
        current = self.omm.get_handoff('canonical')
        self.assertEqual(current['state']['summary'], 'Canonical')
        self.assertEqual(self.omm.get_handoff('legacy')['sha256'], current['sha256'])
        with self.assertRaises(OMMError):
            self.omm.handoff('active','Stale',[],[],[],'test',scope='legacy',
                             expected_sha256=initial['sha256'])
        self.assertEqual(self.omm.get_handoff('canonical')['state']['summary'], 'Canonical')
        context = self.omm.context('anything', scopes=['legacy'], state_scope='owner/repo')
        self.assertIn('Canonical', context)
        self.assertNotIn('Historical', context)

    def test_panel_one_entry_aggregates_sources_and_preserves_distinct_projects(self):
        self.omm.store.append(self.record('legacy', 'Legacy'))
        self.omm.store.append(self.record('different', 'Different'))
        self.omm.store.append(self.record('experiment', 'Experiment'))
        self.omm.import_source('canonical','current.md','# Current')
        p=self.root/'sources/legacy/original.md';p.parent.mkdir(exist_ok=True);p.write_text('# Original')
        data=dashboard_data(self.omm,scope='owner/repo')
        self.assertEqual(set(data['scopes']), {'canonical','different','experiment','global'})
        self.assertEqual(data['scope_labels']['canonical'],'Example')
        self.assertEqual(data['scope_categories']['experiment'],'experiment')
        self.assertEqual(data['organization']['scope'],'canonical')
        self.assertEqual(data['organization']['active_memory_count'],1)
        self.assertEqual({x['path'] for x in data['organization']['sources']},
                         {'sources/canonical/current.md','sources/legacy/original.md'})
        self.assertEqual([r['title'] for r in data['records']],['Legacy'])

    def test_legacy_images_are_listed_without_rewriting_originals(self):
        image=io.BytesIO();Image.new('RGB',(2,2),'green').save(image,format='PNG')
        meta={'title':'Legacy','description':'Synthetic image','origin':'fixture','evidence_type':'synthetic'}
        # Store legacy evidence with the old library, as a pre-catalog client did.
        from omm.image_evidence import import_image
        old=import_image(self.root,'legacy',image.getvalue(),meta)
        new=self.omm.import_image('owner/repo',image.getvalue(),{**meta,'title':'Current'})
        files={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root/'sources').rglob('*') if p.is_file()}
        result=self.omm.list_images('legacy',0,1)
        self.assertEqual(result['total'],2);self.assertEqual(result['next_offset'],1)
        self.assertEqual(self.omm.list_images('canonical',1,1)['next_offset'],None)
        self.assertEqual(files,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files})

    def test_semantic_query_expands_only_explicit_aliases(self):
        self.omm.semantic = Mock()
        with patch.object(self.omm, '_semantic_stale_scopes', return_value=[]) as stale, \
             patch.object(self.omm, '_semantic_query_current', return_value={'memories': [], 'sources': []}) as query:
            self.omm.semantic_search_nonblocking('fixture', scopes=['legacy'])
            selected = stale.call_args.args[1]
            self.assertEqual(set(selected), {'canonical', 'legacy', 'owner/repo', '/old/local/repo'})
            self.assertEqual(query.call_args.args[-1], selected)
            self.assertNotIn('different', selected)

    def test_physical_merge_retains_exact_bytes_and_old_source_artifact_addresses(self):
        import base64
        from omm import artifacts
        from omm.source_documents import read_source_batch
        # Existing legacy bytes must be uploaded with the pre-alias low-level API.
        self.path.unlink()
        source = self.omm.import_source('legacy', 'old.md', '# Historic\nExact bytes\n')
        blob = b'original recovery bytes'
        artifact = self.omm.import_artifact('legacy', base64.b64encode(blob).decode(),
                                          hashlib.sha256(blob).hexdigest(), 'Recovery', 'Fixture', 'Test')
        self.omm.store.append_handoff({'scope':'legacy','summary':'Old','status':'completed',
                                      'updated_at':'2020-01-01T00:00:00+00:00'})
        self.omm.handoff('active','Current',[],[],[],'test',scope='canonical')
        self.catalog()
        plan = self.omm.merge_scope('legacy','canonical')
        self.omm.merge_scope('legacy','canonical',False,plan['plan_sha256'])
        self.assertFalse((self.root/'sources/legacy').exists())
        self.assertEqual(self.omm.get_handoff('canonical')['state']['summary'], 'Current')
        read = read_source_batch(self.root,[{'path':source['source'],'expected_sha256':source['sha256']}])
        self.assertEqual(read['items'][0]['content'], '# Historic\nExact bytes\n')
        card, data = artifacts._read(self.root,artifact['path'])
        self.assertEqual(data,blob)
        self.assertEqual(len(dashboard_data(self.omm,scope='canonical')['organization']['sources']),2)

    def test_workstream_scope_and_panel_use_latest_snapshot_only(self):
        self.omm.create_workstream('new-front', 'Work', scope='owner/repo')
        self.assertEqual(self.omm.get_workstream('new-front')['scope'], 'canonical')
        self.omm.store._append_workstream({'id':'old-front','title':'Old','status':'archived'})
        self.omm.store._append_workstream({'id':'old-front','title':'Old','status':'archived','scope':'canonical'})
        self.assertNotIn('default', dashboard_data(self.omm)['scopes'])
        self.assertEqual(len(self.omm.workstreams()), 2)
        self.assertEqual(len(self.omm.store.workstreams_path.read_text().splitlines()), 3)

    def test_malformed_aliases_fail_closed_and_missing_catalog_is_compatible(self):
        for aliases in [{'a':'b','b':'a'}, {'a':'bad/path'}, {'global':'canonical'}, {'a':'a'}]:
            with self.subTest(aliases=aliases):
                self.catalog(aliases=aliases)
                with self.assertRaises(OMMError):ProjectCatalog(self.root)
        self.path.write_text('{broken')
        with self.assertRaises(OMMError):self.omm.import_source('legacy','bad.md','# Bad')
        self.path.unlink()
        self.assertEqual(ProjectCatalog(self.root).expand(['existing']),['existing'])
        self.assertIsNone(ProjectCatalog(self.root).expand(None))
        self.assertEqual(self.omm.import_source('existing','ok.md','# OK')['source'],
                         'sources/existing/ok.md')
