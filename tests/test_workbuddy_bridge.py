#!/usr/bin/env python3
import argparse
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from workbuddy_bridge import (  # noqa: E402
    approval_digest,
    capture_action,
    execute_action,
    login_action,
    metadata_quality,
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

    def test_login_detects_own_profile_and_returns_collection_url_without_user_closing(self):
        target_user_id = '66d19b54000000001d03a93d'
        own_profile_url = f'https://www.xiaohongshu.com/user/profile/{target_user_id}'

        class FakePage:
            def __init__(self, own_url=''):
                self.own_url = own_url
                self.url = 'about:blank'
                self.closed = False
                self.visited = []

            def goto(self, url, **_kwargs):
                self.url = url
                self.visited.append(url)

            def evaluate(self, _script):
                return self.own_url

            def close(self):
                self.closed = True

            def is_closed(self):
                return self.closed

        primary = FakePage(own_profile_url)
        stale = FakePage()

        class FakeContext:
            def __init__(self):
                self.pages = [primary, stale]
                self.closed = False

            def close(self):
                self.closed = True

        context = FakeContext()

        class FakePlaywright:
            def __init__(self):
                self.chromium = types.SimpleNamespace(
                    launch_persistent_context=lambda *_args, **_kwargs: context
                )

        class FakePlaywrightManager:
            def __enter__(self):
                return FakePlaywright()

            def __exit__(self, *_args):
                return False

        fake_playwright = types.ModuleType('playwright')
        fake_playwright.__path__ = []
        fake_sync_api = types.ModuleType('playwright.sync_api')
        fake_sync_api.sync_playwright = lambda: FakePlaywrightManager()

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch.dict(
                    sys.modules,
                    {
                        'playwright': fake_playwright,
                        'playwright.sync_api': fake_sync_api,
                    },
                ),
            ):
                result = login_action(60, 'collection')

            expected = f'{own_profile_url}?tab=fav&subTab=note'
            self.assertEqual(result['target_page_url'], expected)
            self.assertEqual(result['source'], 'collection')
            self.assertTrue(result['browser_closed_by_tool'])
            self.assertTrue(context.closed)
            self.assertTrue(stale.closed)
            self.assertEqual(primary.visited[-1], expected)
            saved = json.loads(
                (data_dir / 'last_login.json').read_text(encoding='utf-8')
            )
            self.assertEqual(saved['target_page_url'], expected)

    def test_capture_rejects_busy_profile_before_creating_run(self):
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            profile = data_dir / 'playwright-profile'
            profile.mkdir()
            (profile / 'SingletonLock').write_text('busy', encoding='utf-8')
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch(
                    'workbuddy_bridge.BrowserRunner',
                    side_effect=AssertionError('browser must not launch'),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, '专用浏览器仍在使用'):
                    capture_action(
                        'locked-run',
                        'collection',
                        target_url,
                        10,
                        True,
                    )
            self.assertFalse((data_dir / 'runs' / 'locked-run').exists())

    def test_metadata_quality_rejects_id_only_capture(self):
        empty = metadata_quality([{
            'id': '66d19b54000000001d03a93d',
            'title': '',
            'user': '',
            'desc': '',
            'card_text': '',
            'tags': [],
        }])
        usable = metadata_quality([{
            'id': '66d19b54000000001d03a93d',
            'title': '2026 年先读这 10 本书',
            'user': 'BetterLiving编辑手记',
        }])
        self.assertEqual(empty['usable_item_count'], 0)
        self.assertEqual(empty['unusable_item_count'], 1)
        self.assertEqual(usable['usable_item_count'], 1)
        self.assertEqual(usable['unusable_item_count'], 0)

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
