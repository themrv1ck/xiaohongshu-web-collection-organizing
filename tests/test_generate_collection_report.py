#!/usr/bin/env python3

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'generate_collection_report.py'


class CollectionReportTests(unittest.TestCase):
    @staticmethod
    def image_source_sha256(row):
        material = json.dumps({
            'contract_version': 1,
            'id': str(row.get('id') or ''),
            'title': str(row.get('title') or ''),
            'ocr_text': str(row.get('ocr_text') or ''),
            'ocr_run_fingerprint': str(row.get('ocr_run_fingerprint') or ''),
            'ocr_image_count': row.get('ocr_image_count'),
        }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(material.encode('utf-8')).hexdigest()

    def fixture(self):
        first = '1' * 24
        second = '2' * 24
        snapshot = {
            'generated_at': '2026-08-24T04:00:00Z',
            'mode': 'read_only',
            'source': {'writes_performed': False},
            'boards': [
                {
                    'id': 'a' * 24,
                    'name': '运动训练与体态',
                    'declared_total': 1,
                    'accessible_unique_count': 1,
                    'note_ids': [first],
                },
                {
                    'id': 'b' * 24,
                    'name': '无法确定',
                    'declared_total': 1,
                    'accessible_unique_count': 1,
                    'note_ids': [second],
                },
            ],
            'validation': {
                'full_membership_complete': True,
                'board_names_unique': True,
                'pagination_cursor_invariants_passed': True,
                'duplicate_note_ids': [],
                'multi_board_note_ids': [],
                'within_board_duplicates': [],
                'count_mismatch_boards': [],
            },
        }
        classification = [
            {
                'id': first,
                'title': '深蹲动作分析',
                'target_board': '运动训练与体态',
                'content_type': 'video',
                'main_topic': '下肢力量训练',
                'content_summary': '讲解深蹲动作顺序和常见错误。',
            },
            {
                'id': second,
                'title': '暂时无法判断',
                'target_board': '无法确定',
                'content_type': 'image',
                'main_topic': '',
                'content_summary': '',
                'ocr_status': 'ok',
                'ocr_image_set_complete': True,
                'ocr_text': '第1张：一段已经保存的图文笔记文字。',
                'ocr_run_fingerprint': 'f' * 64,
                'ocr_image_count': 1,
            },
        ]
        image_analysis = [
            {
                'id': second,
                'status': 'success',
                'main_topic': '图文内容整理',
                'content_summary': '这篇图文围绕已经保存的文字展开，形成一段完整的整体概括。',
                'source_sha256': self.image_source_sha256(classification[1]),
                'analysis_provider': 'test',
                'analysis_model': 'fixture',
                'analysis_provider_version': '1',
            },
        ]
        return snapshot, classification, image_analysis

    def run_report(self, directory, snapshot, classification, image_analysis=None):
        snapshot_path = directory / 'snapshot.json'
        classification_path = directory / 'classification.json'
        output = directory / 'report.html'
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
        classification_path.write_text(json.dumps(classification, ensure_ascii=False), encoding='utf-8')
        command = [
            sys.executable,
            str(SCRIPT),
            '--board-snapshot', str(snapshot_path),
            '--classification', str(classification_path),
            '--output', str(output),
        ]
        if image_analysis is not None:
            image_analysis_path = directory / 'image_analysis.json'
            image_analysis_path.write_text(
                json.dumps(image_analysis, ensure_ascii=False), encoding='utf-8',
            )
            command.extend(['--image-analysis', str(image_analysis_path)])
        result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
        return result, output

    def test_generates_complete_report_without_browser_or_account_writes(self):
        snapshot, classification, image_analysis = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            result, output = self.run_report(
                Path(tmp), snapshot, classification, image_analysis,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            report = output.read_text(encoding='utf-8')
        self.assertEqual(payload['board_count'], 2)
        self.assertEqual(payload['note_count'], 2)
        self.assertFalse(payload['writes_to_xiaohongshu'])
        self.assertIn('运动训练与体态', report)
        self.assertIn('下肢力量训练', report)
        self.assertIn('讲解深蹲动作顺序和常见错误。', report)
        self.assertIn('等待你自行调整到合适专辑', report)
        self.assertIn('没有重新读取收藏', report)

    def test_includes_image_summary_and_never_embeds_raw_ocr_as_report_content(self):
        snapshot, classification, image_analysis = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            result, output = self.run_report(
                Path(tmp), snapshot, classification, image_analysis,
            )
            report = output.read_text(encoding='utf-8') if output.exists() else ''
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('形成一段完整的整体概括', report)
        self.assertIn('图文内容概括', report)
        self.assertIn('data-content-source="image_summary"', report)
        self.assertNotIn('第1张：一段已经保存的图文笔记文字。', report)
        self.assertNotIn('查看完整图文 OCR 文字', report)

    def test_requires_exact_image_analysis_for_complete_ocr_rows(self):
        snapshot, classification, image_analysis = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            missing_result, missing_output = self.run_report(
                Path(tmp), snapshot, classification,
            )
        self.assertNotEqual(missing_result.returncode, 0)
        self.assertIn('图文摘要', missing_result.stderr)
        self.assertFalse(missing_output.exists())

        image_analysis[0]['source_sha256'] = '0' * 64
        with tempfile.TemporaryDirectory() as tmp:
            changed_result, changed_output = self.run_report(
                Path(tmp), snapshot, classification, image_analysis,
            )
        self.assertNotEqual(changed_result.returncode, 0)
        self.assertIn('来源哈希', changed_result.stderr)
        self.assertFalse(changed_output.exists())

    def test_rejects_ocr_marked_ok_when_image_set_is_incomplete(self):
        snapshot, classification, image_analysis = self.fixture()
        classification[1]['ocr_image_set_complete'] = False
        with tempfile.TemporaryDirectory() as tmp:
            result, output = self.run_report(
                Path(tmp), snapshot, classification, image_analysis,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OCR', result.stderr)
        self.assertFalse(output.exists())

    def test_report_has_dark_mode_and_long_text_safe_single_column_layout(self):
        snapshot, classification, image_analysis = self.fixture()
        classification[0]['main_topic'] = '没有空格的超长主题' * 40
        with tempfile.TemporaryDirectory() as tmp:
            result, output = self.run_report(
                Path(tmp), snapshot, classification, image_analysis,
            )
            report = output.read_text(encoding='utf-8') if output.exists() else ''
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('id="theme-toggle"', report)
        self.assertIn(':root[data-theme="dark"]', report)
        self.assertIn('prefers-color-scheme: dark', report)
        self.assertIn('grid-template-columns:minmax(0,1fr)', report)
        self.assertIn('overflow-wrap:anywhere', report)

    def test_rejects_incomplete_duplicate_or_changed_membership(self):
        snapshot, classification, image_analysis = self.fixture()
        cases = []
        incomplete = json.loads(json.dumps(snapshot))
        incomplete['validation']['full_membership_complete'] = False
        cases.append((incomplete, classification, '快照不完整'))
        duplicate = json.loads(json.dumps(snapshot))
        duplicate['boards'][1]['note_ids'] = duplicate['boards'][0]['note_ids']
        cases.append((duplicate, classification, '重复'))
        changed = json.loads(json.dumps(snapshot))
        changed['boards'][0]['note_ids'].append('3' * 24)
        changed['boards'][0]['declared_total'] = 2
        changed['boards'][0]['accessible_unique_count'] = 2
        cases.append((changed, classification, '成员变化'))
        for index, (case_snapshot, case_classification, _label) in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                result, output = self.run_report(
                    Path(tmp), case_snapshot, case_classification, image_analysis,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_rejects_classification_target_that_differs_from_final_album(self):
        snapshot, classification, image_analysis = self.fixture()
        classification[0]['target_board'] = '无法确定'
        with tempfile.TemporaryDirectory() as tmp:
            result, output = self.run_report(
                Path(tmp), snapshot, classification, image_analysis,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('与最终专辑', result.stderr)
        self.assertFalse(output.exists())

    def test_redacts_sensitive_query_tokens_from_saved_text(self):
        snapshot, classification, image_analysis = self.fixture()
        classification[0]['content_summary'] = (
            '参考 https://www.xiaohongshu.com/explore/'
            + '1' * 24
            + '?xsec_token=secret-value&sign=secret-sign'
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, output = self.run_report(
                Path(tmp), snapshot, classification, image_analysis,
            )
            report = output.read_text(encoding='utf-8') if output.exists() else ''
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('secret-value', report)
        self.assertNotIn('secret-sign', report)


if __name__ == '__main__':
    unittest.main()
