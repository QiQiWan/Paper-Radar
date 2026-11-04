# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""AI query planner HTTP contract without a live paid model."""
import json
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from paper_radar.webapp import Handler


class AIPlanningHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.server.daemon_threads=True
        cls.server.token='test-csrf'
        cls.thread=Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
    def post(self,payload):
        req=Request(self.base+'/api/ai/plan',
            data=json.dumps(payload).encode('utf-8'),method='POST',
            headers={'Content-Type':'application/json','X-Local-Token':'test-csrf'})
        return urlopen(req,timeout=5)
    def test_explicit_plan_does_not_mutate_settings(self):
        goal='我希望检索复杂几何和非结构化网格上的神经算子相关研究。'
        plan={'topic':'神经算子','keywords':['neural operator','unstructured mesh'],
              'notes':[],'exclusions':[]}
        with patch('paper_radar.webapp.get_api_key',return_value='test'):
            with patch('paper_radar.webapp.plan_research',return_value=plan) as helper:
                with self.post({'goal':goal}) as response:
                    obj=json.load(response)
        self.assertTrue(obj['ok'])
        self.assertEqual(obj['plan'],plan)
        self.assertEqual(helper.call_args.args[0],goal)
    def test_missing_api_key_is_actionable(self):
        with patch('paper_radar.webapp.get_api_key',return_value=''):
            with self.assertRaises(HTTPError) as problem:
                self.post({'goal':'关于神经算子和复杂几何的学习泛化问题。'})
        self.assertEqual(problem.exception.code,400)
        self.assertIn('API Key',problem.exception.read().decode('utf-8'))

if __name__=='__main__':unittest.main()
