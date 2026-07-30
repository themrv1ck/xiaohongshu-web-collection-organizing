#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from run_reassign_batch import BOARD_TRANSACTION_JS, BOARD_VERIFICATION_JS, BrowserRunner, LIVE_API_RESOLVER_JS, build_browser_job, choose_backend, execute_batch, filter_classification_for_resume, merge_report_chunk, parse_browser_job_id, parse_js_json, poll_browser_job  # noqa: E402
from extract_visible_items import arc_js_macos, extract_with_js  # noqa: E402
from xhs_ocr_common import detect_ocr_provider, infer_board, load_taxonomy, run_tesseract_ocr  # noqa: E402


class CoreScriptTests(unittest.TestCase):
    TRANSACTION_MODEL_JS = r'''
function createTransactionModel(options) {
  options = options || {};
  const noteId = 'note-1';
  const sourceBoardId = 'source-board';
  const targetBoardId = 'target-board';
  const source = new Set(options.sourceNoteIds === undefined ? [noteId] : options.sourceNoteIds);
  const target = new Set(options.targetNoteIds || []);
  const calls = [];
  const writes = [];
  let collected = true;
  let b1CallCount = 0;

  function notesFor(boardId) {
    return boardId === sourceBoardId ? source : target;
  }

  const api = {
    U_: async function(request) {
      const boardId = request.resourceParams.boardId;
      calls.push({method: 'U_', boardId});
      return {id: boardId, total: notesFor(boardId).size};
    },
    Ks: async function(request) {
      const boardId = request.params.boardId;
      calls.push({method: 'Ks', boardId});
      return {
        notes: Array.from(notesFor(boardId), function(id) { return {noteId: id}; }),
        cursor: '',
        hasMore: false
      };
    },
    LN: async function(payload) {
      calls.push({method: 'LN', payload});
      writes.push({method: 'LN', payload});
      collected = false;
      source.delete(payload.noteIds);
      target.delete(payload.noteIds);
      if (options.lnFailure) throw new Error(options.lnFailure);
      return {};
    },
    B1: async function(payload) {
      calls.push({method: 'B1', payload});
      writes.push({method: 'B1', payload});
      b1CallCount += 1;
      if (b1CallCount <= Number(options.b1FailureCount || 0)) {
        throw new Error(options.b1Failure || 'collect failed');
      }
      collected = true;
      return {};
    },
    d0: async function(payload) {
      calls.push({method: 'd0', payload});
      writes.push({method: 'd0', payload});
      if (payload.targetBoardId === targetBoardId) {
        if (options.targetMoveFailure) throw new Error(options.targetMoveFailure);
        if (!options.targetMoveNoop && collected) {
          source.delete(payload.notesId);
          target.add(payload.notesId);
        }
      } else if (payload.targetBoardId === sourceBoardId) {
        if (options.sourceMoveFailure) throw new Error(options.sourceMoveFailure);
        if (!options.sourceMoveNoop && collected) {
          target.delete(payload.notesId);
          source.add(payload.notesId);
        }
      }
      return {};
    }
  };

  return {
    api, calls, writes, source, target,
    noteId, sourceBoardId, targetBoardId
  };
}
'''

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / args[0]), *args[1:]],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

    def run_live_api_resolver_js(self, scenario):
        proc = subprocess.run(
            ['node', '-e', LIVE_API_RESOLVER_JS + '\n' + scenario],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def run_board_verification_js(self, scenario):
        proc = subprocess.run(
            ['node', '-e', BOARD_VERIFICATION_JS + '\n' + scenario],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def run_board_transaction_js(self, scenario):
        proc = subprocess.run(
            [
                'node', '-e',
                BOARD_VERIFICATION_JS + '\n' + BOARD_TRANSACTION_JS + '\n' +
                self.TRANSACTION_MODEL_JS + '\n' + scenario,
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def test_default_taxonomy_and_classifier(self):
        boards = load_taxonomy(None)
        item = {
            'id': '66d19b54000000001d03a93d',
            'title': '滑雪换刃练习',
            'desc': '',
            'tags': ['滑雪'],
            'user': '',
            'card_text': '滑雪 单板 换刃',
        }
        board, confidence, reason, review_state = infer_board(item, None, boards)
        self.assertEqual(board, '滑雪')
        self.assertIn(confidence, {'medium', 'high'})
        self.assertTrue(reason)
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

    def test_dry_run_report_and_retry_queue(self):
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
            self.assertEqual(data['mode'], 'dry_run')
            self.assertEqual(data['processed'][0]['status'], 'planned')
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
            self.assertEqual(data['excluded_note_ids'], ['note-1'])
            self.assertEqual(data['note_to_board'], {'note-1': '滑雪'})
            self.assertIn('generated_at', data)

    def test_classify_excludes_existing_board_items(self):
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
                'excluded_note_ids': ['note-1'],
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
            self.assertTrue(data[0]['excluded'])
            self.assertEqual(data[0]['exclude_reason'], 'user_kept_existing_boards')
            self.assertEqual(data[0]['source_board'], '滑雪')
            self.assertEqual(data[0]['target_board'], '')
            self.assertNotIn('excluded', data[1])

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
                    'exclude_reason': 'user_kept_existing_boards',
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
                    {'id': 'note-3', 'title': '跳过项', 'target_board': '', 'status': 'skipped', 'error': 'user_kept_existing_boards'},
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
            self.assertTrue(data['action_required'])

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
        self.assertEqual(report['missing_boards'], ['滑雪'])
        self.assertEqual(report['board_counts_before'], {'滑雪': 1})
        self.assertEqual(report['board_counts_after'], {'滑雪': 1})
        self.assertEqual(report['board_count_checks'], {
            '滑雪': {'declared_total': 2, 'accessible_total': 1, 'count_mismatch': True, 'page_count': 1},
        })

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
            self.assertEqual(runner.eval('1 + 1'), 'ok')
            mocked.assert_called_once_with(
                '1 + 1',
                tab_marker='xhs-skill-worker-test',
                window_id='window-test',
                tab_id='tab-test',
                expected_url_substring='/user/profile/test',
            )

    def test_arc_transport_counts_strict_id_and_url_matches_then_wraps_runtime_marker(self):
        captured = {}

        def fake_osascript(script):
            captured['script'] = script
            js_path = script.split('POSIX file "', 1)[1].split('"', 1)[0]
            captured['js_source'] = Path(js_path).read_text(encoding='utf-8')
            return 'ok'

        with patch('extract_visible_items.require_macos_app_running'), patch('extract_visible_items.osascript', fake_osascript):
            self.assertEqual(arc_js_macos(
                'document.title',
                'xhs-skill-worker-test',
                'window-test',
                'tab-test',
                '/user/profile/test',
            ), 'ok')

        script = captured['script']
        self.assertIn('set matchCount to 0', script)
        self.assertIn('set matchCount to matchCount + 1', script)
        self.assertIn('currentWindowId is equal to expectedWindowId', script)
        self.assertIn('currentTabId is equal to expectedTabId', script)
        self.assertIn('(targetURL contains "xiaohongshu.com")', script)
        self.assertIn('(targetURL contains expectedURLPart)', script)
        self.assertNotIn('targetMarker', script)
        self.assertIn('if matchCount is 0 then error', script)
        self.assertIn('if matchCount is greater than 1 then error', script)
        self.assertNotIn('if targetTab is not missing value then exit repeat', script)
        js_source = captured['js_source']
        self.assertIn('window.name !== "xhs-skill-worker-test"', js_source)
        self.assertLess(js_source.index('window.name !=='), js_source.index('document.title'))

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
                execute_batch([], {}, args, Path(tmp) / 'report.json')
        runner.assert_not_called()

    def test_execute_batch_waits_fixed_delay_between_items(self):
        args = type('Args', (), {
            'browser': 'safari',
            'arc_tab_marker': '',
            'inter_item_delay_sec': 2.5,
            'max_moves_per_session': 2,
            'allow_low_confidence': False,
            'verify_pages': 1,
            'user_id': '',
            'timeout_sec': 10,
        })()
        runner = type('Runner', (), {
            'eval': lambda self, js: 'xhs_skill_123_456',
            'close': lambda self: None,
        })()
        report = {'processed': [], 'errors': [], 'missing_boards': [], 'board_counts_before': {}, 'board_counts_after': {}}
        classification = [
            {'id': 'note-1', 'title': '一', 'target_board': '滑雪', 'confidence': 'high'},
            {'id': 'note-2', 'title': '二', 'target_board': '滑雪', 'confidence': 'high'},
        ]
        result = {'processed': [], 'errors': [], 'missing_boards': [], 'board_counts_before': {}, 'board_counts_after': {}}
        with tempfile.TemporaryDirectory() as tmp, \
                patch('run_reassign_batch.BrowserRunner', return_value=runner), \
                patch('run_reassign_batch.poll_browser_job', return_value=result), \
                patch('run_reassign_batch.time.sleep') as sleep:
            execute_batch(classification, report, args, Path(tmp) / 'report.json')
        sleep.assert_called_once_with(2.5)

    def test_execute_batch_persists_first_error_then_stops_before_next_item(self):
        args = type('Args', (), {
            'browser': 'safari',
            'arc_tab_marker': '',
            'inter_item_delay_sec': 0,
            'max_moves_per_session': 2,
            'allow_low_confidence': False,
            'verify_pages': 10,
            'user_id': '',
            'timeout_sec': 10,
        })()
        calls = {'eval': 0, 'closed': False}

        class Runner:
            def eval(self, js):
                calls['eval'] += 1
                return 'xhs_skill_123_456'

            def close(self):
                calls['closed'] = True

        failed_row = {
            'id': 'note-1', 'title': '一', 'target_board': '滑雪',
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
            {'id': 'note-1', 'title': '一', 'target_board': '滑雪', 'confidence': 'high'},
            {'id': 'note-2', 'title': '二', 'target_board': '滑雪', 'confidence': 'high'},
        ]
        with tempfile.TemporaryDirectory() as tmp, \
                patch('run_reassign_batch.BrowserRunner', return_value=Runner()), \
                patch('run_reassign_batch.poll_browser_job', return_value=result) as poll:
            report_path = Path(tmp) / 'report.json'
            with self.assertRaisesRegex(RuntimeError, '已先写入报告'):
                execute_batch(classification, report, args, report_path)
            persisted = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertEqual(calls['eval'], 1)
        self.assertEqual(poll.call_count, 1)
        self.assertTrue(calls['closed'])
        self.assertEqual(persisted['processed'], [failed_row])
        self.assertEqual(persisted['errors'], [failed_row])

    def test_browser_job_checks_security_immediately_before_each_write_and_rethrows(self):
        args = type('Args', (), {
            'allow_low_confidence': False,
            'verify_pages': 1,
            'user_id': '',
        })()
        job = build_browser_job([
            {'id': 'note-1', 'title': '一', 'target_board': '滑雪', 'confidence': 'high'},
        ], args)
        self.assertIn('class SecurityChallengeError extends Error', job)
        self.assertIn('class ExecutePageBindingError extends Error', job)
        self.assertIn(
            "assertNoSecurityChallenge();\n          assertExpectedExecutePage();\n          await api.d0",
            job,
        )
        self.assertIn("Boolean(item.source_board_id) &&\n          item.source_board_id !== board.id", job)
        self.assertIn("assertTransactionSafe, null, events, 'initial recollect could not start safely'", job)
        self.assertIn("assertTransactionSafe, null, events, 'target move could not start safely'", job)
        self.assertIn("error.name === 'HighRiskStateUncertainError'", job)
        self.assertIn("error.name === 'SecurityChallengeError' || error.name === 'ExecutePageBindingError'", job)
        self.assertIn('SAFETY_BREAKER:', job)

    def test_live_api_resolver_accepts_one_exact_factory_and_renamed_exports(self):
        result = self.run_live_api_resolver_js(r'''
function strictFactory(module, exports, req) {
  function uncollect(payload) { return req.http.post("/api/sns/web/v1/note/uncollect", payload); }
  function collect(payload) { return req.http.post("/api/sns/web/v1/note/collect", payload); }
  function move(payload) { return req.http.post("/api/sns/web/v1/note/move", payload); }
  function boardNotes(params) { return req.http.get("/api/sns/web/v1/board/note", params); }
  function userBoards(params) { return req.http.get("/api/sns/web/v1/board/user", params); }
  function boardDetail(params) { return req.http.get("/api/sns/web/v1/board/{boardId}", params); }
  exports.renamedUncollect = uncollect;
  exports.renamedCollect = collect;
  exports.renamedMove = move;
  exports.renamedBoardNotes = boardNotes;
  exports.renamedUserBoards = userBoards;
  exports.renamedBoardDetail = boardDetail;
}
const factories = {currentBuildModule: strictFactory};
function req(id) {
  const module = {exports: {}};
  factories[id](module, module.exports, req);
  return module.exports;
}
req.m = factories;
req.c = {};
const api = findApi(req);
console.log(JSON.stringify({LN: api.LN.name, B1: api.B1.name, d0: api.d0.name, Ks: api.Ks.name, yC: api.yC.name, U_: api.U_.name}));
''')
        self.assertEqual(result, {
            'LN': 'uncollect', 'B1': 'collect', 'd0': 'move',
            'Ks': 'boardNotes', 'yC': 'userBoards', 'U_': 'boardDetail',
        })

    def test_live_api_resolver_rejects_zero_factory_matches_without_legacy_cache_fallback(self):
        result = self.run_live_api_resolver_js(r'''
const factories = {unrelated: function(module, exports) { exports.value = 1; }};
function req(id) { return factories[id]({exports: {}}, {}, req); }
req.m = factories;
req.c = {
  40122: {exports: {LN: function LN() {}, B1: function B1() {}, d0: function d0() {}, Ks: function Ks() {}, yC: function yC() {}, U_: function U_() {}}}
};
try {
  findApi(req);
  console.log(JSON.stringify({error: ''}));
} catch (error) {
  console.log(JSON.stringify({error: error.message}));
}
''')
        self.assertIn('factory match count must be 1; found 0', result['error'])
        self.assertNotIn('req.c', LIVE_API_RESOLVER_JS)
        self.assertNotIn('40122', LIVE_API_RESOLVER_JS)

    def test_live_api_resolver_rejects_multiple_factory_matches_before_require(self):
        result = self.run_live_api_resolver_js(r'''
function strictFactory(module, exports, req) {
  function uncollect(payload) { return req.http.post("/api/sns/web/v1/note/uncollect", payload); }
  function collect(payload) { return req.http.post("/api/sns/web/v1/note/collect", payload); }
  function move(payload) { return req.http.post("/api/sns/web/v1/note/move", payload); }
  function boardNotes(params) { return req.http.get("/api/sns/web/v1/board/note", params); }
  function userBoards(params) { return req.http.get("/api/sns/web/v1/board/user", params); }
  function boardDetail(params) { return req.http.get("/api/sns/web/v1/board/{boardId}", params); }
  exports.ln = uncollect; exports.b1 = collect;
  exports.a = move; exports.b = boardNotes; exports.c = userBoards; exports.d = boardDetail;
}
const factories = {first: strictFactory, second: strictFactory};
let requireCalls = 0;
function req(id) { requireCalls += 1; return {}; }
req.m = factories;
try {
  findApi(req);
  console.log(JSON.stringify({error: '', requireCalls}));
} catch (error) {
  console.log(JSON.stringify({error: error.message, requireCalls}));
}
''')
        self.assertIn('factory match count must be 1; found 2', result['error'])
        self.assertEqual(result['requireCalls'], 0)

    def test_live_api_resolver_rejects_zero_or_multiple_matching_export_functions(self):
        result = self.run_live_api_resolver_js(r'''
function missingExportFactory(module, exports, req) {
  function uncollect(payload) { return req.http.post("/api/sns/web/v1/note/uncollect", payload); }
  function collect(payload) { return req.http.post("/api/sns/web/v1/note/collect", payload); }
  function move(payload) { return req.http.post("/api/sns/web/v1/note/move", payload); }
  function boardNotes(params) { return req.http.get("/api/sns/web/v1/board/note", params); }
  function userBoards(params) { return req.http.get("/api/sns/web/v1/board/user", params); }
  function boardDetail(params) { return req.http.get("/api/sns/web/v1/board/{boardId}", params); }
  exports.ln = uncollect; exports.b1 = collect;
  exports.a = move; exports.b = boardNotes; exports.d = boardDetail;
}
function duplicateExportFactory(module, exports, req) {
  function uncollect(payload) { return req.http.post("/api/sns/web/v1/note/uncollect", payload); }
  function collect(payload) { return req.http.post("/api/sns/web/v1/note/collect", payload); }
  function move(payload) { return req.http.post("/api/sns/web/v1/note/move", payload); }
  function moveAgain(payload) { return req.http.post("/api/sns/web/v1/note/move", payload); }
  function boardNotes(params) { return req.http.get("/api/sns/web/v1/board/note", params); }
  function userBoards(params) { return req.http.get("/api/sns/web/v1/board/user", params); }
  function boardDetail(params) { return req.http.get("/api/sns/web/v1/board/{boardId}", params); }
  exports.ln = uncollect; exports.b1 = collect;
  exports.a = move; exports.a2 = moveAgain; exports.b = boardNotes; exports.c = userBoards; exports.d = boardDetail;
}
function resolve(factory) {
  const factories = {only: factory};
  function req(id) {
    const module = {exports: {}};
    factories[id](module, module.exports, req);
    return module.exports;
  }
  req.m = factories;
  try { findApi(req); return ''; } catch (error) { return error.message; }
}
console.log(JSON.stringify({zero: resolve(missingExportFactory), multiple: resolve(duplicateExportFactory)}));
''')
        self.assertIn('yC export match count must be 1; found 0', result['zero'])
        self.assertIn('d0 export match count must be 1; found 2', result['multiple'])

    def test_board_snapshot_uses_direct_contract_and_reads_every_page_with_exact_ui_params(self):
        result = self.run_board_verification_js(r'''
(async function() {
  const calls = [];
  const api = {
    U_: async function(options) {
      calls.push({method: 'U_', options});
      return {id: 'board-1', total: 3};
    },
    Ks: async function(options) {
      calls.push({method: 'Ks', options});
      if (options.params.cursor === '') {
        return {notes: [{noteId: 'note-1'}, {noteId: 'note-2'}], cursor: 'note-2', hasMore: true};
      }
      return {notes: [{noteId: 'note-3'}], cursor: '', hasMore: false};
    }
  };
  const snapshot = await boardSnapshot(api, 'board-1', 10);
  console.log(JSON.stringify({snapshot, calls}));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertEqual(result['snapshot'], {
            'noteIds': ['note-1', 'note-2', 'note-3'],
            'declaredTotal': 3,
            'accessibleTotal': 3,
            'countMismatch': False,
            'pageCount': 2,
        })
        self.assertEqual(result['calls'], [
            {
                'method': 'U_',
                'options': {
                    'params': {'imageFormats': 'jpg,webp,avif'},
                    'resourceParams': {'boardId': 'board-1'},
                },
            },
            {
                'method': 'Ks',
                'options': {
                    'params': {
                        'boardId': 'board-1', 'num': 30, 'cursor': '',
                        'imageFormats': 'jpg,webp,avif',
                    },
                },
            },
            {
                'method': 'Ks',
                'options': {
                    'params': {
                        'boardId': 'board-1', 'num': 30, 'cursor': 'note-2',
                        'imageFormats': 'jpg,webp,avif',
                    },
                },
            },
        ])

    def test_board_snapshot_rejects_empty_or_repeated_cursor_while_has_more(self):
        result = self.run_board_verification_js(r'''
async function failureFor(pages) {
  let index = 0;
  const api = {
    U_: async function() { return {id: 'board-1', total: 0}; },
    Ks: async function() { return pages[index++]; }
  };
  try { await boardSnapshot(api, 'board-1', 10); return ''; }
  catch (error) { return error.message; }
}
(async function() {
  const empty = await failureFor([{notes: [], cursor: '', hasMore: true}]);
  const repeated = await failureFor([
    {notes: [], cursor: 'cursor-1', hasMore: true},
    {notes: [], cursor: 'cursor-1', hasMore: true}
  ]);
  console.log(JSON.stringify({empty, repeated}));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertIn('hasMore=true with an empty cursor', result['empty'])
        self.assertIn('hasMore=true with a repeated cursor', result['repeated'])

    def test_board_snapshot_rejects_wrapped_payload_and_incomplete_budget_but_records_count_mismatch(self):
        result = self.run_board_verification_js(r'''
async function run(api, maxPages) {
  try { await boardSnapshot(api, 'board-1', maxPages); return ''; }
  catch (error) { return error.message; }
}
(async function() {
  const wrapped = await run({
    U_: async function() { return {id: 'board-1', total: 1}; },
    Ks: async function() { return {data: {notes: [{noteId: 'note-1'}], cursor: '', hasMore: false}}; }
  }, 10);
  const wrongNoteId = await run({
    U_: async function() { return {id: 'board-1', total: 1}; },
    Ks: async function() { return {notes: [{id: 'note-1'}], cursor: '', hasMore: false}; }
  }, 10);
  const mismatch = await boardSnapshot({
    U_: async function() { return {id: 'board-1', total: 2}; },
    Ks: async function() { return {notes: [{noteId: 'note-1'}], cursor: '', hasMore: false}; }
  }, 'board-1', 10);
  const incomplete = await run({
    U_: async function() { return {id: 'board-1', total: 2}; },
    Ks: async function() { return {notes: [{noteId: 'note-1'}], cursor: 'note-1', hasMore: true}; }
  }, 1);
  console.log(JSON.stringify({wrapped, wrongNoteId, mismatch, incomplete}));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertIn('response.notes must be an array', result['wrapped'])
        self.assertIn('notes[0].noteId must be a non-empty string', result['wrongNoteId'])
        self.assertEqual(result['mismatch'], {
            'noteIds': ['note-1'],
            'declaredTotal': 2,
            'accessibleTotal': 1,
            'countMismatch': True,
            'pageCount': 1,
        })
        self.assertIn('exceeded maxPages before completion', result['incomplete'])

    def test_cross_board_transaction_succeeds_with_adjacent_uncollect_and_recollect(self):
        result = self.run_board_transaction_js(r'''
(async function() {
  const model = createTransactionModel();
  const events = [];
  const transaction = await moveAcrossBoardsTransaction(
    model.api, model.noteId, model.sourceBoardId, model.targetBoardId,
    10, events, function() {}
  );
  console.log(JSON.stringify({
    writes: model.writes,
    callMethods: model.calls.map(function(call) { return call.method; }),
    events,
    source: Array.from(model.source),
    target: Array.from(model.target),
    targetSnapshot: transaction.targetSnapshot
  }));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertEqual(result['writes'], [
            {'method': 'LN', 'payload': {'noteIds': 'note-1'}},
            {'method': 'B1', 'payload': {'noteId': 'note-1'}},
            {'method': 'd0', 'payload': {'targetBoardId': 'target-board', 'notesId': 'note-1'}},
        ])
        self.assertIn(['LN', 'B1'], [
            result['callMethods'][index:index + 2]
            for index in range(len(result['callMethods']) - 1)
        ])
        self.assertEqual(result['source'], [])
        self.assertEqual(result['target'], ['note-1'])
        self.assertEqual(result['targetSnapshot']['noteIds'], ['note-1'])
        self.assertIn('transaction:target_verified', result['events'])

    def test_cross_board_transaction_preflight_failure_has_zero_writes(self):
        result = self.run_board_transaction_js(r'''
async function attempt(options) {
  const model = createTransactionModel(options);
  const events = [];
  let error = '';
  try {
    await moveAcrossBoardsTransaction(
      model.api, model.noteId, model.sourceBoardId, model.targetBoardId,
      10, events, function() {}
    );
  } catch (caught) {
    error = caught.message;
  }
  return {writes: model.writes, events, error};
}
(async function() {
  const sourceMissing = await attempt({sourceNoteIds: []});
  const targetPresent = await attempt({targetNoteIds: ['note-1']});
  console.log(JSON.stringify({sourceMissing, targetPresent}));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertEqual(result['sourceMissing']['writes'], [])
        self.assertIn('source board', result['sourceMissing']['error'])
        self.assertIn('transaction:preflight:source_missing', result['sourceMissing']['events'])
        self.assertEqual(result['targetPresent']['writes'], [])
        self.assertIn('target board', result['targetPresent']['error'])
        self.assertIn('transaction:preflight:target_present', result['targetPresent']['events'])

    def test_cross_board_target_failure_rolls_back_and_still_fails_item(self):
        result = self.run_board_transaction_js(r'''
(async function() {
  const model = createTransactionModel({targetMoveNoop: true});
  const events = [];
  let error = {};
  try {
    await moveAcrossBoardsTransaction(
      model.api, model.noteId, model.sourceBoardId, model.targetBoardId,
      10, events, function() {}
    );
  } catch (caught) {
    error = {name: caught.name, message: caught.message};
  }
  console.log(JSON.stringify({
    writes: model.writes,
    events,
    error,
    source: Array.from(model.source),
    target: Array.from(model.target)
  }));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertEqual([call['method'] for call in result['writes']], [
            'LN', 'B1', 'd0', 'LN', 'B1', 'd0',
        ])
        self.assertEqual(result['writes'][3:5], [
            {'method': 'LN', 'payload': {'noteIds': 'note-1'}},
            {'method': 'B1', 'payload': {'noteId': 'note-1'}},
        ])
        self.assertEqual(result['writes'][-1]['payload']['targetBoardId'], 'source-board')
        self.assertEqual(result['error']['name'], 'CrossBoardTransactionError')
        self.assertIn('source rollback verified', result['error']['message'])
        self.assertIn('transaction:rollback:succeeded', result['events'])
        self.assertEqual(result['source'], ['note-1'])
        self.assertEqual(result['target'], [])

    def test_cross_board_first_recollect_failure_does_not_retry_then_rolls_back(self):
        result = self.run_board_transaction_js(r'''
(async function() {
  const model = createTransactionModel({b1FailureCount: 1});
  const events = [];
  let error = {};
  try {
    await moveAcrossBoardsTransaction(
      model.api, model.noteId, model.sourceBoardId, model.targetBoardId,
      10, events, function() {}
    );
  } catch (caught) {
    error = {name: caught.name, message: caught.message};
  }
  console.log(JSON.stringify({
    writes: model.writes,
    events,
    error,
    source: Array.from(model.source),
    target: Array.from(model.target)
  }));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertEqual([call['method'] for call in result['writes']], [
            'LN', 'B1', 'LN', 'B1', 'd0',
        ])
        self.assertIn('transaction:recollect_failed', result['events'])
        self.assertNotIn('transaction:recollect_retry', result['events'])
        self.assertIn('transaction:rollback:succeeded', result['events'])
        self.assertEqual(result['error']['name'], 'CrossBoardTransactionError')
        self.assertEqual(result['source'], ['note-1'])
        self.assertEqual(result['target'], [])

    def test_cross_board_rollback_verification_failure_is_explicit_high_risk(self):
        result = self.run_board_transaction_js(r'''
(async function() {
  const model = createTransactionModel({targetMoveNoop: true, sourceMoveNoop: true});
  const events = [];
  let error = {};
  try {
    await moveAcrossBoardsTransaction(
      model.api, model.noteId, model.sourceBoardId, model.targetBoardId,
      10, events, function() {}
    );
  } catch (caught) {
    error = {name: caught.name, message: caught.message};
  }
  console.log(JSON.stringify({writes: model.writes, events, error}));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertEqual(result['error']['name'], 'HighRiskStateUncertainError')
        self.assertTrue(result['error']['message'].startswith('HIGH_RISK_STATE_UNCERTAIN:'))
        self.assertIn('transaction:rollback:failed', result['events'])
        self.assertIn('transaction:high_risk_state_uncertain', result['events'])

    def test_cross_board_security_failure_after_writes_never_starts_rollback(self):
        result = self.run_board_transaction_js(r'''
(async function() {
  const model = createTransactionModel({targetMoveFailure: 'security verification'});
  const events = [];
  let error = {};
  function guard(cause) {
    if (cause && String(cause.message || cause).includes('security')) {
      const securityError = new Error('SAFETY_BREAKER: security verification');
      securityError.name = 'SecurityChallengeError';
      throw securityError;
    }
  }
  try {
    await moveAcrossBoardsTransaction(
      model.api, model.noteId, model.sourceBoardId, model.targetBoardId,
      10, events, guard
    );
  } catch (caught) {
    error = {name: caught.name, message: caught.message};
  }
  console.log(JSON.stringify({writes: model.writes, events, error}));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertEqual([call['method'] for call in result['writes']], ['LN', 'B1', 'd0'])
        self.assertNotIn('transaction:rollback', result['events'])
        self.assertEqual(result['error']['name'], 'HighRiskStateUncertainError')
        self.assertTrue(result['error']['message'].startswith('HIGH_RISK_STATE_UNCERTAIN:'))

    def test_cross_board_page_binding_guard_failure_after_writes_never_rolls_back(self):
        result = self.run_board_transaction_js(r'''
(async function() {
  const model = createTransactionModel();
  const events = [];
  let error = {};
  let guardCalls = 0;
  function guard() {
    guardCalls += 1;
    if (guardCalls === 5) {
      const bindingError = new Error('Arc worker runtime marker no longer matches');
      bindingError.name = 'ExecutePageBindingError';
      throw bindingError;
    }
  }
  try {
    await moveAcrossBoardsTransaction(
      model.api, model.noteId, model.sourceBoardId, model.targetBoardId,
      10, events, guard
    );
  } catch (caught) {
    error = {name: caught.name, message: caught.message};
  }
  console.log(JSON.stringify({writes: model.writes, events, error, guardCalls}));
})().catch(function(error) { console.error(error); process.exit(1); });
''')
        self.assertEqual([call['method'] for call in result['writes']], [])
        self.assertNotIn('transaction:rollback', result['events'])
        self.assertEqual(result['error']['name'], 'ExecutePageBindingError')
        self.assertIn('Arc worker runtime marker no longer matches', result['error']['message'])

    def test_browser_job_injects_main_world_script_and_uses_dom_state_bridge(self):
        args = type('Args', (), {
            'allow_low_confidence': False,
            'verify_pages': 1,
            'user_id': '',
        })()
        job = build_browser_job([], args)
        self.assertIn("document.createElement('script')", job)
        self.assertIn("dataset.xhsSkillState = 'pending'", job)
        self.assertIn('mainWorldJob.toString()', job)
        self.assertNotIn('window.__xhsSkillRuns', job)
        self.assertNotIn('window.__xhsSkillReq', job)
        self.assertNotIn('fetch(', job)
        self.assertIn('declared_total: snapshot.declaredTotal', job)
        self.assertIn('accessible_total: snapshot.accessibleTotal', job)
        self.assertIn('count_mismatch: snapshot.countMismatch', job)
        subprocess.run(
            ['node', '-e', 'new Function(process.argv[1]);', job],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

    def test_poll_browser_job_reads_and_cleans_dom_state_bridge(self):
        captured = {}

        class Runner:
            def eval(self, js):
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
def js_eval(js):
    if js.startswith('window.scrollBy') or js.startswith('window.scrollTo'):
        return 'ok'
    return json.dumps(states.pop(0), ensure_ascii=False)
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
