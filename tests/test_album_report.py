#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'generate_album_report.py'
DEPTH_LABELS = {'quick': '快速报告', 'light': '轻度报告', 'deep': '深度报告'}


def report_path(directory: Path, board: str, depth: str) -> Path:
    return directory / f'小红书专辑《{board}》{DEPTH_LABELS[depth]}.html'


def detail_dir(directory: Path, board: str, depth: str) -> Path:
    return directory / f'小红书专辑《{board}》{DEPTH_LABELS[depth]}'


class AlbumReportCliTests(unittest.TestCase):
    def run_report(self, directory, items, classification, *, board='训练与康复示例', depth='deep', state='planned', details=None, extra_args=None):
        items_path = directory / 'items.json'
        classification_path = directory / 'classification.json'
        details_path = directory / 'details.json'
        items_path.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
        classification_path.write_text(json.dumps(classification, ensure_ascii=False), encoding='utf-8')
        if details is None:
            details = {
                'items': {
                    row['id']: {
                        'one_line': row.get('content_summary') or row.get('title') or '单条内容摘要',
                        'what_it_says': row.get('content_summary') or '这条笔记围绕标题所示主题展开。',
                        'direct_statements': ['这是从现有证据中提炼的直接结论。'],
                        'key_points': ['这里列出内容中最重要的具体观点。'],
                        'practical_takeaways': ['进入原笔记时，优先核对这一具体做法。'],
                        'boundaries': ['只采用现有证据明确支持的内容。'],
                        'watch_segments': [],
                        'evidence_contract': (
                            {'basis': 'mimo_audio_plus_mimo_vl_full_timeline'}
                            if depth == 'deep' and row.get('content_type') == 'video'
                            else {'basis': 'complete_image_ocr'}
                            if depth == 'deep' and row.get('content_type') == 'image'
                            else {}
                        ),
                    }
                    for row in classification
                    if row.get('id')
                },
            }
        details_path.write_text(json.dumps(details, ensure_ascii=False), encoding='utf-8')
        command = [
            sys.executable,
            str(SCRIPT),
            '--items', str(items_path),
            '--classification', str(classification_path),
            '--board-name', board,
            '--depth', depth,
            '--state', state,
            '--details', str(details_path),
            '--output-dir', str(directory),
        ]
        command.extend(extra_args or [])
        return subprocess.run(
            command,
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_light_and_deep_reports_coexist_as_independent_report_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = 'e' * 24
            items = [{
                'id': note_id,
                'title': '同一条内容',
                'user': '示例作者',
                'content_type': 'video',
            }]
            classification = [{
                'id': note_id,
                'title': '同一条内容',
                'target_board': '训练与康复示例',
                'confidence': 'high',
                'content_type': 'video',
                'classification_basis': 'video_content',
                'video_analysis_status': 'success',
                'video_analysis_basis': 'full_timeline_visual_with_transcript',
                'visual_status': 'analyzed',
                'content_summary': '同一条内容的可核验摘要。',
            }]
            details = {'items': {note_id: {
                'one_line': '同一条内容的可核验摘要。',
                'what_it_says': '这条内容用于验证轻度与深度页面采用不同的信息结构。',
                'direct_statements': ['两种模式共用同一条笔记身份。'],
                'key_points': ['轻度只展示紧凑证据卡，深度保留完整内容卡。'],
                'practical_takeaways': ['深度报告可按观看节点回到原视频。'],
                'boundaries': ['轻度模式不读取完整时轴画面。'],
                'evidence_contract': {
                    'basis': 'mimo_audio_plus_mimo_vl_full_timeline',
                },
                'watch_segments': [{
                    'start': '00:10',
                    'end': '00:24',
                    'title': '关键动作演示',
                    'reason': '这一段集中展示动作顺序。',
                }],
            }}}

            self.run_report(directory, items, classification, depth='light', details=details)
            self.run_report(directory, items, classification, depth='deep', details=details)

            light_index = report_path(directory, '训练与康复示例', 'light')
            deep_index = report_path(directory, '训练与康复示例', 'deep')
            light_detail = detail_dir(directory, '训练与康复示例', 'light') / '01-同一条内容.html'
            deep_detail = detail_dir(directory, '训练与康复示例', 'deep') / '01-同一条内容.html'
            self.assertTrue(light_index.is_file())
            self.assertTrue(deep_index.is_file())
            self.assertTrue(light_detail.is_file())
            self.assertTrue(deep_detail.is_file())
            light_index_html = light_index.read_text(encoding='utf-8')
            deep_index_html = deep_index.read_text(encoding='utf-8')
            light_html = light_detail.read_text(encoding='utf-8')
            deep_html = deep_detail.read_text(encoding='utf-8')
            self.assertIn('class="mode-light"', light_index_html)
            self.assertNotIn('小红书专辑《训练与康复示例》深度报告', light_index_html)
            self.assertIn('class="mode-deep"', deep_index_html)
            self.assertNotIn('小红书专辑《训练与康复示例》轻度报告', deep_index_html)
            self.assertIn('class="mode-light"', light_html)
            self.assertIn('轻度证据能确认什么', light_html)
            self.assertNotIn('值得补看', light_html)
            self.assertNotIn('关键动作演示', light_html)
            self.assertIn('class="mode-deep"', deep_html)
            self.assertIn('这条到底讲什么？', deep_html)
            self.assertIn('核心内容卡', deep_html)
            self.assertIn('值得补看', deep_html)
            self.assertIn('关键动作演示', deep_html)
            self.assertIn('../小红书专辑《训练与康复示例》深度报告.html', deep_html)
            self.assertIn('../小红书专辑《训练与康复示例》轻度报告.html', light_html)
            self.assertNotIn('小红书专辑《训练与康复示例》轻度报告', deep_html)
            self.assertNotIn('小红书专辑《训练与康复示例》深度报告', light_html)
            self.assertNotIn('报告模式切换', light_index_html + deep_index_html)
            self.assertNotIn('单条报告模式切换', light_html + deep_html)

    def test_default_filename_and_title_include_board_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            items = directory / 'items.json'
            classification = directory / 'classification.json'
            items.write_text(json.dumps([
                {
                    'id': '1' * 24,
                    'title': '肩胛稳定练习',
                    'user': '示例作者',
                    'content_type': 'image',
                },
            ], ensure_ascii=False), encoding='utf-8')
            classification.write_text(json.dumps([
                {
                    'id': '1' * 24,
                    'title': '肩胛稳定练习',
                    'target_board': '训练与康复示例',
                    'confidence': 'high',
                    'content_type': 'image',
                    'classification_basis': 'metadata_only',
                },
            ], ensure_ascii=False), encoding='utf-8')

            self.run_report(
                directory,
                json.loads(items.read_text(encoding='utf-8')),
                json.loads(classification.read_text(encoding='utf-8')),
            )

            report = report_path(directory, '训练与康复示例', 'deep')
            self.assertTrue(report.is_file())
            detail = detail_dir(directory, '训练与康复示例', 'deep') / '01-肩胛稳定练习.html'
            self.assertTrue(detail.is_file())
            html = report.read_text(encoding='utf-8')
            self.assertIn('<title>小红书专辑《训练与康复示例》深度报告</title>', html)
            self.assertIn('小红书专辑《训练与康复示例》深度报告/01-肩胛稳定练习.html', html)

    def test_report_joins_items_and_renders_content_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.run_report(
                directory,
                [{
                    'id': '2' * 24,
                    'title': '改善足底筋膜紧张',
                    'user': '康复示例作者',
                    'desc': '足底放松和小腿训练',
                    'tags': ['足底', '康复'],
                    'content_type': 'video',
                    'source_lists': ['收藏'],
                }],
                [{
                    'id': '2' * 24,
                    'title': '改善足底筋膜紧张',
                    'target_board': '训练与康复示例',
                    'confidence': 'high',
                    'content_type': 'video',
                    'classification_basis': 'video_content',
                    'video_analysis_status': 'success',
                    'video_analysis_basis': 'full_timeline_visual_with_transcript',
                    'visual_status': 'analyzed',
                    'main_topic': '足底筋膜与小腿张力',
                    'content_summary': '讲解足底放松顺序，并配合小腿力量训练。',
                    'reason': ['完整文字稿持续讨论足底放松'],
                }],
                depth='deep',
                state='planned',
            )

            html = report_path(directory, '训练与康复示例', 'deep').read_text(encoding='utf-8')
            self.assertIn('改善足底筋膜紧张', html)
            self.assertIn('康复示例作者', html)
            self.assertIn('足底筋膜与小腿张力', html)
            self.assertIn('讲解足底放松顺序，并配合小腿力量训练。', html)
            self.assertIn('深度报告', html)
            self.assertIn('计划版', html)

            detail = (detail_dir(directory, '训练与康复示例', 'deep') / '01-改善足底筋膜紧张.html').read_text(encoding='utf-8')
            self.assertIn('这条到底讲什么？', detail)
            self.assertIn('直接结论', detail)
            self.assertIn('具体观点', detail)
            self.assertIn('可直接采用', detail)
            self.assertIn('注意边界', detail)
            self.assertIn('回到专辑报告', detail)

    def test_verified_report_requires_membership_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            items = directory / 'items.json'
            classification = directory / 'classification.json'
            items.write_text(json.dumps([{
                'id': '3' * 24,
                'title': '呼吸训练',
                'content_type': 'image',
            }], ensure_ascii=False), encoding='utf-8')
            classification.write_text(json.dumps([{
                'id': '3' * 24,
                'title': '呼吸训练',
                'target_board': '训练与康复示例',
                'confidence': 'high',
                'content_type': 'image',
            }], ensure_ascii=False), encoding='utf-8')

            proc = subprocess.run([
                sys.executable,
                str(SCRIPT),
                '--items', str(items),
                '--classification', str(classification),
                '--board-name', '训练与康复示例',
                '--depth', 'light',
                '--state', 'verified',
                '--output-dir', str(directory),
            ], cwd=str(ROOT), capture_output=True, text=True)

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('核验版必须提供 --membership-snapshot', proc.stderr)

    def test_verified_report_rejects_item_missing_from_target_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            snapshot = directory / 'snapshot.json'
            snapshot.write_text(json.dumps({
                'mode': 'read_only',
                'source': {'writes_performed': False},
                'boards': [{
                    'id': 'a' * 24,
                    'name': '训练与康复示例',
                    'note_ids': [],
                }],
                'validation': {'full_membership_complete': True},
            }, ensure_ascii=False), encoding='utf-8')
            items = directory / 'items.json'
            classification = directory / 'classification.json'
            items.write_text(json.dumps([{
                'id': '4' * 24,
                'title': '肩颈放松',
                'content_type': 'image',
            }], ensure_ascii=False), encoding='utf-8')
            classification.write_text(json.dumps([{
                'id': '4' * 24,
                'title': '肩颈放松',
                'target_board': '训练与康复示例',
                'confidence': 'high',
                'content_type': 'image',
            }], ensure_ascii=False), encoding='utf-8')

            proc = subprocess.run([
                sys.executable,
                str(SCRIPT),
                '--items', str(items),
                '--classification', str(classification),
                '--board-name', '训练与康复示例',
                '--depth', 'light',
                '--state', 'verified',
                '--membership-snapshot', str(snapshot),
                '--output-dir', str(directory),
            ], cwd=str(ROOT), capture_output=True, text=True)

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('不在核验后的目标专辑', proc.stderr)

    def test_report_redacts_sensitive_tokens_and_never_reuses_raw_href(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.run_report(
                directory,
                [{
                    'id': '5' * 24,
                    'title': '安全示例',
                    'user': '作者',
                    'href': 'https://www.xiaohongshu.com/explore/' + '5' * 24 + '?xsec_token=href-secret',
                    'desc': '正文 xsec_token=desc-secret',
                    'content_type': 'image',
                }],
                [{
                    'id': '5' * 24,
                    'title': '安全示例',
                    'target_board': '训练与康复示例',
                    'confidence': 'high',
                    'content_type': 'image',
                    'classification_basis': 'metadata_only',
                    'content_summary': 'authorization: Bearer summary-secret',
                }],
                depth='quick',
            )

            html = report_path(directory, '训练与康复示例', 'quick').read_text(encoding='utf-8')
            detail = (detail_dir(directory, '训练与康复示例', 'quick') / '01-安全示例.html').read_text(encoding='utf-8')
            combined = html + detail
            self.assertNotIn('href-secret', combined)
            self.assertNotIn('desc-secret', combined)
            self.assertNotIn('summary-secret', combined)
            self.assertIn('https://www.xiaohongshu.com/explore/' + '5' * 24, detail)
            self.assertIn('打开快速页 →', html)
            self.assertNotIn('打开深度报告 →', html)

    def test_report_renders_album_synthesis_with_supporting_note_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            synthesis = directory / 'synthesis.json'
            synthesis.write_text(json.dumps({
                'overview': '这个专辑集中讨论足底、肩胛与呼吸训练。',
                'reader_value': ['快速判断问题更接近足底、肩胛还是呼吸。'],
                'reading_path': ['先从症状最明确的条目进入，再看训练动作。'],
                'subtopics': [{
                    'name': '足底与小腿',
                    'summary': '先放松紧张组织，再进行力量训练。',
                    'supporting_note_ids': ['6' * 24],
                }],
            }, ensure_ascii=False), encoding='utf-8')
            self.run_report(
                directory,
                [{
                    'id': '6' * 24,
                    'title': '足底训练',
                    'content_type': 'video',
                }],
                [{
                    'id': '6' * 24,
                    'title': '足底训练',
                    'target_board': '训练与康复示例',
                    'confidence': 'high',
                    'content_type': 'video',
                    'content_summary': '足底训练步骤。',
                }],
                extra_args=['--synthesis', str(synthesis)],
            )

            html = report_path(directory, '训练与康复示例', 'deep').read_text(encoding='utf-8')
            self.assertIn('这个专辑集中讨论足底、肩胛与呼吸训练。', html)
            self.assertIn('足底与小腿', html)
            self.assertIn('先放松紧张组织，再进行力量训练。', html)
            self.assertIn('你能从这个专辑得到什么', html)
            self.assertIn('快速判断问题更接近足底、肩胛还是呼吸。', html)
            self.assertIn('建议阅读顺序', html)
            self.assertIn('先从症状最明确的条目进入，再看训练动作。', html)
            self.assertIn('666666666666666666666666', html)

    def test_verified_report_keeps_unresolved_item_that_is_really_in_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = '7' * 24
            snapshot = directory / 'snapshot.json'
            snapshot.write_text(json.dumps({
                'mode': 'read_only',
                'source': {'writes_performed': False},
                'boards': [{
                    'id': 'b' * 24,
                    'name': '训练与康复示例',
                    'note_ids': [note_id],
                }],
                'validation': {'full_membership_complete': True},
            }, ensure_ascii=False), encoding='utf-8')
            self.run_report(
                directory,
                [{
                    'id': note_id,
                    'title': '暂时无法分析的视频',
                    'content_type': 'video',
                }],
                [{
                    'id': note_id,
                    'title': '暂时无法分析的视频',
                    'target_board': '',
                    'confidence': 'low',
                    'content_type': 'video',
                    'classification_basis': 'video_content',
                    'video_analysis_status': 'failed',
                    'review_state': 'video_content_unavailable',
                }],
                state='verified',
                extra_args=['--membership-snapshot', str(snapshot)],
            )

            html = report_path(directory, '训练与康复示例', 'deep').read_text(encoding='utf-8')
            self.assertIn('暂时无法分析的视频', html)
            self.assertIn('内容待核实', html)

    def test_light_report_labels_ocr_and_metadata_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.run_report(
                directory,
                [
                    {'id': '8' * 24, 'title': '图文动作说明', 'content_type': 'image'},
                    {'id': '9' * 24, 'title': '未分析视频', 'content_type': 'video'},
                ],
                [
                    {
                        'id': '8' * 24,
                        'title': '图文动作说明',
                        'target_board': '训练与康复示例',
                        'confidence': 'high',
                        'content_type': 'image',
                        'classification_basis': 'metadata_and_ocr',
                        'ocr_status': 'ok',
                        'ocr_image_set_complete': True,
                        'ocr_text': '动作说明文字',
                    },
                    {
                        'id': '9' * 24,
                        'title': '未分析视频',
                        'target_board': '训练与康复示例',
                        'confidence': 'medium',
                        'content_type': 'video',
                        'classification_basis': 'metadata_only',
                    },
                ],
                depth='light',
            )

            html = report_path(directory, '训练与康复示例', 'light').read_text(encoding='utf-8')
            self.assertIn('完整图片 OCR', html)
            self.assertIn('仅元数据', html)
            self.assertIn('轻度正文 2 / 2', html)

    def test_deep_report_rejects_audio_only_when_visual_contract_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = 'b' * 24
            audio_only_details = {'items': {note_id: {
                'one_line': '这段摘要只来自音轨文字稿。',
                'what_it_says': '视觉合同失败，因此不能生成深度报告。',
                'direct_statements': ['音轨文字稿可用。'],
                'key_points': ['完整时轴视觉不可用。'],
                'practical_takeaways': ['必须重新完成视觉分析。'],
                'boundaries': ['不能把音频报告标记为深度报告。'],
                'watch_segments': [],
                'evidence_contract': {'basis': 'mimo_audio_only'},
            }}}
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                self.run_report(
                    directory,
                    [{'id': note_id, 'title': '视觉严格失败的视频', 'content_type': 'video'}],
                    [{
                        'id': note_id,
                        'title': '视觉严格失败的视频',
                        'target_board': '训练与康复示例',
                        'confidence': 'high',
                        'content_type': 'video',
                        'video_analysis_status': 'success',
                        'video_analysis_basis': 'full_timeline_visual_with_transcript',
                        'visual_status': 'failed',
                        'review_state': 'visual_analysis_unavailable',
                        'content_summary': '这段摘要来自通过质量门的完整文字稿。',
                    }],
                    depth='deep',
                    details=audio_only_details,
                )

            self.assertIn(
                'evidence_contract.basis=mimo_audio_plus_mimo_vl_full_timeline',
                caught.exception.stderr,
            )
            self.assertFalse(report_path(directory, '训练与康复示例', 'deep').exists())

    def test_deep_report_rejects_image_without_complete_ocr_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = 'f' * 24
            incomplete_details = {'items': {note_id: {
                'one_line': '图文摘要。',
                'what_it_says': '这条图文没有完整 OCR 合同。',
                'direct_statements': ['只有部分图片文字。'],
                'key_points': ['图片集合未完整读取。'],
                'practical_takeaways': ['先完成全部图片 OCR。'],
                'boundaries': ['不能生成深度图文报告。'],
                'watch_segments': [],
                'evidence_contract': {'basis': 'partial_image_ocr'},
            }}}
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                self.run_report(
                    directory,
                    [{'id': note_id, 'title': 'OCR 不完整的图文', 'content_type': 'image'}],
                    [{
                        'id': note_id,
                        'title': 'OCR 不完整的图文',
                        'target_board': '训练与康复示例',
                        'confidence': 'high',
                        'content_type': 'image',
                    }],
                    depth='deep',
                    details=incomplete_details,
                )

            self.assertIn(
                'evidence_contract.basis=complete_image_ocr',
                caught.exception.stderr,
            )
            self.assertFalse(report_path(directory, '训练与康复示例', 'deep').exists())

    def test_light_detail_is_useful_but_explicitly_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = 'c' * 24
            details = {
                'items': {
                    note_id: {
                        'one_line': '从标题可确认，这是一条关于户外人年度装备偏好的统计型内容。',
                        'what_it_says': '它要回答的是：104 位户外人在过去一年最常把哪些装备列为个人最爱。',
                        'direct_statements': ['标题明确给出样本量为 104 位户外人。'],
                        'key_points': ['适合用来建立装备趋势清单，但不能替代实际测评。'],
                        'practical_takeaways': ['打开原视频后，优先核对统计口径、装备榜单和入选理由。'],
                        'boundaries': ['轻度报告未读取视频正文，不声称知道具体入选装备。'],
                        'watch_segments': [],
                    },
                },
            }
            self.run_report(
                directory,
                [{'id': note_id, 'title': '统计了104位户外人去年最爱的装备！', 'user': '阿发阿夸', 'content_type': 'video'}],
                [{'id': note_id, 'title': '统计了104位户外人去年最爱的装备！', 'target_board': '其他', 'confidence': 'high', 'content_type': 'video', 'classification_basis': 'metadata_only'}],
                board='其他',
                depth='light',
                details=details,
            )

            index = report_path(directory, '其他', 'light').read_text(encoding='utf-8')
            detail = (detail_dir(directory, '其他', 'light') / '01-统计了104位户外人去年最爱的装备！.html').read_text(encoding='utf-8')
            self.assertIn('轻度报告', index)
            self.assertIn('打开单条报告', index)
            self.assertIn('它要回答的是：104 位户外人在过去一年最常把哪些装备列为个人最爱。', detail)
            self.assertIn('轻度报告未读取视频正文', detail)

    def test_light_video_with_unusable_subtitle_or_audio_recommends_deep_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = 'd' * 24
            self.run_report(
                directory,
                [{
                    'id': note_id,
                    'title': '画面字幕承载主要内容的视频',
                    'user': '示例作者',
                    'content_type': 'video',
                }],
                [{
                    'id': note_id,
                    'title': '画面字幕承载主要内容的视频',
                    'target_board': '其他',
                    'confidence': 'low',
                    'content_type': 'video',
                    'classification_basis': 'metadata_only',
                    'main_topic': '根据标题推断的髋旋转纠正',
                    'content_summary': '根据标题猜测这条视频会讲具体的髋旋转动作。',
                }],
                board='其他',
                depth='light',
                details={'items': {note_id: {
                    'status': 'deep_recommended',
                    'reason': 'subtitle_audio_insufficient',
                }}},
            )

            index = report_path(directory, '其他', 'light').read_text(encoding='utf-8')
            detail = (detail_dir(directory, '其他', 'light') / '01-画面字幕承载主要内容的视频.html').read_text(encoding='utf-8')
            self.assertIn('字幕 / 音频内容不足', index)
            self.assertIn('建议深度报告', index)
            self.assertIn('轻度证据不足', index)
            self.assertIn('轻度模式无法从字幕或音轨取得可用讲解；建议生成深度报告。', index)
            self.assertNotIn('根据标题推断的髋旋转纠正', index)
            self.assertNotIn('根据标题猜测这条视频会讲具体的髋旋转动作', index)
            self.assertIn('轻度模式无法从字幕或音轨取得这条视频的可用讲解', detail)
            self.assertIn('MiMo 听觉与完整时轴视觉', detail)
            self.assertNotIn('标题显示它可能', detail)
            self.assertNotIn('标题信息与文字稿无法互相印证', detail)

    def test_report_refuses_to_publish_an_empty_album(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            items = directory / 'items.json'
            classification = directory / 'classification.json'
            items.write_text('[]', encoding='utf-8')
            classification.write_text('[]', encoding='utf-8')
            proc = subprocess.run([
                sys.executable,
                str(SCRIPT),
                '--items', str(items),
                '--classification', str(classification),
                '--board-name', '其他',
                '--depth', 'light',
                '--state', 'planned',
                '--output-dir', str(directory),
            ], cwd=str(ROOT), capture_output=True, text=True)

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('没有可生成报告的专辑条目', proc.stderr)

    def test_verified_report_accepts_complete_target_when_unrelated_board_mismatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            note_id = 'a' * 24
            snapshot = directory / 'snapshot.json'
            snapshot.write_text(json.dumps({
                'mode': 'read_only',
                'source': {'writes_performed': False},
                'boards': [
                    {
                        'id': 'b' * 24,
                        'name': '训练与康复示例',
                        'declared_total': 1,
                        'accessible_unique_count': 1,
                        'declared_vs_accessible_delta': 0,
                        'note_ids': [note_id],
                    },
                    {
                        'id': 'c' * 24,
                        'name': '无关专辑',
                        'declared_total': 2,
                        'accessible_unique_count': 1,
                        'declared_vs_accessible_delta': 1,
                        'note_ids': ['d' * 24],
                    },
                ],
                'validation': {
                    'board_names_unique': True,
                    'pagination_cursor_invariants_passed': True,
                    'duplicate_note_ids': [],
                    'multi_board_note_ids': [],
                    'within_board_duplicates': [],
                    'count_mismatch_boards': ['无关专辑'],
                    'full_membership_complete': False,
                },
            }, ensure_ascii=False), encoding='utf-8')
            self.run_report(
                directory,
                [{'id': note_id, 'title': '完整目标条目', 'content_type': 'image'}],
                [{
                    'id': note_id,
                    'title': '完整目标条目',
                    'target_board': '训练与康复示例',
                    'confidence': 'high',
                    'content_type': 'image',
                }],
                state='verified',
                extra_args=['--membership-snapshot', str(snapshot)],
            )

            report = report_path(directory, '训练与康复示例', 'deep')
            self.assertTrue(report.is_file())


if __name__ == '__main__':
    unittest.main()
