"""Compare a cold FTS build with a one-document update on isolated fixtures."""
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from omm.service import OMM

with tempfile.TemporaryDirectory(prefix='omm-incremental-') as temporary:
    root = Path(temporary)
    o = OMM(root)
    o.init()
    directory = root / 'sources/fixture'
    directory.mkdir(parents=True)
    body = 'Synthetic protocol evidence and reversible verification.\n' * 100
    for i in range(2000):
        (directory / f'{i}.md').write_text(f'# Fixture{i}\n\n{body}')
    started = perf_counter()
    o.rebuild()
    cold = perf_counter() - started
    (directory / '0.md').write_text('# Updated\n\nUniqueUpdatedEvidence\n')
    started = perf_counter()
    o.rebuild()
    updated = perf_counter() - started
    assert o.search_sources('UniqueUpdatedEvidence')[0].path == 'sources/fixture/0.md'
    print(json.dumps({'dataset':'synthetic, temporary; not production or token savings',
                      'documents':2000,'cold_build_seconds':round(cold,4),
                      'one_document_update_seconds':round(updated,4)}))
