#!/usr/bin/env python3
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from workbuddy_bridge import (  # noqa: E402
    approval_digest,
    build_parser,
    capture_action,
    capture_workbuddy_groups,
    execute_action,
    login_action,
    metadata_quality,
    prepare_action,
    run_command,
    status_action,
    validate_run_id,
    validate_xhs_url,
    write_workbuddy_classification,
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

    def test_capture_defaults_to_v2_controlled_group_contract(self):
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        args = build_parser().parse_args([
            'capture',
            '--source', 'collection',
            '--page-url', target_url,
        ])
        self.assertEqual(args.batch_size, 200)
        self.assertEqual(args.pause_minutes, 3)
        self.assertNotIn('segment_limit', vars(args))
        self.assertNotIn('quick_classify', vars(args))
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('batch_size=200', skill)
        self.assertIn('pause_minutes=3', skill)

    def test_workbuddy_auto_pagination_saves_200_item_groups_and_waits_180_seconds(self):
        payload = {
            'scrollY': 9000,
            'innerHeight': 1000,
            'scrollHeight': 10000,
            'location': (
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav&subTab=note'
            ),
            'title': '收藏',
            'declaredItemCount': 205,
            'loginRequired': False,
            'securityMarker': '',
            'items': [
                {
                    'id': f'{index:024x}',
                    'title': f'条目 {index}',
                    'page_index': index,
                }
                for index in range(205)
            ],
        }
        browser_calls = []

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            safety = directory / 'xhs_safety_state.json'
            with (
                patch(
                    'workbuddy_bridge.read_stable_items_snapshot',
                    return_value=(payload, 1),
                ),
                patch('workbuddy_bridge.time.sleep') as sleep,
            ):
                result = capture_workbuddy_groups(
                    lambda script: browser_calls.append(script) or 'ok',
                    directory,
                    'collection',
                    200,
                    3,
                    safety,
                )
            first = json.loads(
                (directory / 'visible_items.segment-001.json').read_text(encoding='utf-8')
            )
            second = json.loads(
                (directory / 'visible_items.segment-002.json').read_text(encoding='utf-8')
            )

        self.assertEqual([len(first), len(second)], [200, 5])
        self.assertEqual([call.args for call in sleep.call_args_list], [(180,)])
        self.assertEqual(result['count'], 205)
        self.assertEqual(result['segment_count'], 2)
        self.assertTrue(result['crawl_complete'])
        self.assertEqual(
            len([script for script in browser_calls if 'scrollTo(0, 0)' in script]),
            1,
        )

    def test_workbuddy_auto_pagination_stops_at_stable_frontend_end_when_ui_count_differs(self):
        payload = {
            'scrollY': 9000,
            'innerHeight': 1000,
            'scrollHeight': 10000,
            'location': (
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav&subTab=note'
            ),
            'title': '收藏',
            'declaredItemCount': 209,
            'loginRequired': False,
            'securityMarker': '',
            'items': [
                {
                    'id': f'{index:024x}',
                    'title': f'条目 {index}',
                    'page_index': index,
                }
                for index in range(205)
            ],
        }
        browser_calls = []

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with (
                patch(
                    'workbuddy_bridge.read_stable_items_snapshot',
                    side_effect=[(payload, 1), (payload, 1)],
                ),
                patch('workbuddy_bridge.time.sleep') as sleep,
            ):
                result = capture_workbuddy_groups(
                    lambda script: browser_calls.append(script) or 'ok',
                    directory,
                    'collection',
                    200,
                    3,
                    directory / 'xhs_safety_state.json',
                )

        self.assertEqual([call.args for call in sleep.call_args_list], [(180,)])
        self.assertEqual(
            len([script for script in browser_calls if 'Date.now() + 2500' in script]),
            1,
        )
        self.assertTrue(result['crawl_complete'])
        self.assertEqual(result['warnings'], [{
            'code': 'declared_count_mismatch',
            'declared_count': 209,
            'accessible_count': 205,
        }])

    def test_prepare_writes_only_classification_for_real_captured_ids(self):
        first_id = '66d19b54000000001d03a93d'
        second_id = '66d19b54000000001d03a93e'
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'visible_items.json').write_text(
                json.dumps([
                    {'id': first_id, 'title': '客厅照明改造'},
                    {'id': second_id, 'title': '看不出主题'},
                ], ensure_ascii=False),
                encoding='utf-8',
            )
            result = write_workbuddy_classification(directory, [
                {
                    'id': first_id,
                    'target_board': '居住空间',
                    'confidence': 'high',
                    'reason': ['内容明确讨论客厅照明'],
                    'review_state': 'classified',
                },
                {
                    'id': second_id,
                    'target_board': '',
                    'confidence': 'low',
                    'reason': ['内容不足'],
                    'review_state': 'pending',
                },
            ], ['居住空间'])
            taxonomy = json.loads(
                (directory / 'board_taxonomy.json').read_text(encoding='utf-8')
            )
            classification = json.loads(
                (directory / 'classification.json').read_text(encoding='utf-8')
            )
            with self.assertRaisesRegex(RuntimeError, '不属于本次抓取'):
                write_workbuddy_classification(directory, [{
                    'id': '66d19b54000000001d03aff',
                    'target_board': '无关类别',
                }], ['居住空间'])

        self.assertEqual(result['taxonomy'], ['居住空间'])
        self.assertEqual(taxonomy, {'boards': ['居住空间']})
        self.assertEqual([row['id'] for row in classification], [first_id, second_id])
        self.assertEqual(classification[0]['title'], '客厅照明改造')
        self.assertEqual(classification[1]['target_board'], '')

    def test_prepare_first_phase_returns_real_board_names_before_classification(self):
        user_id = '66d19b54000000001d03a93d'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            directory.mkdir(parents=True)
            (directory / 'visible_items.json').write_text('[]', encoding='utf-8')

            def fake_run_command(args, **_kwargs):
                snapshot = Path(args[2])
                snapshot.write_text(json.dumps({
                    'mode': 'read_only',
                    'source': {
                        'browser': 'playwright',
                        'writes_performed': False,
                        'user_id': user_id,
                        'expected_url_substring': expected,
                    },
                    'boards': [
                        {'id': 'a' * 24, 'name': '阅读', 'note_ids': []},
                        {'id': 'b' * 24, 'name': '运动', 'note_ids': []},
                    ],
                }, ensure_ascii=False), encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '{}', '')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command) as command,
            ):
                result = prepare_action('run-1', user_id, page_url, expected, 100)

        self.assertEqual(result['phase'], 'board_inventory')
        self.assertEqual(result['existing_board_names'], ['阅读', '运动'])
        self.assertEqual(result['blockers'], [])
        self.assertIsNone(result['approval_digest'])
        self.assertEqual(command.call_count, 1)
        self.assertIn('capture_board_snapshot.py', command.call_args.args[0][1])

    def test_prepare_first_phase_stops_when_account_has_no_existing_boards(self):
        user_id = '66d19b54000000001d03a93d'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            directory.mkdir(parents=True)
            (directory / 'visible_items.json').write_text('[]', encoding='utf-8')

            def fake_run_command(args, **_kwargs):
                Path(args[2]).write_text(json.dumps({
                    'mode': 'read_only',
                    'source': {
                        'browser': 'playwright',
                        'writes_performed': False,
                        'user_id': user_id,
                        'expected_url_substring': expected,
                    },
                    'boards': [],
                }), encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '{}', '')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command),
            ):
                result = prepare_action('run-1', user_id, page_url, expected, 100)

        self.assertEqual(result['existing_board_names'], [])
        self.assertFalse(result['classification_required'])
        self.assertEqual(result['blockers'], ['no_existing_boards'])

    def test_prepare_second_phase_reuses_bound_snapshot_and_rejects_new_categories(self):
        user_id = '66d19b54000000001d03a93d'
        note_id = '66d19b54000000001d03a93e'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            directory.mkdir(parents=True)
            (directory / 'visible_items.json').write_text(json.dumps([
                {'id': note_id, 'title': '一本书的读后感'},
            ], ensure_ascii=False), encoding='utf-8')
            (directory / 'board_snapshot.json').write_text(json.dumps({
                'mode': 'read_only',
                'source': {
                    'browser': 'playwright',
                    'writes_performed': False,
                    'user_id': user_id,
                    'expected_url_substring': expected,
                },
                'boards': [{
                    'id': 'a' * 24,
                    'name': '阅读',
                    'declared_total': 0,
                    'page_count': 1,
                    'note_ids': [],
                }],
                'validation': {
                    'pagination_cursor_invariants_passed': True,
                    'board_names_unique': True,
                    'within_board_duplicates': [],
                },
            }, ensure_ascii=False), encoding='utf-8')

            commands = []

            def fake_run_command(args, **_kwargs):
                commands.append(args)
                if 'build_created_boards.py' in args[1]:
                    Path(args[4]).write_text(json.dumps({
                        'confirmed': ['阅读'],
                        'created': [],
                        'missing': [],
                        'failed': [],
                    }, ensure_ascii=False), encoding='utf-8')
                elif 'run_reassign_batch.py' in args[1]:
                    Path(args[3]).write_text(json.dumps({
                        'mode': 'dry_run',
                        'ready_for_execute': True,
                        'blockers': [],
                        'warnings': [],
                        'processed': [{
                            'id': note_id,
                            'target_board': '阅读',
                            'source_board_id': '',
                            'membership_state': 'not_in_any_board',
                            'status': 'planned',
                        }],
                    }, ensure_ascii=False), encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '{}', '')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command),
            ):
                with self.assertRaisesRegex(RuntimeError, '真实已有专辑'):
                    prepare_action('run-1', user_id, page_url, expected, 100, [{
                        'id': note_id,
                        'target_board': '插件预设类别',
                    }])
                result = prepare_action('run-1', user_id, page_url, expected, 100, [{
                    'id': note_id,
                    'target_board': '阅读',
                    'confidence': 'high',
                    'reason': ['真实内容与已有专辑一致'],
                }])

        self.assertEqual(result['phase'], 'dry_run')
        self.assertTrue(result['ready_for_execute'])
        self.assertEqual(result['taxonomy'], ['阅读'])
        self.assertIsNotNone(result['approval_digest'])
        self.assertEqual(len(commands), 2)
        self.assertFalse(any('capture_board_snapshot.py' in args[1] for args in commands))

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group cancellation contract')
    def test_run_command_sigterm_closes_spawned_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pid_file = directory / 'child.pid'
            child_script = directory / 'child.py'
            child_script.write_text(
                'import os, pathlib, sys, time\n'
                'pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")\n'
                'time.sleep(60)\n',
                encoding='utf-8',
            )
            parent_code = (
                f'import sys; sys.path.insert(0, {str(SCRIPTS)!r}); '
                'from workbuddy_bridge import run_command; '
                f'run_command([{sys.executable!r}, {str(child_script)!r}, {str(pid_file)!r}])'
            )
            parent = subprocess.Popen(
                [sys.executable, '-c', parent_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            child_pid = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not pid_file.is_file():
                    time.sleep(0.05)
                self.assertTrue(pid_file.is_file())
                child_pid = int(pid_file.read_text(encoding='utf-8'))
                os.kill(parent.pid, signal.SIGTERM)
                parent.communicate(timeout=10)
                self.assertNotEqual(parent.returncode, 0)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_mcp_windows_cancellation_uses_exact_spawned_process_tree(self):
        server_source = (
            ROOT / 'workbuddy-plugin-src' / 'server.mjs'
        ).read_text(encoding='utf-8')
        self.assertIn("'taskkill.exe'", server_source)
        self.assertIn("['/PID', String(child.pid), '/T', '/F']", server_source)

    def test_workbuddy_normal_results_forbid_unrequested_visualization(self):
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        contract = next(
            line for line in skill.splitlines()
            if '普通整理结果直接在当前对话里用简短纯文本报告' in line
        )
        self.assertIn('不调用可视化 Skill', contract)
        self.assertIn('组件渲染', contract)
        self.assertIn('present_files', contract)
        self.assertIn('只有用户明确要求图表、网页或文件交付时才允许', contract)

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
                        200,
                        3,
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
