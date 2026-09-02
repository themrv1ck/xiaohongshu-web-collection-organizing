#!/usr/bin/env python3
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from capture_board_snapshot import capture_snapshot  # noqa: E402
from create_board import build_create_board_job, execute_create_board  # noqa: E402
from run_reassign_batch import apply_batch, build_browser_job  # noqa: E402
from verify_board_membership import build_snapshot_job  # noqa: E402
from xhs_safety import classify_safety_error  # noqa: E402


class Security300031RegressionTests(unittest.TestCase):
    def test_executable_sources_do_not_register_custom_xhs_runtime_chunks(self):
        forbidden = 'webpackChunk' + 'xhs_pc_web'
        offenders = []
        for path in sorted(SCRIPTS.glob('*.py')):
            if forbidden in path.read_text(encoding='utf-8'):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_all_known_redirect_signatures_halt_as_security_challenges(self):
        samples = (
            'https://www.xiaohongshu.com/website-login/error',
            '当前请求异常',
            '300031',
            '安全验证 当前请求异常 300031',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                classified = classify_safety_error(sample)
                self.assertIsNotNone(classified)
                self.assertEqual(classified[0], 'security_challenge')

    def test_read_and_create_jobs_are_visible_ui_and_historical_move_stays_disabled(self):
        create_args = argparse.Namespace(
            name='无法确定', desc='', privacy=0, execute=True,
            user_id='1' * 24, verify_pages=100,
            arc_tab_marker='marker',
            arc_expected_url_substring='/user/profile/',
            arc_window_id='window', arc_tab_id='tab', timeout_sec=30,
        )
        move_args = argparse.Namespace(
            allow_low_confidence=False,
            verify_pages=100,
            user_id='1' * 24,
            arc_tab_marker='',
            expected_url_substring='/user/profile/',
            arc_expected_url_substring='',
        )
        jobs = (
            build_snapshot_job('1' * 24, 100, 'marker', '/user/profile/'),
            build_create_board_job(create_args),
        )
        for job in jobs:
            for forbidden in ('webpackChunkxhs_pc_web', '/api/sns/web/v1/board', 'req.m'):
                self.assertNotIn(forbidden, job)
        with self.assertRaisesRegex(RuntimeError, '内部模块探测已禁用'):
            build_browser_job([], move_args)

    def test_account_operations_stop_before_browser_constructor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture_args = argparse.Namespace(
                output=str(root / 'board_snapshot.json'),
                browser='playwright', user_id='1' * 24,
                expected_url_substring='/user/profile/', verify_pages=100,
                timeout_sec=30, safety_state='', arc_window_id='',
                arc_tab_id='', arc_tab_marker='',
                arc_expected_url_substring='', url=None, channel='chromium',
                user_data_dir=None, cdp_url=None, headless=False,
            )
            create_args = argparse.Namespace(
                name='无法确定', desc='', privacy=0, execute=True,
                user_id='1' * 24, report=str(root / 'create.json'),
                safety_state='', verify_pages=100, timeout_sec=30,
                arc_window_id='window', arc_tab_id='tab',
                arc_tab_marker='marker',
                arc_expected_url_substring='/user/profile/',
            )
            move_args = argparse.Namespace(
                browser='safari', arc_window_id='', arc_tab_id='',
                arc_tab_marker='', arc_expected_url_substring='',
                expected_url_substring='', inter_item_delay_sec=0,
                max_moves_per_session=1, allow_low_confidence=False,
                verify_pages=100, user_id='', timeout_sec=30,
                safety_state='',
            )
            move_rows = [{
                'id': '2' * 24, 'title': '测试', 'target_board': '无法确定',
                'confidence': 'high', 'membership_state': 'not_in_any_board',
                'archive_lifecycle_state': 'first_archive_pending',
                'source_board_id': '',
            }]
            report = {
                'processed': [], 'errors': [], 'missing_boards': [],
                'board_counts_before': {}, 'board_counts_after': {},
            }

            with patch('capture_board_snapshot.ArcVisibleUiSession') as arc:
                with self.assertRaisesRegex(RuntimeError, '只支持.*Arc'):
                    capture_snapshot(capture_args)
                arc.assert_not_called()

            with patch('run_reassign_batch.BrowserRunner') as browser:
                with self.assertRaisesRegex(RuntimeError, '内部模块探测已禁用'):
                    apply_batch(move_rows, report, move_args, root / 'move.json')
                browser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
