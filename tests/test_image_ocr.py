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

from classify_items import main as classify_items_main  # noqa: E402
from enrich_note_images import SecurityBlockError, enrich_item_from_html, main as enrich_note_images_main  # noqa: E402
from extract_visible_items import ITEMS_JS  # noqa: E402
from xhs_safety import SafetyHaltedError, load_safety_state  # noqa: E402
from xhs_ocr_common import (  # noqa: E402
    build_cache_path,
    download_image,
    image_set_sha256,
    perform_ocr_for_items,
    resolve_image_urls,
    run_tesseract_ocr,
)


class ImageOcrTests(unittest.TestCase):
    @staticmethod
    def complete_image_item(item_id='image-note', image_urls=None):
        image_urls = image_urls or [
            'https://ci.xiaohongshu.com/image-note-cover.jpg',
            'https://ci.xiaohongshu.com/image-note-page-2.jpg',
        ]
        return {
            'id': item_id,
            'title': '多图图文笔记',
            'content_type': 'image',
            'image_urls': image_urls,
            'image_count': len(image_urls),
            'image_urls_complete': True,
            'image_list_source': 'mobile_ssr_note_data.imageList',
            'image_enrichment_status': 'ok',
        }

    @staticmethod
    def fake_download(url, dest, timeout_sec=20):
        del timeout_sec
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = url.encode('utf-8')
        dest.write_bytes(payload)
        return len(payload)

    @staticmethod
    def fake_valid_image_download(url, dest, timeout_sec=20):
        del url, timeout_sec
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = b'\xff\xd8\xff\xe0test-image'
        dest.write_bytes(payload)
        return len(payload)

    @staticmethod
    def setup_state_html(note_data, suffix=''):
        state = {
            'LAUNCHER_SSR_STORE_PAGE_DATA': {
                'noteData': note_data,
            }
        }
        return (
            '<html><body><script>window.__SETUP_SERVER_STATE__='
            + json.dumps(state, ensure_ascii=False)
            + ';</script>'
            + suffix
            + '</body></html>'
        )

    @staticmethod
    def evaluate_collection_items_js(
        note_card,
        *,
        dom_title='测试图文',
        dom_text='测试图文 #测试',
    ):
        note_id = str(note_card['noteId'])
        script = f'''
const noteCard = {json.dumps(note_card, ensure_ascii=False)};
const link = {{
  href: "https://www.xiaohongshu.com/explore/{note_id}",
  innerText: {json.dumps(dom_title, ensure_ascii=False)}
}};
const image = {{currentSrc: "https://ci.xiaohongshu.com/{note_id}-cover.jpg", src: ""}};
const section = {{
  innerText: {json.dumps(dom_text, ensure_ascii=False)},
  __vueParentComponent: null,
  getAttribute(name) {{ return name === "data-index" ? "0" : ""; }},
  querySelector(selector) {{
    if (selector === "a.title" || selector === "a.cover") return link;
    if (selector === "img") return image;
    return null;
  }},
  querySelectorAll() {{ return []; }}
}};
global.window = {{
  scrollY: 0,
  innerHeight: 800,
  __INITIAL_STATE__: {{user: {{notes: [[{{noteCard}}]]}}}}
}};
global.document = {{
  title: "收藏",
  body: {{innerText: ""}},
  documentElement: {{scrollHeight: 800}},
  querySelectorAll() {{ return [section]; }}
}};
global.location = {{href: "https://www.xiaohongshu.com/user/profile/test?tab=collect"}};
process.stdout.write(eval({json.dumps(ITEMS_JS)}));
'''
        proc = subprocess.run(
            ['node', '-e', script],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return json.loads(proc.stdout)

    def test_collection_card_uses_structured_metadata_when_dom_text_is_empty(self):
        note_id = '66d19b54000000001d03a93d'
        data = self.evaluate_collection_items_js(
            {
                'noteId': note_id,
                'type': 'normal',
                'displayTitle': '2026 年先读这 10 本书',
                'user': {'nickname': 'BetterLiving编辑手记'},
            },
            dom_title='',
            dom_text='',
        )

        self.assertEqual(len(data['items']), 1)
        item = data['items'][0]
        self.assertEqual(item['title'], '2026 年先读这 10 本书')
        self.assertEqual(item['user'], 'BetterLiving编辑手记')
        self.assertEqual(
            item['card_text'],
            '2026 年先读这 10 本书 BetterLiving编辑手记',
        )

    def test_complete_multi_image_set_keeps_per_image_results_and_aggregates_text(self):
        def fake_ocr(_provider, image_path, _swift_script, _tesseract_lang):
            source_url = image_path.read_text(encoding='utf-8')
            if source_url.endswith('cover.jpg'):
                return {
                    'text': '老钱风西装',
                    'lines': [{'text': '老钱风西装', 'confidence': 0.94}],
                    'average_confidence': 0.94,
                    'provider': 'swift',
                }
            return {
                'text': '香水推荐',
                'lines': [{'text': '香水推荐', 'confidence': 0.90}],
                'average_confidence': 0.90,
                'provider': 'swift',
            }

        with tempfile.TemporaryDirectory() as tmp, \
                patch('xhs_ocr_common.download_image', side_effect=self.fake_download), \
                patch('xhs_ocr_common.run_ocr', side_effect=fake_ocr):
            results = perform_ocr_for_items(
                [self.complete_image_item()],
                Path(tmp) / 'ocr_results.json',
                provider='swift',
            )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['image_set_complete'])
        self.assertEqual(result['image_count_declared'], 2)
        self.assertEqual(result['image_count_available'], 2)
        self.assertEqual(result['image_count_processed'], 2)
        self.assertEqual([image['image_index'] for image in result['images']], [0, 1])
        self.assertEqual([image['status'] for image in result['images']], ['ok', 'ok'])
        self.assertEqual(
            [image['ocr_text'] for image in result['images']],
            ['老钱风西装', '香水推荐'],
        )
        self.assertEqual(result['ocr_text'], '第1张：老钱风西装 第2张：香水推荐')
        self.assertEqual(
            [(line['image_index'], line['text']) for line in result['ocr_lines']],
            [(0, '老钱风西装'), (1, '香水推荐')],
        )
        self.assertAlmostEqual(result['ocr_confidence'], 0.92)

    def test_textless_visual_images_are_successful_empty_ocr_not_visual_understanding(self):
        empty_ocr = {
            'text': '',
            'lines': [],
            'average_confidence': None,
            'provider': 'swift',
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch('xhs_ocr_common.download_image', side_effect=self.fake_download), \
                patch('xhs_ocr_common.run_ocr', return_value=empty_ocr):
            results = perform_ocr_for_items(
                [self.complete_image_item(item_id='textless-note')],
                Path(tmp) / 'ocr_results.json',
                provider='swift',
            )

        result = results[0]
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['ocr_text'], '')
        self.assertEqual(result['ocr_lines'], [])
        self.assertIsNone(result['ocr_confidence'])
        self.assertEqual([image['status'] for image in result['images']], ['ok', 'ok'])
        self.assertEqual([image['ocr_text'] for image in result['images']], ['', ''])

    def test_swift_string_lines_are_normalized_with_image_indexes(self):
        swift_result = {
            'text': '型号 A1 安装步骤',
            'lines': ['型号 A1', '安装步骤'],
            'average_confidence': 0.88,
            'provider': 'swift',
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch('xhs_ocr_common.download_image', side_effect=self.fake_download), \
                patch('xhs_ocr_common.run_ocr', return_value=swift_result):
            result = perform_ocr_for_items(
                [self.complete_image_item(
                    item_id='swift-lines-note',
                    image_urls=['https://ci.xiaohongshu.com/swift-lines.jpg'],
                )],
                Path(tmp) / 'ocr_results.json',
                provider='swift',
            )[0]

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['images'][0]['ocr_lines'], [
            {'text': '型号 A1', 'confidence': None, 'image_index': 0},
            {'text': '安装步骤', 'confidence': None, 'image_index': 0},
        ])
        self.assertEqual(result['ocr_lines'], result['images'][0]['ocr_lines'])

    def test_incomplete_image_set_is_unavailable_and_never_runs_partial_ocr(self):
        item = self.complete_image_item(
            item_id='incomplete-note',
            image_urls=['https://ci.xiaohongshu.com/incomplete-cover.jpg'],
        )
        item['image_count'] = 3
        item['image_urls_complete'] = False

        with tempfile.TemporaryDirectory() as tmp, \
                patch('xhs_ocr_common.download_image') as download, \
                patch('xhs_ocr_common.run_ocr') as run_ocr:
            results = perform_ocr_for_items(
                [item],
                Path(tmp) / 'ocr_results.json',
                provider='swift',
            )

        result = results[0]
        self.assertEqual(result['status'], 'incomplete_image_set')
        self.assertFalse(result['image_set_complete'])
        self.assertEqual(result['image_count_declared'], 3)
        self.assertEqual(result['image_count_available'], 1)
        self.assertEqual(result['image_count_processed'], 0)
        self.assertEqual(result['images'], [])
        self.assertEqual(result['ocr_text'], '')
        self.assertIn('封面和全部内页图片', result['error'])
        download.assert_not_called()
        run_ocr.assert_not_called()

    def test_untrusted_complete_flag_cannot_bypass_authoritative_image_source(self):
        item = self.complete_image_item(
            item_id='untrusted-complete-note',
            image_urls=['https://ci.xiaohongshu.com/observed-cover.jpg'],
        )
        item['image_list_source'] = 'collection_card_cover_only'

        with tempfile.TemporaryDirectory() as tmp, \
                patch('xhs_ocr_common.download_image') as download, \
                patch('xhs_ocr_common.run_ocr') as run_ocr:
            result = perform_ocr_for_items(
                [item],
                Path(tmp) / 'ocr_results.json',
                provider='swift',
            )[0]

        self.assertEqual(result['status'], 'incomplete_image_set')
        self.assertFalse(result['image_set_complete'])
        download.assert_not_called()
        run_ocr.assert_not_called()

    def test_current_incomplete_image_set_never_reuses_a_previous_complete_ok_result(self):
        image_url = 'https://ci.xiaohongshu.com/reuse-guard.jpg'
        complete = self.complete_image_item('reuse-guard-note', [image_url])
        previous_ocr = {
            'text': '不应复用的旧文字',
            'lines': [{'text': '不应复用的旧文字', 'confidence': 0.9}],
            'average_confidence': 0.9,
            'provider': 'swift',
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'ocr_results.json'
            with patch('xhs_ocr_common.download_image', side_effect=self.fake_valid_image_download), \
                    patch('xhs_ocr_common.run_ocr', return_value=previous_ocr):
                previous = perform_ocr_for_items([complete], output, provider='swift')[0]
            self.assertEqual(previous['status'], 'ok')

            with patch('xhs_ocr_common.run_ocr') as same_config_ocr:
                reused = perform_ocr_for_items([complete], output, provider='swift')[0]
            self.assertEqual(reused['ocr_text'], '第1张：不应复用的旧文字')
            same_config_ocr.assert_not_called()

            incomplete = dict(complete)
            incomplete['image_count'] = 2
            incomplete['image_urls_complete'] = False
            with patch('xhs_ocr_common.download_image') as download, \
                    patch('xhs_ocr_common.run_ocr') as run_ocr:
                result = perform_ocr_for_items([incomplete], output, provider='swift')[0]

        self.assertEqual(result['status'], 'incomplete_image_set')
        self.assertFalse(result['image_set_complete'])
        self.assertEqual(result['ocr_text'], '')
        self.assertNotEqual(result.get('ocr_text'), previous['ocr_text'])
        download.assert_not_called()
        run_ocr.assert_not_called()

    def test_one_image_failure_makes_the_whole_image_set_unavailable(self):
        image_urls = [
            'https://ci.xiaohongshu.com/failure-cover.jpg',
            'https://ci.xiaohongshu.com/failure-page-2.jpg',
            'https://ci.xiaohongshu.com/failure-page-3.jpg',
        ]

        def fake_ocr(_provider, image_path, _swift_script, _tesseract_lang):
            source_url = image_path.read_text(encoding='utf-8')
            if source_url.endswith('page-2.jpg'):
                raise RuntimeError('page 2 OCR failed')
            text = '封面文字' if source_url.endswith('cover.jpg') else '第三页文字'
            return {
                'text': text,
                'lines': [{'text': text, 'confidence': 0.9}],
                'average_confidence': 0.9,
                'provider': 'swift',
            }

        with tempfile.TemporaryDirectory() as tmp, \
                patch('xhs_ocr_common.download_image', side_effect=self.fake_download), \
                patch('xhs_ocr_common.run_ocr', side_effect=fake_ocr):
            results = perform_ocr_for_items(
                [self.complete_image_item('failure-note', image_urls)],
                Path(tmp) / 'ocr_results.json',
                provider='swift',
            )

        result = results[0]
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['image_set_complete'])
        self.assertEqual(result['image_count_processed'], 3)
        self.assertEqual([image['status'] for image in result['images']], ['ok', 'error', 'ok'])
        self.assertEqual(result['images'][1]['error'], 'page 2 OCR failed')
        self.assertEqual(result['ocr_text'], '')
        self.assertEqual(result['ocr_lines'], [])
        self.assertIsNone(result['ocr_confidence'])
        self.assertIn('未把部分结果用于分类', result['error'])

    def test_changed_image_set_invalidates_the_saved_note_level_ocr_result(self):
        old_item = self.complete_image_item(
            'cache-note',
            [
                'https://ci.xiaohongshu.com/cache-cover.jpg',
                'https://ci.xiaohongshu.com/cache-page-2.jpg',
            ],
        )
        changed_item = self.complete_image_item(
            'cache-note',
            [
                'https://ci.xiaohongshu.com/cache-cover.jpg',
                'https://ci.xiaohongshu.com/cache-page-2-replaced.jpg',
            ],
        )
        old_ocr = {
            'text': '旧结果',
            'lines': [{'text': '旧结果', 'confidence': 0.8}],
            'average_confidence': 0.8,
            'provider': 'swift',
        }
        refreshed_ocr = {
            'text': '重跑结果',
            'lines': [{'text': '重跑结果', 'confidence': 0.9}],
            'average_confidence': 0.9,
            'provider': 'swift',
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'ocr_results.json'
            with patch('xhs_ocr_common.download_image', side_effect=self.fake_download), \
                    patch('xhs_ocr_common.run_ocr', return_value=old_ocr):
                old_result = perform_ocr_for_items(
                    [old_item], output, provider='swift',
                )[0]

            with patch('xhs_ocr_common.download_image', side_effect=self.fake_download), \
                    patch('xhs_ocr_common.run_ocr', return_value=refreshed_ocr) as run_ocr:
                refreshed_result = perform_ocr_for_items(
                    [changed_item], output, provider='swift',
                )[0]

        self.assertNotEqual(old_result['image_set_sha256'], refreshed_result['image_set_sha256'])
        self.assertEqual(run_ocr.call_count, 2)
        self.assertEqual(refreshed_result['ocr_text'], '第1张：重跑结果 第2张：重跑结果')
        self.assertNotIn('旧结果', refreshed_result['ocr_text'])

    def test_provider_and_tesseract_language_changes_invalidate_saved_ocr_result(self):
        item = self.complete_image_item(
            'ocr-config-note',
            ['https://ci.xiaohongshu.com/ocr-config.jpg'],
        )

        def ocr_result(text, provider):
            return {
                'text': text,
                'lines': [{'text': text, 'confidence': 0.9}],
                'average_confidence': 0.9,
                'provider': provider,
            }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'ocr_results.json'
            with patch('xhs_ocr_common.download_image', side_effect=self.fake_valid_image_download), \
                    patch('xhs_ocr_common.run_ocr', return_value=ocr_result('Swift 结果', 'swift')):
                swift_result = perform_ocr_for_items(
                    [item], output, provider='swift', tesseract_lang='chi_sim',
                )[0]

            with patch('xhs_ocr_common.run_ocr') as same_swift_ocr:
                same_swift_result = perform_ocr_for_items(
                    [item], output, provider='swift', tesseract_lang='chi_sim',
                )[0]
            same_swift_ocr.assert_not_called()
            self.assertEqual(same_swift_result['ocr_text'], swift_result['ocr_text'])

            with patch('xhs_ocr_common.run_ocr', return_value=ocr_result('Tesseract 中文', 'tesseract')) as changed_provider_ocr:
                tesseract_result = perform_ocr_for_items(
                    [item], output, provider='tesseract', tesseract_lang='chi_sim',
                )[0]
            self.assertEqual(changed_provider_ocr.call_count, 1)
            self.assertEqual(tesseract_result['ocr_text'], '第1张：Tesseract 中文')

            with patch('xhs_ocr_common.run_ocr') as same_tesseract_ocr:
                perform_ocr_for_items(
                    [item], output, provider='tesseract', tesseract_lang='chi_sim',
                )
            same_tesseract_ocr.assert_not_called()

            with patch('xhs_ocr_common.run_ocr', return_value=ocr_result('中英联合', 'tesseract')) as changed_language_ocr:
                bilingual_result = perform_ocr_for_items(
                    [item], output, provider='tesseract', tesseract_lang='chi_sim+eng',
                )[0]
            self.assertEqual(changed_language_ocr.call_count, 1)
            self.assertEqual(bilingual_result['ocr_text'], '第1张：中英联合')

        self.assertNotEqual(
            swift_result['ocr_run_fingerprint'],
            tesseract_result['ocr_run_fingerprint'],
        )
        self.assertNotEqual(
            tesseract_result['ocr_run_fingerprint'],
            bilingual_result['ocr_run_fingerprint'],
        )

    def test_image_transform_query_is_part_of_the_image_set_identity(self):
        low_resolution = [
            'https://ci.xiaohongshu.com/same-image.jpg?imageView2/2/w/320/format/webp'
        ]
        high_resolution = [
            'https://ci.xiaohongshu.com/same-image.jpg?imageView2/2/w/2048/format/webp'
        ]
        self.assertNotEqual(
            image_set_sha256(low_resolution),
            image_set_sha256(high_resolution),
        )

    def test_invalid_cached_image_is_redownloaded_before_ocr(self):
        item = self.complete_image_item(
            'invalid-cache-note',
            ['https://ci.xiaohongshu.com/invalid-cache.jpg'],
        )
        ocr = {
            'text': '重下后识别成功',
            'lines': [{'text': '重下后识别成功', 'confidence': 0.9}],
            'average_confidence': 0.9,
            'provider': 'swift',
        }
        for invalid_bytes in (b'', b'<html>security verification</html>'):
            with self.subTest(invalid_bytes=invalid_bytes), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                output = tmp_path / 'ocr_results.json'
                cache_dir = tmp_path / 'ocr_cache'
                cache_path = build_cache_path(cache_dir, item['id'], item['image_urls'][0])
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(invalid_bytes)

                with patch('xhs_ocr_common.download_image', side_effect=self.fake_valid_image_download) as redownload, \
                        patch('xhs_ocr_common.run_ocr', return_value=ocr):
                    result = perform_ocr_for_items(
                        [item], output, cache_dir=cache_dir, provider='swift',
                    )[0]

                self.assertEqual(result['status'], 'ok')
                self.assertEqual(redownload.call_count, 1)
                self.assertTrue(cache_path.read_bytes().startswith(b'\xff\xd8\xff'))

    def test_non_image_download_response_never_reaches_the_final_cache_path(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                del exc_type, exc, traceback

            def read(self):
                return b'<html><body>security verification</body></html>'

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / 'downloaded.jpg'
            with patch('xhs_ocr_common.urllib.request.urlopen', return_value=FakeResponse()):
                with self.assertRaisesRegex(RuntimeError, 'not a supported image'):
                    download_image(
                        'https://ci.xiaohongshu.com/not-an-image.jpg',
                        destination,
                    )
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name(destination.name + '.part').exists())

    def test_tesseract_default_language_matches_chinese_environment_requirement(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='中文文字\n', stderr='',
        )
        with patch('xhs_ocr_common.shutil.which', return_value='/usr/bin/tesseract'), \
                patch('xhs_ocr_common.tesseract_language_ready', side_effect=lambda language: language == 'chi_sim'), \
                patch('xhs_ocr_common.subprocess.run', return_value=completed) as run:
            result = run_tesseract_ocr(Path('/tmp/not-read-by-mock.jpg'))

        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-l') + 1], 'chi_sim')
        self.assertEqual(result['text'], '中文文字')

    def test_empty_preferred_image_urls_does_not_hide_a_later_nonempty_image_list(self):
        item = {
            'image_urls': [],
            'imageList': [
                {'urlDefault': 'https://ci.xiaohongshu.com/from-image-list-1.jpg'},
                {'url': 'https://ci.xiaohongshu.com/from-image-list-2.jpg'},
            ],
            'cover_image_url': 'https://ci.xiaohongshu.com/cover-only.jpg',
        }
        self.assertEqual(resolve_image_urls(item), [
            'https://ci.xiaohongshu.com/from-image-list-1.jpg',
            'https://ci.xiaohongshu.com/from-image-list-2.jpg',
        ])

    def test_missing_or_duplicate_note_ids_are_rejected_before_ocr(self):
        missing_id = self.complete_image_item('', [
            'https://ci.xiaohongshu.com/missing-id.jpg',
        ])
        duplicate_a = self.complete_image_item('duplicate-note', [
            'https://ci.xiaohongshu.com/duplicate-a.jpg',
        ])
        duplicate_b = self.complete_image_item('duplicate-note', [
            'https://ci.xiaohongshu.com/duplicate-b.jpg',
        ])
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'ocr_results.json'
            with self.assertRaisesRegex(ValueError, '非空 note id'):
                perform_ocr_for_items([missing_id], output, provider='none')
            with self.assertRaisesRegex(ValueError, '重复 note id'):
                perform_ocr_for_items([duplicate_a, duplicate_b], output, provider='none')

    def test_video_items_never_enter_the_image_ocr_pipeline(self):
        image_item = self.complete_image_item(
            'image-only',
            ['https://ci.xiaohongshu.com/image-only.jpg'],
        )
        video_item = self.complete_image_item(
            'video-must-be-skipped',
            ['https://ci.xiaohongshu.com/video-cover.jpg'],
        )
        video_item['content_type'] = 'video'
        ocr = {
            'text': '图文文字',
            'lines': [{'text': '图文文字', 'confidence': 0.9}],
            'average_confidence': 0.9,
            'provider': 'swift',
        }

        with tempfile.TemporaryDirectory() as tmp, \
                patch('xhs_ocr_common.download_image', side_effect=self.fake_download) as download, \
                patch('xhs_ocr_common.run_ocr', return_value=ocr) as run_ocr:
            results = perform_ocr_for_items(
                [image_item, video_item],
                Path(tmp) / 'ocr_results.json',
                provider='swift',
            )

        self.assertEqual([result['id'] for result in results], ['image-only'])
        self.assertEqual(download.call_count, 1)
        self.assertEqual(run_ocr.call_count, 1)

    def test_collection_card_observed_image_list_is_never_declared_complete(self):
        note_id = '66d19b54000000001d03a93d'
        data = self.evaluate_collection_items_js({
            'noteId': note_id,
            'type': 'normal',
            'imageList': [
                {'urlDefault': 'https://ci.xiaohongshu.com/observed-cover.jpg'},
                {'urlDefault': 'https://ci.xiaohongshu.com/observed-page-2.jpg'},
            ],
        })

        self.assertEqual(len(data['items']), 1)
        item = data['items'][0]
        self.assertEqual(item['image_urls'], [
            'https://ci.xiaohongshu.com/observed-cover.jpg',
            'https://ci.xiaohongshu.com/observed-page-2.jpg',
        ])
        self.assertIsNone(item['image_count'])
        self.assertFalse(item['image_urls_complete'])
        self.assertEqual(item['image_list_source'], 'collection_card_observed_images')

    def test_enrich_note_images_extracts_cover_and_every_inner_image_from_synthetic_html(self):
        note_id = '66d19b54000000001d03a93d'
        state = {
            'LAUNCHER_SSR_STORE_PAGE_DATA': {
                'noteData': {
                    'noteId': note_id,
                    'type': 'normal',
                    'imageList': [
                        {'urlDefault': 'https://ci.xiaohongshu.com/enrich-cover.jpg'},
                        {
                            'infoList': [
                                {
                                    'imageScene': 'WB_DFT',
                                    'url': 'https://ci.xiaohongshu.com/enrich-page-2.jpg',
                                }
                            ]
                        },
                        {'url': 'https://ci.xiaohongshu.com/enrich-page-3.jpg'},
                    ],
                }
            }
        }
        html = (
            '<html><body><script>window.__SETUP_SERVER_STATE__='
            + json.dumps(state, ensure_ascii=False)
            + ';</script></body></html>'
        )

        enriched = enrich_item_from_html(
            {'id': note_id, 'title': '合成多图', 'content_type': 'image'},
            html,
        )

        self.assertEqual(enriched['image_urls'], [
            'https://ci.xiaohongshu.com/enrich-cover.jpg',
            'https://ci.xiaohongshu.com/enrich-page-2.jpg',
            'https://ci.xiaohongshu.com/enrich-page-3.jpg',
        ])
        self.assertEqual(enriched['cover_image_url'], enriched['image_urls'][0])
        self.assertEqual(enriched['image_count'], 3)
        self.assertTrue(enriched['image_urls_complete'])
        self.assertEqual(enriched['image_list_source'], 'mobile_ssr_note_data.imageList')
        self.assertEqual(enriched['image_enrichment_status'], 'ok')
        self.assertEqual(enriched['image_enrichment_error'], '')

    def test_detail_video_type_corrects_upstream_image_label_and_never_enters_ocr(self):
        note_id = '66d19b54000000001d03a93d'
        html = self.setup_state_html({
            'noteId': note_id,
            'type': 'video',
            'imageList': [
                {'urlDefault': 'https://ci.xiaohongshu.com/video-cover.jpg'},
            ],
        })
        enriched = enrich_item_from_html(
            {'id': note_id, 'title': '上游误标图文', 'content_type': 'image'},
            html,
        )

        self.assertEqual(enriched['content_type'], 'video')
        self.assertEqual(enriched['content_type_source'], 'mobile_ssr_note_data.type')
        self.assertEqual(enriched['image_urls'], [])
        self.assertEqual(enriched['image_count'], 0)
        self.assertFalse(enriched['image_urls_complete'])
        self.assertEqual(enriched['image_enrichment_status'], 'not_applicable')

        with tempfile.TemporaryDirectory() as tmp, \
                patch('xhs_ocr_common.download_image') as download, \
                patch('xhs_ocr_common.run_ocr') as run_ocr:
            results = perform_ocr_for_items(
                [enriched], Path(tmp) / 'ocr_results.json', provider='none',
            )
        self.assertEqual(results, [])
        download.assert_not_called()
        run_ocr.assert_not_called()

    def test_detail_missing_type_is_a_hard_failure(self):
        note_id = '66d19b54000000001d03a93d'
        html = self.setup_state_html({
            'noteId': note_id,
            'imageList': [
                {'urlDefault': 'https://ci.xiaohongshu.com/missing-type.jpg'},
            ],
        })
        with self.assertRaisesRegex(ValueError, '类型'):
            enrich_item_from_html(
                {'id': note_id, 'content_type': 'image'},
                html,
            )

    def test_enrich_note_images_rejects_synthetic_security_pages_before_parsing_images(self):
        item = {
            'id': '66d19b54000000001d03a93d',
            'title': '安全页不应解析',
            'content_type': 'image',
        }
        pages = [
            '<html><body>安全验证</body></html>',
            '<html><script>window.__SETUP_SERVER_STATE__={"code":300012};</script></html>',
        ]
        for html in pages:
            with self.subTest(html=html), self.assertRaises(SecurityBlockError):
                enrich_item_from_html(item, html)

    def test_batch_security_block_stops_requests_marks_remaining_and_exits_nonzero(self):
        items = [
            {
                'id': '66d19b54000000001d03a93d',
                'title': '第一条',
                'content_type': 'image',
                'image_urls': ['https://ci.xiaohongshu.com/first.jpg'],
                'image_count': None,
                'image_urls_complete': False,
            },
            {
                'id': '66d19b54000000001d03a93e',
                'title': '第二条',
                'content_type': 'image',
                'image_urls': ['https://ci.xiaohongshu.com/second.jpg'],
                'image_count': None,
                'image_urls_complete': False,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'visible_items.json'
            out = Path(tmp) / 'image_items.json'
            src.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
            argv = [
                'enrich_note_images.py', str(src), str(out),
                '--allow-detail-requests', '--max-items', '2',
                '--request-interval', '0',
            ]
            with patch.object(sys, 'argv', argv), \
                    patch(
                        'enrich_note_images.fetch_note_html',
                        side_effect=SecurityBlockError('安全验证'),
                    ) as fetch:
                with self.assertRaises(SystemExit) as stopped:
                    enrich_note_images_main()
            rows = json.loads(out.read_text(encoding='utf-8'))

        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(rows[0]['image_enrichment_status'], 'security_blocked')
        self.assertEqual(rows[1]['image_enrichment_status'], 'not_requested_after_security_block')
        self.assertFalse(rows[0]['image_urls_complete'])
        self.assertFalse(rows[1]['image_urls_complete'])

    def test_default_enrichment_never_fetches_a_detail_page(self):
        items = [{
            'id': '66d19b54000000001d03a93d',
            'title': '第一条',
            'content_type': 'image',
            'image_urls': ['https://ci.xiaohongshu.com/first.jpg'],
            'image_count': None,
            'image_urls_complete': False,
        }]
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'visible_items.json'
            out = Path(tmp) / 'image_items.json'
            src.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
            with patch.object(sys, 'argv', ['enrich_note_images.py', str(src), str(out)]), \
                    patch('enrich_note_images.fetch_note_html') as fetch:
                enrich_note_images_main()
            rows = json.loads(out.read_text(encoding='utf-8'))

        fetch.assert_not_called()
        self.assertEqual(rows[0]['image_enrichment_status'], 'detail_request_not_enabled')

    def test_workbuddy_rejects_cookie_less_detail_enrichment(self):
        with patch.dict('os.environ', {'XHS_HOST': 'workbuddy'}, clear=False), \
                patch('enrich_note_images.fetch_note_html') as fetch:
            with self.assertRaisesRegex(
                RuntimeError,
                'xhs_workbuddy_capture',
            ):
                enrich_note_images_main()

        fetch.assert_not_called()

    def test_workbuddy_capture_artifact_rejects_cookie_less_enrichment_without_host_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            src = directory / 'visible_items.json'
            out = directory / 'image_items.json'
            src.write_text('[]', encoding='utf-8')
            (directory / 'crawl_manifest.json').write_text(
                json.dumps({'capture_mode': 'workbuddy_segmented'}),
                encoding='utf-8',
            )
            argv = ['enrich_note_images.py', str(src), str(out)]
            with patch.object(sys, 'argv', argv), \
                    patch.dict(
                        'os.environ',
                        {'XHS_HOST': '', 'WORKBUDDY_CONFIG_DIR': ''},
                        clear=False,
                    ), \
                    patch('enrich_note_images.fetch_note_html') as fetch:
                with self.assertRaisesRegex(
                    RuntimeError,
                    'organizing_depth=light',
                ):
                    enrich_note_images_main()

        fetch.assert_not_called()

    def test_security_halt_blocks_later_resume_before_second_detail_request(self):
        items = [{
            'id': '66d19b54000000001d03a93d',
            'title': '第一条',
            'content_type': 'image',
            'image_urls': ['https://ci.xiaohongshu.com/first.jpg'],
            'image_count': None,
            'image_urls_complete': False,
        }]
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'visible_items.json'
            out = Path(tmp) / 'image_items.json'
            src.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
            first_argv = [
                'enrich_note_images.py', str(src), str(out), '--allow-detail-requests', '--max-items', '1',
            ]
            with patch.object(sys, 'argv', first_argv), \
                    patch('enrich_note_images.fetch_note_html', side_effect=SecurityBlockError('安全验证')):
                with self.assertRaises(SystemExit):
                    enrich_note_images_main()
            state = load_safety_state(Path(tmp) / 'xhs_safety_state.json')
            with patch.object(sys, 'argv', first_argv + ['--resume']), \
                    patch('enrich_note_images.fetch_note_html') as fetch:
                with self.assertRaises(SafetyHaltedError):
                    enrich_note_images_main()

        self.assertEqual(state['state'], 'security_halted')
        fetch.assert_not_called()

    def test_security_marker_still_wins_when_setup_state_has_an_invalid_image_list(self):
        note_id = '66d19b54000000001d03a93d'
        html = self.setup_state_html(
            {
                'noteId': note_id,
                'type': 'normal',
                'imageList': [],
            },
            suffix='<div>安全验证</div>',
        )
        with self.assertRaises(SecurityBlockError):
            enrich_item_from_html(
                {'id': note_id, 'content_type': 'image'},
                html,
            )

    def test_classifier_blocks_metadata_fallback_for_incomplete_or_failed_image_ocr(self):
        for ocr_status, image_set_complete, fingerprint, expected_reason in (
            ('incomplete_image_set', False, '', 'ocr_incomplete_image_set'),
            ('error', True, '', 'ocr_error'),
            ('ok', True, '', 'ocr_run_fingerprint_missing'),
        ):
            with self.subTest(ocr_status=ocr_status), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                visible_path = tmp_path / 'visible.json'
                classification_path = tmp_path / 'classification.json'
                visible_path.write_text(json.dumps([
                    {
                        'id': 'image-needs-complete-ocr',
                        'title': '滑雪固定器设置',
                        'desc': '单板滑雪固定器角度',
                        'tags': ['滑雪'],
                        'card_text': '滑雪 单板 固定器',
                        'content_type': 'image',
                        'image_urls': ['https://ci.xiaohongshu.com/cover.jpg'],
                        'image_count': 1,
                        'image_urls_complete': True,
                    }
                ], ensure_ascii=False), encoding='utf-8')
                ocr_entry = {
                    'id': 'image-needs-complete-ocr',
                    'status': ocr_status,
                    'image_set_complete': image_set_complete,
                    'ocr_run_fingerprint': fingerprint,
                    'image_count_processed': 1 if image_set_complete else 0,
                    'ocr_text': '固定器',
                    'ocr_confidence': 0.9,
                    'images': [],
                    'error': 'OCR evidence unavailable',
                }
                argv = [
                    'classify_items.py',
                    str(visible_path),
                    str(classification_path),
                    '--ocr-results',
                    str(tmp_path / 'ocr_results.json'),
                ]
                with patch.object(sys, 'argv', argv), \
                        patch('classify_items.perform_ocr_for_items', return_value=[ocr_entry]):
                    classify_items_main()

                row = json.loads(classification_path.read_text(encoding='utf-8'))[0]
                self.assertEqual(row['target_board'], '')
                self.assertEqual(row['confidence'], 'low')
                self.assertEqual(row['classification_basis'], 'image_ocr_incomplete')
                self.assertEqual(row['review_state'], 'image_ocr_incomplete')
                self.assertEqual(row['reason'], [expected_reason])
                self.assertEqual(row['ocr_status'], ocr_status)
                self.assertEqual(row['ocr_run_fingerprint'], '')

    def test_examples_show_complete_multi_image_evidence_and_exclude_video_ocr(self):
        visible = json.loads((ROOT / 'examples' / 'visible_items.example.json').read_text(encoding='utf-8'))
        image_items = json.loads((ROOT / 'examples' / 'image_items.example.json').read_text(encoding='utf-8'))
        ocr_results = json.loads((ROOT / 'examples' / 'ocr_results.example.json').read_text(encoding='utf-8'))
        classification = json.loads((ROOT / 'examples' / 'classification.example.json').read_text(encoding='utf-8'))

        visible_image = next(item for item in visible if item['content_type'] == 'image')
        self.assertFalse(visible_image['image_urls_complete'])
        self.assertIsNone(visible_image['image_count'])

        image_item = next(item for item in image_items if item['content_type'] == 'image')
        video_item = next(item for item in image_items if item['content_type'] == 'video')
        self.assertTrue(image_item['image_urls_complete'])
        self.assertEqual(image_item['image_count'], len(image_item['image_urls']))
        self.assertEqual(image_item['cover_image_url'], image_item['image_urls'][0])

        self.assertEqual([entry['id'] for entry in ocr_results], [image_item['id']])
        self.assertNotIn(video_item['id'], {entry['id'] for entry in ocr_results})
        ocr_entry = ocr_results[0]
        self.assertTrue(ocr_entry['image_set_complete'])
        self.assertEqual(ocr_entry['image_count_processed'], len(image_item['image_urls']))
        self.assertEqual(len(ocr_entry['images']), len(image_item['image_urls']))
        self.assertEqual(ocr_entry['images'][-1]['status'], 'ok')
        self.assertEqual(ocr_entry['images'][-1]['ocr_text'], '')
        self.assertEqual(ocr_entry['images'][-1]['ocr_lines'], [])
        self.assertIn('第1张：老钱风西装', ocr_entry['ocr_text'])
        self.assertIn('第2张：香水推荐', ocr_entry['ocr_text'])
        self.assertNotIn('第3张：', ocr_entry['ocr_text'])

        image_classification = next(row for row in classification if row['id'] == image_item['id'])
        video_classification = next(row for row in classification if row['id'] == video_item['id'])
        self.assertEqual(image_classification['ocr_image_count'], len(image_item['image_urls']))
        self.assertEqual(len(image_classification['ocr_image_evidence']), len(image_item['image_urls']))
        self.assertEqual(video_classification['ocr_status'], 'skipped')
        self.assertEqual(video_classification['ocr_image_evidence'], [])


if __name__ == '__main__':
    unittest.main()
