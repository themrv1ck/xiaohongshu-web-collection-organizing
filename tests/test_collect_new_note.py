#!/usr/bin/env python3
import argparse
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from collect_new_note import execute_collect, validate_args  # noqa: E402
from verify_board_membership import MembershipContractError  # noqa: E402


def args(root: Path, **overrides):
    values = {
        'note_id': '1' * 24,
        'target_board': '无法确定',
        'report': str(root / 'collect.json'),
        'execute': False,
        'user_id': '2' * 24,
        'arc_window_id': 'window',
        'arc_tab_id': 'tab',
        'arc_tab_marker': 'marker',
        'arc_expected_url_substring': '/user/profile/',
        'verify_pages': 100,
        'timeout_sec': 30,
        'safety_state': '',
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class CollectNewNoteTests(unittest.TestCase):
    def test_arguments_protect_identity_and_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            validate_args(args(root))
            for broken in (
                args(root, note_id='bad'),
                args(root, target_board=''),
                args(root, target_board=' 无法确定'),
                args(root, arc_tab_marker=''),
            ):
                with self.assertRaises(MembershipContractError):
                    validate_args(broken)

    def test_dry_run_uses_exact_arc_binding_and_writes_audit_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = {
                'status': 'planned',
                'writePerformed': False,
                'note_id': '1' * 24,
                'target_board': '无法确定',
                'board_id': '3' * 24,
                'events': ['preflight:note_uncollected', 'dry_run:no_account_changes'],
            }
            with patch('collect_new_note.ArcVisibleUiSession') as session_cls, patch(
                'collect_new_note.collect_new_note_into_board', return_value=expected
            ) as collect:
                result = execute_collect(args(root))
            session_cls.assert_called_once_with('window', 'tab', 'marker', '2' * 24)
            collect.assert_called_once()
            self.assertFalse(collect.call_args.kwargs['execute'])
            self.assertTrue(result['passed'])
            self.assertFalse(result['writePerformed'])
            self.assertTrue((root / 'collect.json').exists())


if __name__ == '__main__':
    unittest.main()
