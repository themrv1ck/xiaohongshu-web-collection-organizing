#!/usr/bin/env python3
import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from workbuddy_bridge import (  # noqa: E402
    approval_digest,
    execute_action,
    status_action,
    validate_run_id,
    validate_xhs_url,
)


class WorkBuddyBridgeTests(unittest.TestCase):
    def workbuddy_env(self, data_dir: Path):
        return {
            'XHS_HOST': 'workbuddy',
            'CODEBUDDY_PLUGIN_DATA': str(data_dir),
            'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
        }

    def write_ready_plan(self, data_dir: Path, run_id: str = 'run-1'):
        directory = data_dir / 'runs' / run_id
        directory.mkdir(parents=True)
        (directory / 'classification.json').write_text(
            json.dumps([{
                'id': '66d19b54000000001d03a93d',
                'target_board': '滑雪',
            }], ensure_ascii=False),
            encoding='utf-8',
        )
        (directory / 'board_snapshot.json').write_text(
            json.dumps({'mode': 'read_only'}, ensure_ascii=False),
            encoding='utf-8',
        )
        (directory / 'created_boards.json').write_text(
            json.dumps({'created_boards': ['滑雪']}, ensure_ascii=False),
            encoding='utf-8',
        )
        report = {
            'mode': 'dry_run',
            'ready_for_execute': True,
            'blockers': [],
            'processed': [{
                'id': '66d19b54000000001d03a93d',
                'target_board': '滑雪',
                'source_board_id': '',
                'membership_state': 'not_in_any_board',
                'status': 'planned',
            }],
        }
        (directory / 'run_report.json').write_text(
            json.dumps(report, ensure_ascii=False),
            encoding='utf-8',
        )
        return directory, report

    def test_status_requires_explicit_workbuddy_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'WorkBuddy Plugin'):
                    status_action()
            data_dir = Path(tmp)
            with patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True):
                status = status_action()
            self.assertTrue(status['ok'])
            self.assertEqual(status['runtime']['host'], 'workbuddy')
            self.assertIn('install_required', status['dependencies'])

    def test_run_id_cannot_escape_persistent_runs_directory(self):
        for invalid in ('', '../escape', 'a/b', '.hidden', 'x' * 65):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RuntimeError):
                    validate_run_id(invalid)
        self.assertEqual(validate_run_id('run-20260731_01'), 'run-20260731_01')

    def test_source_url_contract_is_exact(self):
        collection = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav'
        )
        self.assertEqual(validate_xhs_url(collection, 'collection'), collection)
        with self.assertRaisesRegex(RuntimeError, 'tab=fav'):
            validate_xhs_url(collection.replace('tab=fav', 'tab=liked'), 'collection')
        with self.assertRaisesRegex(RuntimeError, 'xiaohongshu.com'):
            validate_xhs_url('https://example.com/?tab=fav', 'collection')

    def test_approval_digest_changes_when_plan_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, report = self.write_ready_plan(Path(tmp))
            before = approval_digest(directory, report)
            report['processed'][0]['target_board'] = '运动'
            after = approval_digest(directory, report)
            self.assertNotEqual(before, after)

    def test_execute_rejects_before_browser_when_approval_digest_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory, report = self.write_ready_plan(data_dir)
            digest = approval_digest(directory, report)
            classification = json.loads(
                (directory / 'classification.json').read_text(encoding='utf-8')
            )
            classification[0]['target_board'] = '运动'
            (directory / 'classification.json').write_text(
                json.dumps(classification, ensure_ascii=False),
                encoding='utf-8',
            )
            with patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True):
                with patch('workbuddy_bridge.run_command') as run_command:
                    with self.assertRaisesRegex(RuntimeError, 'approval_digest'):
                        execute_action(
                            'run-1',
                            '66d19b54000000001d03a93d',
                            (
                                'https://www.xiaohongshu.com/user/profile/'
                                '66d19b54000000001d03a93d?tab=fav'
                            ),
                            '/user/profile/66d19b54000000001d03a93d',
                            digest,
                            10,
                        )
                    run_command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
