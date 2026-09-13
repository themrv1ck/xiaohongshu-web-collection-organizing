#!/usr/bin/env python3
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


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

    def test_read_create_and_move_jobs_use_only_visible_ui(self):
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
            build_browser_job([{
                'id': '2' * 24,
                'target_board': '无法确定',
                'confidence': 'high',
                'membership_state': 'not_in_any_board',
                'archive_lifecycle_state': 'first_archive_pending',
                'source_primary': '收藏',
            }], move_args),
        )
        for job in jobs:
            for forbidden in ('webpackChunkxhs_pc_web', '/api/sns/web/v1/', 'req.m', '/search_result'):
                self.assertNotIn(forbidden, job)

    def test_visible_snapshot_closes_only_its_runner(self):
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
            runner = Mock()
            snapshot = {
                'mode': 'read_only',
                'source': {'writes_performed': False},
                'boards': [],
                'validation': {
                    'board_count': 0,
                    'full_membership_complete': True,
                    'count_mismatch_boards': [],
                },
            }
            with patch('capture_board_snapshot.BrowserRunner', return_value=runner), patch(
                'capture_board_snapshot.capture_visible_album_snapshot', return_value=snapshot
            ):
                capture_snapshot(capture_args)
            runner.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
