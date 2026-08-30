#!/usr/bin/env python3
import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from workbuddy_bridge import (  # noqa: E402
    OWN_PROFILE_LINK_JS,
    approval_basis,
    approval_digest,
    build_parser,
    canonical_json,
    main as bridge_main,
    capture_action,
    capture_workbuddy_groups,
    collect_workbuddy_detail_hrefs,
    download_workbuddy_authenticated_images,
    enrich_workbuddy_image_items,
    execute_action,
    execute_planned_board_creations,
    login_action,
    metadata_quality,
    prepare_action,
    setup_action,
    run_command,
    run_workbuddy_ocr,
    status_action,
    validate_run_id,
    validate_trusted_evidence,
    validate_workbuddy_capture_evidence,
    validate_xhs_url,
    verify_mcp_launch_attestation,
    workbuddy_classification_inputs,
    write_workbuddy_classification,
    _MCP_EXECUTE_CAPABILITY,
)
from xhs_safety import SafetyHaltedError, load_safety_state  # noqa: E402
from xhs_ocr_common import ocr_run_fingerprint  # noqa: E402


class WorkBuddyBridgeTests(unittest.TestCase):
    def workbuddy_env(self, data_dir: Path):
        return {
            'XHS_HOST': 'workbuddy',
            'CODEBUDDY_PLUGIN_DATA': str(data_dir),
            'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
        }

    def fake_authenticated_download(self, _runner, note_id, image_urls, directory):
        files = []
        hashes = []
        for index, _url in enumerate(image_urls):
            data = b'\x89PNG\r\n\x1a\n' + bytes([index + 1]) * 32
            digest = hashlib.sha256(data).hexdigest()
            relative = f'authenticated_images/{note_id}-{index:03d}.png'
            path = Path(directory) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files.append(relative)
            hashes.append(digest)
        return files, hashes

    def trusted_evidence(self, directory: Path, stage: str):
        manifest = json.loads(
            (directory / 'crawl_manifest.json').read_text(encoding='utf-8')
        )
        depth = manifest['organizing_depth']
        names = [
            'visible_items.json',
            'crawl_manifest.json',
            'xhs_safety_state.json',
        ]
        if depth == 'light':
            names.extend(['image_items.json', 'ocr_results.json'])
        if stage in {'inventory', 'plan'}:
            names.append('board_snapshot.json')
        if stage == 'plan':
            names.extend([
                'classification.json',
                'created_boards.json',
                'run_report.json',
                'approval.json',
            ])
        return {
            'schema': 'xhs_workbuddy_trusted_evidence_v1',
            'receipt_id': 'test-receipt',
            'run_id': directory.name,
            'stage': stage,
            'bindings': {
                'user_id': manifest['capture_user_id'],
                'page_binding': manifest['capture_page_binding'],
                'source': manifest['capture_source'],
                'organizing_depth': depth,
            },
            'artifacts': {
                name: {
                    'sha256': hashlib.sha256(
                        (directory / name).read_bytes()
                    ).hexdigest(),
                    'size': (directory / name).stat().st_size,
                }
                for name in names
            },
        }

    def write_capture_contract(
        self,
        directory: Path,
        rows,
        *,
        image_ocr_enabled: bool = False,
        ready_for_classification: bool = True,
        blockers=None,
        image_rows=None,
        ocr_rows=None,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        visible = directory / 'visible_items.json'
        visible.write_text(
            json.dumps(rows, ensure_ascii=False),
            encoding='utf-8',
        )
        image_items = directory / 'image_items.json'
        ocr_results = directory / 'ocr_results.json'
        if image_rows is not None:
            image_items.write_text(
                json.dumps(image_rows, ensure_ascii=False),
                encoding='utf-8',
            )
        if ocr_rows is not None:
            ocr_results.write_text(
                json.dumps(ocr_rows, ensure_ascii=False),
                encoding='utf-8',
            )
        file_hash = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        capture_user_id = '66d19b54000000001d03a93d'
        capture_page = (
            'https://www.xiaohongshu.com/user/profile/'
            + capture_user_id
            + '?tab=fav'
        )
        provider = 'tesseract' if image_ocr_enabled else None
        tesseract_lang = 'chi_sim' if image_ocr_enabled else None
        expected_fingerprint = (
            ocr_run_fingerprint(
                provider,
                tesseract_lang,
                ROOT / 'scripts' / 'ocr_image.swift.txt',
            )
            if image_ocr_enabled
            else None
        )
        manifest = {
            'capture_mode': 'workbuddy_segmented',
            'source': '收藏',
            'item_count': len(rows),
            'crawl_complete': True,
            'capture_source': 'collection',
            'capture_user_id': capture_user_id,
            'capture_page_binding': capture_page,
            'capture_tab': 'fav',
            'visible_items': str(visible),
            'visible_items_sha256': file_hash(visible),
            'organizing_depth': 'light' if image_ocr_enabled else 'quick',
            'report_requested': False,
            'image_ocr_enabled': image_ocr_enabled,
            'ready_for_classification': ready_for_classification,
            'image_ocr_blockers': list(blockers or []),
            'image_items': str(image_items) if image_rows is not None else None,
            'image_items_sha256': (
                file_hash(image_items) if image_rows is not None else None
            ),
            'ocr_results': str(ocr_results) if ocr_rows is not None else None,
            'ocr_results_sha256': (
                file_hash(ocr_results) if ocr_rows is not None else None
            ),
            'ocr_provider': provider,
            'ocr_tesseract_lang': tesseract_lang,
            'ocr_expected_fingerprint': expected_fingerprint,
        }
        (directory / 'crawl_manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding='utf-8',
        )
        (directory / 'xhs_safety_state.json').write_text(
            json.dumps({'status': 'active'}),
            encoding='utf-8',
        )
        return manifest

    def write_valid_ocr_capture_contract(self, directory: Path):
        note_id = '66d19b54000000001d03a93d'
        image_dir = directory / 'authenticated_images'
        image_dir.mkdir(parents=True, exist_ok=True)
        image_files = []
        image_hashes = []
        for index in range(2):
            data = b'\x89PNG\r\n\x1a\n' + bytes([index + 1]) * 32
            digest = hashlib.sha256(data).hexdigest()
            relative = f'authenticated_images/{note_id}-{index:03d}.png'
            (directory / relative).write_bytes(data)
            image_files.append(relative)
            image_hashes.append(digest)
        visible_rows = [{
            'id': note_id,
            'title': '上海两天一夜',
            'desc': '周末出行',
            'content_type': 'image',
        }]
        image_rows = [{
            **visible_rows[0],
            'content_type_source': (
                'workbuddy_authenticated_frontend.noteData.type'
            ),
            'detail_state_source': 'initial_state_note_detail_map',
            'image_files': image_files,
            'image_file_sha256': image_hashes,
            'image_count': 2,
            'image_urls_complete': True,
            'image_list_source': (
                'workbuddy_authenticated_frontend.noteData.imageList.local_copy'
            ),
            'image_enrichment_status': 'ok',
            'image_enrichment_error': '',
        }]
        image_set = hashlib.sha256(json.dumps(
            [f'sha256:{value}' for value in image_hashes],
            ensure_ascii=False,
            separators=(',', ':'),
        ).encode('utf-8')).hexdigest()
        ocr_rows = [{
            'id': note_id,
            'status': 'ok',
            'ocr_text': '第1张：上海路线\n第2张：餐厅清单',
            'ocr_confidence': 0.95,
            'image_count_declared': 2,
            'image_count_available': 2,
            'image_count_processed': 2,
            'image_set_complete': True,
            'image_set_sha256': image_set,
            'ocr_run_fingerprint': ocr_run_fingerprint(
                'tesseract',
                'chi_sim',
                ROOT / 'scripts' / 'ocr_image.swift.txt',
            ),
            'images': [
                {
                    'image_index': index,
                    'status': 'ok',
                    'ocr_text': text,
                    'ocr_confidence': 0.95,
                    'image_sha256': image_hashes[index],
                    'source_image_sha256': image_hashes[index],
                    'error': '',
                }
                for index, text in enumerate(
                    ('上海路线', '餐厅清单'),
                )
            ],
        }]
        self.write_capture_contract(
            directory,
            visible_rows,
            image_ocr_enabled=True,
            image_rows=image_rows,
            ocr_rows=ocr_rows,
        )
        return note_id

    def write_ready_plan(self, data_dir: Path, run_id: str = 'run-1'):
        directory = data_dir / 'runs' / run_id
        directory.mkdir(parents=True)
        self.write_capture_contract(directory, [{
            'id': '66d19b54000000001d03a93d',
            'title': '测试收藏',
            'content_type': 'image',
        }])
        (directory / 'classification.json').write_text(
            json.dumps([{
                'id': '66d19b54000000001d03a93d',
                'target_board': '旅行',
                'confidence': 'high',
            }], ensure_ascii=False),
            encoding='utf-8',
        )
        (directory / 'board_snapshot.json').write_text(
            json.dumps({
                'mode': 'read_only',
                'source': {
                    'browser': 'playwright',
                    'writes_performed': False,
                    'user_id': '66d19b54000000001d03a93d',
                    'verify_pages': 100,
                    'expected_url_substring': (
                        'https://www.xiaohongshu.com/user/profile/'
                        '66d19b54000000001d03a93d?tab=fav'
                    ),
                    'live_page_binding': (
                        'https://www.xiaohongshu.com/user/profile/'
                        '66d19b54000000001d03a93d?tab=fav'
                    ),
                    'live_account_user_id': '66d19b54000000001d03a93d',
                    'safety_state': str(directory / 'xhs_safety_state.json'),
                },
                'boards': [{
                    'id': 'aaaaaaaaaaaaaaaaaaaaaaaa',
                    'name': '旅行',
                    'declared_total': 0,
                    'accessible_unique_count': 0,
                    'page_count': 1,
                    'note_ids': [],
                }],
                'validation': {
                    'pagination_cursor_invariants_passed': True,
                    'board_names_unique': True,
                    'within_board_duplicates': [],
                    'full_membership_complete': True,
                },
            }, ensure_ascii=False),
            encoding='utf-8',
        )
        (directory / 'created_boards.json').write_text(
            json.dumps({'confirmed': ['旅行'], 'missing': []}, ensure_ascii=False),
            encoding='utf-8',
        )
        report = {
            'mode': 'dry_run',
            'ready_for_execute': True,
            'blockers': [],
            'processed': [{
                'id': '66d19b54000000001d03a93d',
                'target_board': '旅行',
                'source_board_id': '',
                'membership_state': 'not_in_any_board',
                'archive_lifecycle_state': 'first_archive_pending',
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

    def test_windows_setup_reuses_system_edge_without_downloading_chromium(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                **self.workbuddy_env(data_dir),
                'XHS_WORKBUDDY_PLATFORM': 'win32',
            }
            commands = []

            def fake_run_command(args, **_kwargs):
                commands.append(args)
                return subprocess.CompletedProcess(args, 0, '', '')

            with (
                patch.dict(os.environ, env, clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command),
                patch(
                    'workbuddy_bridge.find_windows_edge_executable',
                    return_value=Path('C:/Program Files/Microsoft/Edge/Application/msedge.exe'),
                ),
            ):
                result = setup_action()

        self.assertEqual(result['browser_channel'], 'msedge')
        self.assertFalse(any(
            command[-2:] == ['install', 'chromium'] for command in commands
        ))

    def test_capture_defaults_to_v2_controlled_group_contract(self):
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        args = build_parser().parse_args([
            'capture',
            '--source', 'collection',
            '--page-url', target_url,
            '--organizing-depth', 'quick',
        ])
        self.assertEqual(args.batch_size, 200)
        self.assertEqual(args.pause_minutes, 3)
        self.assertNotIn('segment_limit', vars(args))
        self.assertNotIn('quick_classify', vars(args))
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('固定每组 200 条', skill)
        self.assertIn('非末组间隔 3 分钟', skill)

    def test_workbuddy_auto_pagination_saves_200_item_groups_and_waits_180_seconds(self):
        payload = {
            'scrollY': 9000,
            'innerHeight': 1000,
            'scrollHeight': 10000,
            'location': (
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav&subTab=note'
                '&xsec_token=page-secret-never-persist'
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

        def fake_eval(script):
            browser_calls.append(script)
            if script == OWN_PROFILE_LINK_JS:
                return (
                    'https://www.xiaohongshu.com/user/profile/'
                    '66d19b54000000001d03a93d'
                )
            return 'ok'

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
                    fake_eval,
                    directory,
                    'collection',
                    200,
                    3,
                    safety,
                    expected_page_url=payload['location'],
                )
            first = json.loads(
                (directory / 'visible_items.segment-001.json').read_text(encoding='utf-8')
            )
            second = json.loads(
                (directory / 'visible_items.segment-002.json').read_text(encoding='utf-8')
            )
            persisted = '\n'.join(
                path.read_text(encoding='utf-8')
                for path in directory.glob('*.json')
            )

        self.assertEqual([len(first), len(second)], [200, 5])
        self.assertEqual([call.args for call in sleep.call_args_list], [(180,)])
        self.assertEqual(result['count'], 205)
        self.assertEqual(result['segment_count'], 2)
        self.assertTrue(result['crawl_complete'])
        self.assertNotIn('page-secret-never-persist', persisted)
        self.assertNotIn('xsec_token', persisted)
        self.assertEqual(
            len([script for script in browser_calls if 'scrollTo(0, 0)' in script]),
            1,
        )

    def test_workbuddy_count_mismatch_preserves_rows_but_blocks_classification(self):
        payload = {
            'scrollY': 9000,
            'innerHeight': 1000,
            'scrollHeight': 10000,
            'location': (
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav&subTab=note'
                '&xsec_token=page-secret-never-persist'
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

        def fake_eval(script):
            browser_calls.append(script)
            if script == OWN_PROFILE_LINK_JS:
                return (
                    'https://www.xiaohongshu.com/user/profile/'
                    '66d19b54000000001d03a93d'
                )
            return 'ok'

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
                    fake_eval,
                    directory,
                    'collection',
                    200,
                    3,
                    directory / 'xhs_safety_state.json',
                    expected_page_url=payload['location'],
                )

        self.assertEqual([call.args for call in sleep.call_args_list], [(180,)])
        self.assertEqual(
            len([script for script in browser_calls if 'Date.now() + 2500' in script]),
            1,
        )
        self.assertEqual(browser_calls.count(OWN_PROFILE_LINK_JS), 2)
        self.assertFalse(result['crawl_complete'])
        self.assertFalse(result['ready_for_classification'])
        self.assertEqual(result['stopped_reason'], 'capture_coverage_incomplete')
        self.assertEqual(result['blockers'], [
            'declared_count_mismatch',
            'missing_page_positions',
        ])
        self.assertEqual(result['warnings'], [
            {
                'code': 'declared_count_mismatch',
                'declared_count': 209,
                'accessible_count': 205,
            },
            {
                'code': 'missing_page_positions',
                'count': 4,
                'sample': [205, 206, 207, 208],
            },
        ])

    def test_workbuddy_page_index_gap_preserves_rows_but_blocks_classification(self):
        payload = {
            'scrollY': 9000,
            'innerHeight': 1000,
            'scrollHeight': 10000,
            'location': (
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav&subTab=note'
            ),
            'title': '收藏',
            'declaredItemCount': 4,
            'loginRequired': False,
            'securityMarker': '',
            'items': [{
                'id': f'{index:024x}',
                'title': f'条目 {index}',
                'page_index': page_index,
            } for index, page_index in enumerate((0, 2, 3, None))],
        }

        def fake_eval(script):
            if script == OWN_PROFILE_LINK_JS:
                return (
                    'https://www.xiaohongshu.com/user/profile/'
                    '66d19b54000000001d03a93d'
                )
            return 'ok'

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch(
                'workbuddy_bridge.read_stable_items_snapshot',
                return_value=(payload, 1),
            ):
                result = capture_workbuddy_groups(
                    fake_eval,
                    directory,
                    'collection',
                    200,
                    3,
                    directory / 'xhs_safety_state.json',
                    expected_page_url=payload['location'],
                )
            visible = json.loads(
                (directory / 'visible_items.json').read_text(encoding='utf-8')
            )

        self.assertEqual(len(visible), 4)
        self.assertFalse(result['crawl_complete'])
        self.assertFalse(result['ready_for_classification'])
        self.assertEqual(result['blockers'], [
            'invalid_page_positions',
            'missing_page_positions',
        ])
        self.assertEqual(result['warnings'], [
            {'code': 'invalid_page_positions'},
            {
                'code': 'missing_page_positions',
                'count': 1,
                'sample': [1],
            },
        ])

    def test_workbuddy_missing_declared_count_preserves_rows_but_blocks_classification(self):
        expected_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        payload = {
            'scrollY': 9000,
            'innerHeight': 1000,
            'scrollHeight': 10000,
            'location': expected_url,
            'title': '收藏',
            'declaredItemCount': None,
            'loginRequired': False,
            'securityMarker': '',
            'items': [{
                'id': f'{index:024x}',
                'title': f'条目 {index}',
                'page_index': index,
            } for index in range(3)],
        }

        def fake_eval(script):
            if script == OWN_PROFILE_LINK_JS:
                return (
                    'https://www.xiaohongshu.com/user/profile/'
                    '66d19b54000000001d03a93d'
                )
            return 'ok'

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch(
                'workbuddy_bridge.read_stable_items_snapshot',
                side_effect=[(payload, 1), (payload, 1)],
            ):
                result = capture_workbuddy_groups(
                    fake_eval,
                    directory,
                    'collection',
                    200,
                    3,
                    directory / 'xhs_safety_state.json',
                    expected_page_url=expected_url,
                )
            visible = json.loads(
                (directory / 'visible_items.json').read_text(encoding='utf-8')
            )

        self.assertEqual(len(visible), 3)
        self.assertFalse(result['crawl_complete'])
        self.assertFalse(result['ready_for_classification'])
        self.assertEqual(result['blockers'], ['declared_count_unavailable'])
        self.assertEqual(result['warnings'], [{
            'code': 'declared_count_unavailable',
        }])

    def test_workbuddy_rejects_one_note_occupying_two_page_positions(self):
        user_id = '66d19b54000000001d03a93d'
        note_id = '66d19b54000000001d03a93e'
        page_url = (
            f'https://www.xiaohongshu.com/user/profile/{user_id}?tab=fav'
        )
        payload = {
            'scrollY': 900,
            'innerHeight': 100,
            'scrollHeight': 1000,
            'location': page_url,
            'title': '收藏',
            'declaredItemCount': 2,
            'loginRequired': False,
            'securityMarker': '',
            'items': [
                {'id': note_id, 'title': '同一条', 'page_index': 0},
                {'id': note_id, 'title': '同一条', 'page_index': 1},
            ],
        }

        def fake_eval(script):
            if script == OWN_PROFILE_LINK_JS:
                return f'https://www.xiaohongshu.com/user/profile/{user_id}'
            return 'ok'

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch(
                'workbuddy_bridge.read_stable_items_snapshot',
                return_value=(payload, 2),
            ):
                result = capture_workbuddy_groups(
                    fake_eval,
                    directory,
                    'collection',
                    200,
                    3,
                    directory / 'xhs_safety_state.json',
                    expected_page_url=page_url,
                )

        self.assertFalse(result['ready_for_classification'])
        self.assertIn('note_position_conflict', result['blockers'])
        self.assertIn('declared_count_mismatch', result['blockers'])

    def test_capture_hard_stops_when_live_page_binding_changes(self):
        expected_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        payload = {
            'scrollY': 900,
            'innerHeight': 100,
            'scrollHeight': 1000,
            'location': expected_url.replace('tab=fav', 'tab=liked'),
            'title': '点赞',
            'declaredItemCount': 1,
            'loginRequired': False,
            'securityMarker': '',
            'items': [{
                'id': '66d19b54000000001d03a93e',
                'title': '不应写入的卡片',
                'page_index': 0,
            }],
        }
        href_sink = {}

        def fake_eval(script):
            if script == OWN_PROFILE_LINK_JS:
                return (
                    'https://www.xiaohongshu.com/user/profile/'
                    '66d19b54000000001d03a93d'
                )
            return 'ok'

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            safety = directory / 'xhs_safety_state.json'
            with patch(
                'workbuddy_bridge.read_stable_items_snapshot',
                return_value=(payload, 1),
            ):
                with self.assertRaisesRegex(
                    SafetyHaltedError,
                    'capture_page_binding_lost',
                ):
                    capture_workbuddy_groups(
                        fake_eval,
                        directory,
                        'collection',
                        200,
                        3,
                        safety,
                        href_sink,
                        expected_page_url=expected_url,
                    )
            state = load_safety_state(safety)

            self.assertEqual(state['halt']['reason_code'], 'page_binding_lost')
            self.assertEqual(href_sink, {})
            self.assertFalse((directory / 'visible_items.json').exists())

    def test_capture_hard_stops_when_logged_in_account_differs(self):
        expected_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        payload = {
            'scrollY': 900,
            'innerHeight': 100,
            'scrollHeight': 1000,
            'location': expected_url,
            'title': '收藏',
            'declaredItemCount': 1,
            'loginRequired': False,
            'securityMarker': '',
            'items': [{
                'id': '66d19b54000000001d03a93e',
                'title': '不应写入的卡片',
                'page_index': 0,
            }],
        }

        def fake_eval(script):
            if script == OWN_PROFILE_LINK_JS:
                return (
                    'https://www.xiaohongshu.com/user/profile/'
                    '77d19b54000000001d03a93d'
                )
            return 'ok'

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            safety = directory / 'xhs_safety_state.json'
            with patch(
                'workbuddy_bridge.read_stable_items_snapshot',
                return_value=(payload, 1),
            ):
                with self.assertRaisesRegex(
                    SafetyHaltedError,
                    'capture_account_binding_mismatch',
                ):
                    capture_workbuddy_groups(
                        fake_eval,
                        directory,
                        'collection',
                        200,
                        3,
                        safety,
                        expected_page_url=expected_url,
                    )
            state = load_safety_state(safety)

            self.assertEqual(
                state['halt']['reason_code'],
                'account_binding_mismatch',
            )
            self.assertFalse((directory / 'visible_items.json').exists())

    def test_capture_hard_stops_when_logged_in_account_cannot_be_verified(self):
        expected_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        payload = {
            'scrollY': 900,
            'innerHeight': 100,
            'scrollHeight': 1000,
            'location': expected_url,
            'title': '收藏',
            'declaredItemCount': 1,
            'loginRequired': False,
            'securityMarker': '',
            'items': [{
                'id': '66d19b54000000001d03a93e',
                'title': '不应写入的卡片',
                'page_index': 0,
            }],
        }

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            safety = directory / 'xhs_safety_state.json'
            with patch(
                'workbuddy_bridge.read_stable_items_snapshot',
                return_value=(payload, 1),
            ):
                with self.assertRaisesRegex(
                    SafetyHaltedError,
                    'capture_account_binding_unavailable',
                ):
                    capture_workbuddy_groups(
                        lambda _script: '',
                        directory,
                        'collection',
                        200,
                        3,
                        safety,
                        expected_page_url=expected_url,
                    )
            state = load_safety_state(safety)

            self.assertEqual(
                state['halt']['reason_code'],
                'account_binding_unavailable',
            )
            self.assertFalse((directory / 'visible_items.json').exists())

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
            ], ['居住空间', '无法确定'])
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

        self.assertEqual(result['taxonomy'], ['居住空间', '无法确定'])
        self.assertEqual(taxonomy, {'boards': ['居住空间', '无法确定']})
        self.assertEqual([row['id'] for row in classification], [first_id, second_id])
        self.assertEqual(classification[0]['title'], '客厅照明改造')
        self.assertEqual(classification[1]['target_board'], '无法确定')
        self.assertTrue(classification[1]['uncertain_assignment'])
        self.assertEqual(
            classification[1]['review_state'],
            'manual_reclassification_required',
        )

    def test_workbuddy_excludes_existing_board_members_from_model_and_plan(self):
        protected_id = '66d19b54000000001d03a93d'
        unassigned_id = '66d19b54000000001d03a93e'
        visible_rows = [
            {'id': protected_id, 'title': '用户已手动整理'},
            {'id': unassigned_id, 'title': '尚未归档'},
        ]
        evidence = {
            'visible_ids': [protected_id, unassigned_id],
            'visible_by_id': {row['id']: row for row in visible_rows},
            'image_by_id': {},
            'ocr_by_id': {},
            'image_ocr_enabled': False,
        }
        safe_inputs = workbuddy_classification_inputs(evidence, {protected_id})
        self.assertEqual([row['id'] for row in safe_inputs], [unassigned_id])

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'visible_items.json').write_text(
                json.dumps(visible_rows, ensure_ascii=False),
                encoding='utf-8',
            )
            result = write_workbuddy_classification(
                directory,
                [{
                    'id': unassigned_id,
                    'target_board': '阅读',
                    'confidence': 'high',
                    'reason': ['内容是书摘'],
                }],
                ['阅读', '用户手动专辑'],
                evidence,
                {protected_id: '用户手动专辑'},
            )
            classification = json.loads(
                (directory / 'classification.json').read_text(encoding='utf-8')
            )
            with self.assertRaisesRegex(RuntimeError, '已归档保护笔记'):
                write_workbuddy_classification(
                    directory,
                    [
                        {'id': protected_id, 'target_board': '阅读'},
                        {'id': unassigned_id, 'target_board': '阅读'},
                    ],
                    ['阅读'],
                    evidence,
                    {protected_id: '用户手动专辑'},
                )

        self.assertEqual(result['classification_count'], 2)
        protected_row, unassigned_row = classification
        self.assertTrue(protected_row['excluded'])
        self.assertEqual(
            protected_row['exclude_reason'],
            'existing_board_member_protected',
        )
        self.assertEqual(protected_row['source_board'], '用户手动专辑')
        self.assertEqual(protected_row['target_board'], '')
        self.assertEqual(
            protected_row['archive_lifecycle_state'],
            'first_archive_confirmed',
        )
        self.assertEqual(unassigned_row['target_board'], '阅读')
        self.assertEqual(
            unassigned_row['archive_lifecycle_state'],
            'first_archive_pending',
        )

    def test_valid_workbuddy_ocr_is_bound_and_merged_without_sensitive_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = self.write_valid_ocr_capture_contract(directory)
            evidence = validate_workbuddy_capture_evidence(directory)
            safe_inputs = workbuddy_classification_inputs(evidence)
            result = write_workbuddy_classification(
                directory,
                [{
                    'id': note_id,
                    'target_board': '上海旅行',
                    'confidence': 'high',
                    'reason': ['OCR 显示上海路线和餐厅清单'],
                }],
                ['上海旅行'],
                evidence,
            )
            classification = json.loads(
                (directory / 'classification.json').read_text(encoding='utf-8')
            )
            persisted = json.dumps(classification, ensure_ascii=False)

        self.assertEqual(result['classification_count'], 1)
        self.assertEqual(safe_inputs[0]['id'], note_id)
        self.assertIn('上海路线', safe_inputs[0]['ocr_text'])
        self.assertNotIn('href', safe_inputs[0])
        self.assertNotIn('image_urls', safe_inputs[0])
        self.assertNotIn('cover_image_url', safe_inputs[0])
        self.assertNotIn('local-only', json.dumps(safe_inputs, ensure_ascii=False))
        self.assertEqual(
            classification[0]['classification_basis'],
            'workbuddy_authenticated_frontend_ocr',
        )
        self.assertEqual(classification[0]['ocr_status'], 'ok')
        self.assertEqual(
            classification[0]['ocr_text'],
            '第1张：上海路线 第2张：餐厅清单',
        )
        self.assertEqual(
            classification[0]['ocr_run_fingerprint'],
            ocr_run_fingerprint(
                'tesseract',
                'chi_sim',
                ROOT / 'scripts' / 'ocr_image.swift.txt',
            ),
        )
        self.assertTrue(classification[0]['ocr_image_set_complete'])
        self.assertNotIn('href', classification[0])
        self.assertNotIn('image_urls', classification[0])
        self.assertNotIn('cover_image_url', classification[0])
        self.assertNotIn('xsec_token', persisted)
        self.assertNotIn('sign=local-only', persisted)

    def test_prepare_blocks_incomplete_ocr_evidence_before_browser(self):
        user_id = '66d19b54000000001d03a93d'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            self.write_capture_contract(
                directory,
                [{
                    'id': '66d19b54000000001d03a93e',
                    'title': '图文',
                    'content_type': 'image',
                }],
                image_ocr_enabled=True,
                ready_for_classification=False,
                blockers=['ocr_results_incomplete'],
                image_rows=[],
                ocr_rows=[],
            )
            trusted = self.trusted_evidence(directory, 'capture')
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command') as command,
                patch(
                    'workbuddy_bridge.BrowserRunner',
                    side_effect=AssertionError('browser must not launch'),
                ) as browser,
            ):
                with self.assertRaisesRegex(RuntimeError, '内容证据未通过'):
                    prepare_action(
                        'run-1', user_id, page_url, expected, 100,
                        trusted_evidence=trusted,
                    )

        command.assert_not_called()
        browser.assert_not_called()

    def test_classification_boundary_redacts_credentials_inside_ocr_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = self.write_valid_ocr_capture_contract(directory)
            evidence = validate_workbuddy_capture_evidence(directory)
            evidence['ocr_by_id'][note_id]['ocr_text'] = (
                '路线 xsec_token=ocr-secret authorization: Bearer model-secret '
                'a1=real-xhs-session-secret'
            )
            inputs = workbuddy_classification_inputs(evidence)
            write_workbuddy_classification(
                directory,
                [{
                    'id': note_id,
                    'target_board': '旅行',
                    'confidence': 'high',
                    'reason': ['OCR'],
                }],
                ['旅行'],
                evidence,
            )
            persisted = (directory / 'classification.json').read_text(
                encoding='utf-8'
            )

        rendered = json.dumps(inputs, ensure_ascii=False) + persisted
        self.assertNotIn('ocr-secret', rendered)
        self.assertNotIn('model-secret', rendered)
        self.assertNotIn('real-xhs-session-secret', rendered)
        self.assertIn('<redacted>', rendered)

    def test_prepare_rejects_capture_from_different_account_before_browser(self):
        captured_user_id = '66d19b54000000001d03a93d'
        requested_user_id = '66d19b54000000001d03a93e'
        expected = f'/user/profile/{requested_user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            self.write_capture_contract(directory, [{
                'id': captured_user_id,
                'title': '账号 A 的收藏',
                'content_type': 'image',
            }])
            trusted = self.trusted_evidence(directory, 'capture')
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command') as command,
            ):
                with self.assertRaisesRegex(RuntimeError, 'trusted_evidence_binding_mismatch'):
                    prepare_action(
                        'run-1',
                        requested_user_id,
                        page_url,
                        expected,
                        100,
                        trusted_evidence=trusted,
                    )

        command.assert_not_called()

    def test_trusted_collection_receipt_rejects_liked_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            self.write_capture_contract(directory, [{
                'id': '66d19b54000000001d03a93d',
                'title': '测试收藏',
                'content_type': 'image',
            }])
            trusted = self.trusted_evidence(directory, 'capture')
            with patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True):
                with self.assertRaisesRegex(
                    RuntimeError,
                    'trusted_evidence_binding_mismatch',
                ):
                    validate_trusted_evidence(
                        directory,
                        trusted,
                        expected_stage='capture',
                        expected_user_id='66d19b54000000001d03a93d',
                        expected_page_url=(
                            'https://www.xiaohongshu.com/user/profile/'
                            '66d19b54000000001d03a93d?tab=liked'
                        ),
                    )

    def test_prepare_rejects_signed_capture_tampering_before_browser(self):
        user_id = '66d19b54000000001d03a93d'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            self.write_capture_contract(directory, [{
                'id': '66d19b54000000001d03a93e',
                'title': '真实收藏',
                'content_type': 'image',
            }])
            trusted = self.trusted_evidence(directory, 'capture')
            (directory / 'visible_items.json').write_text(
                json.dumps([{
                    'id': '66d19b54000000001d03a93e',
                    'title': '被替换的收藏',
                    'content_type': 'image',
                }], ensure_ascii=False),
                encoding='utf-8',
            )
            manifest_path = directory / 'crawl_manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['visible_items_sha256'] = hashlib.sha256(
                (directory / 'visible_items.json').read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding='utf-8',
            )
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command') as command,
                patch(
                    'workbuddy_bridge.BrowserRunner',
                    side_effect=AssertionError('browser must not launch'),
                ) as browser,
            ):
                with self.assertRaisesRegex(RuntimeError, 'trusted_evidence_changed'):
                    prepare_action(
                        'run-1',
                        user_id,
                        page_url,
                        expected,
                        100,
                        trusted_evidence=trusted,
                    )

        command.assert_not_called()
        browser.assert_not_called()

    def test_prepare_first_phase_returns_real_board_names_before_classification(self):
        user_id = '66d19b54000000001d03a93d'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            directory.mkdir(parents=True)
            self.write_capture_contract(directory, [])
            trusted = self.trusted_evidence(directory, 'capture')

            def fake_run_command(args, **_kwargs):
                snapshot = Path(args[2])
                snapshot.write_text(json.dumps({
                    'mode': 'read_only',
                    'source': {
                        'browser': 'playwright',
                        'writes_performed': False,
                        'user_id': user_id,
                        'expected_url_substring': page_url,
                        'live_page_binding': page_url,
                        'live_account_user_id': user_id,
                        'verify_pages': 100,
                    },
                    'boards': [
                        {'id': 'a' * 24, 'name': '阅读', 'note_ids': []},
                        {'id': 'b' * 24, 'name': '运动', 'note_ids': []},
                    ],
                    'validation': {
                        'pagination_cursor_invariants_passed': True,
                        'board_names_unique': True,
                        'within_board_duplicates': [],
                        'full_membership_complete': True,
                    },
                }, ensure_ascii=False), encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '{}', '')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command) as command,
            ):
                result = prepare_action(
                    'run-1', user_id, page_url, expected, 100,
                    trusted_evidence=trusted,
                )

        self.assertEqual(result['phase'], 'board_inventory')
        self.assertEqual(result['existing_board_names'], ['阅读', '运动'])
        self.assertEqual(result['classification_inputs'], [])
        self.assertEqual(result['classification_input_count'], 0)
        self.assertEqual(result['verify_pages'], 100)
        self.assertEqual(result['blockers'], [])
        self.assertIsNone(result['approval_digest'])
        self.assertEqual(command.call_count, 1)
        self.assertIn('capture_board_snapshot.py', command.call_args.args[0][1])

    def test_prepare_first_phase_offers_bound_board_creation_when_account_is_empty(self):
        user_id = '66d19b54000000001d03a93d'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            directory.mkdir(parents=True)
            self.write_capture_contract(directory, [])
            trusted = self.trusted_evidence(directory, 'capture')

            def fake_run_command(args, **_kwargs):
                Path(args[2]).write_text(json.dumps({
                    'mode': 'read_only',
                    'source': {
                        'browser': 'playwright',
                        'writes_performed': False,
                        'user_id': user_id,
                        'expected_url_substring': page_url,
                        'live_page_binding': page_url,
                        'live_account_user_id': user_id,
                        'verify_pages': 100,
                    },
                    'boards': [],
                    'validation': {
                        'pagination_cursor_invariants_passed': True,
                        'board_names_unique': True,
                        'within_board_duplicates': [],
                        'full_membership_complete': True,
                    },
                }), encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '{}', '')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command),
            ):
                result = prepare_action(
                    'run-1', user_id, page_url, expected, 100,
                    trusted_evidence=trusted,
                )

        self.assertEqual(result['existing_board_names'], [])
        self.assertTrue(result['classification_required'])
        self.assertTrue(result['board_creation_required'])
        self.assertEqual(result['blockers'], [])
        self.assertIn('proposed_board_names', result['next_action'])

    def test_prepare_zero_board_plan_binds_proposed_boards_and_privacy(self):
        user_id = '66d19b54000000001d03a93d'
        note_id = '66d19b54000000001d03a93e'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            directory.mkdir(parents=True)
            self.write_capture_contract(directory, [{
                'id': note_id,
                'title': '一本书的读后感',
                'content_type': 'image',
            }])
            (directory / 'board_snapshot.json').write_text(json.dumps({
                'mode': 'read_only',
                'source': {
                    'browser': 'playwright',
                    'writes_performed': False,
                    'user_id': user_id,
                    'expected_url_substring': page_url,
                    'live_page_binding': page_url,
                    'live_account_user_id': user_id,
                    'verify_pages': 100,
                },
                'boards': [],
                'validation': {
                    'pagination_cursor_invariants_passed': True,
                    'board_names_unique': True,
                    'within_board_duplicates': [],
                    'full_membership_complete': True,
                },
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'inventory')
            commands = []

            def fake_run_command(args, **_kwargs):
                commands.append(args)
                if 'build_created_boards.py' in args[1]:
                    Path(args[4]).write_text(json.dumps({
                        'confirmed': [],
                        'created': [],
                        'missing': ['阅读'],
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
                            'membership_state': 'target_board_planned',
                            'archive_lifecycle_state': 'first_archive_pending',
                            'status': 'planned',
                        }],
                    }, ensure_ascii=False), encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '{}', '')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command),
            ):
                result = prepare_action(
                    'run-1', user_id, page_url, expected, 100,
                    [{
                        'id': note_id,
                        'target_board': '阅读',
                        'confidence': 'high',
                        'reason': ['真实内容与提议专辑一致'],
                    }],
                    max_moves=10,
                    trusted_evidence=trusted,
                    proposed_board_names=['阅读'],
                    new_board_privacy='private',
                )
            created = json.loads(
                (directory / 'created_boards.json').read_text(encoding='utf-8')
            )

        self.assertTrue(result['ready_for_execute'])
        self.assertEqual(result['planned_board_creations'], [
            {'name': '阅读', 'privacy': 1},
        ])
        self.assertEqual(created['planned'], [{'name': '阅读', 'privacy': 1}])
        self.assertEqual(created['missing'], [])
        self.assertTrue(any('--allow-planned-board-creation' in args for args in commands))

    def test_prepare_routes_empty_target_to_created_uncertain_board(self):
        user_id = '66d19b54000000001d03a93d'
        note_id = '66d19b54000000001d03a93e'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            self.write_capture_contract(directory, [{
                'id': note_id,
                'title': '现有证据仍无法判断主题',
                'content_type': 'image',
            }])
            (directory / 'board_snapshot.json').write_text(json.dumps({
                'mode': 'read_only',
                'source': {
                    'browser': 'playwright',
                    'writes_performed': False,
                    'user_id': user_id,
                    'expected_url_substring': page_url,
                    'live_page_binding': page_url,
                    'live_account_user_id': user_id,
                    'verify_pages': 100,
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
                    'full_membership_complete': True,
                },
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'inventory')

            def fake_run_command(args, **_kwargs):
                if 'build_created_boards.py' in args[1]:
                    Path(args[4]).write_text(json.dumps({
                        'confirmed': [],
                        'created': [],
                        'missing': ['无法确定'],
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
                            'target_board': '无法确定',
                            'source_board_id': '',
                            'membership_state': 'not_in_any_board',
                            'archive_lifecycle_state': 'first_archive_pending',
                            'status': 'planned',
                        }],
                    }, ensure_ascii=False), encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '{}', '')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command),
            ):
                result = prepare_action(
                    'run-1', user_id, page_url, expected, 100,
                    [{
                        'id': note_id,
                        'target_board': '',
                        'confidence': 'low',
                        'reason': ['证据不足'],
                    }],
                    max_moves=10,
                    trusted_evidence=trusted,
                    new_board_privacy='private',
                )
            classification = json.loads(
                (directory / 'classification.json').read_text(encoding='utf-8')
            )

        self.assertEqual(result['planned_board_creations'], [
            {'name': '无法确定', 'privacy': 1},
        ])
        self.assertEqual(result['taxonomy'], ['无法确定'])
        self.assertEqual(classification[0]['target_board'], '无法确定')
        self.assertTrue(classification[0]['uncertain_assignment'])
        self.assertEqual(result['planned_move_count'], 1)

    def test_prepare_second_phase_reuses_bound_snapshot_and_rejects_new_categories(self):
        user_id = '66d19b54000000001d03a93d'
        note_id = '66d19b54000000001d03a93e'
        expected = f'/user/profile/{user_id}'
        page_url = f'https://www.xiaohongshu.com{expected}?tab=fav'
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory = data_dir / 'runs' / 'run-1'
            directory.mkdir(parents=True)
            self.write_capture_contract(directory, [
                {
                    'id': note_id,
                    'title': '一本书的读后感',
                    'content_type': 'image',
                },
            ])
            (directory / 'board_snapshot.json').write_text(json.dumps({
                'mode': 'read_only',
                'source': {
                    'browser': 'playwright',
                    'writes_performed': False,
                    'user_id': user_id,
                    'expected_url_substring': page_url,
                    'live_page_binding': page_url,
                    'live_account_user_id': user_id,
                    'verify_pages': 100,
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
                    'full_membership_complete': True,
                },
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'inventory')

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
                            'archive_lifecycle_state': 'first_archive_pending',
                            'status': 'planned',
                        }],
                    }, ensure_ascii=False), encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '{}', '')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command', side_effect=fake_run_command),
            ):
                with self.assertRaisesRegex(RuntimeError, 'max_moves_per_session'):
                    prepare_action(
                        'run-1',
                        user_id,
                        page_url,
                        expected,
                        100,
                        [{'id': note_id, 'target_board': '阅读'}],
                        trusted_evidence=trusted,
                    )
                with self.assertRaisesRegex(RuntimeError, '真实已有专辑'):
                    prepare_action(
                        'run-1',
                        user_id,
                        page_url,
                        expected,
                        100,
                        [{
                            'id': note_id,
                            'target_board': '插件预设类别',
                        }],
                        max_moves=10,
                        trusted_evidence=trusted,
                    )
                with self.assertRaisesRegex(RuntimeError, 'verify_pages'):
                    prepare_action(
                        'run-1',
                        user_id,
                        page_url,
                        expected,
                        99,
                        [{'id': note_id, 'target_board': '阅读'}],
                        max_moves=10,
                        trusted_evidence=trusted,
                    )
                result = prepare_action(
                    'run-1',
                    user_id,
                    page_url,
                    expected,
                    100,
                    [{
                        'id': note_id,
                        'target_board': '阅读',
                        'confidence': 'high',
                        'reason': ['真实内容与已有专辑一致'],
                    }],
                    max_moves=10,
                    trusted_evidence=trusted,
                )
                approval = json.loads(
                    (directory / 'approval.json').read_text(encoding='utf-8')
                )

        self.assertEqual(result['phase'], 'dry_run')
        self.assertTrue(result['ready_for_execute'])
        self.assertEqual(result['taxonomy'], ['阅读'])
        self.assertIsNotNone(result['approval_digest'])
        self.assertEqual(result['max_moves_per_session'], 10)
        self.assertEqual(result['verify_pages'], 100)
        self.assertEqual(approval['basis']['max_moves_per_session'], 10)
        self.assertEqual(approval['basis']['verify_pages'], 100)
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
        self.assertIn("'setup',\n        [],\n        1_800_000,\n        extra.signal", server_source)
        self.assertIn('(timeout_seconds + 30) * 1000,\n        extra.signal', server_source)
        self.assertIn('], 1_800_000, extra.signal, {', server_source)

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
                        'quick',
                    )
            self.assertFalse((data_dir / 'runs' / 'locked-run').exists())

    def test_capture_coverage_blocker_stops_detail_and_ocr_before_classification(self):
        note_id = '66d19b54000000001d03a93e'
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )

        class FakeRunner:
            def __init__(self, *_args):
                self.closed = False

            def run_javascript(self, _script):
                return 'ok'

            def close(self):
                self.closed = True

        def incomplete_capture(
            _js_eval,
            directory,
            _source,
            _batch_size,
            _pause_minutes,
            safety,
            _detail_href_sink,
            *,
            expected_page_url,
        ):
            self.assertEqual(expected_page_url, target_url)
            rows = [{
                'id': note_id,
                'title': '仅抓到的一条收藏',
                'content_type': 'image',
            }]
            (directory / 'visible_items.json').write_text(
                json.dumps(rows, ensure_ascii=False),
                encoding='utf-8',
            )
            manifest = {
                'capture_mode': 'workbuddy_segmented',
                'source': '收藏',
                'item_count': 1,
                'crawl_complete': False,
                'ready_for_classification': False,
                'stopped_reason': 'capture_coverage_incomplete',
                'blockers': ['declared_count_mismatch'],
                'warnings': [{
                    'code': 'declared_count_mismatch',
                    'declared_count': 314,
                    'accessible_count': 1,
                }],
                'safety_state': str(safety),
            }
            (directory / 'crawl_manifest.json').write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding='utf-8',
            )
            return {
                'count': 1,
                'output': str(directory / 'visible_items.json'),
                'manifest': str(directory / 'crawl_manifest.json'),
                'crawl_complete': False,
                'ready_for_classification': False,
                'stopped_reason': 'capture_coverage_incomplete',
                'blockers': ['declared_count_mismatch'],
                'warnings': manifest['warnings'],
                'safety_state': str(safety),
            }

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.BrowserRunner', FakeRunner),
                patch(
                    'workbuddy_bridge.capture_workbuddy_groups',
                    side_effect=incomplete_capture,
                ),
                patch('workbuddy_bridge.wait_for_profile_release'),
                patch(
                    'workbuddy_bridge.enrich_workbuddy_image_items',
                    side_effect=AssertionError('detail must not run'),
                ) as enrich,
                patch(
                    'workbuddy_bridge.run_workbuddy_ocr',
                    side_effect=AssertionError('OCR must not run'),
                ) as ocr,
            ):
                result = capture_action(
                    'coverage-blocked',
                    'collection',
                    target_url,
                    200,
                    3,
                    'light',
                )
            manifest = json.loads(
                (data_dir / 'runs' / 'coverage-blocked' / 'crawl_manifest.json')
                .read_text(encoding='utf-8')
            )

        enrich.assert_not_called()
        ocr.assert_not_called()
        self.assertFalse(result['ready_for_classification'])
        self.assertEqual(result['blockers'], ['declared_count_mismatch'])
        self.assertFalse(manifest['ready_for_classification'])

    def test_capture_uses_explicit_depth_and_fixed_200_by_3_grouping(self):
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        parsed = build_parser().parse_args([
            'capture',
            '--source', 'collection',
            '--page-url', target_url,
            '--organizing-depth', 'light',
            '--batch-size', '10',
            '--pause-minutes', '3',
            '--generate-report',
        ])
        self.assertEqual(parsed.organizing_depth, 'light')
        self.assertEqual(parsed.batch_size, 10)
        self.assertEqual(parsed.pause_minutes, 3)
        self.assertTrue(parsed.generate_report)
        self.assertNotIn('detail_request_limit', vars(parsed))

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch(
                    'workbuddy_bridge.BrowserRunner',
                    side_effect=AssertionError('browser must not launch'),
                ) as browser,
            ):
                for invalid in (None, 0, 10, 201, True):
                    with self.subTest(invalid=invalid), self.assertRaisesRegex(
                        RuntimeError,
                        '固定读取 200',
                    ):
                        capture_action(
                            'ocr-authorization-run',
                            'collection',
                            target_url,
                            invalid,
                            3,
                            'light',
                        )
                with self.assertRaisesRegex(RuntimeError, '固定暂停 3'):
                    capture_action(
                        'ocr-pause-run',
                        'collection',
                        target_url,
                        200,
                        1,
                        'light',
                    )
                with self.assertRaisesRegex(RuntimeError, 'deep.*尚未接入'):
                    capture_action(
                        'deep-not-supported',
                        'collection',
                        target_url,
                        200,
                        3,
                        'deep',
                    )
                with self.assertRaisesRegex(RuntimeError, '快速整理不生成'):
                    capture_action(
                        'quick-report-not-supported',
                        'collection',
                        target_url,
                        200,
                        3,
                        'quick',
                        True,
                    )
            browser.assert_not_called()

    def test_capture_collects_raw_detail_href_only_in_memory(self):
        note_id = '66d19b54000000001d03a93d'
        secret_href = (
            f'https://www.xiaohongshu.com/explore/{note_id}'
            '?xsec_token=secret-never-persist'
        )
        payload = {
            'scrollY': 900,
            'innerHeight': 100,
            'scrollHeight': 1000,
            'location': (
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav&subTab=note'
            ),
            'title': '收藏',
            'declaredItemCount': 1,
            'loginRequired': False,
            'securityMarker': '',
            'items': [{
                'id': note_id,
                'title': '测试图文',
                'content_type': 'image',
                'page_index': 0,
            }],
        }
        href_sink = {}

        def fake_eval(script):
            if script == OWN_PROFILE_LINK_JS:
                return (
                    'https://www.xiaohongshu.com/user/profile/'
                    '66d19b54000000001d03a93d'
                )
            if 'href: new URL(href' in script:
                return json.dumps([{'id': note_id, 'href': secret_href}])
            return 'ok'

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch(
                'workbuddy_bridge.read_stable_items_snapshot',
                return_value=(payload, 1),
            ):
                capture_workbuddy_groups(
                    fake_eval,
                    directory,
                    'collection',
                    200,
                    3,
                    directory / 'xhs_safety_state.json',
                    href_sink,
                    expected_page_url=payload['location'],
                )
            persisted = '\n'.join(
                path.read_text(encoding='utf-8')
                for path in directory.glob('*.json')
            )

        self.assertEqual(href_sink[note_id], secret_href)
        self.assertNotIn('secret-never-persist', persisted)
        self.assertNotIn('xsec_token', persisted)

    def test_authenticated_image_download_persists_only_local_bytes_and_hashes(self):
        source_url = (
            'https://ci.xiaohongshu.com/image.jpg'
            '?xsec_token=memory-only&sign=memory-only'
        )
        image_data = b'\xff\xd8\xff' + b'image-bytes' * 4

        class Response:
            ok = True

            def body(self):
                return image_data

            def dispose(self):
                return None

        requested = []
        request = types.SimpleNamespace(
            get=lambda url, **_kwargs: requested.append(url) or Response(),
        )
        runner = types.SimpleNamespace(
            context=types.SimpleNamespace(request=request),
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            files, hashes = download_workbuddy_authenticated_images(
                runner,
                '66d19b54000000001d03a93d',
                [source_url],
                directory,
            )
            persisted = (directory / files[0]).read_bytes()

        self.assertEqual(requested, [source_url])
        self.assertEqual(persisted, image_data)
        self.assertEqual(hashes, [hashlib.sha256(image_data).hexdigest()])
        self.assertNotIn('xsec', json.dumps({'files': files, 'hashes': hashes}))

    def test_capture_reuses_same_context_for_detail_images_then_runs_complete_ocr(self):
        note_id = '66d19b54000000001d03a93d'
        video_id = '66d19b54000000001d03a93e'
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        secret_href = (
            f'https://www.xiaohongshu.com/explore/{note_id}'
            '?xsec_token=secret-in-memory-only'
        )

        class FakeDetailPage:
            def __init__(self):
                self.goto_urls = []
                self.closed = False

            def goto(self, url, **_kwargs):
                self.goto_urls.append(url)

            def evaluate(self, _script, requested_id):
                if requested_id == video_id:
                    return {
                        'location': f'https://www.xiaohongshu.com/explore/{requested_id}',
                        'title': '测试视频详情',
                        'loginRequired': False,
                        'securityMarker': '',
                        'stateSource': 'initial_state_note_detail_map',
                        'noteData': {
                            'noteId': requested_id,
                            'type': 'video',
                            'imageList': None,
                        },
                    }
                return {
                    'location': f'https://www.xiaohongshu.com/explore/{requested_id}',
                    'title': '测试详情',
                    'loginRequired': False,
                    'securityMarker': '',
                    'stateSource': 'initial_state_note_detail_map',
                    'noteData': {
                        'noteId': requested_id,
                        'type': 'normal',
                        'imageList': [
                            'https://ci.xiaohongshu.com/cover.jpg?sign=image-secret',
                            'https://ci.xiaohongshu.com/inner.jpg?xsec_token=image-secret',
                        ],
                    },
                }

            def close(self):
                self.closed = True

        detail_page = FakeDetailPage()

        class FakeRunner:
            def __init__(self, *_args):
                self.closed = False
                self.context = types.SimpleNamespace(new_page=lambda: detail_page)

            def run_javascript(self, _script):
                return 'ok'

            def close(self):
                self.closed = True

        runner_holder = {}

        def make_runner(*args):
            runner = FakeRunner(*args)
            runner_holder['runner'] = runner
            return runner

        def fake_capture(
            _js_eval,
            directory,
            _source,
            _batch_size,
            _pause_minutes,
            safety,
            detail_href_sink,
            *,
            expected_page_url,
        ):
            self.assertEqual(expected_page_url, target_url)
            rows = [
                {
                    'id': note_id,
                    'title': '两页图文',
                    'content_type': 'image',
                    'image_urls': ['https://ci.xiaohongshu.com/card-cover.jpg'],
                    'image_urls_complete': False,
                },
                {
                    'id': video_id,
                    'title': '视频',
                    'content_type': 'video',
                },
            ]
            self.write_capture_contract(directory, rows)
            detail_href_sink[note_id] = secret_href
            detail_href_sink[video_id] = (
                f'https://www.xiaohongshu.com/explore/{video_id}'
                '?xsec_token=video-secret-in-memory-only'
            )
            return {
                'count': 2,
                'output': str(directory / 'visible_items.json'),
                'crawl_complete': True,
                'ready_for_classification': True,
                'blockers': [],
                'safety_state': str(safety),
            }

        def fake_ocr(command, **_kwargs):
            image_items = json.loads(Path(command[2]).read_text(encoding='utf-8'))
            image_row = next(row for row in image_items if row['id'] == note_id)
            count = image_row['image_count']
            image_hashes = image_row['image_file_sha256']
            image_references = [f'sha256:{value}' for value in image_hashes]
            image_set = hashlib.sha256(json.dumps(
                image_references,
                ensure_ascii=False,
                separators=(',', ':'),
            ).encode('utf-8')).hexdigest()
            Path(command[3]).write_text(json.dumps([{
                'id': note_id,
                'status': 'ok',
                'image_set_complete': True,
                'image_set_sha256': image_set,
                'image_count_declared': count,
                'image_count_available': count,
                'image_count_processed': count,
                'ocr_run_fingerprint': 'a' * 64,
                'images': [
                    {
                        'image_index': index,
                        'status': 'ok',
                        'source_image_sha256': image_hashes[index],
                        'image_sha256': image_hashes[index],
                    }
                    for index in range(count)
                ],
            }], ensure_ascii=False), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, '{}', '')

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.BrowserRunner', side_effect=make_runner),
                patch('workbuddy_bridge.capture_workbuddy_groups', side_effect=fake_capture),
                patch(
                    'workbuddy_bridge.download_workbuddy_authenticated_images',
                    side_effect=self.fake_authenticated_download,
                ),
                patch('workbuddy_bridge.run_command', side_effect=fake_ocr) as command,
                patch('workbuddy_bridge.detect_ocr_provider', return_value='tesseract'),
                patch('workbuddy_bridge.ocr_run_fingerprint', return_value='a' * 64),
                patch('workbuddy_bridge.wait_for_profile_release'),
                patch('workbuddy_bridge.time.sleep'),
            ):
                result = capture_action(
                    'image-ocr-success',
                    'collection',
                    target_url,
                    200,
                    3,
                    'light',
                )
            run_dir = data_dir / 'runs' / 'image-ocr-success'
            image_items = json.loads(
                (run_dir / 'image_items.json').read_text(encoding='utf-8')
            )
            persisted = '\n'.join(
                path.read_text(encoding='utf-8')
                for path in run_dir.glob('*.json')
            )

        self.assertEqual(detail_page.goto_urls, [
            secret_href,
            (
                f'https://www.xiaohongshu.com/explore/{video_id}'
                '?xsec_token=video-secret-in-memory-only'
            ),
        ])
        self.assertTrue(detail_page.closed)
        self.assertTrue(runner_holder['runner'].closed)
        self.assertTrue(result['browser_closed_by_tool'])
        self.assertEqual(result['requested'], 2)
        self.assertEqual(result['succeeded'], 2)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['ocr_ok'], 1)
        self.assertEqual(result['ocr_failed'], 0)
        self.assertTrue(result['ready_for_classification'])
        self.assertEqual(command.call_count, 1)
        self.assertTrue(image_items[0]['image_urls_complete'])
        self.assertEqual(image_items[0]['image_count'], 2)
        self.assertEqual(image_items[1]['image_enrichment_status'], 'not_applicable')
        self.assertNotIn('secret-in-memory-only', json.dumps(result))
        self.assertNotIn('secret-in-memory-only', persisted)
        self.assertNotIn('image-secret', persisted)
        self.assertNotIn('xsec_token', persisted)

    def test_detail_navigation_error_redacts_xsec_and_blocks_metadata_fallback(self):
        note_id = '66d19b54000000001d03a93d'
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        secret_href = (
            f'https://www.xiaohongshu.com/explore/{note_id}'
            '?xsec_token=secret-must-not-leak'
        )

        class FailingPage:
            def __init__(self):
                self.closed = False

            def goto(self, url, **_kwargs):
                raise RuntimeError(f'navigation failed for {url}')

            def close(self):
                self.closed = True

        detail_page = FailingPage()

        class FakeRunner:
            def __init__(self, *_args):
                self.closed = False
                self.context = types.SimpleNamespace(new_page=lambda: detail_page)

            def run_javascript(self, _script):
                return 'ok'

            def close(self):
                self.closed = True

        runner_holder = {}

        def make_runner(*args):
            runner = FakeRunner(*args)
            runner_holder['runner'] = runner
            return runner

        def fake_capture(
            _js_eval,
            directory,
            _source,
            _batch_size,
            _pause_minutes,
            safety,
            detail_href_sink,
            *,
            expected_page_url,
        ):
            self.assertEqual(expected_page_url, target_url)
            rows = [{
                'id': note_id,
                'title': '失败图文',
                'content_type': 'image',
            }]
            self.write_capture_contract(directory, rows)
            detail_href_sink[note_id] = secret_href
            return {
                'count': 1,
                'output': str(directory / 'visible_items.json'),
                'crawl_complete': True,
                'ready_for_classification': True,
                'blockers': [],
                'safety_state': str(safety),
            }

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.BrowserRunner', side_effect=make_runner),
                patch('workbuddy_bridge.capture_workbuddy_groups', side_effect=fake_capture),
                patch('workbuddy_bridge.run_command') as command,
                patch('workbuddy_bridge.wait_for_profile_release'),
            ):
                result = capture_action(
                    'image-ocr-failed',
                    'collection',
                    target_url,
                    200,
                    3,
                    'light',
                )
            run_dir = data_dir / 'runs' / 'image-ocr-failed'
            image_items = json.loads(
                (run_dir / 'image_items.json').read_text(encoding='utf-8')
            )
            persisted = '\n'.join(
                path.read_text(encoding='utf-8')
                for path in run_dir.glob('*.json')
            )

        command.assert_not_called()
        self.assertTrue(detail_page.closed)
        self.assertTrue(runner_holder['runner'].closed)
        self.assertFalse(result['ready_for_classification'])
        self.assertEqual(result['image_ocr_blockers'], ['detail_navigation_failed'])
        self.assertIn('不得改用封面 OCR 或元数据分类', result['next_action'])
        self.assertEqual(image_items[0]['image_enrichment_status'], 'error')
        self.assertEqual(
            image_items[0]['image_enrichment_error'],
            'detail_navigation_failed',
        )
        self.assertNotIn('secret-must-not-leak', json.dumps(result))
        self.assertNotIn('secret-must-not-leak', persisted)
        self.assertNotIn('xsec_token', persisted)

    def test_bridge_error_json_redacts_transient_detail_query(self):
        note_id = '66d19b54000000001d03a93d'
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )
        secret_error = RuntimeError(
            'goto failed: '
            f'https://www.xiaohongshu.com/explore/{note_id}'
            '?xsec_token=bridge-secret'
        )
        stdout = io.StringIO()
        argv = [
            'workbuddy_bridge.py',
            'capture',
            '--source', 'collection',
            '--page-url', target_url,
            '--organizing-depth', 'light',
        ]
        with (
            patch.object(sys, 'argv', argv),
            patch('workbuddy_bridge.capture_action', side_effect=secret_error),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit),
        ):
            bridge_main()

        payload = stdout.getvalue()
        self.assertNotIn('bridge-secret', payload)
        self.assertNotIn('xsec_token', payload)
        self.assertIn('<redacted_query>', payload)

    def test_bridge_prepare_reads_private_receipt_without_classification(self):
        user_id = '66d19b54000000001d03a93d'
        trusted = {
            'schema': 'xhs_workbuddy_trusted_evidence_v1',
            'receipt_id': 'test-receipt',
            'run_id': 'run-1',
            'stage': 'capture',
            'bindings': {},
            'artifacts': {},
        }
        stdout = io.StringIO()
        argv = [
            'workbuddy_bridge.py',
            'prepare',
            '--run-id', 'run-1',
            '--user-id', user_id,
            '--page-url', (
                f'https://www.xiaohongshu.com/user/profile/{user_id}?tab=fav'
            ),
            '--expected-url-substring', f'/user/profile/{user_id}',
            '--trusted-evidence-stdin',
            '--mcp-launch-fd', '3',
        ]
        with (
            patch.object(sys, 'argv', argv),
            patch.object(sys, 'stdin', io.StringIO(json.dumps({
                'trusted_evidence': trusted,
            }))),
            patch(
                'workbuddy_bridge.prepare_action',
                return_value={'ok': True},
            ) as action,
            patch('workbuddy_bridge.verify_mcp_launch_attestation') as launch,
            redirect_stdout(stdout),
        ):
            bridge_main()

        launch.assert_called_once()
        self.assertEqual(action.call_args.args[7], trusted)
        self.assertIsNone(action.call_args.args[5])

    def test_direct_prepare_cli_without_private_launch_fd_is_rejected(self):
        user_id = '66d19b54000000001d03a93d'
        command = [
            sys.executable,
            str(ROOT / 'scripts' / 'workbuddy_bridge.py'),
            'prepare',
            '--run-id', 'run-1',
            '--user-id', user_id,
            '--page-url', (
                f'https://www.xiaohongshu.com/user/profile/{user_id}?tab=fav'
            ),
            '--expected-url-substring', f'/user/profile/{user_id}',
            '--trusted-evidence-stdin',
        ]
        result = subprocess.run(
            command,
            input=json.dumps({
                'trusted_evidence': {
                    'schema': 'xhs_workbuddy_trusted_evidence_v1',
                    'receipt_id': 'forged',
                    'run_id': 'run-1',
                    'stage': 'capture',
                    'bindings': {},
                    'artifacts': {},
                },
            }),
            text=True,
            capture_output=True,
            env=self.workbuddy_env(Path(tempfile.gettempdir()) / 'xhs-test'),
            close_fds=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mcp_launch_attestation_fd_missing', result.stdout)

    def test_private_launch_fd_signature_binds_action_args_and_evidence(self):
        key = b'k' * 32
        args = ['--run-id', 'run-1', '--mcp-launch-fd', '3']
        trusted = {
            'schema': 'xhs_workbuddy_trusted_evidence_v1',
            'receipt_id': 'receipt-1',
        }
        nonce = 'A' * 24
        basis = {
            'schema': 'xhs_workbuddy_launch_attestation_v1',
            'nonce': nonce,
            'action': 'prepare',
            'args': args,
            'trusted_evidence': trusted,
        }
        signature = base64.urlsafe_b64encode(
            hmac.new(
                key,
                canonical_json(basis).encode('utf-8'),
                hashlib.sha256,
            ).digest()
        ).decode('ascii').rstrip('=')
        payload = {
            'trusted_evidence': trusted,
            'launch_attestation': {
                'schema': 'xhs_workbuddy_launch_attestation_v1',
                'nonce': nonce,
                'signature': signature,
            },
        }
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, key)
        finally:
            os.close(write_fd)

        verify_mcp_launch_attestation(
            'prepare',
            args,
            payload,
            key_fd=read_fd,
        )

    def test_bridge_error_redacts_json_and_percent_encoded_credentials(self):
        secret_error = RuntimeError(
            '{"xsec_token":"json-secret","authorization":"bearer-secret"} '
            'xsec_token%3Dencoded-secret%26next%3D1'
        )
        stdout = io.StringIO()
        argv = [
            'workbuddy_bridge.py',
            'capture',
            '--source', 'collection',
            '--page-url', (
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav'
            ),
            '--organizing-depth', 'light',
        ]
        with (
            patch.object(sys, 'argv', argv),
            patch('workbuddy_bridge.capture_action', side_effect=secret_error),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit),
        ):
            bridge_main()

        payload = stdout.getvalue()
        self.assertNotIn('json-secret', payload)
        self.assertNotIn('bearer-secret', payload)
        self.assertNotIn('encoded-secret', payload)
        self.assertIn('<redacted>', payload)

    def test_detail_login_required_marks_safety_halt_and_closes_same_context(self):
        note_id = '66d19b54000000001d03a93d'
        target_url = (
            'https://www.xiaohongshu.com/user/profile/'
            '66d19b54000000001d03a93d?tab=fav&subTab=note'
        )

        class LoginPage:
            def __init__(self):
                self.closed = False

            def goto(self, _url, **_kwargs):
                return None

            def evaluate(self, _script, requested_id):
                return {
                    'location': f'https://www.xiaohongshu.com/explore/{requested_id}',
                    'title': '登录',
                    'loginRequired': True,
                    'securityMarker': '',
                    'stateSource': '',
                    'noteData': None,
                }

            def close(self):
                self.closed = True

        detail_page = LoginPage()

        class FakeRunner:
            def __init__(self, *_args):
                self.closed = False
                self.context = types.SimpleNamespace(new_page=lambda: detail_page)

            def run_javascript(self, _script):
                return 'ok'

            def close(self):
                self.closed = True

        runner_holder = {}

        def make_runner(*args):
            runner = FakeRunner(*args)
            runner_holder['runner'] = runner
            return runner

        def fake_capture(
            _js_eval,
            directory,
            _source,
            _batch_size,
            _pause_minutes,
            safety,
            detail_href_sink,
            *,
            expected_page_url,
        ):
            self.assertEqual(expected_page_url, target_url)
            rows = [{
                'id': note_id,
                'title': '需要登录的图文',
                'content_type': 'image',
            }]
            self.write_capture_contract(directory, rows)
            detail_href_sink[note_id] = (
                f'https://www.xiaohongshu.com/explore/{note_id}'
                '?xsec_token=login-secret'
            )
            return {
                'count': 1,
                'output': str(directory / 'visible_items.json'),
                'crawl_complete': True,
                'ready_for_classification': True,
                'blockers': [],
                'safety_state': str(safety),
            }

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.BrowserRunner', side_effect=make_runner),
                patch('workbuddy_bridge.capture_workbuddy_groups', side_effect=fake_capture),
                patch('workbuddy_bridge.run_command') as command,
                patch('workbuddy_bridge.wait_for_profile_release'),
            ):
                with self.assertRaisesRegex(SafetyHaltedError, 'login_required'):
                    capture_action(
                        'image-ocr-login-halt',
                        'collection',
                        target_url,
                        200,
                        3,
                        'light',
                    )
            run_dir = data_dir / 'runs' / 'image-ocr-login-halt'
            state = load_safety_state(run_dir / 'xhs_safety_state.json')
            persisted = '\n'.join(
                path.read_text(encoding='utf-8')
                for path in run_dir.glob('*.json')
            )

        command.assert_not_called()
        self.assertTrue(detail_page.closed)
        self.assertTrue(runner_holder['runner'].closed)
        self.assertEqual(state['state'], 'security_halted')
        self.assertEqual(state['halt']['reason_code'], 'login_required')
        self.assertNotIn('login-secret', persisted)
        self.assertNotIn('xsec_token', persisted)

    def test_detail_enrichment_runs_all_candidates_in_200_bounded_groups(self):
        rows = [
            {
                'id': f'{index + 1:024x}',
                'title': f'图文 {index + 1}',
                'content_type': 'image',
            }
            for index in range(3)
        ]

        class DetailPage:
            def __init__(self):
                self.closed = False

            def goto(self, _url, **_kwargs):
                return None

            def evaluate(self, _script, note_id):
                return {
                    'location': f'https://www.xiaohongshu.com/explore/{note_id}',
                    'title': '图文详情',
                    'loginRequired': False,
                    'securityMarker': '',
                    'stateSource': 'setup_server_state',
                    'noteData': {
                        'noteId': note_id,
                        'type': 'normal',
                        'imageList': [
                            f'https://ci.xiaohongshu.com/{note_id}.jpg',
                        ],
                    },
                }

            def close(self):
                self.closed = True

        detail_page = DetailPage()

        class SameContextRunner:
            context = types.SimpleNamespace(new_page=lambda: detail_page)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with (
                patch('workbuddy_bridge.time.sleep') as sleep,
                patch(
                    'workbuddy_bridge.download_workbuddy_authenticated_images',
                    side_effect=self.fake_authenticated_download,
                ),
            ):
                result = enrich_workbuddy_image_items(
                    SameContextRunner(),
                    rows,
                    {
                        row['id']: f'https://www.xiaohongshu.com/explore/{row["id"]}'
                        for row in rows
                    },
                    2,
                    3,
                    directory / 'image_items.json',
                    directory / 'xhs_safety_state.json',
                    request_interval=0,
                )
                image_items = json.loads(
                    (directory / 'image_items.json').read_text(encoding='utf-8')
                )

        self.assertTrue(detail_page.closed)
        self.assertTrue(result['ready_for_ocr'])
        self.assertEqual(result['requested'], 3)
        self.assertEqual(result['succeeded'], 3)
        self.assertEqual(result['detail_group_count'], 2)
        self.assertEqual([call.args for call in sleep.call_args_list], [(180,)])
        self.assertTrue(all(row['image_urls_complete'] for row in image_items))
        self.assertTrue(all(
            row['image_list_source']
            == 'workbuddy_authenticated_frontend.noteData.imageList.local_copy'
            for row in image_items
        ))
        self.assertTrue(all(
            row['content_type_source']
            == 'workbuddy_authenticated_frontend.noteData.type'
            for row in image_items
        ))

    def test_observed_video_is_still_opened_and_can_be_confirmed_as_image(self):
        note_id = '66d19b54000000001d03a93d'

        class DetailPage:
            def __init__(self):
                self.goto_count = 0

            def goto(self, _url, **_kwargs):
                self.goto_count += 1

            def evaluate(self, _script, requested_id):
                return {
                    'location': f'https://www.xiaohongshu.com/explore/{requested_id}',
                    'title': '真实图文详情',
                    'loginRequired': False,
                    'securityMarker': '',
                    'stateSource': 'setup_server_state',
                    'noteData': {
                        'noteId': requested_id,
                        'type': 'normal',
                        'imageList': ['https://ci.xiaohongshu.com/real-image.jpg'],
                    },
                }

            def close(self):
                return None

        detail_page = DetailPage()
        runner = types.SimpleNamespace(
            context=types.SimpleNamespace(new_page=lambda: detail_page),
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch(
                'workbuddy_bridge.download_workbuddy_authenticated_images',
                side_effect=self.fake_authenticated_download,
            ):
                result = enrich_workbuddy_image_items(
                    runner,
                    [{
                        'id': note_id,
                        'title': '列表误标视频',
                        'content_type': 'video',
                    }],
                    {
                        note_id: f'https://www.xiaohongshu.com/explore/{note_id}',
                    },
                    200,
                    3,
                    directory / 'image_items.json',
                    directory / 'xhs_safety_state.json',
                    request_interval=0,
                )
            rows = json.loads(
                (directory / 'image_items.json').read_text(encoding='utf-8')
            )

        self.assertEqual(detail_page.goto_count, 1)
        self.assertTrue(result['ready_for_ocr'])
        self.assertEqual(rows[0]['content_type'], 'image')
        self.assertTrue(rows[0]['image_urls_complete'])

    def test_incomplete_local_ocr_result_blocks_classification(self):
        note_id = '66d19b54000000001d03a93d'
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            image_items = directory / 'image_items.json'
            image_data = b'\x89PNG\r\n\x1a\n' + b'x' * 32
            image_hash = hashlib.sha256(image_data).hexdigest()
            image_path = directory / 'authenticated_images' / f'{note_id}-000.png'
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(image_data)
            image_items.write_text(json.dumps([{
                'id': note_id,
                'content_type': 'image',
                'image_files': [f'authenticated_images/{note_id}-000.png'],
                'image_file_sha256': [image_hash],
                'image_count': 1,
                'image_urls_complete': True,
                'image_enrichment_status': 'ok',
                'image_list_source': (
                    'workbuddy_authenticated_frontend.noteData.imageList.local_copy'
                ),
            }], ensure_ascii=False), encoding='utf-8')

            def fake_ocr(command, **_kwargs):
                Path(command[3]).write_text(json.dumps([{
                    'id': note_id,
                    'status': 'ok',
                    'image_set_complete': True,
                    'image_count_declared': 1,
                    'image_count_available': 1,
                    'image_count_processed': 1,
                    'ocr_run_fingerprint': 'b' * 64,
                    'images': [{'image_index': 0, 'status': 'error'}],
                }], ensure_ascii=False), encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '{}', '')

            with patch('workbuddy_bridge.run_command', side_effect=fake_ocr):
                result = run_workbuddy_ocr(directory, image_items)

        self.assertFalse(result['ready_for_classification'])
        self.assertEqual(result['ocr_ok'], 0)
        self.assertEqual(result['ocr_failed'], 1)
        self.assertEqual(result['blockers'], ['ocr_results_incomplete'])

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
            before = approval_digest(directory, report, 10)
            report['processed'][0]['target_board'] = '运动'
            after = approval_digest(directory, report, 10)
            self.assertNotEqual(before, after)

    def test_approval_digest_binds_confirmed_max_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, report = self.write_ready_plan(Path(tmp))
            approved_for_ten = approval_digest(directory, report, 10)
            raised_to_twenty = approval_digest(directory, report, 20)

        self.assertNotEqual(approved_for_ten, raised_to_twenty)

    def test_approval_basis_binds_snapshot_verify_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, report = self.write_ready_plan(Path(tmp))
            snapshot_path = directory / 'board_snapshot.json'
            snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
            snapshot['source']['verify_pages'] = 100
            snapshot_path.write_text(
                json.dumps(snapshot, ensure_ascii=False),
                encoding='utf-8',
            )

            basis = approval_basis(directory, report, 10, verify_pages=100)

        self.assertEqual(basis['verify_pages'], 100)

    def test_approval_digest_binds_authenticated_ocr_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory, report = self.write_ready_plan(data_dir)
            note_id = self.write_valid_ocr_capture_contract(directory)
            classification = json.loads(
                (directory / 'classification.json').read_text(encoding='utf-8')
            )
            classification[0]['id'] = note_id
            (directory / 'classification.json').write_text(
                json.dumps(classification, ensure_ascii=False),
                encoding='utf-8',
            )
            report['processed'][0]['id'] = note_id
            before = approval_digest(directory, report, 10)

            ocr_path = directory / 'ocr_results.json'
            ocr_rows = json.loads(ocr_path.read_text(encoding='utf-8'))
            ocr_rows[0]['ocr_text'] += '\n补充内容'
            ocr_path.write_text(
                json.dumps(ocr_rows, ensure_ascii=False),
                encoding='utf-8',
            )
            manifest_path = directory / 'crawl_manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['ocr_results_sha256'] = hashlib.sha256(
                ocr_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding='utf-8',
            )
            after = approval_digest(directory, report, 10)

        self.assertNotEqual(before, after)

    def test_visible_metadata_change_invalidates_capture_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, report = self.write_ready_plan(Path(tmp))
            approval_digest(directory, report, 10)
            visible_path = directory / 'visible_items.json'
            rows = json.loads(visible_path.read_text(encoding='utf-8'))
            rows[0]['title'] = '被修改的标题'
            visible_path.write_text(
                json.dumps(rows, ensure_ascii=False),
                encoding='utf-8',
            )
            with self.assertRaisesRegex(RuntimeError, '抓取完成时的证据不一致'):
                approval_digest(directory, report, 10)

    def test_ocr_entry_cannot_self_authorize_an_arbitrary_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_valid_ocr_capture_contract(directory)
            ocr_path = directory / 'ocr_results.json'
            rows = json.loads(ocr_path.read_text(encoding='utf-8'))
            rows[0]['ocr_run_fingerprint'] = 'f' * 64
            ocr_path.write_text(
                json.dumps(rows, ensure_ascii=False),
                encoding='utf-8',
            )
            manifest_path = directory / 'crawl_manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['ocr_expected_fingerprint'] = 'f' * 64
            manifest['ocr_results_sha256'] = hashlib.sha256(
                ocr_path.read_bytes()
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding='utf-8',
            )
            with self.assertRaisesRegex(RuntimeError, '运行指纹无效'):
                validate_workbuddy_capture_evidence(directory)

    def test_execute_rejects_account_different_from_bound_capture_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory, report = self.write_ready_plan(data_dir)
            digest = approval_digest(directory, report, 10)
            (directory / 'approval.json').write_text(json.dumps({
                'approval_digest': digest,
                'basis': approval_basis(directory, report, 10),
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'plan')
            other_user_id = '66d19b54000000001d03a93e'
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command') as command,
            ):
                with self.assertRaisesRegex(RuntimeError, 'trusted_evidence_binding_mismatch'):
                    execute_action(
                        'run-1',
                        other_user_id,
                        (
                            'https://www.xiaohongshu.com/user/profile/'
                            f'{other_user_id}?tab=fav'
                        ),
                        f'/user/profile/{other_user_id}',
                        digest,
                        10,
                        trusted_evidence=trusted,
                        _launch_capability=_MCP_EXECUTE_CAPABILITY,
                    )

        command.assert_not_called()

    def test_execute_rejects_before_browser_when_approval_digest_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory, report = self.write_ready_plan(data_dir)
            digest = approval_digest(directory, report, 10)
            (directory / 'approval.json').write_text(json.dumps({
                'approval_digest': digest,
                'basis': approval_basis(directory, report, 10),
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'plan')
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
                    with self.assertRaisesRegex(RuntimeError, 'trusted_evidence_changed'):
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
                            trusted_evidence=trusted,
                            _launch_capability=_MCP_EXECUTE_CAPABILITY,
                        )
                    run_command.assert_not_called()

    def test_execute_rejects_self_consistent_plan_tampering_before_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory, report = self.write_ready_plan(data_dir)
            original_digest = approval_digest(directory, report, 10)
            (directory / 'approval.json').write_text(json.dumps({
                'approval_digest': original_digest,
                'basis': approval_basis(directory, report, 10),
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'plan')

            classification = json.loads(
                (directory / 'classification.json').read_text(encoding='utf-8')
            )
            classification[0]['target_board'] = '运动'
            (directory / 'classification.json').write_text(
                json.dumps(classification, ensure_ascii=False),
                encoding='utf-8',
            )
            report['processed'][0]['target_board'] = '运动'
            (directory / 'run_report.json').write_text(
                json.dumps(report, ensure_ascii=False),
                encoding='utf-8',
            )
            forged_digest = approval_digest(directory, report, 10)
            (directory / 'approval.json').write_text(json.dumps({
                'approval_digest': forged_digest,
                'basis': approval_basis(directory, report, 10),
            }, ensure_ascii=False), encoding='utf-8')

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command') as run_command,
                patch(
                    'workbuddy_bridge.BrowserRunner',
                    side_effect=AssertionError('browser must not launch'),
                ) as browser,
            ):
                with self.assertRaisesRegex(RuntimeError, 'trusted_evidence_changed'):
                    execute_action(
                        'run-1',
                        '66d19b54000000001d03a93d',
                        (
                            'https://www.xiaohongshu.com/user/profile/'
                            '66d19b54000000001d03a93d?tab=fav'
                        ),
                        '/user/profile/66d19b54000000001d03a93d',
                        forged_digest,
                        10,
                        trusted_evidence=trusted,
                        _launch_capability=_MCP_EXECUTE_CAPABILITY,
                    )

            run_command.assert_not_called()
            browser.assert_not_called()

    def test_execute_rejects_max_moves_above_confirmed_limit_before_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory, report = self.write_ready_plan(data_dir)
            approved_digest = approval_digest(directory, report, 10)
            (directory / 'approval.json').write_text(json.dumps({
                'approval_digest': approved_digest,
                'basis': approval_basis(directory, report, 10),
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'plan')
            forged_higher_digest = approval_digest(directory, report, 20)
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command') as run_command,
            ):
                with self.assertRaisesRegex(RuntimeError, '移动上限'):
                    execute_action(
                        'run-1',
                        '66d19b54000000001d03a93d',
                        (
                            'https://www.xiaohongshu.com/user/profile/'
                            '66d19b54000000001d03a93d?tab=fav'
                        ),
                        '/user/profile/66d19b54000000001d03a93d',
                        forged_higher_digest,
                        20,
                        trusted_evidence=trusted,
                        _launch_capability=_MCP_EXECUTE_CAPABILITY,
                    )

            run_command.assert_not_called()

    def test_execute_rejects_verify_pages_changed_from_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory, report = self.write_ready_plan(data_dir)
            digest = approval_digest(directory, report, 10, verify_pages=100)
            (directory / 'approval.json').write_text(json.dumps({
                'approval_digest': digest,
                'basis': approval_basis(
                    directory,
                    report,
                    10,
                    verify_pages=100,
                ),
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'plan')
            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch('workbuddy_bridge.run_command') as run_command,
            ):
                with self.assertRaisesRegex(RuntimeError, 'verify_pages'):
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
                        trusted_evidence=trusted,
                        verify_pages=99,
                        _launch_capability=_MCP_EXECUTE_CAPABILITY,
                    )

            run_command.assert_not_called()

    def test_execute_waits_for_commit_after_local_preflight_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            directory, report = self.write_ready_plan(data_dir)
            digest = approval_digest(directory, report, 10)
            (directory / 'approval.json').write_text(json.dumps({
                'approval_digest': digest,
                'basis': approval_basis(directory, report, 10),
            }, ensure_ascii=False), encoding='utf-8')
            trusted = self.trusted_evidence(directory, 'plan')
            events = []

            def profile_preflight():
                events.append('profile_checked')

            def commit_barrier():
                self.assertEqual(events, ['profile_checked', 'live_binding_checked'])
                events.append('receipt_committed')

            def execute_in_memory(_items, execution_report, args, report_path, commit_callback):
                (directory / 'classification.json').write_text(
                    json.dumps([{
                        'id': '66d19b54000000001d03a93d',
                        'target_board': '篡改目标',
                        'confidence': 'high',
                    }], ensure_ascii=False),
                    encoding='utf-8',
                )
                self.assertEqual(_items[0]['target_board'], '旅行')
                events.append('live_binding_checked')
                commit_callback()
                self.assertEqual(
                    events,
                    ['profile_checked', 'live_binding_checked', 'receipt_committed'],
                )
                self.assertEqual(args.verify_pages, 100)
                events.append('write_started')
                execution_report['session_status'] = 'completed'
                execution_report['processed'] = []
                execution_report['errors'] = []
                Path(report_path).write_text(
                    json.dumps(execution_report, ensure_ascii=False),
                    encoding='utf-8',
                )

            with (
                patch.dict(os.environ, self.workbuddy_env(data_dir), clear=True),
                patch(
                    'workbuddy_bridge.require_profile_available',
                    side_effect=profile_preflight,
                ),
                patch(
                    'workbuddy_bridge.apply_batch',
                    side_effect=execute_in_memory,
                ),
                patch(
                    'workbuddy_bridge.await_mcp_execute_commit',
                    side_effect=commit_barrier,
                ),
            ):
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
                    trusted_evidence=trusted,
                    _launch_capability=_MCP_EXECUTE_CAPABILITY,
                )

            self.assertEqual(
                events,
                [
                    'profile_checked',
                    'live_binding_checked',
                    'receipt_committed',
                    'write_started',
                ],
            )

    def test_planned_board_creation_uses_same_runner_and_records_verified_result(self):
        runner = types.SimpleNamespace(
            run_javascript=lambda _job: 'xhs_skill_123_456',
        )
        args = types.SimpleNamespace(
            user_id='66d19b54000000001d03a93d',
            verify_pages=100,
            timeout_sec=120,
            expected_url_substring=(
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav'
            ),
        )
        report = {}
        with (
            patch('workbuddy_bridge.validate_write_live_binding') as binding,
            patch('workbuddy_bridge.poll_browser_job', return_value={
                'status': 'created',
                'writePerformed': True,
                'board': {
                    'id': 'a' * 24,
                    'name': '阅读',
                    'privacy': 1,
                },
                'emptyBoardVerified': True,
            }),
        ):
            execute_planned_board_creations(
                runner,
                [{'name': '阅读', 'privacy': 1}],
                args,
                report,
            )

        self.assertEqual(binding.call_count, 2)
        self.assertEqual(report['board_creations'], [{
            'name': '阅读',
            'privacy': 1,
            'status': 'created',
            'board': {'id': 'a' * 24, 'name': '阅读', 'privacy': 1},
            'empty_board_verified': True,
            'write_performed': True,
        }])

    def test_planned_board_batch_marks_partial_creation_as_uncertain(self):
        runner = types.SimpleNamespace(
            run_javascript=lambda _job: 'xhs_skill_123_456'
        )
        args = types.SimpleNamespace(
            user_id='66d19b54000000001d03a93d',
            verify_pages=100,
            timeout_sec=120,
            expected_url_substring=(
                'https://www.xiaohongshu.com/user/profile/'
                '66d19b54000000001d03a93d?tab=fav'
            ),
        )
        first = {
            'status': 'created',
            'writePerformed': True,
            'board': {'id': 'a' * 24, 'name': '阅读', 'privacy': 1},
            'emptyBoardVerified': True,
        }
        with (
            patch('workbuddy_bridge.validate_write_live_binding'),
            patch(
                'workbuddy_bridge.poll_browser_job',
                side_effect=[first, RuntimeError('second board preflight failed')],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, 'HIGH_RISK_STATE_UNCERTAIN'):
                execute_planned_board_creations(
                    runner,
                    [
                        {'name': '阅读', 'privacy': 1},
                        {'name': '旅行', 'privacy': 1},
                    ],
                    args,
                    {},
                )


if __name__ == '__main__':
    unittest.main()
