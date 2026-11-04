# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Integration checks for the real localhost HTTP handler, without external traffic."""
from __future__ import annotations
import json
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import unittest
from paper_radar.webapp import Handler, runtime
from paper_radar import __version__

class LocalHttpContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.server.daemon_threads=True
        cls.server.token='integration-test-token'
        cls.thread=Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=3)
        cls.server.server_close()

    def read(self,path):
        with urlopen(self.base+path,timeout=5) as response:
            return json.load(response)

    def test_state_with_provider_catalog_routes(self):
        # A local import inside do_GET used to shadow get_api_key and caused 500.
        with patch.object(runtime,'metrics',return_value={'archived':0,'matched':0,'downloaded':0,'starred':0,'unread':0}):
            state=self.read('/api/state')
        self.assertEqual(state['version'],__version__)
        self.assertTrue(any(x['name']=='sciencedirect' for x in state['sources']))
        catalog=self.read('/api/provider-catalog')
        self.assertIn('configured',catalog)
        self.assertIn('DeepSeek',catalog['configured'])

    def test_markdown_preview_path_restriction(self):
        for invalid in ['/api/report-preview?name=bad.txt', '/api/report-preview?name=..%2Fsettings.json']:
            with self.assertRaises(HTTPError) as err:
                urlopen(self.base+invalid,timeout=5)
            self.assertEqual(err.exception.code,400)

    def test_health_and_library_pagination(self):
        self.assertEqual(self.read('/api/health')['app'],'paper-radar')
        result=self.read('/api/papers?limit=30&offset=0')
        self.assertIn('total',result)
        self.assertLessEqual(len(result['papers']),30)

if __name__=='__main__': unittest.main()
