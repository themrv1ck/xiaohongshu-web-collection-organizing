#!/usr/bin/env python3
import argparse
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from create_board import build_create_board_job, validate_args, validate_result  # noqa: E402
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
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class CreateBoardTests(unittest.TestCase):
    def test_job_uses_exact_official_create_contract_and_strict_postflight(self):
        job = build_create_board_job(args())
        self.assertIn("'/api/sns/web/v1/board'", job)
        self.assertIn("'web创建专辑'", job)
        self.assertIn(r'/\.\s*post\s*\(/', job)
        self.assertIn('name: payload.name', job)
        self.assertIn('desc: payload.desc', job)
        self.assertIn('privacy: payload.privacy', job)
        self.assertIn('after.boardCount !== before.boardCount + 1', job)
        self.assertIn('assertOldBoardsUnchanged(before, after)', job)
        self.assertIn('snapshot.accessibleTotal !== 0', job)
        self.assertIn('response.boardCount == null ? rawBoards.length', job)
        self.assertIn("events: ['preflight:board_already_exists', 'verify:existing_board_empty']", job)
        self.assertIn('HIGH_RISK_STATE_UNCERTAIN', job)
        self.assertIn('no delete rollback attempted', job)
        self.assertNotIn('await api.LN(', job)
        self.assertNotIn('await api.B1(', job)
        self.assertNotIn('await api.d0(', job)
        self.assertNotIn('fetch(', job)
        subprocess.run(
            ['node', '-e', 'new Function(process.argv[1]);', job],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

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


if __name__ == '__main__':
    unittest.main()
