#!/usr/bin/env python3
import argparse
import copy
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from run_reassign_batch import BOARD_VERIFICATION_JS, LIVE_API_RESOLVER_JS  # noqa: E402
from capture_board_snapshot import capture_snapshot, validate_args as validate_snapshot_capture_args  # noqa: E402
from verify_board_membership import (  # noqa: E402
    MembershipContractError,
    build_snapshot_job,
    expected_board_membership,
    normalize_live_snapshot,
    validate_arc_locator,
    validate_input_contract,
    verify_final_snapshot,
)


class VerifyBoardMembershipTests(unittest.TestCase):
    BOARD_IDS = {
        '专辑A': 'a' * 24,
        '专辑B': 'b' * 24,
        '其他': 'c' * 24,
    }

    @staticmethod
    def note_id(number):
        return f'{number:024x}'

    def fixture(self):
        safe_rows = []
        classification = []
        board_notes = {name: [] for name in self.BOARD_IDS}
        baseline_hash = 'd' * 64
        cross_rows = []

        for index in range(160):
            note_id = self.note_id(index + 1)
            target = '专辑A' if index % 2 == 0 else '专辑B'
            safe_rows.append({'id': note_id, 'target_board': target})
            if index < 121:
                status = 'already_in_target'
                current = target
            elif index < 159:
                status = 'in_other_board'
                current = '专辑B' if target == '专辑A' else '专辑A'
                cross_rows.append({
                    'id': note_id,
                    'target_board': target,
                    'source_board_id': self.BOARD_IDS[current],
                    'membership_evidence_sha256': baseline_hash,
                })
            else:
                status = 'not_in_any_board'
                current = None
            if current:
                board_notes[current].append(note_id)
            classification.append({
                'id': note_id,
                'target_board': target,
                'status': status,
                'boards': [] if not current else [{
                    'board_id': self.BOARD_IDS[current],
                    'board_name': current,
                }],
            })

        board_notes['其他'].append(self.note_id(1000))
        baseline = {
            'mode': 'read_only',
            'source': {'writes_performed': False},
            'boards': [
                {'id': board_id, 'name': name, 'note_ids': board_notes[name]}
                for name, board_id in self.BOARD_IDS.items()
            ],
            'classification': {'items': classification},
        }
        contract = validate_input_contract(safe_rows, cross_rows, baseline, baseline_hash)
        expected = expected_board_membership(contract)
        browser_result = {
            'board_count': len(expected),
            'board_list_page_count': 1,
            'boards': [
                {
                    'id': board_id,
                    'name': contract['board_by_id'][board_id]['name'],
                    'privacy': 0,
                    'declared_total': len(note_ids),
                    'accessible_unique_count': len(note_ids),
                    'page_count': max(1, (len(note_ids) + 29) // 30),
                    'note_ids': sorted(note_ids),
                }
                for board_id, note_ids in expected.items()
            ],
        }
        args = argparse.Namespace(
            arc_window_id='window-id',
            arc_tab_id='tab-id',
            arc_tab_marker='worker-marker',
            arc_expected_url_substring='/user/profile/',
        )
        return contract, browser_result, args

    @staticmethod
    def move(result, note_id, source_name, target_name):
        by_name = {board['name']: board for board in result['boards']}
        by_name[source_name]['note_ids'].remove(note_id)
        by_name[source_name]['accessible_unique_count'] -= 1
        by_name[source_name]['declared_total'] -= 1
        by_name[target_name]['note_ids'].append(note_id)
        by_name[target_name]['accessible_unique_count'] += 1
        by_name[target_name]['declared_total'] += 1

    def test_snapshot_job_reads_only_visible_album_cards(self):
        job = build_snapshot_job(
            '1' * 24,
            100,
            'worker-marker',
            'https://www.xiaohongshu.com/user/profile/' + '1' * 24 + '?tab=fav',
        )
        self.assertIn('a[href*="/board/"]', job)
        for forbidden in ('webpackChunkxhs_pc_web', '/api/sns/web/v1/board', 'req.m'):
            self.assertNotIn(forbidden, job)

    def test_arc_requires_all_four_locators(self):
        args = argparse.Namespace(
            arc_window_id='window-id', arc_tab_id='tab-id',
            arc_tab_marker='worker-marker', arc_expected_url_substring='/user/profile/',
            verify_pages=100, timeout_sec=300, user_id='1' * 24,
        )
        validate_arc_locator(args)
        for field in ('arc_window_id', 'arc_tab_id', 'arc_tab_marker', 'arc_expected_url_substring'):
            broken = copy.copy(args)
            setattr(broken, field, '')
            with self.subTest(field=field), self.assertRaises(MembershipContractError):
                validate_arc_locator(broken)

    def test_snapshot_capture_requires_explicit_browser_user_and_expected_url(self):
        args = argparse.Namespace(
            browser='safari',
            user_id='1' * 24,
            expected_url_substring='/user/profile/',
            verify_pages=100,
            timeout_sec=300,
            arc_window_id='',
            arc_tab_id='',
            arc_tab_marker='',
            arc_expected_url_substring='',
        )
        self.assertEqual(validate_snapshot_capture_args(args), 'safari')

        missing_url = copy.copy(args)
        missing_url.expected_url_substring = ''
        with self.assertRaisesRegex(MembershipContractError, 'expected-url-substring'):
            validate_snapshot_capture_args(missing_url)

        invalid_user = copy.copy(args)
        invalid_user.user_id = 'not-a-user-id'
        with self.assertRaisesRegex(MembershipContractError, '24-character'):
            validate_snapshot_capture_args(invalid_user)

    def test_snapshot_count_mismatch_hard_stops_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'board_snapshot.json'
            args = argparse.Namespace(
                output=str(output),
                browser='arc',
                user_id='1' * 24,
                expected_url_substring='/user/profile/',
                verify_pages=100,
                timeout_sec=300,
                safety_state=str(Path(tmp) / 'xhs_safety_state.json'),
                arc_window_id='window',
                arc_tab_id='tab',
                arc_tab_marker='marker',
                arc_expected_url_substring='/user/profile/',
            )
            with (
                patch('capture_board_snapshot.ensure_active_session'),
                patch('capture_board_snapshot.ArcVisibleUiSession'),
                patch('capture_board_snapshot.capture_visible_album_snapshot', side_effect=RuntimeError('专辑成员缺页：声明 2，只读取到 1')),
            ):
                with self.assertRaisesRegex(RuntimeError, '专辑成员缺页'):
                    capture_snapshot(args)

    def test_strict_success_is_160_target_38_source_absent_and_no_duplicates(self):
        contract, result, args = self.fixture()
        snapshot = normalize_live_snapshot(result, args)
        report = verify_final_snapshot(snapshot, contract)
        self.assertTrue(report['passed'])
        self.assertEqual(report['assertions']['target_membership']['actual'], 160)
        self.assertEqual(report['assertions']['classification_items_globally_exactly_once']['actual'], 160)
        self.assertEqual(report['assertions']['cross_source_absent']['actual'], 38)
        self.assertEqual(report['assertions']['unassigned_now_target']['actual'], 1)
        self.assertEqual(report['assertions']['global_duplicate_note_ids']['actual'], 0)
        self.assertTrue(report['assertions']['full_board_membership_matches_expected'])

    def test_strict_failures_are_reported(self):
        contract, final_result, args = self.fixture()
        cross_id, cross = next(iter(contract['cross_by_id'].items()))
        target = cross['target_board']
        source = contract['board_by_id'][cross['source_board_id']]['name']
        unassigned_id = contract['unassigned_ids'][0]
        unassigned_target = contract['safe_by_id'][unassigned_id]['target_board']

        source_present = copy.deepcopy(final_result)
        self.move(source_present, cross_id, target, source)
        source_report = verify_final_snapshot(normalize_live_snapshot(source_present, args), contract)
        self.assertFalse(source_report['passed'])
        self.assertEqual(source_report['assertions']['cross_source_absent']['actual'], 37)

        duplicate = copy.deepcopy(final_result)
        duplicate_board = '专辑B' if target == '专辑A' else '专辑A'
        board = next(row for row in duplicate['boards'] if row['name'] == duplicate_board)
        board['note_ids'].append(cross_id)
        board['accessible_unique_count'] += 1
        board['declared_total'] += 1
        duplicate_report = verify_final_snapshot(normalize_live_snapshot(duplicate, args), contract)
        self.assertFalse(duplicate_report['passed'])
        self.assertEqual(duplicate_report['assertions']['global_duplicate_note_ids']['actual'], 1)

        missing = copy.deepcopy(final_result)
        board = next(row for row in missing['boards'] if row['name'] == unassigned_target)
        board['note_ids'].remove(unassigned_id)
        board['accessible_unique_count'] -= 1
        board['declared_total'] -= 1
        missing_report = verify_final_snapshot(normalize_live_snapshot(missing, args), contract)
        self.assertFalse(missing_report['passed'])
        self.assertEqual(missing_report['assertions']['unassigned_now_target']['actual'], 0)


if __name__ == '__main__':
    unittest.main()
