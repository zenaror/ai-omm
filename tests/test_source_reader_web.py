import base64
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from omm.service import OMM
from omm.web_server import make_handler, dashboard_data

class SourceReaderWebTests(unittest.TestCase):
    def test_authenticated_exact_paging_and_stale_sha_rejection(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'OMM_SEMANTIC_ENABLED':'false', 'OMM_WEB_USERNAME':'test', 'OMM_WEB_PASSWORD':'fixture'}):
            omm = OMM(Path(root)); omm.init()
            folder = Path(root)/'sources/demo'; folder.mkdir(parents=True)
            source = folder/'study.md'; content = '# Current study\n<script>fixture</script>\n' + 'á' * 18000
            source.write_text(content)
            payload = dashboard_data(omm, scope='demo')
            item = payload['organization']['sources'][0]
            self.assertIn('file_modified_at', item)
            self.assertEqual(item['sha256'], hashlib.sha256(content.encode()).hexdigest())
            server = ThreadingHTTPServer(('127.0.0.1',0), make_handler(omm))
            Thread(target=server.serve_forever, daemon=True).start()
            try:
                def get(params, authenticated=True):
                    connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                    headers = {'Authorization':'Basic '+base64.b64encode(b'test:fixture').decode()} if authenticated else {}
                    connection.request('GET','/api/source?'+urlencode(params),headers=headers)
                    result=connection.getresponse(); status=result.status; body=result.read();connection.close()
                    return status, json.loads(body) if body else {}
                params = {'path': item['path'], 'expected_sha256': item['sha256']}
                self.assertEqual(get(params,False)[0],401)
                status,data=get(params); self.assertEqual(status,200)
                first=data['items'][0];self.assertEqual(first['content'],content[:16000])
                _,data=get({**params,'start_char':first['next_start_char']})
                self.assertEqual(first['content']+data['items'][0]['content'],content)
                self.assertIsNone(data['items'][0]['next_start_char'])
                source.write_text('# Changed')
                self.assertEqual(get(params)[0],409)
                self.assertEqual(get({'path':'sources/demo/../../secret.md'})[0],400)
                self.assertEqual(get({'path':'sources/demo/missing.md'})[0],404)
            finally:
                server.shutdown();server.server_close()
