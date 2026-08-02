#!/usr/bin/env python3
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'finalize_album_deep_report_data.py'
SCREEN_VIDEO_ID = 'a' * 24
IMAGE_ID = 'f' * 24
OTHER_VIDEO_IDS = [f'{value:024x}' for value in range(1, 3)]
VIDEO_IDS = [SCREEN_VIDEO_ID, *OTHER_VIDEO_IDS]


def stable_sha256(value) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def detail_for(note_id: str, evidence: dict, marker: str) -> dict:
    return {
        'one_line': f'{marker}：{note_id}',
        'what_it_says': f'{marker}正文：{note_id}',
        'path_table': [],
        'direct_statements': ['直接陈述'],
        'key_points': ['关键点'],
        'practical_takeaways': ['可采用内容'],
        'boundaries': ['边界'],
        'watch_segments': [],
        'evidence_contract': {
            'basis': 'mimo_audio_plus_mimo_vl_full_timeline',
            'transcript_sha256': evidence['transcript_sha256'],
            'visual_evidence_sha256': evidence['visual_evidence_hash'],
            'visual_provider': 'mimo-vl-mlx',
            'visual_prompt_version': evidence['prompt_version'],
            'frame_count': len(evidence['frames']),
            'screen_text_segment_count': len(evidence['screen_text_timeline']['segments']),
        },
    }


def visual_evidence(note_id: str, *, report_text_track: str = 'mimo_audio') -> dict:
    segments = [{'start': 0.0, 'end': 1.0, 'text': f'听觉正文 {note_id}'}]
    transcript_hash = stable_sha256(segments)
    frames = [
        {
            'index': 0,
            'timestamp_seconds': 0.0,
            'endpoint': 'start',
            'sha256': 'a' * 64,
            'ocr_text': '起点文字',
            'ocr_provider': 'macos_vision',
        },
        {
            'index': 1,
            'timestamp_seconds': 1.0,
            'endpoint': 'end',
            'sha256': 'b' * 64,
            'ocr_text': '终点文字',
            'ocr_provider': 'macos_vision',
        },
    ]
    analyzed_frames = [
        {
            'index': frame['index'],
            'timestamp_seconds': frame['timestamp_seconds'],
            'sha256': frame['sha256'],
            'observation': '已分析画面',
            'visible_text': [frame['ocr_text']],
            'actions': ['动作'],
            'uncertainty': '',
        }
        for frame in frames
    ]
    evidence = {
        'evidence_version': 'watchbrief_v5.visual_evidence.v1',
        'prompt_version': 'watchbrief_v5.mimo_visual_prompt.v1',
        'provider': {
            'provider': 'mimo-vl-mlx',
            'model': 'test-mimo-vl',
            'version': 'mlx-vlm-test',
        },
        'inference': {'batch_count': 1},
        'video_sha256': 'c' * 64,
        'duration_seconds': 1.0,
        'sampling': {
            'includes_start': True,
            'includes_end': True,
            'timestamps_seconds': [0.0, 1.0],
        },
        'frames': frames,
        'analysis': {
            'overall_visual_summary': '完整时轴画面已分析。',
            'frames': analyzed_frames,
            'visual_caveats': [],
        },
        'screen_text_timeline': {
            'provider': 'macos_vision',
            'includes_start': True,
            'includes_end': True,
            'verbatim_visible_text': True,
            'text_detected': True,
            'segments': [
                {
                    'start': 0.0,
                    'end': 1.0,
                    'text': '逐字屏幕文字',
                    'sample_frame_sha256': frames[0]['sha256'],
                }
            ],
        },
        'transcript_sha256': transcript_hash,
        'audio_evidence': {
            'provider': 'mimo_audio',
            'transcript_sha256': transcript_hash,
            'segments': segments,
        },
        'report_text_track': report_text_track,
    }
    if report_text_track == 'screen_text':
        evidence['multimodal_transcript_sha256'] = 'd' * 64
    evidence['visual_evidence_hash'] = stable_sha256(evidence)
    return evidence


class FinalizeAlbumDeepReportDataTests(unittest.TestCase):
    def fixtures(self, directory: Path):
        ordered_ids = [VIDEO_IDS[0], IMAGE_ID, *VIDEO_IDS[1:]]
        items = [
            {
                'id': note_id,
                'title': f'笔记 {position}',
                'content_type': 'image' if note_id == IMAGE_ID else 'video',
            }
            for position, note_id in enumerate(ordered_ids, 1)
        ]
        classification = [
            {
                'id': note_id,
                'content_type': 'image' if note_id == IMAGE_ID else 'video',
                'confidence': 'low',
                'content_summary': '旧摘要',
                'visual_status': 'failed' if note_id != IMAGE_ID else '',
                'visual_reason_code': 'old_failure' if note_id != IMAGE_ID else '',
            }
            for note_id in reversed(ordered_ids)
        ]
        visual_items = {
            note_id: visual_evidence(
                note_id,
                report_text_track='screen_text' if note_id == SCREEN_VIDEO_ID else 'mimo_audio',
            )
            for note_id in VIDEO_IDS
        }
        generated_details = {
            'contract': 'xiaohongshu.album.watchbrief_details.v2',
            'items': {
                note_id: detail_for(note_id, visual_items[note_id], 'Qwen')
                for note_id in OTHER_VIDEO_IDS
            },
        }
        screen_video_details = {
            'contract': 'xiaohongshu.album.watchbrief_details.v2',
            'items': {
                SCREEN_VIDEO_ID: detail_for(
                    SCREEN_VIDEO_ID,
                    visual_items[SCREEN_VIDEO_ID],
                    'screen track',
                )
            },
        }
        image_details = {
            'contract': 'xiaohongshu.album.watchbrief_details.v2',
            'items': {
                IMAGE_ID: {
                    'one_line': '完整三图 OCR 摘要。',
                    'what_it_says': '完整三图 OCR 正文。',
                    'path_table': [],
                    'direct_statements': ['图文明示内容'],
                    'key_points': ['图文关键点'],
                    'practical_takeaways': ['图文可采用内容'],
                    'boundaries': ['只覆盖图片明确文字'],
                    'watch_segments': [],
                },
            },
        }
        ocr_results = [{
            'id': IMAGE_ID,
            'status': 'ok',
            'ocr_provider': 'swift',
            'ocr_run_fingerprint': 'd' * 64,
            'image_count_declared': 3,
            'image_count_available': 3,
            'image_count_processed': 3,
            'image_set_complete': True,
            'image_set_sha256': 'e' * 64,
            'images': [
                {
                    'image_index': index,
                    'status': 'ok',
                    'ocr_text': f'第 {index + 1} 张文字',
                    'ocr_provider': 'swift',
                    'image_sha256': f'{index + 1:064x}',
                    'source_url_sha256': f'{index + 101:064x}',
                }
                for index in range(3)
            ],
        }]
        values = {
            'items': items,
            'classification': classification,
            'video_details': generated_details,
            'screen_video_details': screen_video_details,
            'image_details': image_details,
            'ocr_results': ocr_results,
            'visual_evidence': {
                'contract': 'xiaohongshu.album.visual_evidence.v1',
                'items': visual_items,
            },
        }
        paths = {}
        for name, value in values.items():
            path = directory / f'{name}.json'
            write_json(path, value)
            paths[name] = path
        return values, paths

    def run_finalizer(self, directory: Path, mutate=None):
        values, paths = self.fixtures(directory)
        if mutate is not None:
            mutate(values)
            for name, value in values.items():
                write_json(paths[name], value)
        details_output = directory / 'deep-details.json'
        classification_output = directory / 'deep-classification.json'
        command = [
            sys.executable,
            str(SCRIPT),
            '--items', str(paths['items']),
            '--classification', str(paths['classification']),
            '--detail-bundle', str(paths['video_details']),
            '--detail-bundle', str(paths['screen_video_details']),
            '--detail-bundle', str(paths['image_details']),
            '--ocr-results', str(paths['ocr_results']),
            '--visual-evidence', str(paths['visual_evidence']),
            '--output-details', str(details_output),
            '--output-classification', str(classification_output),
        ]
        process = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
        return process, details_output, classification_output, values

    def test_complete_evidence_produces_arbitrary_ordered_details_and_classifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, values = self.run_finalizer(Path(tmp))

            self.assertEqual(process.returncode, 0, process.stderr)
            details = json.loads(details_path.read_text(encoding='utf-8'))
            classification = json.loads(classification_path.read_text(encoding='utf-8'))
            expected_order = [row['id'] for row in values['items']]
            self.assertEqual(details['contract'], 'xiaohongshu.album.watchbrief_details.v3')
            self.assertEqual(list(details['items']), expected_order)
            self.assertEqual([row['id'] for row in classification], expected_order)
            self.assertTrue(
                details['items'][SCREEN_VIDEO_ID]['one_line'].startswith('screen track')
            )
            image_contract = details['items'][IMAGE_ID]['evidence_contract']
            self.assertEqual(image_contract['basis'], 'complete_image_ocr')
            self.assertEqual(image_contract['image_set_sha256'], 'e' * 64)
            self.assertEqual(image_contract['image_count'], 3)
            visual_items = values['visual_evidence']['items']
            for row in classification:
                if row['id'] == IMAGE_ID:
                    self.assertEqual(row['ocr_status'], 'ok')
                    self.assertTrue(row['ocr_image_set_complete'])
                    continue
                detail = details['items'][row['id']]
                self.assertEqual(row['confidence'], 'high')
                self.assertEqual(row['visual_status'], 'analyzed')
                self.assertEqual(row['visual_reason_code'], '')
                self.assertEqual(row['content_summary'], detail['one_line'])
                self.assertEqual(
                    row['visual_evidence_sha256'],
                    visual_items[row['id']]['visual_evidence_hash'],
                )

    def test_missing_one_video_evidence_blocks_both_outputs(self):
        def mutate(values):
            values['visual_evidence']['items'].pop(OTHER_VIDEO_IDS[-1])

        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, _ = self.run_finalizer(
                Path(tmp),
                mutate=mutate,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn('visual evidence bundle id 不完整', process.stderr)
            self.assertFalse(details_path.exists())
            self.assertFalse(classification_path.exists())

    def test_each_detail_bundle_contract_must_match_its_visual_evidence(self):
        def mutate(values):
            contract = values['screen_video_details']['items'][SCREEN_VIDEO_ID][
                'evidence_contract'
            ]
            contract['visual_evidence_sha256'] = '0' * 64

        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, _ = self.run_finalizer(
                Path(tmp),
                mutate=mutate,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn(
                f'视频 detail 证据合同不一致：{SCREEN_VIDEO_ID}#visual_evidence_sha256',
                process.stderr,
            )
            self.assertFalse(details_path.exists())
            self.assertFalse(classification_path.exists())

    def test_image_ocr_must_be_complete_with_matching_dynamic_counts_and_valid_hashes(self):
        cases = (
            (
                lambda values: values['ocr_results'][0].update(
                    {'image_set_complete': False}
                ),
                '图文 OCR 未完整成功',
            ),
            (
                lambda values: values['ocr_results'][0].update(
                    {'image_count_processed': 2}
                ),
                '图文 OCR 声明、可用和已处理图片数必须一致',
            ),
            (
                lambda values: values['ocr_results'][0].update(
                    {'image_set_sha256': 'not-a-hash'}
                ),
                '图文集合 hash',
            ),
        )
        for mutate, expected_error in cases:
            with self.subTest(expected_error=expected_error), tempfile.TemporaryDirectory() as tmp:
                process, details_path, classification_path, _ = self.run_finalizer(
                    Path(tmp),
                    mutate=mutate,
                )

                self.assertNotEqual(process.returncode, 0)
                self.assertIn(expected_error, process.stderr)
                self.assertFalse(details_path.exists())
                self.assertFalse(classification_path.exists())

    def test_detail_bundles_must_exactly_cover_items_with_deep_basis(self):
        cases = (
            (
                lambda values: values['video_details']['items'].pop(OTHER_VIDEO_IDS[0]),
                'details bundles id 不完整',
            ),
            (
                lambda values: values['video_details']['items'][OTHER_VIDEO_IDS[0]][
                    'evidence_contract'
                ].update({'basis': 'transcript_only'}),
                '视频 detail 证据 basis 无效',
            ),
        )
        for mutate, expected_error in cases:
            with self.subTest(expected_error=expected_error), tempfile.TemporaryDirectory() as tmp:
                process, details_path, classification_path, _ = self.run_finalizer(
                    Path(tmp),
                    mutate=mutate,
                )

                self.assertNotEqual(process.returncode, 0)
                self.assertIn(expected_error, process.stderr)
                self.assertFalse(details_path.exists())
                self.assertFalse(classification_path.exists())

    def test_corrupt_visual_evidence_hash_blocks_both_outputs(self):
        def mutate(values):
            evidence = values['visual_evidence']['items'][OTHER_VIDEO_IDS[0]]
            evidence['analysis']['overall_visual_summary'] = 'hash 后被篡改'

        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, _ = self.run_finalizer(
                Path(tmp),
                mutate=mutate,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn('视觉证据 hash 不一致', process.stderr)
            self.assertFalse(details_path.exists())
            self.assertFalse(classification_path.exists())

    def test_final_frame_screen_text_may_be_a_point_observation(self):
        target_id = OTHER_VIDEO_IDS[0]

        def mutate(values):
            evidence = values['visual_evidence']['items'][target_id]
            evidence['screen_text_timeline']['segments'].append({
                'start': 1.0,
                'end': 1.0,
                'text': '终点逐字屏幕文字',
                'sample_frame_sha256': evidence['frames'][-1]['sha256'],
            })
            evidence.pop('visual_evidence_hash')
            evidence['visual_evidence_hash'] = stable_sha256(evidence)
            contract = values['video_details']['items'][target_id]['evidence_contract']
            contract['visual_evidence_sha256'] = evidence['visual_evidence_hash']
            contract['screen_text_segment_count'] = 2

        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, _ = self.run_finalizer(
                Path(tmp),
                mutate=mutate,
            )

            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(details_path.exists())
            self.assertTrue(classification_path.exists())

    def test_audio_primary_video_accepts_successful_ocr_with_no_detected_text(self):
        target_id = OTHER_VIDEO_IDS[0]

        def mutate(values):
            evidence = values['visual_evidence']['items'][target_id]
            evidence['screen_text_timeline']['segments'] = []
            evidence['screen_text_timeline']['text_detected'] = False
            evidence.pop('visual_evidence_hash')
            evidence['visual_evidence_hash'] = stable_sha256(evidence)
            contract = values['video_details']['items'][target_id]['evidence_contract']
            contract['visual_evidence_sha256'] = evidence['visual_evidence_hash']
            contract['screen_text_segment_count'] = 0

        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, _ = self.run_finalizer(
                Path(tmp),
                mutate=mutate,
            )

            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(details_path.exists())
            self.assertTrue(classification_path.exists())

    def test_nonterminal_zero_length_screen_text_is_rejected(self):
        target_id = OTHER_VIDEO_IDS[0]

        def mutate(values):
            evidence = values['visual_evidence']['items'][target_id]
            evidence['screen_text_timeline']['segments'].append({
                'start': 0.0,
                'end': 0.0,
                'text': '非法零时长文字',
                'sample_frame_sha256': evidence['frames'][0]['sha256'],
            })
            evidence.pop('visual_evidence_hash')
            evidence['visual_evidence_hash'] = stable_sha256(evidence)
            contract = values['video_details']['items'][target_id]['evidence_contract']
            contract['visual_evidence_sha256'] = evidence['visual_evidence_hash']
            contract['screen_text_segment_count'] = 2

        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, _ = self.run_finalizer(
                Path(tmp),
                mutate=mutate,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn('屏幕文字分段时间无效', process.stderr)
            self.assertFalse(details_path.exists())
            self.assertFalse(classification_path.exists())

    def test_primary_screen_track_may_use_its_own_dense_frame_sampling(self):
        def mutate(values):
            evidence = values['visual_evidence']['items'][SCREEN_VIDEO_ID]
            evidence['screen_text_timeline']['segments'][0][
                'sample_frame_sha256'
            ] = '9' * 64
            evidence.pop('visual_evidence_hash')
            evidence['visual_evidence_hash'] = stable_sha256(evidence)
            contract = values['screen_video_details']['items'][SCREEN_VIDEO_ID][
                'evidence_contract'
            ]
            contract['visual_evidence_sha256'] = evidence['visual_evidence_hash']

        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, _ = self.run_finalizer(
                Path(tmp),
                mutate=mutate,
            )

            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(details_path.exists())
            self.assertTrue(classification_path.exists())

    def test_audio_primary_track_screen_text_must_bind_a_timeline_frame(self):
        target_id = OTHER_VIDEO_IDS[0]

        def mutate(values):
            evidence = values['visual_evidence']['items'][target_id]
            evidence['screen_text_timeline']['segments'][0][
                'sample_frame_sha256'
            ] = '9' * 64
            evidence.pop('visual_evidence_hash')
            evidence['visual_evidence_hash'] = stable_sha256(evidence)
            contract = values['video_details']['items'][target_id]['evidence_contract']
            contract['visual_evidence_sha256'] = evidence['visual_evidence_hash']

        with tempfile.TemporaryDirectory() as tmp:
            process, details_path, classification_path, _ = self.run_finalizer(
                Path(tmp),
                mutate=mutate,
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn('屏幕文字没有绑定当前帧', process.stderr)
            self.assertFalse(details_path.exists())
            self.assertFalse(classification_path.exists())


if __name__ == '__main__':
    unittest.main()
