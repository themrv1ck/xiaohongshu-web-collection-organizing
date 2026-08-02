#!/usr/bin/env python3
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'build_album_deep_details.py'
NOTE_ID = 'a' * 24


def stable_sha256(value) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class AlbumDeepDetailsTests(unittest.TestCase):
    def fake_qwen_payload(self, mode='valid'):
        payload = {
            'one_line': '视频展示深蹲偏移后，如何用 90/90 比较两侧髋旋转并复测。',
            'what_it_says': '视频从深蹲时臀部偏向一侧入手，用 90/90 换边比较两侧能否坐直，再根据前腿外旋或后腿内旋受限选择练习，最后回到深蹲复测。',
            'path_table': [
                {'label': '问题表现', 'text': '画面显示深蹲时臀部偏移，两侧膝盖高度和打开程度不同。'},
                {'label': '换边自测', 'text': '视频展示前腿、后腿和两腿之间均约 90°，并换边比较能否坐直。'},
                {'label': '分向处理', 'text': '屏幕文字显示前腿外旋和后腿内旋受限需分别选择对应练习。'},
                {'label': '回到复测', 'text': '视频展示练习后先复测 90/90 坐直，再回到深蹲观察偏移变化。'},
            ],
            'direct_statements': [
                '画面显示深蹲时两侧膝盖高度和打开程度并不一致。',
                '屏幕文字显示出现这种左右差异时要检查髋关节旋转活动度。',
            ],
            'key_points': [
                '视频把左右换边比较设为判断活动度差异的关键步骤。',
                '视频把坐直改善和重新深蹲组成练习后的复测闭环。',
            ],
            'practical_takeaways': [
                '按视频演示，应先换边完成 90/90 比较，再针对受限方向练习并复测。',
            ],
            'boundaries': [
                '视频没有证明所有深蹲偏移都由髋旋转活动度不足造成。',
                '视频没有给出练习次数、阻力、频率或疼痛人群的适用条件。',
            ],
            'watch_segments': [
                {
                    'anchor_id': 'vf_0000',
                    'title': '深蹲偏移和两膝差异',
                    'reason': '画面直接展示臀部偏向一侧，以及两膝高度和打开程度不同。',
                },
                {
                    'anchor_id': 'vf_0001',
                    'title': '90/90 换边判读',
                    'reason': '画面展示换边比较能否坐直，屏幕文字区分前腿外旋和后腿内旋。',
                },
                {
                    'anchor_id': 'vf_0003',
                    'title': '松解后回到深蹲复测',
                    'reason': '画面展示向内推膝做关节松解，随后重新深蹲检查偏移是否改变。',
                },
            ],
        }
        if mode == 'duplicate_text':
            payload['key_points'][0] = payload['direct_statements'][0]
        elif mode == 'forged_anchor':
            payload['watch_segments'][0]['anchor_id'] = 'vf_9999'
        elif mode == 'duplicate_anchor':
            payload['watch_segments'][1]['anchor_id'] = 'vf_0000'
        elif mode == 'out_of_order':
            payload['watch_segments'][1]['anchor_id'] = 'vf_0003'
            payload['watch_segments'][2]['anchor_id'] = 'vf_0001'
        elif mode == 'overlapping_anchor':
            payload['watch_segments'][2]['anchor_id'] = 'vf_0002'
        elif mode == 'model_coordinates':
            payload['watch_segments'][0]['start'] = '00:00'
        elif mode == 'missing_attribution':
            payload['practical_takeaways'][0] = '先换边完成 90/90 比较，再针对受限方向练习并复测。'
        return payload

    def make_fake_watchbefore(self, directory: Path, *, qwen_mode='valid') -> Path:
        root = directory / 'watchbefore'
        analyzer = root / 'scripts' / 'analyzer'
        analyzer.mkdir(parents=True)
        (root / 'scripts' / '__init__.py').write_text('', encoding='utf-8')
        (analyzer / '__init__.py').write_text('', encoding='utf-8')
        response_payload = self.fake_qwen_payload(qwen_mode)
        (analyzer / 'local_extract.py').write_text(
            """import copy
import json

REPORT_RESPONSE = json.loads(%r)
MODE = %r
GENERATION_CALLS = 0

def choose_qwen_model(**kwargs):
    assert kwargs['model_id'].startswith('qwen')
    return kwargs['model_id']

def configured_qwen_api_base(value):
    return value.rstrip('/')

def http_json(url, **kwargs):
    global GENERATION_CALLS
    request = kwargs['payload']
    system = request['messages'][0]['content']
    prompt = request['messages'][1]['content']
    assert system.startswith('你是小红书专辑深度报告编辑')
    assert 'audio_segments' in prompt
    assert 'visual_anchors' in prompt
    assert 'screen_text_segments' in prompt
    assert '60 个及以上非空白字符' in system
    assert '长英文论文' in system
    assert '连续照抄英文标题' in prompt
    assert '不要添加证据未明确表达的“而非”“只能”“禁止”“不适用”等排他性判断' in system
    assert '数值中的“至少”“约”“每次”“每天”“组”“次”必须按证据原样保留' in prompt
    assert request['max_tokens'] == 4096
    GENERATION_CALLS += 1
    payload = copy.deepcopy(REPORT_RESPONSE)
    if MODE == 'second_invalid' and GENERATION_CALLS == 2:
        payload['watch_segments'][0]['anchor_id'] = 'vf_9999'
    content = json.dumps(payload, ensure_ascii=False)
    if MODE == 'wrapped_json':
        content = '```json\\n' + content + '\\n```'
    response = {'choices': [{'message': {'content': content}}]}
    callback = kwargs.get('raw_response_callback')
    if callback:
        callback(json.dumps(response, ensure_ascii=False))
    return response

def extract_message_text(response):
    return response['choices'][0]['message']['content']

def parse_qwen_message_json(raw):
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value
""" % (json.dumps(response_payload, ensure_ascii=False), qwen_mode),
            encoding='utf-8',
        )
        return root

    def fixtures(self, directory: Path, *, include_visual=True, corrupt_visual_hash=False):
        items = [{
            'id': NOTE_ID,
            'title': '示例：用 90/90 换边比较后复测深蹲',
            'user': '示例作者',
        }]
        classification = [{
            'id': NOTE_ID,
            'confidence': 'low',
            'content_summary': '旧的 audio-only 分类没有识别主题。',
        }]
        segments = [{
            'start': 0.0,
            'end': 59.98,
            'text': 'One, one, one. Let me see that.',
        }]
        transcript_hash = stable_sha256(segments)
        transcripts = [{
            'id': NOTE_ID,
            'status': 'success',
            'source_kind': 'mimo_audio',
            'transcript_sha256': transcript_hash,
            'segments': segments,
            'coverage': {
                'transcript_quality_passed': True,
                'video_duration_seconds': 59.98,
            },
        }]
        frame_specs = (
            (0, 0.0, 'a', 'start', '深蹲时屁股偏向一侧'),
            (1, 30.0, 'b', '', '前腿90度后腿90度'),
            (2, 34.0, 'd', '', '换边后对比坐直程度'),
            (3, 59.98, 'c', 'end', '向内推膝盖做关节松解'),
        )
        frames = [
            {
                'index': index,
                'timestamp_seconds': timestamp,
                'endpoint': endpoint,
                'sha256': character * 64,
                'ocr_text': text,
            }
            for index, timestamp, character, endpoint, text in frame_specs
        ]
        analyzed_frames = [
            {
                'index': index,
                'timestamp_seconds': timestamp,
                'sha256': character * 64,
                'observation': f'画面展示：{text}。',
                'visible_text': [text],
                'actions': ['换边比较并复测'],
                'uncertainty': '',
            }
            for index, timestamp, character, _endpoint, text in frame_specs
        ]
        evidence = {
            'evidence_version': 'watchbrief_v5.visual_evidence.v1',
            'prompt_version': 'watchbrief_v5.mimo_visual_prompt.v1',
            'provider': {'provider': 'mimo-vl-mlx', 'model': 'test', 'version': 'mlx-vlm-0.5.0'},
            'inference': {'batch_count': 1, 'max_frames_per_request': 1},
            'video_sha256': 'b' * 64,
            'duration_seconds': 59.98,
            'sampling': {
                'includes_start': True,
                'includes_end': True,
                'timestamps_seconds': [0.0, 30.0, 59.98],
            },
            'frames': frames,
            'analysis': {
                'overall_visual_summary': '画面用 90/90 检查髋旋转并展示练习。',
                'frames': analyzed_frames,
                'visual_caveats': ['画面不能证明练习适合所有人。'],
            },
            'transcript_sha256': transcript_hash,
            'audio_evidence': {
                'provider': 'mimo_audio',
                'transcript_sha256': transcript_hash,
                'segments': segments,
                'content_note': '音轨是背景歌，没有中文讲解。',
            },
            'report_text_track': 'screen_text',
            'screen_text_timeline': {
                'provider': 'macos_vision',
                'verbatim_visible_text': True,
                'text_detected': True,
                'segments': [
                    {
                        'start': timestamp,
                        'end': frame_specs[position + 1][1] if position + 1 < len(frame_specs) else 59.98,
                        'text': text,
                        'sample_frame_sha256': character * 64,
                    }
                    for position, (_index, timestamp, character, _endpoint, text) in enumerate(frame_specs)
                ],
            },
        }
        evidence['visual_evidence_hash'] = stable_sha256(evidence)
        if corrupt_visual_hash:
            evidence['visual_evidence_hash'] = 'f' * 64

        paths = {}
        for name, value in (
            ('items', items),
            ('classification', classification),
            ('transcripts', transcripts),
            ('visual', {'items': {NOTE_ID: evidence} if include_visual else {}}),
        ):
            path = directory / f'{name}.json'
            write_json(path, value)
            paths[name] = path
        return paths

    def run_builder(
        self,
        directory: Path,
        *,
        include_visual=True,
        corrupt_visual_hash=False,
        qwen_mode='valid',
    ):
        fixtures = self.fixtures(
            directory,
            include_visual=include_visual,
            corrupt_visual_hash=corrupt_visual_hash,
        )
        output = directory / 'details.json'
        command = [
            sys.executable,
            str(SCRIPT),
            '--items', str(fixtures['items']),
            '--classification', str(fixtures['classification']),
            '--transcripts', str(fixtures['transcripts']),
            '--visual-evidence', str(fixtures['visual']),
            '--watchbefore-root', str(self.make_fake_watchbefore(directory, qwen_mode=qwen_mode)),
            '--note-id', NOTE_ID,
            '--output', str(output),
        ]
        process = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
        return process, output

    def test_low_classification_still_generates_from_audio_and_visual_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            process, output = self.run_builder(Path(tmp))

            self.assertEqual(process.returncode, 0, process.stderr)
            detail = json.loads(output.read_text(encoding='utf-8'))['items'][NOTE_ID]
            serialized = json.dumps(detail, ensure_ascii=False)
            self.assertIn('90/90', serialized)
            self.assertIn('向内推膝做关节松解', serialized)
            self.assertEqual(
                detail['evidence_contract']['basis'],
                'mimo_audio_plus_mimo_vl_full_timeline',
            )
            self.assertEqual(detail['evidence_contract']['report_text_track'], 'screen_text')
            self.assertEqual(len(detail['watch_segments']), 3)
            expected_windows = [('00:00', '00:05'), ('00:30', '00:35'), ('00:55', '01:00')]
            expected_frames = [
                (0, 0.0, 'a' * 64),
                (1, 30.0, 'b' * 64),
                (3, 59.98, 'c' * 64),
            ]
            for segment, (start, end), (index, timestamp, sha256) in zip(
                detail['watch_segments'], expected_windows, expected_frames
            ):
                self.assertEqual((segment['start'], segment['end']), (start, end))
                self.assertEqual(segment['evidence_refs'], [{
                    'type': 'visual_frame',
                    'index': index,
                    'timestamp_seconds': timestamp,
                    'sha256': sha256,
                }])
                self.assertNotIn('anchor_id', segment)
            for banned in ('当前文字稿不足', '标题信息与文字稿无法互相印证', '标题显示它可能'):
                self.assertNotIn(banned, serialized)

    def test_missing_or_corrupt_visual_evidence_blocks_deep_output(self):
        for kwargs, expected in (
            ({'include_visual': False}, '缺少 MiMo-VL 视觉证据'),
            ({'corrupt_visual_hash': True}, '视觉证据 hash 不一致'),
        ):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                process, output = self.run_builder(Path(tmp), **kwargs)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn(expected, process.stderr)
                self.assertFalse(output.exists())

    def test_invalid_final_qwen_contract_hard_fails_without_output(self):
        for qwen_mode, expected in (
            ('duplicate_text', 'direct_statements 与 key_points 不得重复'),
            ('forged_anchor', 'anchor_id 未绑定宿主视觉帧'),
            ('duplicate_anchor', '选择了重复 anchor'),
            ('overlapping_anchor', '宿主固定窗口重叠'),
            ('model_coordinates', '只能包含 anchor_id/title/reason'),
            ('missing_attribution', 'practical_takeaways[0] 没有明确证据归因'),
            ('wrapped_json', '不是严格 JSON 对象'),
        ):
            with self.subTest(qwen_mode=qwen_mode), tempfile.TemporaryDirectory() as tmp:
                process, output = self.run_builder(Path(tmp), qwen_mode=qwen_mode)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn(expected, process.stderr)
                self.assertFalse(output.exists())

    def test_host_sorts_selected_watch_anchors_by_trusted_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            process, output = self.run_builder(Path(tmp), qwen_mode='out_of_order')
            self.assertEqual(process.returncode, 0, process.stderr)
            segments = json.loads(output.read_text(encoding='utf-8'))['items'][NOTE_ID]['watch_segments']
            self.assertEqual([row['start'] for row in segments], ['00:00', '00:30', '00:55'])

    def test_each_valid_item_is_saved_before_a_later_item_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fixtures = self.fixtures(directory)
            second_id = 'second-note-id'

            items = json.loads(fixtures['items'].read_text(encoding='utf-8'))
            second_item = copy.deepcopy(items[0])
            second_item.update({'id': second_id, 'title': '第二条用于验证原子落盘'})
            items.append(second_item)
            write_json(fixtures['items'], items)

            classification = json.loads(fixtures['classification'].read_text(encoding='utf-8'))
            second_classification = copy.deepcopy(classification[0])
            second_classification['id'] = second_id
            classification.append(second_classification)
            write_json(fixtures['classification'], classification)

            transcripts = json.loads(fixtures['transcripts'].read_text(encoding='utf-8'))
            second_transcript = copy.deepcopy(transcripts[0])
            second_transcript['id'] = second_id
            transcripts.append(second_transcript)
            write_json(fixtures['transcripts'], transcripts)

            visual = json.loads(fixtures['visual'].read_text(encoding='utf-8'))
            visual['items'][second_id] = copy.deepcopy(visual['items'][NOTE_ID])
            write_json(fixtures['visual'], visual)

            output = directory / 'details.json'
            command = [
                sys.executable,
                str(SCRIPT),
                '--items', str(fixtures['items']),
                '--classification', str(fixtures['classification']),
                '--transcripts', str(fixtures['transcripts']),
                '--visual-evidence', str(fixtures['visual']),
                '--watchbefore-root', str(self.make_fake_watchbefore(directory, qwen_mode='second_invalid')),
                '--output', str(output),
            ]
            process = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)

            self.assertNotEqual(process.returncode, 0)
            self.assertIn('[1/2] ' + NOTE_ID + ' completed', process.stdout)
            self.assertIn('anchor_id 未绑定宿主视觉帧', process.stderr)
            saved = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(list(saved['items']), [NOTE_ID])


if __name__ == '__main__':
    unittest.main()
