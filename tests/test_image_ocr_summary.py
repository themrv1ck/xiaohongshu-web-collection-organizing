#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from analyze_image_ocr import (  # noqa: E402
    build_summary_rows,
    image_source_sha256,
    validate_batch_payload,
)


class ImageOcrSummaryTests(unittest.TestCase):
    def fixture(self):
        return [
            {
                'id': '1' * 24,
                'title': '医院静脉血同步测试',
                'content_type': 'image',
                'ocr_status': 'ok',
                'ocr_image_set_complete': True,
                'ocr_run_fingerprint': 'a' * 64,
                'ocr_image_count': 2,
                'ocr_text': '第1张：13款动态血糖仪与医院静脉血同步测试。第2张：记录测试结果。',
            },
        ]

    def test_builds_holistic_summary_sidecar_without_copying_raw_ocr(self):
        rows = self.fixture()

        def analyze(batch):
            self.assertEqual([row['id'] for row in batch], ['1' * 24])
            return {'items': [{
                'id': '1' * 24,
                'main_topic': '动态血糖仪对比测试',
                'content_summary': '这篇图文对比多款动态血糖仪与医院静脉血检测结果，重点记录同步测试方法和读数差异。',
            }]}

        result = build_summary_rows(
            rows,
            analyze,
            analysis_identity={'provider': 'test', 'model': 'fixture', 'version': '1'},
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['status'], 'success')
        self.assertEqual(result[0]['source_sha256'], image_source_sha256(rows[0]))
        self.assertNotIn('第1张', result[0]['content_summary'])

    def test_rejects_missing_duplicate_or_raw_ocr_style_batch_output(self):
        note_id = '1' * 24
        good = {
            'items': [{
                'id': note_id,
                'main_topic': '动态血糖仪测试',
                'content_summary': '这篇图文对比不同设备的同步检测结果，并说明测试方法和数据差异。',
            }],
        }
        self.assertEqual(validate_batch_payload(good, [note_id])[0]['id'], note_id)
        cases = [
            {'items': []},
            {'items': [good['items'][0], good['items'][0]]},
            {'items': [{**good['items'][0], 'id': '2' * 24}]},
            {'items': [{**good['items'][0], 'content_summary': '第1张：原始文字 第2张：更多原始文字'}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_batch_payload(payload, [note_id])

    def test_changed_ocr_hash_forces_fresh_analysis_instead_of_reusing_summary(self):
        rows = self.fixture()
        calls = []

        def analyze(batch):
            calls.append(batch[0]['ocr_text'])
            return {'items': [{
                'id': batch[0]['id'],
                'main_topic': '动态血糖仪测试',
                'content_summary': '这篇图文整理动态血糖设备的同步测试过程，并概括主要读数差异。',
            }]}

        first = build_summary_rows(
            rows,
            analyze,
            analysis_identity={'provider': 'test', 'model': 'fixture', 'version': '1'},
        )
        rows[0]['ocr_text'] += '新增一页结果。'
        second = build_summary_rows(
            rows,
            analyze,
            initial_rows=first,
            analysis_identity={'provider': 'test', 'model': 'fixture', 'version': '1'},
        )
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first[0]['source_sha256'], second[0]['source_sha256'])


if __name__ == '__main__':
    unittest.main()
