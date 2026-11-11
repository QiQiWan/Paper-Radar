# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
import tempfile
import threading
import unittest
from unittest.mock import patch

from paper_radar.cloud_api import chat_completion, completion_endpoint, summarize_paper
from paper_radar.core import DEFAULTS, Paper, run_cycle, parse_atom
from docx import Document

SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry><id>https://arxiv.org/abs/2611.00001v1</id>
  <updated>2026-11-02T08:00:00Z</updated><published>2026-11-02T07:00:00Z</published>
  <title>Mock Neural Operator Test Study</title>
  <summary>A small test paper for operator learning with no real findings.</summary>
  <author><name>Test Author</name></author>
  <arxiv:primary_category term="cs.LG"/></entry>
</feed>'''

class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.received_path = self.path
        self.server.received_auth = self.headers.get("Authorization")
        length = int(self.headers.get("Content-Length", 0))
        self.server.received_payload = json.loads(self.rfile.read(length))
        body = json.dumps({"choices": [{"message": {"content": "模拟中文速览：依据标题与摘要。"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_):
        pass

class CloudAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setup_config(self):
        return dict(DEFAULTS,
                    api_provider="自定义",
                    api_base_url=f"http://127.0.0.1:{self.server.server_port}/v1",
                    api_model="mock-model", ai_enabled=True, llm_data_consent=True, ai_limit=2, lookback_days=7)

    def test_url_https_required(self):
        self.assertEqual(completion_endpoint("https://api.example.org/v1"),
                         "https://api.example.org/v1/chat/completions")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            completion_endpoint("http://public.example.com/v1")

    def test_completion_and_secret_never_in_report(self):
        cfg = self.setup_config()
        txt = chat_completion(cfg, "测试", key="sk-placeholder-only")
        self.assertIn("模拟中文速览", txt)
        self.assertEqual(self.server.received_auth, "Bearer sk-placeholder-only")
        self.assertEqual(self.server.received_payload["model"], "mock-model")
        self.assertEqual(self.server.received_path, "/v1/chat/completions")
        with tempfile.TemporaryDirectory() as tmp:
            cfg["output_dir"] = str(Path(tmp) / "reports")
            db = Path(tmp) / "data.sqlite3"
            with patch("paper_radar.cloud_api.get_api_key", return_value="sk-placeholder-only"):
                result = run_cycle(cfg, db_path=db, fetcher=lambda c, logger, now: parse_atom(SAMPLE),
                                   now=datetime(2026, 11, 3, tzinfo=timezone.utc))
            self.assertEqual(result["new"], 1)
            doc = Document(result["weekly"])
            txt = "\n".join(p.text for p in doc.paragraphs)
            self.assertIn("模拟中文速览", txt)
            self.assertNotIn("sk-placeholder-only", txt)
            self.assertIn("中文速览", "\n".join(cell.text for table in doc.tables for row in table.rows for cell in row.cells))
            self.assertEqual(run_cycle(dict(cfg, ai_enabled=False), db_path=db,
                          fetcher=lambda c, logger, now: parse_atom(SAMPLE))["new"], 0)

    def test_api_missing_key_does_not_block_report(self):
        cfg = self.setup_config()
        with tempfile.TemporaryDirectory() as tmp:
            cfg["output_dir"] = str(Path(tmp) / "reports")
            with patch("paper_radar.cloud_api.get_api_key", return_value=""):
                result = run_cycle(cfg, db_path=Path(tmp) / "records.sqlite3",
                                   fetcher=lambda c, logger, now: parse_atom(SAMPLE),
                                   now=datetime(2026,11,3,tzinfo=timezone.utc))
            self.assertEqual(result["new"], 1)
            self.assertTrue(result["weekly"].exists())

if __name__ == "__main__":
    unittest.main()
