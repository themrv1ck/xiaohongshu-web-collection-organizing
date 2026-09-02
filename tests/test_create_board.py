#!/usr/bin/env python3
import argparse
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from create_board import build_create_board_job, execute_create_board, validate_args, validate_result  # noqa: E402
from verify_board_membership import MembershipContractError  # noqa: E402


def args(**overrides):
    values = {
        'name': '其他',
        'desc': '',
        'privacy': 0,
        'execute': True,
        'user_id': '1' * 24,
        'arc_window_id': 'window-id',
        'arc_tab_id': 'tab-id',
        'arc_tab_marker': 'worker-marker',
        'arc_expected_url_substring': '/user/profile/',
        'verify_pages': 100,
        'timeout_sec': 180,
        'report': '',
        'safety_state': '',
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class CreateBoardTests(unittest.TestCase):
    def test_create_job_uses_only_visible_form(self):
        job = build_create_board_job(args())
        self.assertIn(".create-board", job)
        for forbidden in ('webpackChunkxhs_pc_web', '/api/sns/web/v1/board', 'req.m'):
            self.assertNotIn(forbidden, job)

    def test_args_are_strict(self):
        validate_args(args())
        for broken in (
            args(name=''),
            args(name=' 其他'),
            args(privacy=2),
            args(arc_tab_marker=''),
        ):
            with self.assertRaises(MembershipContractError):
                validate_args(broken)

    def test_created_result_requires_verified_empty_board(self):
        result = {
            'status': 'created',
            'writePerformed': True,
            'board': {'id': 'a' * 24, 'name': '其他', 'privacy': 0},
            'emptyBoardVerified': True,
        }
        self.assertEqual(validate_result(result, True), result)
        broken = dict(result, emptyBoardVerified=False)
        with self.assertRaises(MembershipContractError):
            validate_result(broken, True)

    def test_dry_run_and_existing_board_are_idempotent(self):
        planned = {'status': 'planned', 'writePerformed': False, 'board': None}
        self.assertEqual(validate_result(planned, False), planned)
        existing = {
            'status': 'already_exists',
            'writePerformed': False,
            'board': {'id': 'b' * 24, 'name': '其他', 'privacy': 0},
            'emptyBoardVerified': True,
        }
        self.assertEqual(validate_result(existing, True), existing)
        with self.assertRaises(MembershipContractError):
            validate_result(planned, True)

    def test_execute_adapter_reuses_exact_arc_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = str(Path(tmp) / 'create.json')
            result = {
                'status': 'planned',
                'writePerformed': False,
                'board': None,
                'boardCountBefore': 12,
                'boardCountAfter': 12,
                'events': ['dry_run:no_account_changes'],
            }
            with patch('create_board.ArcVisibleUiSession') as session_cls, patch(
                'create_board.create_visible_board', return_value=result
            ) as create:
                output = execute_create_board(args(execute=False, report=report))
            session_cls.assert_called_once_with('window-id', 'tab-id', 'worker-marker', '1' * 24)
            self.assertFalse(create.call_args.kwargs['execute'])
            self.assertTrue(output['passed'])
            self.assertTrue(Path(report).exists())


if __name__ == '__main__':
    unittest.main()
