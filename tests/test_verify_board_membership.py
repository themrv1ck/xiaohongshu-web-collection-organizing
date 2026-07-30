#!/usr/bin/env python3
import argparse
import copy
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from run_reassign_batch import BOARD_VERIFICATION_JS, LIVE_API_RESOLVER_JS  # noqa: E402
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

    def test_snapshot_job_reuses_exact_resolver_and_is_read_only(self):
        job = build_snapshot_job('1' * 24, 100, 'worker-marker', '/user/profile/')
        self.assertIn(LIVE_API_RESOLVER_JS, job)
        self.assertIn(BOARD_VERIFICATION_JS, job)
        self.assertIn('req.m', job)
        self.assertNotIn('req.c', job)
        self.assertIn("document.createElement('script')", job)
        self.assertIn("dataset.xhsSkillState = 'pending'", job)
        self.assertIn('await readApi.yC(', job)
        self.assertIn('await boardSnapshot(readApi, board.id, payload.verifyPages, assertReadContext)', job)
        self.assertNotRegex(job, re.compile(r'(?:readApi|fullApi)\.(?:LN|B1|d0)\s*\('))
        self.assertNotIn('fetch(', job)
        subprocess.run(
            ['node', '-e', 'new Function(process.argv[1]);', job],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

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
