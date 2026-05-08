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


if __name__ == '__main__':
    unittest.main()
