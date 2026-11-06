# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Branding and release safety tests for Paper-Radar v1.10.0."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from paper_radar import __app_id__, __version__
from paper_radar import scheduler
from paper_radar import cloud_api
from paper_radar.launcher import health


class BrandTests(unittest.TestCase):
    def test_new_app_marker(self):
        self.assertEqual(__app_id__, 'paper-radar')
        self.assertRegex(__version__, r'^\d+\.\d+\.\d+$')

    def test_legacy_api_key_lookup(self):
        class OldKeyring:
            @staticmethod
            def get_password(service, provider):
                return 'previously-saved-test-key' if service == 'LiteratureTracker-API' else None
        with patch.object(cloud_api, '_credential_backend', return_value=OldKeyring):
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(cloud_api.get_api_key('DeepSeek'),'previously-saved-test-key')

    def test_new_key_overrides_legacy(self):
        with patch.dict('os.environ', {'PAPER_RADAR_API_KEY':'test-new-key','LITERATURE_TRACKER_API_KEY':'old'}, clear=True):
            self.assertEqual(cloud_api.get_api_key('DeepSeek'),'test-new-key')

    def test_scheduler_registers_and_migrates(self):
        script=scheduler.build_install_script('08:30','pythonw.exe','D:/Paper-Radar/main.py')
        self.assertIn('Paper-Radar_Daily',script)
        self.assertIn('LiteratureTracker_Daily',script)
        self.assertIn('Unregister-ScheduledTask',script)

    def test_release_filter_includes_engine_modules(self):
        from tools.package_release import include
        self.assertTrue(include(Path('paper_radar/downloads/manager.py')))
        self.assertTrue(include(Path('paper_radar/reports/word.py')))
        self.assertTrue(include(Path('docs/images/v16/dashboard.png')))
        for value in ('downloads/sample.pdf','reports/report.docx','data/papers.sqlite3','.venv/lib/test.py','settings.json','paper_radar/__pycache__/core.pyc','logs/run.log'):
            self.assertFalse(include(Path(value)),value)

    def test_readme_and_ui_match_repository(self):
        root=Path(__file__).resolve().parent.parent
        self.assertIn('QiQiWan/Paper-Radar',(root/'README.md').read_text('utf-8'))
        html=(root/'paper_radar/static/index.html').read_text('utf-8')
        self.assertIn(f'Paper-Radar v{__version__}',html)

if __name__=='__main__': unittest.main()
