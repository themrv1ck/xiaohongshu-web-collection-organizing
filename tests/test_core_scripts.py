#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from run_reassign_batch import filter_classification_for_resume, merge_report_chunk  # noqa: E402
from xhs_ocr_common import infer_board, load_taxonomy  # noqa: E402


class CoreScriptTests(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / args[0]), *args[1:]],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

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

    def test_dry_run_report_and_retry_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            classification = tmp_path / 'classification.json'
            report = tmp_path / 'run_report.json'
            retry = tmp_path / 'retry_queue.json'
            classification.write_text(json.dumps([
                {'id': '66d19b54000000001d03a93d', 'title': '滑雪', 'target_board': '滑雪', 'confidence': 'high'},
                {'id': '66d19b54000000001d03a93e', 'title': '待复核', 'target_board': '杂项灵感', 'confidence': 'low'},
            ], ensure_ascii=False), encoding='utf-8')
            self.run_script('run_reassign_batch.py', str(classification), str(report))
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['mode'], 'dry_run')
            self.assertEqual(data['processed'][0]['status'], 'planned')
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

    def test_merge_report_chunk_appends_processed_errors_and_missing_boards(self):
        report = {'processed': [], 'errors': [], 'missing_boards': [], 'board_counts_before': {}, 'board_counts_after': {}}
        chunk = {
            'processed': [{'id': 'note-1', 'status': 'failed', 'target_board': '滑雪'}],
            'errors': [{'id': 'note-1', 'status': 'failed', 'target_board': '滑雪'}],
            'missing_boards': ['滑雪', '滑雪'],
            'board_counts_before': {'滑雪': 1},
            'board_counts_after': {'滑雪': 1},
        }
        merge_report_chunk(report, chunk)
        merge_report_chunk(report, chunk)
        self.assertEqual(len(report['processed']), 2)
        self.assertEqual(len(report['errors']), 2)
        self.assertEqual(report['missing_boards'], ['滑雪'])
        self.assertEqual(report['board_counts_before'], {'滑雪': 1})
        self.assertEqual(report['board_counts_after'], {'滑雪': 1})

    def test_extract_visible_items_merges_source_lists(self):
        from extract_visible_items import merge_items
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
    if js.startswith('window.scrollBy'):
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


if __name__ == '__main__':
    unittest.main()
