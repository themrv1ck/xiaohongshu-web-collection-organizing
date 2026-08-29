#!/usr/bin/env python3
import argparse
import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import verify_board_membership as board_verifier  # noqa: E402
import verify_classification_membership as classification_verifier  # noqa: E402


class VerifyClassificationMembershipTests(unittest.TestCase):
    BOARD_A = 'a' * 24
    BOARD_B = 'b' * 24

    @staticmethod
    def note_id(number):
        return f'{number:024x}'

    def classification(self):
        return [
            {
                'id': self.note_id(1),
                'content_type': 'video',
                'classification_basis': 'video_content',
                'video_analysis_status': 'success',
                'review_state': 'video_content_classified',
                'target_board': '专辑A',
                'confidence': 'high',
            },
            {
                'id': self.note_id(2),
                'content_type': 'video',
                'classification_basis': 'video_content',
                'video_analysis_status': 'success',
                'review_state': 'video_content_classified',
                'target_board': '专辑B',
                'confidence': 'medium',
            },
            {
                'id': self.note_id(3),
                'content_type': 'video',
                'classification_basis': 'video_content',
                'video_analysis_status': 'success',
                'review_state': 'video_content_needs_review',
                'target_board': '',
                'confidence': 'low',
            },
            {
                'id': self.note_id(4),
                'content_type': 'video',
                'classification_basis': 'metadata_and_ocr',
                'video_analysis_status': 'success',
                'review_state': 'video_content_classified',
                'target_board': '专辑A',
                'confidence': 'high',
            },
            {
                'id': self.note_id(5),
                'content_type': 'image',
                'classification_basis': 'video_content',
                'video_analysis_status': 'success',
                'review_state': 'video_content_classified',
                'target_board': '专辑A',
                'confidence': 'high',
            },
        ]

    def browser_result(self):
        return {
            'board_count': 2,
            'board_list_page_count': 1,
            'boards': [
                {
                    'id': self.BOARD_A,
                    'name': '专辑A',
                    'privacy': 0,
                    'declared_total': 1,
                    'accessible_unique_count': 1,
                    'page_count': 1,
                    'note_ids': [self.note_id(1)],
                },
                {
                    'id': self.BOARD_B,
                    'name': '专辑B',
                    'privacy': 0,
                    'declared_total': 1,
                    'accessible_unique_count': 1,
                    'page_count': 1,
                    'note_ids': [self.note_id(2)],
                },
            ],
        }

    def args(self):
        return argparse.Namespace(
            arc_window_id='window-id',
            arc_tab_id='tab-id',
            arc_tab_marker='worker-marker',
            arc_expected_url_substring='/user/profile/',
        )

    def snapshot(self, result=None):
        return board_verifier.normalize_live_snapshot(result or self.browser_result(), self.args())

    def test_reuses_existing_snapshot_stack_without_copying_browser_javascript(self):
        self.assertIs(classification_verifier.build_snapshot_job, board_verifier.build_snapshot_job)
        self.assertIs(classification_verifier.normalize_live_snapshot, board_verifier.normalize_live_snapshot)
        self.assertIs(classification_verifier.BrowserRunner, board_verifier.BrowserRunner)
        source = (SCRIPTS / 'verify_classification_membership.py').read_text(encoding='utf-8')
        self.assertNotIn('LIVE_API_RESOLVER_JS', source)
        self.assertNotIn('BOARD_VERIFICATION_JS', source)
        self.assertNotIn("r'''\n(function()", source)

    def test_selects_only_strictly_safe_videos_and_reports_unresolved(self):
        scope = classification_verifier.build_classification_scope(self.classification())
        self.assertEqual(scope['counts'], {
            'classification_rows': 5,
            'video_rows': 4,
            'safe_video_rows': 2,
            'unresolved_video_rows': 1,
        })
        self.assertEqual([row['id'] for row in scope['safe_videos']], [self.note_id(1), self.note_id(2)])
        self.assertEqual(scope['unresolved_videos'], [{
            'id': self.note_id(3),
            'target_board': '',
            'confidence': 'low',
            'review_state': 'video_content_needs_review',
            'reasons': ['empty_target', 'low_confidence', 'needs_review'],
        }])

    def test_passes_when_every_safe_video_is_exactly_once_in_its_target(self):
        scope = classification_verifier.build_classification_scope(self.classification())
        report = classification_verifier.verify_classification_membership(self.snapshot(), scope)
        self.assertTrue(report['passed'])
        self.assertEqual(report['assertions']['safe_videos_globally_exactly_once']['actual'], 2)
        self.assertEqual(report['assertions']['safe_videos_in_target_board']['actual'], 2)
        self.assertEqual(report['assertions']['global_duplicate_note_ids']['actual'], 0)
        self.assertEqual(report['unresolved_videos'], scope['unresolved_videos'])

    def test_fails_for_wrong_board_missing_board_or_global_duplicate(self):
        base_scope = classification_verifier.build_classification_scope(self.classification())

        wrong = copy.deepcopy(self.browser_result())
        wrong['boards'][0]['note_ids'] = []
        wrong['boards'][0]['declared_total'] = 0
        wrong['boards'][0]['accessible_unique_count'] = 0
        wrong['boards'][1]['note_ids'].append(self.note_id(1))
        wrong['boards'][1]['declared_total'] += 1
        wrong['boards'][1]['accessible_unique_count'] += 1
        wrong_report = classification_verifier.verify_classification_membership(self.snapshot(wrong), base_scope)
        self.assertFalse(wrong_report['passed'])
        self.assertEqual(wrong_report['assertions']['safe_videos_in_target_board']['actual'], 1)

        missing_board_rows = copy.deepcopy(self.classification())
        missing_board_rows[0]['target_board'] = '不存在的专辑'
        missing_scope = classification_verifier.build_classification_scope(missing_board_rows)
        missing_report = classification_verifier.verify_classification_membership(self.snapshot(), missing_scope)
        self.assertFalse(missing_report['passed'])
        self.assertEqual(missing_report['mismatches']['missing_target_boards'], ['不存在的专辑'])

        duplicate = copy.deepcopy(self.browser_result())
        duplicate['boards'][1]['note_ids'].append(self.note_id(1))
        duplicate['boards'][1]['declared_total'] += 1
        duplicate['boards'][1]['accessible_unique_count'] += 1
        duplicate_report = classification_verifier.verify_classification_membership(
            self.snapshot(duplicate), base_scope
        )
        self.assertFalse(duplicate_report['passed'])
        self.assertEqual(duplicate_report['assertions']['global_duplicate_note_ids']['actual'], 1)

    def test_rejects_duplicate_video_ids(self):
        rows = self.classification()
        rows.append(copy.deepcopy(rows[0]))
        with self.assertRaises(board_verifier.MembershipContractError):
            classification_verifier.build_classification_scope(rows)


if __name__ == '__main__':
    unittest.main()
