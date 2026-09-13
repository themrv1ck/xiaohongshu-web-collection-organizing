#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from run_reassign_batch import BrowserRunner, apply_batch, build_browser_job, build_write_binding_probe, choose_backend, filter_classification_for_resume, is_ready_move, merge_report_chunk, parse_browser_job_id, parse_js_json, poll_browser_job, prepare_write_preflight, write_binding_blockers  # noqa: E402
from extract_visible_items import arc_js_macos, extract_with_js, read_stable_items_snapshot  # noqa: E402
from xhs_ocr_common import detect_ocr_provider, infer_board, load_taxonomy, run_tesseract_ocr  # noqa: E402


class CoreScriptTests(unittest.TestCase):

    def test_stable_snapshot_rejects_declared_total_change(self):
        snapshots = iter([
            json.dumps({
                'declaredItemCount': 314,
                'items': [{'id': 'a' * 24, 'page_index': 0}],
            }),
            json.dumps({
                'declaredItemCount': 10,
                'items': [{'id': 'a' * 24, 'page_index': 0}],
            }),
        ])
        with self.assertRaisesRegex(RuntimeError, '声明总数.*发生变化'):
            read_stable_items_snapshot(
                lambda _script: next(snapshots),
                settle_pause=0,
                max_checks=2,
            )

    def test_empty_first_frame_must_stabilize_before_completion(self):
        snapshots = iter([
            json.dumps({'declaredItemCount': 0, 'items': []}),
            json.dumps({
                'declaredItemCount': 314,
                'items': [{'id': 'a' * 24, 'page_index': 0}],
            }),
        ])
        with self.assertRaisesRegex(RuntimeError, '声明总数.*发生变化'):
            read_stable_items_snapshot(
                lambda _script: next(snapshots),
                settle_pause=0,
                max_checks=2,
            )

    def test_workbuddy_execute_probe_binds_exact_tab_and_frontend_account(self):
        user_id = '1' * 24
        binding = (
            f'https://www.xiaohongshu.com/user/profile/{user_id}?tab=fav'
        )
        args = type('Args', (), {
            'user_id': user_id,
            'expected_url_substring': binding,
            'arc_expected_url_substring': '',
        })()
        probe = build_write_binding_probe(args)
        self.assertIn('current.pathname', probe)
        self.assertIn("searchParams.get('tab')", probe)
        self.assertIn("textContent || '').trim() === '我'", probe)
        self.assertIn('account_binding_mismatch', probe)

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / args[0]), *args[1:]],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

    def test_runtime_taxonomy_is_empty_until_real_user_topics_are_supplied(self):
        boards = load_taxonomy(None)
        self.assertEqual(boards, [])
        self.assertEqual(
            load_taxonomy(ROOT / 'templates/board_taxonomy.template.json'),
            [],
        )
        item = {
            'id': '66d19b54000000001d03a93d',
            'title': '滑雪换刃练习',
            'desc': '',
            'tags': ['滑雪'],
            'user': '',
            'card_text': '滑雪 单板 换刃',
        }
        board, confidence, reason, review_state = infer_board(item, None, boards)
        self.assertEqual(board, '')
        self.assertEqual(confidence, 'low')
        self.assertEqual(reason, ['no_rule_match'])
        self.assertEqual(review_state, 'pending')

        board, confidence, reason, review_state = infer_board(
            item,
            None,
            ['滑雪'],
        )
        self.assertEqual(board, '滑雪')
        self.assertIn(confidence, {'medium', 'high'})
        self.assertTrue(reason)
        self.assertEqual(review_state, 'classified')

    def test_runtime_taxonomy_never_translates_hidden_preset_keywords(self):
        item = {
            'id': '66d19b54000000001d03a93d',
            'title': '家具收纳和客厅装修',
            'desc': '餐边柜与家政柜布置',
            'tags': ['家居'],
            'user': '',
            'card_text': '家具 收纳 装修',
        }
        board, confidence, reason, review_state = infer_board(
            item,
            None,
            ['居住空间'],
        )
        self.assertEqual(board, '')
        self.assertEqual(confidence, 'low')
        self.assertEqual(reason, ['no_rule_match'])
        self.assertEqual(review_state, 'pending')

        item['title'] = '居住空间'
        board, confidence, reason, review_state = infer_board(
            item,
            None,
            ['居住空间'],
        )
        self.assertEqual(board, '居住空间')
        self.assertEqual(confidence, 'medium')
        self.assertEqual(reason, ['居住空间'])
        self.assertEqual(review_state, 'classified')

    def test_auto_ocr_provider_requires_working_vision_or_chinese_tesseract(self):
        with (
            patch('xhs_ocr_common.swift_vision_ready', return_value=False),
            patch('xhs_ocr_common.tesseract_language_ready', return_value=True),
        ):
            self.assertEqual(detect_ocr_provider('auto'), 'tesseract')
        with (
            patch('xhs_ocr_common.swift_vision_ready', return_value=False),
            patch('xhs_ocr_common.tesseract_language_ready', return_value=False),
        ):
            self.assertEqual(detect_ocr_provider('auto'), 'none')

    def test_tesseract_does_not_fallback_to_english_when_chinese_data_is_missing(self):
        with (
            patch('xhs_ocr_common.shutil.which', return_value='/usr/bin/tesseract'),
            patch('xhs_ocr_common.tesseract_language_ready', side_effect=lambda language: language == 'eng'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'chi_sim'):
                run_tesseract_ocr(Path('/tmp/not-used.png'), languages='chi_sim+eng')

    def test_classification_preview_cannot_be_mistaken_for_execution_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            classification = tmp_path / 'classification.json'
            report = tmp_path / 'run_report.json'
            retry = tmp_path / 'retry_queue.json'
            classification.write_text(json.dumps([
                {
                    'id': '66d19b54000000001d03a93d',
                    'title': '滑雪',
                    'target_board': '滑雪',
                    'confidence': 'high',
                    'source_board_id': 'source-board-1',
                },
                {'id': '66d19b54000000001d03a93e', 'title': '待复核', 'target_board': '杂项灵感', 'confidence': 'low'},
            ], ensure_ascii=False), encoding='utf-8')
            self.run_script('run_reassign_batch.py', str(classification), str(report))
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['mode'], 'classification_preview')
            self.assertFalse(data['ready_for_execute'])
            self.assertEqual(data['missing_boards'], None)
            self.assertEqual(data['blockers'], [
                'board_validation_not_run',
                'membership_validation_not_run',
            ])
            self.assertEqual(data['processed'][0]['status'], 'preview_only')
            self.assertEqual(data['processed'][0]['membership_state'], 'not_checked')
            self.assertEqual(data['processed'][0]['archive_lifecycle_state'], 'not_checked')
            self.assertEqual(data['processed'][0]['source_board_id'], 'source-board-1')
            self.assertEqual(data['processed'][1]['status'], 'needs_review')
            self.run_script('build_retry_queue.py', str(report), str(retry))
            self.assertEqual(json.loads(retry.read_text(encoding='utf-8')), [])

    def test_build_existing_boards_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / 'existing_boards.json'
            out = tmp_path / 'existing_boards_inventory.json'
            src.write_text(json.dumps({
                'boards': [
                    {'name': '滑雪', 'notes': [{'id': 'note-1', 'title': '固定器'}]},
                    '穿搭发型与品味',
                ],
            }, ensure_ascii=False), encoding='utf-8')
            self.run_script('build_existing_boards_inventory.py', str(src), str(out))
            data = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(data['boards'], ['滑雪', '穿搭发型与品味'])
            self.assertEqual(data['located_note_ids'], ['note-1'])
            self.assertEqual(data['note_to_board'], {'note-1': '滑雪'})
            self.assertIn('generated_at', data)

    def test_classify_uses_existing_inventory_as_location_not_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            visible = tmp_path / 'visible_items.json'
            inventory = tmp_path / 'existing_boards_inventory.json'
            classification = tmp_path / 'classification.json'
            visible.write_text(json.dumps([
                {'id': 'note-1', 'title': '滑雪固定器角度', 'desc': '', 'tags': ['滑雪'], 'card_text': '滑雪 固定器'},
                {'id': 'note-2', 'title': '男士西装', 'desc': '', 'tags': ['穿搭'], 'card_text': '西装 穿搭'},
            ], ensure_ascii=False), encoding='utf-8')
            inventory.write_text(json.dumps({
                'boards': ['滑雪'],
                'located_note_ids': ['note-1'],
                'note_to_board': {'note-1': '滑雪'},
                'generated_at': '2026-05-09T00:00:00Z',
            }, ensure_ascii=False), encoding='utf-8')
            self.run_script(
                'classify_items.py',
                '--skip-ocr',
                str(visible),
                str(classification),
                '--existing-boards-inventory',
                str(inventory),
            )
            data = json.loads(classification.read_text(encoding='utf-8'))
            self.assertNotIn('excluded', data[0])
            self.assertEqual(data[0]['source_board'], '滑雪')
            self.assertEqual(data[0]['archive_lifecycle_state'], 'first_archive_pending')
            self.assertNotIn('excluded', data[1])
            self.assertEqual(data[1]['archive_lifecycle_state'], 'first_archive_pending')
            override = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / 'classify_items.py'),
                    '--skip-ocr',
                    str(visible),
                    str(classification),
                    '--existing-boards-inventory',
                    str(inventory),
                    '--include-existing-boards',
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(override.returncode, 0)
            self.assertIn('unrecognized arguments: --include-existing-boards', override.stderr)

    def test_dry_run_skips_excluded_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            classification = tmp_path / 'classification.json'
            report = tmp_path / 'run_report.json'
            classification.write_text(json.dumps([
                {
                    'id': 'note-1',
                    'title': '滑雪固定器角度',
                    'target_board': '滑雪',
                    'confidence': 'high',
                    'excluded': True,
                    'exclude_reason': 'skill_archived_board_member_protected',
                    'source_board': '滑雪',
                }
            ], ensure_ascii=False), encoding='utf-8')
            self.run_script('run_reassign_batch.py', str(classification), str(report))
            data = json.loads(report.read_text(encoding='utf-8'))
            row = data['processed'][0]
            self.assertEqual(row['status'], 'skipped')
            self.assertIn('skip:existing_board_excluded', row['events'])
            self.assertNotIn('note_move:CALLED', row['events'])
            self.assertEqual(data['errors'], [])

    def test_retry_queue_dedupes_failed_items_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            report = tmp_path / 'run_report.json'
            retry = tmp_path / 'retry_queue.json'
            failed = {
                'id': 'note-1',
                'title': '失败项',
                'target_board': '滑雪',
                'status': 'failed',
                'events': ['board:missing:滑雪'],
                'error': 'target board not found',
            }
            report.write_text(json.dumps({
                'processed': [
                    failed,
                    dict(failed),
                    {'id': 'note-2', 'title': '复核项', 'target_board': '', 'status': 'needs_review', 'error': 'missing target_board'},
                    {'id': 'note-3', 'title': '跳过项', 'target_board': '', 'status': 'skipped', 'error': 'skill_archived_board_member_protected'},
                    {'id': 'note-4', 'title': '核验失败', 'target_board': '穿搭发型与品味', 'status': 'verification_failed', 'events': ['verify:note_missing'], 'error': ''},
                ],
                'errors': [dict(failed)],
            }, ensure_ascii=False), encoding='utf-8')
            self.run_script('build_retry_queue.py', str(report), str(retry))
            data = json.loads(retry.read_text(encoding='utf-8'))
            self.assertEqual(len(data), 2)
            self.assertEqual(
                {(item['id'], item['target_board'], item['reason']) for item in data},
                {
                    ('note-1', '滑雪', 'target board not found'),
                    ('note-4', '穿搭发型与品味', 'verify:note_missing'),
                },
            )

    def test_build_created_boards_reports_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            taxonomy = tmp_path / 'taxonomy.json'
            existing = tmp_path / 'existing.json'
            out = tmp_path / 'created_boards.json'
            taxonomy.write_text(json.dumps({'boards': ['滑雪', '体态纠正与康复']}, ensure_ascii=False), encoding='utf-8')
            existing.write_text(json.dumps({'boards': ['滑雪']}, ensure_ascii=False), encoding='utf-8')
            self.run_script('build_created_boards.py', str(taxonomy), str(existing), str(out))
            data = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(data['confirmed'], ['滑雪'])
            self.assertEqual(data['missing'], ['体态纠正与康复'])
            self.assertNotIn('', data['confirmed'])
            self.assertNotIn('', data['missing'])
            self.assertTrue(data['action_required'])

    def test_build_created_boards_accepts_classification_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            classification = tmp_path / 'classification.json'
            snapshot = tmp_path / 'board_snapshot.json'
            out = tmp_path / 'created_boards.json'
            classification.write_text(json.dumps([
                {'id': 'a' * 24, 'target_board': '滑雪'},
                {'id': 'b' * 24, 'target_board': '体态纠正与康复'},
                {'id': 'c' * 24, 'title': '不应成为专辑名', 'target_board': ''},
            ], ensure_ascii=False), encoding='utf-8')
            snapshot.write_text(json.dumps({
                'boards': [
                    {'id': 'd' * 24, 'name': '滑雪', 'note_ids': []},
                ],
            }, ensure_ascii=False), encoding='utf-8')
            self.run_script(
                'build_created_boards.py',
                str(classification),
                str(snapshot),
                str(out),
            )
            data = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(data['confirmed'], ['滑雪'])
            self.assertEqual(data['missing'], ['体态纠正与康复'])
            self.assertNotIn('不应成为专辑名', data['missing'])

    def test_verified_dry_run_keeps_unregistered_album_members_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            classification = tmp_path / 'classification.json'
            snapshot = tmp_path / 'board_snapshot.json'
            created = tmp_path / 'created_boards.json'
            report = tmp_path / 'run_report.json'
            target_a = 'a' * 24
            target_b = 'b' * 24
            already_id = '1' * 24
            cross_id = '2' * 24
            unassigned_id = '3' * 24
            classification.write_text(json.dumps([
                {
                    'id': already_id, 'title': '已在目标',
                    'target_board': '专辑A', 'confidence': 'high',
                },
                {
                    'id': cross_id, 'title': '跨专辑',
                    'target_board': '专辑B', 'confidence': 'high',
                },
                {
                    'id': unassigned_id, 'title': '未归档',
                    'target_board': '专辑B', 'confidence': 'medium',
                },
            ], ensure_ascii=False), encoding='utf-8')
            snapshot.write_text(json.dumps({
                'generated_at': '2026-07-30T00:00:00Z',
                'mode': 'read_only',
                'source': {
                    'browser': 'safari',
                    'user_id': 'f' * 24,
                    'expected_url_substring': '/user/profile/',
                    'writes_performed': False,
                },
                'boards': [
                    {
                        'id': target_a, 'name': '专辑A',
                        'declared_total': 2, 'accessible_unique_count': 2,
                        'declared_vs_accessible_delta': 0, 'page_count': 1,
                        'note_ids': [already_id, cross_id],
                    },
                    {
                        'id': target_b, 'name': '专辑B',
                        'declared_total': 0, 'accessible_unique_count': 0,
                        'declared_vs_accessible_delta': 0, 'page_count': 1,
                        'note_ids': [],
                    },
                ],
                'membership': {},
                'validation': {
                    'board_count': 2,
                    'board_names_unique': True,
                    'pagination_cursor_invariants_passed': True,
                    'within_board_duplicates': [],
                    'full_membership_complete': True,
                },
            }, ensure_ascii=False), encoding='utf-8')
            created.write_text(json.dumps({
                'confirmed': ['专辑A', '专辑B'],
                'created': [],
                'missing': [],
                'failed': [],
                'action_required': '',
            }, ensure_ascii=False), encoding='utf-8')
            self.run_script(
                'run_reassign_batch.py',
                str(classification),
                str(report),
                '--board-snapshot',
                str(snapshot),
                '--created-boards',
                str(created),
            )
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['mode'], 'dry_run')
            self.assertTrue(data['ready_for_execute'])
            self.assertEqual(data['blockers'], [])
            self.assertEqual(data['missing_boards'], [])
            self.assertEqual(data['board_validation_status'], 'verified')
            self.assertEqual(data['membership_validation_status'], 'verified')
            rows = {row['id']: row for row in data['processed']}
            self.assertEqual(rows[already_id]['status'], 'planned')
            self.assertEqual(rows[already_id]['membership_state'], 'unarchived_board_member')
            self.assertEqual(rows[already_id]['archive_lifecycle_state'], 'first_archive_pending')
            self.assertEqual(rows[cross_id]['status'], 'planned')
            self.assertEqual(rows[cross_id]['membership_state'], 'unarchived_board_member')
            self.assertEqual(rows[cross_id]['archive_lifecycle_state'], 'first_archive_pending')
            self.assertEqual(rows[cross_id]['source_board_id'], target_a)
            self.assertEqual(rows[unassigned_id]['status'], 'planned')
            self.assertEqual(rows[unassigned_id]['membership_state'], 'not_in_any_board')
            self.assertEqual(rows[unassigned_id]['archive_lifecycle_state'], 'first_archive_pending')
            self.assertEqual(rows[unassigned_id]['source_board_id'], '')

    def test_verified_dry_run_blocks_missing_target_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            classification = tmp_path / 'classification.json'
            snapshot = tmp_path / 'board_snapshot.json'
            created = tmp_path / 'created_boards.json'
            report = tmp_path / 'run_report.json'
            classification.write_text(json.dumps([
                {
                    'id': '1' * 24, 'title': '目标缺失',
                    'target_board': '不存在的专辑', 'confidence': 'high',
                },
            ], ensure_ascii=False), encoding='utf-8')
            snapshot.write_text(json.dumps({
                'mode': 'read_only',
                'source': {'browser': 'safari', 'writes_performed': False},
                'boards': [{
                    'id': 'a' * 24, 'name': '专辑A',
                    'declared_total': 0, 'page_count': 1, 'note_ids': [],
                }],
                'validation': {
                    'pagination_cursor_invariants_passed': True,
                    'within_board_duplicates': [],
                    'full_membership_complete': True,
                },
            }, ensure_ascii=False), encoding='utf-8')
            created.write_text(json.dumps({
                'confirmed': [],
                'created': [],
                'missing': ['不存在的专辑'],
                'failed': [],
            }, ensure_ascii=False), encoding='utf-8')
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / 'run_reassign_batch.py'),
                    str(classification),
                    str(report),
                    '--board-snapshot',
                    str(snapshot),
                    '--created-boards',
                    str(created),
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['mode'], 'dry_run_blocked')
            self.assertFalse(data['ready_for_execute'])
            self.assertEqual(data['missing_boards'], ['不存在的专辑'])
            self.assertIn('missing_target_board:不存在的专辑', data['blockers'])

    def test_unregistered_album_member_is_not_protected_before_target_validation(self):
        note_id = '1' * 24
        source_board_id = 'a' * 24
        result = prepare_write_preflight(
            [{
                'id': note_id,
                'title': '用户已手动整理',
                'target_board': '模型误判出的不存在专辑',
                'confidence': 'high',
            }],
            {
                'mode': 'read_only',
                'source': {'browser': 'safari', 'writes_performed': False},
                'boards': [{
                    'id': source_board_id,
                    'name': '用户手动专辑',
                    'declared_total': 1,
                    'page_count': 1,
                    'note_ids': [note_id],
                }],
                'validation': {
                    'board_names_unique': True,
                    'pagination_cursor_invariants_passed': True,
                    'within_board_duplicates': [],
                    'full_membership_complete': True,
                },
            },
            {'confirmed': ['用户手动专辑'], 'missing': []},
            allow_low_confidence=False,
        )
        self.assertFalse(result['ready_for_execute'])
        self.assertEqual(result['missing_boards'], ['模型误判出的不存在专辑'])
        self.assertEqual(result['required_target_boards'], [])
        row = result['resolved_items'][0]
        self.assertFalse(row.get('excluded', False))
        self.assertEqual(row['membership_state'], 'unarchived_board_member')
        self.assertEqual(row['archive_lifecycle_state'], 'first_archive_pending')
        self.assertEqual(row['source_board_id'], source_board_id)

    def test_preflight_blocks_unregistered_multi_board_membership(self):
        note_id = '1' * 24
        board_a = 'a' * 24
        board_b = 'b' * 24
        result = prepare_write_preflight(
            [{
                'id': note_id,
                'title': '成员关系不明确',
                'target_board': '专辑A',
                'confidence': 'high',
            }],
            {
                'mode': 'read_only',
                'source': {'browser': 'safari', 'writes_performed': False},
                'boards': [
                    {
                        'id': board_a, 'name': '专辑A',
                        'declared_total': 1, 'page_count': 1,
                        'note_ids': [note_id],
                    },
                    {
                        'id': board_b, 'name': '专辑B',
                        'declared_total': 1, 'page_count': 1,
                        'note_ids': [note_id],
                    },
                ],
                'validation': {
                    'pagination_cursor_invariants_passed': True,
                    'within_board_duplicates': [],
                    'full_membership_complete': False,
                },
            },
            {
                'confirmed': ['专辑A', '专辑B'],
                'missing': [],
            },
            allow_low_confidence=False,
        )
        self.assertFalse(result['ready_for_execute'])
        self.assertIn(f'unarchived_note_in_multiple_boards:{note_id}', result['blockers'])
        self.assertFalse(result['resolved_items'][0].get('excluded', False))
        self.assertEqual(result['resolved_items'][0]['source_board_id'], '')

    def test_preflight_blocks_declared_board_count_mismatch(self):
        note_id = '1' * 24
        board_id = 'a' * 24
        result = prepare_write_preflight(
            [{
                'id': note_id,
                'title': '已归档条目',
                'target_board': '专辑A',
                'confidence': 'high',
            }],
            {
                'mode': 'read_only',
                'source': {'browser': 'playwright', 'writes_performed': False},
                'boards': [{
                    'id': board_id,
                    'name': '专辑A',
                    'declared_total': 2,
                    'page_count': 1,
                    'note_ids': [note_id],
                }],
                'validation': {
                    'board_names_unique': True,
                    'pagination_cursor_invariants_passed': True,
                    'within_board_duplicates': [],
                    'full_membership_complete': True,
                },
            },
            {
                'confirmed': ['专辑A'],
                'missing': [],
            },
            allow_low_confidence=False,
        )
        self.assertFalse(result['ready_for_execute'])
        self.assertEqual(result['blockers'], [
            'full_membership_incomplete',
            'board_count_mismatch:专辑A',
        ])
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['board_validation_status'], 'blocked')
        self.assertEqual(
            result['resolved_items'][0]['membership_state'],
            'unarchived_board_member',
        )
        self.assertEqual(
            result['resolved_items'][0]['archive_lifecycle_state'],
            'first_archive_pending',
        )

    def test_preflight_accepts_empty_inventory_only_for_bound_planned_boards(self):
        note_id = '1' * 24
        snapshot = {
            'mode': 'read_only',
            'source': {'browser': 'playwright', 'writes_performed': False},
            'boards': [],
            'validation': {
                'board_names_unique': True,
                'pagination_cursor_invariants_passed': True,
                'within_board_duplicates': [],
                'full_membership_complete': True,
            },
        }
        created = {
            'confirmed': [],
            'planned': [{'name': '阅读', 'privacy': 0}],
            'created': [],
            'missing': [],
            'failed': [],
        }
        classification = [{
            'id': note_id,
            'title': '读后感',
            'target_board': '阅读',
            'confidence': 'high',
        }]
        with self.assertRaisesRegex(Exception, 'explicit allow_planned_board_creation'):
            prepare_write_preflight(
                classification,
                snapshot,
                created,
                allow_low_confidence=False,
            )
        result = prepare_write_preflight(
            classification,
            snapshot,
            created,
            allow_low_confidence=False,
            allow_planned_board_creation=True,
        )
        self.assertTrue(result['ready_for_execute'])
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['planned_board_creations'], [
            {'name': '阅读', 'privacy': 0},
        ])
        self.assertEqual(
            result['resolved_items'][0]['membership_state'],
            'not_in_any_board',
        )
        self.assertEqual(
            result['resolved_items'][0]['archive_lifecycle_state'],
            'first_archive_pending',
        )
        self.assertEqual(result['resolved_items'][0]['target_board_state'], 'planned')

    def test_execute_without_preflight_evidence_is_blocked_before_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            classification = tmp_path / 'classification.json'
            report = tmp_path / 'run_report.json'
            classification.write_text(json.dumps([
                {
                    'id': '1' * 24, 'title': '不允许直接执行',
                    'target_board': '滑雪', 'confidence': 'high',
                },
            ], ensure_ascii=False), encoding='utf-8')
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / 'run_reassign_batch.py'),
                    str(classification),
                    str(report),
                    '--execute',
                    '--browser',
                    'safari',
                    '--max-moves-per-session',
                    '1',
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['mode'], 'execute_blocked')
            self.assertFalse(data['ready_for_execute'])
            self.assertEqual(data['blockers'], [
                'board_validation_not_run',
                'membership_validation_not_run',
            ])

    def test_workbuddy_rejects_direct_execute_script_before_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            classification = tmp_path / 'classification.json'
            report = tmp_path / 'run_report.json'
            classification.write_text(json.dumps([{
                'id': '1' * 24,
                'target_board': '阅读',
                'confidence': 'high',
            }], ensure_ascii=False), encoding='utf-8')
            env = dict(os.environ)
            env['XHS_HOST'] = 'workbuddy'
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / 'run_reassign_batch.py'),
                    str(classification),
                    str(report),
                    '--execute',
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('xhs_workbuddy_execute', proc.stderr)

    def test_execute_binding_must_match_snapshot_browser_user_page_and_safety_session(self):
        safety_state = str(Path('/tmp/xhs-safety-state.json'))
        source = {
            'browser': 'safari',
            'user_id': '1' * 24,
            'expected_url_substring': '/user/profile/',
            'verify_pages': 100,
            'safety_state': safety_state,
        }
        args = type('Args', (), {
            'browser': 'safari',
            'user_id': '1' * 24,
            'expected_url_substring': '/user/profile/',
            'arc_expected_url_substring': '',
            'verify_pages': 100,
            'safety_state': safety_state,
        })()
        self.assertEqual(write_binding_blockers(source, args), [])

        changed = type('Args', (), {
            'browser': 'chrome',
            'user_id': '2' * 24,
            'expected_url_substring': '/explore',
            'arc_expected_url_substring': '',
            'verify_pages': 99,
            'safety_state': '/tmp/other-safety-state.json',
        })()
        self.assertEqual(write_binding_blockers(source, changed), [
            'snapshot_browser_changed',
            'snapshot_user_changed',
            'snapshot_page_binding_changed',
            'snapshot_verify_pages_changed',
            'snapshot_safety_session_changed',
        ])

    def test_resume_filters_successful_items_and_preserves_report_rows(self):
        classification = [
            {'id': 'note-1', 'title': '已完成', 'target_board': '滑雪', 'confidence': 'high'},
            {'id': 'note-2', 'title': '待处理', 'target_board': '穿搭发型与品味', 'confidence': 'high'},
        ]
        previous_report = {
            'processed': [
                {'id': 'note-1', 'title': '已完成', 'target_board': '滑雪', 'status': 'success', 'events': ['verify:note_present'], 'error': ''},
                {'id': 'note-3', 'title': '失败旧项', 'target_board': '滑雪', 'status': 'failed', 'events': ['error'], 'error': 'old failure'},
            ],
        }
        pending, preserved = filter_classification_for_resume(classification, previous_report)
        self.assertEqual([item['id'] for item in pending], ['note-2'])
        self.assertEqual([item['id'] for item in preserved], ['note-1'])

    def test_resume_rejects_success_when_target_board_changed(self):
        classification = [
            {'id': 'note-1', 'title': '已重新分类', 'target_board': '思考与成长', 'confidence': 'high'},
        ]
        previous_report = {
            'processed': [
                {'id': 'note-1', 'title': '旧分类', 'target_board': '滑雪', 'status': 'success'},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, '旧目标专辑'):
            filter_classification_for_resume(classification, previous_report)

    def test_merge_report_chunk_appends_processed_errors_and_missing_boards(self):
        report = {'processed': [], 'errors': [], 'missing_boards': [], 'board_counts_before': {}, 'board_counts_after': {}, 'board_count_checks': {}}
        chunk = {
            'board_list_count': 181,
            'board_list_page_count': 2,
            'processed': [{'id': 'note-1', 'status': 'failed', 'target_board': '滑雪'}],
            'errors': [{'id': 'note-1', 'status': 'failed', 'target_board': '滑雪'}],
            'missing_boards': ['滑雪', '滑雪'],
            'board_counts_before': {'滑雪': 1},
            'board_counts_after': {'滑雪': 1},
            'board_count_checks': {
                '滑雪': {'declared_total': 2, 'accessible_total': 1, 'count_mismatch': True, 'page_count': 1},
            },
        }
        merge_report_chunk(report, chunk)
        merge_report_chunk(report, chunk)
        self.assertEqual(len(report['processed']), 2)
        self.assertEqual(len(report['errors']), 2)
        self.assertEqual(report['board_list_count'], 181)
        self.assertEqual(report['board_list_page_count'], 2)
        self.assertEqual(report['missing_boards'], ['滑雪'])
        self.assertEqual(report['board_counts_before'], {'滑雪': 1})
        self.assertEqual(report['board_counts_after'], {'滑雪': 1})
        self.assertEqual(report['board_count_checks'], {
            '滑雪': {'declared_total': 2, 'accessible_total': 1, 'count_mismatch': True, 'page_count': 1},
        })
        with self.assertRaisesRegex(RuntimeError, 'board_list_count changed'):
            merge_report_chunk(report, {**chunk, 'board_list_count': 180})

    def test_execute_requires_explicit_browser_and_arc_transport_is_supported(self):
        with self.assertRaises(RuntimeError):
            choose_backend('auto')
        self.assertEqual(choose_backend('arc'), 'arc')
        self.assertEqual(parse_js_json(json.dumps(json.dumps({'done': True}))), {'done': True})
        self.assertEqual(parse_browser_job_id('xhs_skill_123_456'), 'xhs_skill_123_456')
        self.assertEqual(parse_browser_job_id(json.dumps('xhs_skill_123_456')), 'xhs_skill_123_456')
        self.assertEqual(parse_browser_job_id(json.dumps(json.dumps('xhs_skill_123_456'))), 'xhs_skill_123_456')
        with self.assertRaisesRegex(RuntimeError, 'invalid Xiaohongshu job id'):
            parse_browser_job_id('wrong-id')
        args = type('Args', (), {
            'arc_window_id': 'window-test',
            'arc_tab_id': 'tab-test',
            'arc_tab_marker': 'xhs-skill-worker-test',
            'arc_expected_url_substring': '/user/profile/test',
        })()
        runner = BrowserRunner('arc', args)
        with patch('run_reassign_batch.arc_js_macos', return_value='ok') as mocked:
            self.assertEqual(runner.run_javascript('1 + 1'), 'ok')
            mocked.assert_called_once_with(
                '1 + 1',
                tab_marker='xhs-skill-worker-test',
                window_id='window-test',
                tab_id='tab-test',
                expected_url_substring='/user/profile/test',
            )

    def test_arc_transport_counts_strict_id_and_url_matches_then_wraps_runtime_marker(self):
        captured = {}

        def fake_jxa_osascript(script):
            captured['script'] = script
            return 'ok'

        with patch('extract_visible_items.require_macos_app_running'), patch('extract_visible_items.jxa_osascript', fake_jxa_osascript):
            self.assertEqual(arc_js_macos(
                'document.title',
                'xhs-skill-worker-test',
                'window-test',
                'tab-test',
                '/user/profile/test',
            ), 'ok')

        script = captured['script']
        self.assertIn('const expectedWindowId = "window-test"', script)
        self.assertIn('const expectedTabId = "tab-test"', script)
        self.assertIn('candidate.id() === expectedWindowId', script)
        self.assertIn('candidate.id() === expectedTabId', script)
        self.assertIn('targetTab.url()', script)
        self.assertIn('targetURL.includes("xiaohongshu.com")', script)
        self.assertIn('app.execute(targetTab, {javascript: jsSource})', script)
        self.assertNotIn('repeat with w in windows', script)
        self.assertIn('window.name !== \\"xhs-skill-worker-test\\"', script)
        self.assertIn('return eval(\\"document.title\\");', script)
        self.assertLess(script.index('window.name !=='), script.index('document.title'))

    def test_arc_execute_requires_unique_tab_marker_before_opening_browser(self):
        args = type('Args', (), {
            'browser': 'arc',
            'arc_window_id': '',
            'arc_tab_id': '',
            'arc_tab_marker': '',
            'arc_expected_url_substring': '',
            'inter_item_delay_sec': 0,
        })()
        with tempfile.TemporaryDirectory() as tmp, patch('run_reassign_batch.BrowserRunner') as runner:
            with self.assertRaisesRegex(RuntimeError, '--arc-tab-marker'):
                apply_batch([], {}, args, Path(tmp) / 'report.json')
        runner.assert_not_called()

    def test_execute_batch_waits_fixed_delay_between_items(self):
        user_id = 'f' * 24
        args = type('Args', (), {
            'browser': 'safari',
            'arc_tab_marker': '',
            'expected_url_substring': f'https://www.xiaohongshu.com/user/profile/{user_id}?tab=fav',
            'arc_expected_url_substring': '',
            'inter_item_delay_sec': 2.5,
            'max_moves_per_session': 2,
            'allow_low_confidence': False,
            'allow_recollect': True,
            'verify_pages': 1,
            'user_id': user_id,
            'timeout_sec': 10,
        })()
        runner = type('Runner', (), {
            'run_javascript': lambda self, js: 'xhs_skill_123_456',
            'close': lambda self: None,
        })()
        report = {'processed': [], 'errors': [], 'missing_boards': [], 'board_counts_before': {}, 'board_counts_after': {}}
        classification = [
            {'id': '1' * 24, 'title': '一', 'target_board': '滑雪', 'confidence': 'high', 'membership_state': 'not_in_any_board', 'archive_lifecycle_state': 'first_archive_pending', 'source_board_id': '', 'source_primary': '收藏'},
            {'id': '2' * 24, 'title': '二', 'target_board': '滑雪', 'confidence': 'high', 'membership_state': 'not_in_any_board', 'archive_lifecycle_state': 'first_archive_pending', 'source_board_id': '', 'source_primary': '收藏'},
        ]
        def success(item, *_args):
            return {'processed': [{'id': item['id'], 'target_board': '滑雪', 'status': 'success', 'verified': True}], 'errors': []}
        with tempfile.TemporaryDirectory() as tmp, \
                patch('run_reassign_batch.BrowserRunner', return_value=runner), \
                patch('run_reassign_batch.validate_write_live_binding'), \
                patch('run_reassign_batch.validate_live_assignment_membership'), \
                patch('run_reassign_batch.read_target_board_state', return_value=({'boards': []}, {'note_ids': []})), \
                patch('run_reassign_batch.open_exact_source_note', return_value={'collected': True}), \
                patch('run_reassign_batch.poll_browser_job', return_value={}), \
                patch('run_reassign_batch.successful_visible_assignment_chunk', side_effect=success), \
                patch('run_reassign_batch.time.sleep') as sleep:
            apply_batch(classification, report, args, Path(tmp) / 'report.json')
        sleep.assert_called_once_with(2.5)

    def test_execute_batch_creates_confirmed_boards_after_commit_before_moves(self):
        user_id = 'f' * 24
        args = type('Args', (), {
            'browser': 'safari',
            'arc_tab_marker': '',
            'expected_url_substring': f'https://www.xiaohongshu.com/user/profile/{user_id}?tab=fav',
            'arc_expected_url_substring': '',
            'inter_item_delay_sec': 0,
            'max_moves_per_session': 1,
            'allow_low_confidence': False,
            'allow_recollect': True,
            'verify_pages': 1,
            'user_id': user_id,
            'timeout_sec': 10,
        })()
        events = []

        class Runner:
            def run_javascript(self, _js):
                events.append('move')
                return 'xhs_skill_123_456'

            def close(self):
                events.append('close')

        report = {
            'processed': [], 'errors': [], 'missing_boards': [],
            'board_counts_before': {}, 'board_counts_after': {},
        }
        classification = [{
            'id': '1' * 24, 'title': '一', 'target_board': '阅读',
            'confidence': 'high',
            'membership_state': 'not_in_any_board',
            'archive_lifecycle_state': 'first_archive_pending',
            'source_board_id': '',
            'source_primary': '收藏',
        }]
        result = {
            'processed': [], 'errors': [], 'missing_boards': [],
            'board_counts_before': {}, 'board_counts_after': {},
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch('run_reassign_batch.BrowserRunner', return_value=Runner()), \
                patch('run_reassign_batch.build_browser_job', return_value='safe-test-job'), \
                patch('run_reassign_batch.validate_write_live_binding'), \
                patch('run_reassign_batch.validate_live_assignment_membership'), \
                patch('run_reassign_batch.read_target_board_state', return_value=({'boards': []}, {'note_ids': []})), \
                patch('run_reassign_batch.open_exact_source_note', return_value={'collected': True}), \
                patch('run_reassign_batch.poll_browser_job', return_value=result), \
                patch('run_reassign_batch.successful_visible_assignment_chunk', return_value=result):
            apply_batch(
                classification,
                report,
                args,
                Path(tmp) / 'report.json',
                commit_callback=lambda: events.append('commit'),
                post_commit_callback=lambda _runner: events.append('create'),
            )
        self.assertEqual(events, ['commit', 'create', 'move', 'close'])

    def test_execute_batch_persists_first_error_then_stops_before_next_item(self):
        user_id = 'f' * 24
        args = type('Args', (), {
            'browser': 'safari',
            'arc_tab_marker': '',
            'expected_url_substring': f'https://www.xiaohongshu.com/user/profile/{user_id}?tab=fav',
            'arc_expected_url_substring': '',
            'inter_item_delay_sec': 0,
            'max_moves_per_session': 2,
            'allow_low_confidence': False,
            'allow_recollect': True,
            'verify_pages': 10,
            'user_id': user_id,
            'timeout_sec': 10,
        })()
        calls = {'eval': 0, 'closed': False}

        class Runner:
            def run_javascript(self, js):
                calls['eval'] += 1
                return 'xhs_skill_123_456'

            def close(self):
                calls['closed'] = True

        failed_row = {
            'id': '1' * 24, 'title': '一', 'target_board': '滑雪',
            'status': 'verification_failed', 'events': ['note_move:CALLED', 'verify:note_missing'],
            'error': 'note not found in target board after move',
        }
        result = {
            'processed': [failed_row], 'errors': [failed_row], 'missing_boards': [],
            'board_counts_before': {'滑雪': 1}, 'board_counts_after': {'滑雪': 1},
        }
        report = {
            'processed': [], 'errors': [], 'missing_boards': [],
            'board_counts_before': {}, 'board_counts_after': {},
        }
        classification = [
            {'id': '1' * 24, 'title': '一', 'target_board': '滑雪', 'confidence': 'high', 'membership_state': 'not_in_any_board', 'archive_lifecycle_state': 'first_archive_pending', 'source_board_id': '', 'source_primary': '收藏'},
            {'id': '2' * 24, 'title': '二', 'target_board': '滑雪', 'confidence': 'high', 'membership_state': 'not_in_any_board', 'archive_lifecycle_state': 'first_archive_pending', 'source_board_id': '', 'source_primary': '收藏'},
        ]
        with tempfile.TemporaryDirectory() as tmp, \
                patch('run_reassign_batch.BrowserRunner', return_value=Runner()), \
                patch('run_reassign_batch.validate_write_live_binding'), \
                patch('run_reassign_batch.validate_live_assignment_membership'), \
                patch('run_reassign_batch.read_target_board_state', return_value=({'boards': []}, {'note_ids': []})), \
                patch('run_reassign_batch.open_exact_source_note', return_value={'collected': True}), \
                patch('run_reassign_batch.poll_browser_job', return_value={}) as poll, \
                patch('run_reassign_batch.successful_visible_assignment_chunk', return_value=result):
            report_path = Path(tmp) / 'report.json'
            with self.assertRaisesRegex(RuntimeError, '已先写入报告'):
                apply_batch(classification, report, args, report_path)
            persisted = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertEqual(calls['eval'], 1)
        self.assertEqual(poll.call_count, 1)
        self.assertTrue(calls['closed'])
        self.assertEqual(persisted['processed'], [failed_row])
        self.assertEqual(persisted['errors'], [failed_row])

    def test_browser_job_uses_visible_exact_note_assignment(self):
        args = type('Args', (), {
            'allow_low_confidence': False,
            'allow_recollect': True,
            'verify_pages': 1,
            'user_id': 'f' * 24,
            'arc_tab_marker': '',
        })()
        job = build_browser_job([{
            'id': '1' * 24, 'title': '一', 'target_board': '滑雪', 'confidence': 'high',
            'membership_state': 'not_in_any_board',
            'archive_lifecycle_state': 'first_archive_pending',
            'source_primary': '收藏',
        }], args)
        self.assertIn('exact_note_id_only', job)
        self.assertIn('1' * 24, job)
        for forbidden in ('req.m', 'webpackChunkxhs_pc_web', '/api/sns/web/v1/', 'search'):
            self.assertNotIn(forbidden, job)

    def test_first_archive_requires_pending_state_and_confirmed_state_is_locked(self):
        item = {
            'id': 'note-1',
            'target_board': '阅读',
            'confidence': 'high',
            'membership_state': 'not_in_any_board',
            'archive_lifecycle_state': 'first_archive_pending',
            'source_board_id': '',
        }
        self.assertTrue(is_ready_move(item, allow_low_confidence=False))
        self.assertFalse(is_ready_move(
            {**item, 'archive_lifecycle_state': 'first_archive_confirmed'},
            allow_low_confidence=False,
        ))
        self.assertFalse(is_ready_move(
            {key: value for key, value in item.items() if key != 'archive_lifecycle_state'},
            allow_low_confidence=False,
        ))
        self.assertFalse(is_ready_move(
            {**item, 'confidence': 'low'},
            allow_low_confidence=False,
        ))
        self.assertTrue(is_ready_move(
            {**item, 'target_board': '无法确定', 'confidence': 'low'},
            allow_low_confidence=False,
        ))

    def test_browser_job_rejects_empty_batch(self):
        args = type('Args', (), {
            'allow_low_confidence': False,
            'allow_recollect': True,
            'verify_pages': 1,
            'user_id': '',
        })()
        with self.assertRaisesRegex(RuntimeError, '每次必须且只能处理一条'):
            build_browser_job([], args)

    def test_poll_browser_job_reads_and_cleans_dom_state_bridge(self):
        captured = {}

        class Runner:
            def run_javascript(self, js):
                captured['js'] = js
                return json.dumps({'done': True, 'ok': True, 'result': {'processed': []}})

        result = poll_browser_job(Runner(), 'run-123', 1)
        self.assertEqual(result, {'processed': []})
        self.assertIn("document.getElementById(\"xhs-skill-run-state-run-123\")", captured['js'])
        self.assertIn('node.dataset.xhsSkillState', captured['js'])
        self.assertIn('node.remove()', captured['js'])
        self.assertNotIn('window.__xhsSkillRuns', captured['js'])

    def test_extract_visible_items_merges_source_lists(self):
        from extract_visible_items import merge_items, parse_js_json_result, resolve_backend
        existing = [
            {'id': 'note-1', 'title': '同一笔记', 'source_lists': ['收藏'], 'source_primary': '收藏'},
            {'id': 'note-2', 'title': '只在收藏', 'source_lists': ['收藏'], 'source_primary': '收藏'},
        ]
        incoming = [
            {'id': 'note-1', 'title': '同一笔记更新', 'desc': '补充描述'},
            {'id': 'note-3', 'title': '只在点赞'},
        ]
        merged = merge_items(existing, incoming, '点赞')
        by_id = {item['id']: item for item in merged}
        self.assertEqual(by_id['note-1']['source_lists'], ['收藏', '点赞'])
        self.assertEqual(by_id['note-1']['source_primary'], '收藏')
        self.assertEqual(by_id['note-1']['desc'], '补充描述')
        self.assertEqual(by_id['note-3']['source_lists'], ['点赞'])
        self.assertEqual([item['id'] for item in merged], ['note-1', 'note-2', 'note-3'])
        payload = {'location': 'https://www.xiaohongshu.com', 'items': []}
        direct = json.dumps(payload, ensure_ascii=False)
        arc_wrapped = json.dumps(direct, ensure_ascii=False)
        self.assertEqual(parse_js_json_result(direct), payload)
        self.assertEqual(parse_js_json_result(arc_wrapped), payload)
        with self.assertRaises(RuntimeError):
            resolve_backend('auto')
        self.assertEqual(resolve_backend('macos-arc'), 'macos-arc')

    def test_extract_visible_items_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            simulator = tmp_path / 'simulate_extract.py'
            simulator.write_text(
                """
import json
from pathlib import Path
import sys
ROOT = Path(__import__('os').environ['XHS_SKILL_ROOT'])
sys.path.insert(0, str(ROOT / 'scripts'))
from extract_visible_items import extract_with_js
states = [
  {'scrollY':0,'innerHeight':100,'scrollHeight':1000,'location':'https://www.xiaohongshu.com/explore','title':'xhs','loginRequired':False,'items':[{'id':'note-1','title':'一','href':'https://www.xiaohongshu.com/explore/note-1'}]},
  {'scrollY':1000,'innerHeight':100,'scrollHeight':1000,'location':'https://www.xiaohongshu.com/explore','title':'xhs','loginRequired':False,'items':[{'id':'note-1','title':'一','href':'https://www.xiaohongshu.com/explore/note-1'},{'id':'note-2','title':'二','href':'https://www.xiaohongshu.com/explore/note-2'}]},
  {'scrollY':1000,'innerHeight':100,'scrollHeight':1000,'location':'https://www.xiaohongshu.com/explore','title':'xhs','loginRequired':False,'items':[{'id':'note-1','title':'一','href':'https://www.xiaohongshu.com/explore/note-1'},{'id':'note-2','title':'二','href':'https://www.xiaohongshu.com/explore/note-2'}]},
  {'scrollY':1000,'innerHeight':100,'scrollHeight':1000,'location':'https://www.xiaohongshu.com/explore','title':'xhs','loginRequired':False,'items':[{'id':'note-1','title':'一','href':'https://www.xiaohongshu.com/explore/note-1'},{'id':'note-2','title':'二','href':'https://www.xiaohongshu.com/explore/note-2'}]},
  {'scrollY':1000,'innerHeight':100,'scrollHeight':1000,'location':'https://www.xiaohongshu.com/explore','title':'xhs','loginRequired':False,'items':[{'id':'note-1','title':'一','href':'https://www.xiaohongshu.com/explore/note-1'},{'id':'note-2','title':'二','href':'https://www.xiaohongshu.com/explore/note-2'}]},
]
final_state = states[-1]
def js_eval(js):
    if js.startswith('window.scrollBy') or js.startswith('window.scrollTo'):
        return 'ok'
    return json.dumps(states.pop(0) if states else final_state, ensure_ascii=False)
out = Path(sys.argv[1])
manifest = Path(sys.argv[2])
print(json.dumps(extract_with_js(js_eval, out, 5, 0, manifest), ensure_ascii=False))
""",
                encoding='utf-8',
            )
            out = tmp_path / 'visible.json'
            manifest = tmp_path / 'crawl_manifest.json'
            env = dict(__import__('os').environ)
            env['XHS_SKILL_ROOT'] = str(ROOT)
            subprocess.run([sys.executable, str(simulator), str(out), str(manifest)], cwd=str(ROOT), env=env, check=True)
            data = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(data['item_count'], 2)
            self.assertEqual(data['stopped_reason'], 'bottom_stable')
            self.assertGreaterEqual(len(data['scroll_snapshots']), 4)

    def test_extract_does_not_call_incomplete_declared_count_bottom_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = {
                'scrollY': 1000, 'innerHeight': 100, 'scrollHeight': 1000,
                'location': 'https://www.xiaohongshu.com/explore', 'title': 'xhs',
                'loginRequired': False, 'declaredItemCount': 5,
                'items': [
                    {'id': 'note-1', 'title': '一'},
                    {'id': 'note-2', 'title': '二'},
                ],
            }

            def js_eval(js):
                if js.startswith('window.scroll'):
                    return 'ok'
                return json.dumps(payload, ensure_ascii=False)

            extract_with_js(js_eval, tmp_path / 'visible.json', 5, 0, tmp_path / 'manifest.json')
            manifest = json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))

        self.assertEqual(manifest['item_count'], 2)
        self.assertEqual(manifest['stopped_reason'], 'max_scrolls_reached')
        self.assertFalse(manifest['crawl_complete'])


if __name__ == '__main__':
    unittest.main()
