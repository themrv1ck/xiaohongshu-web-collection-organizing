#!/usr/bin/env python3
"""Adapt verified Xiaohongshu transcripts into the WatchBefore content contract."""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from copy import deepcopy
from math import ceil, floor
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def timecode(seconds) -> str:
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'


def stable_sha256(value) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def validate_multimodal_evidence(note_id: str, transcript: dict, evidence: dict) -> dict:
    """Require an auditable MiMo-audio + MiMo-VL evidence pair for deep reports."""
    if transcript.get('status') != 'success' or transcript.get('source_kind') != 'mimo_audio':
        raise ValueError(f'深度报告要求成功的 MiMo 听觉文字稿：{note_id}')
    coverage = transcript.get('coverage') if isinstance(transcript.get('coverage'), dict) else {}
    if coverage.get('transcript_quality_passed') is not True:
        raise ValueError(f'MiMo 听觉文字稿未通过技术质量门：{note_id}')
    raw_segments = transcript.get('segments')
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError(f'MiMo 听觉文字稿没有分段：{note_id}')
    transcript_hash = stable_sha256(raw_segments)
    if transcript_hash != str(transcript.get('transcript_sha256') or ''):
        raise ValueError(f'MiMo 听觉文字稿 hash 不一致：{note_id}')

    if not isinstance(evidence, dict):
        raise ValueError(f'深度报告缺少 MiMo-VL 视觉证据：{note_id}')
    evidence_hash = str(evidence.get('visual_evidence_hash') or '')
    if re.fullmatch(r'[0-9a-f]{64}', evidence_hash) is None:
        raise ValueError(f'MiMo-VL 视觉证据 hash 无效：{note_id}')
    hash_material = dict(evidence)
    hash_material.pop('visual_evidence_hash', None)
    if stable_sha256(hash_material) != evidence_hash:
        raise ValueError(f'MiMo-VL 视觉证据 hash 不一致：{note_id}')
    provider = evidence.get('provider') if isinstance(evidence.get('provider'), dict) else {}
    if provider.get('provider') != 'mimo-vl-mlx':
        raise ValueError(f'深度报告视觉证据不是 MiMo-VL：{note_id}')
    if str(evidence.get('transcript_sha256') or '') != transcript_hash:
        raise ValueError(f'MiMo-VL 视觉证据没有绑定当前听觉文字稿：{note_id}')
    audio_evidence = evidence.get('audio_evidence') if isinstance(evidence.get('audio_evidence'), dict) else {}
    if (
        audio_evidence.get('provider') != 'mimo_audio'
        or audio_evidence.get('transcript_sha256') != transcript_hash
        or audio_evidence.get('segments') != raw_segments
    ):
        raise ValueError(f'深度报告的 MiMo 听觉证据没有完整绑定：{note_id}')

    frames = evidence.get('frames')
    analysis = evidence.get('analysis') if isinstance(evidence.get('analysis'), dict) else {}
    analyzed_frames = analysis.get('frames')
    if not isinstance(frames, list) or not frames or not isinstance(analyzed_frames, list):
        raise ValueError(f'MiMo-VL 视觉证据没有完整时轴帧：{note_id}')
    if len(frames) != len(analyzed_frames):
        raise ValueError(f'MiMo-VL 帧清单与分析行数不一致：{note_id}')
    sampling = evidence.get('sampling') if isinstance(evidence.get('sampling'), dict) else {}
    if sampling.get('includes_start') is not True or sampling.get('includes_end') is not True:
        raise ValueError(f'MiMo-VL 视觉证据没有覆盖视频首尾：{note_id}')
    for position, (frame, analyzed) in enumerate(zip(frames, analyzed_frames)):
        if not isinstance(frame, dict) or not isinstance(analyzed, dict):
            raise ValueError(f'MiMo-VL 帧证据格式无效：{note_id}')
        expected = (
            frame.get('index'),
            frame.get('timestamp_seconds'),
            frame.get('sha256'),
        )
        actual = (
            analyzed.get('index'),
            analyzed.get('timestamp_seconds'),
            analyzed.get('sha256'),
        )
        if expected != actual or expected[0] != position:
            raise ValueError(f'MiMo-VL 帧坐标或 hash 没有由宿主正确绑定：{note_id}')
    screen_track = (
        evidence.get('screen_text_timeline')
        if isinstance(evidence.get('screen_text_timeline'), dict)
        else {}
    )
    screen_segments = screen_track.get('segments')
    if (
        screen_track.get('verbatim_visible_text') is not True
        or not isinstance(screen_segments, list)
    ):
        raise ValueError(f'深度报告缺少逐字屏幕文字时间线：{note_id}')
    if (
        not isinstance(screen_track.get('text_detected'), bool)
        or screen_track['text_detected'] != bool(screen_segments)
    ):
        raise ValueError(f'深度报告屏幕文字状态不一致：{note_id}')
    report_text_track = str(evidence.get('report_text_track') or '').strip()
    if report_text_track not in {'mimo_audio', 'screen_text'}:
        raise ValueError(f'深度报告未显式指定主内容文字轨：{note_id}')
    if report_text_track == 'screen_text' and not screen_segments:
        raise ValueError(f'深度报告把空屏幕文字轨设为主内容：{note_id}')
    return {
        'basis': 'mimo_audio_plus_mimo_vl_full_timeline',
        'transcript_sha256': transcript_hash,
        'visual_evidence_sha256': evidence_hash,
        'visual_provider': 'mimo-vl-mlx',
        'visual_prompt_version': str(evidence.get('prompt_version') or ''),
        'frame_count': len(frames),
        'screen_text_segment_count': len(screen_segments),
        'report_text_track': report_text_track,
    }


FINAL_DETAIL_FIELDS = (
    'one_line',
    'what_it_says',
    'path_table',
    'direct_statements',
    'key_points',
    'practical_takeaways',
    'boundaries',
    'watch_segments',
)
MODEL_WATCH_SEGMENT_FIELDS = frozenset(('anchor_id', 'title', 'reason'))
WATCH_WINDOW_SECONDS = 5
INTERNAL_LABELS = (
    '[MiMo',
    '[屏幕文字',
    '片段线索：',
    '当前文字稿不足',
    '标题信息与文字稿',
)
GENERIC_WATCH_TITLES = (
    '方法与方案',
    '高价值核心段',
    '核心逻辑入口',
    '首选片段',
    '可选补看',
    '备选',
)
CLAIM_PREFIXES = ('视频', '画面', '屏幕文字', '按视频')
BOUNDARY_PREFIXES = ('视频', '画面', '屏幕文字', '音轨', '现有证据', '本报告')


ALBUM_DEEP_SYSTEM_PROMPT = """你是小红书专辑深度报告编辑。
你会同时收到宿主哈希绑定的 MiMo 听觉文字稿、MiMo-VL 完整时轴帧分析和屏幕文字时间线。
只能输出一个严格 JSON 对象，不得输出 Markdown、解释、代码块或 JSON 之外的字符。
不得使用标题猜测内容；不得把画面写成口播；不得把口播主张写成系统已独立验证的事实。
必须严格区分“该方法不够或不是重点”和“该方法会加重问题”，不得把两种不同强度的主张合并成同一因果结论。
医疗、康复、体态和训练效果主张必须明确归因于“视频称”“视频指出”“视频引用”“画面显示”或“屏幕文字显示”。
one_line 和 what_it_says 的第一个词必须就是“视频”，不得先写主题再把“视频”放到句中。
one_line 只允许概括一个核心机制和一个对应行动，不得把多个误区或方法合并成同一个“会加重”“会改善”结论。
文献截图只能写成“视频引用”或“画面显示文献信息”，不得写成系统已独立核验、证实或证明。
不要添加证据未明确表达的“而非”“只能”“禁止”“不适用”等排他性判断。
数值中的“至少”“约”“每次”“每天”“组”“次”必须按证据原样保留，不得互换或丢失。
任何数字、剂量、次数和时长都必须逐字存在于输入证据，禁止补充常识性参数，例如证据未说时不得自行写“冰敷15-20分钟”。
除 path_table.label 和 watch_segments.title 外，所有字符串条目都必须以中文句号、问号、叹号或分号结尾。
任何字段都不得从任一单个 audio segment 或 screen_text segment 连续照抄 60 个及以上非空白字符。
画面中的长英文论文、期刊或研究标题只能用短中文概括其用途，不得连续照抄英文标题。
所有 direct_statements、key_points 和 practical_takeaways 的每一条都必须以“视频”“画面”“屏幕文字”或“按视频”开头；boundaries 每一条必须以合同指定的证据主体开头。
边界只能写输入证据确实未提供的信息，不得与正文、直接主张或实践项中已有的频率、时长、次数、人群或方法矛盾。
主文必须说清视频具体讲了什么；观看节点必须说清在那里会看到什么，不得使用泛化标题。
观看节点只能选择宿主提供的 anchor_id 并撰写 title/reason；不得输出或计算时间码、帧 index、timestamp、hash 或 evidence_refs。
不得复制长段原文，不得输出内部证据标签，不得截断半句。
""".strip()


def visual_anchor_id(index: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError('宿主视觉帧 index 无效')
    return f'vf_{index:04d}'


def host_watch_window(timestamp_seconds: float, duration_seconds: float) -> tuple[int, int]:
    """把单个可信帧绑定到由宿主决定的 5 秒观看窗口。"""
    if (
        isinstance(timestamp_seconds, bool)
        or not isinstance(timestamp_seconds, (int, float))
        or isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
    ):
        raise ValueError('宿主视觉帧时间坐标无效')
    duration_limit = ceil(float(duration_seconds))
    timestamp = float(timestamp_seconds)
    if duration_limit < 5:
        raise ValueError('视频不足 5 秒，无法绑定深度报告观看节点')
    if timestamp < 0 or timestamp > duration_limit:
        raise ValueError('宿主视觉帧时间坐标超出视频')
    window_length = min(WATCH_WINDOW_SECONDS, duration_limit)
    start = min(floor(timestamp / window_length) * window_length, duration_limit - window_length)
    end = start + window_length
    if not start <= timestamp <= end or not 5 <= end - start <= 30:
        raise ValueError('宿主无法把视觉帧绑定到合法观看窗口')
    return start, end


def validate_text(
    value,
    label: str,
    *,
    minimum: int = 2,
    maximum: int,
    sentence: bool = True,
) -> str:
    if not isinstance(value, str) or value != value.strip() or '\n' in value:
        raise ValueError(f'{label} 必须是无换行、无首尾空格的字符串')
    if not minimum <= len(value) <= maximum:
        raise ValueError(f'{label} 长度必须为 {minimum} 到 {maximum} 字符')
    if any(marker in value for marker in INTERNAL_LABELS):
        raise ValueError(f'{label} 泄露内部标签或空话')
    if '。；' in value or '；。' in value or '。。' in value:
        raise ValueError(f'{label} 包含错误标点')
    if sentence and value[-1] not in '。！？；':
        raise ValueError(f'{label} 必须是完整句子')
    return value


def normalized_text_key(value: str) -> str:
    return re.sub(r'[\s\W_]+', '', value, flags=re.UNICODE).lower()


def validate_string_list(
    value,
    label: str,
    *,
    minimum: int,
    maximum: int,
    required_prefixes: tuple[str, ...],
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f'{label} 必须是 {minimum} 到 {maximum} 条字符串')
    keys = set()
    for position, item in enumerate(value):
        validate_text(item, f'{label}[{position}]', maximum=180)
        if not item.startswith(required_prefixes):
            raise ValueError(f'{label}[{position}] 没有明确证据归因')
        key = normalized_text_key(item)
        if key in keys:
            raise ValueError(f'{label} 包含重复条目')
        keys.add(key)
    return value


def iter_report_texts(detail: dict):
    yield detail['one_line']
    yield detail['what_it_says']
    for row in detail['path_table']:
        yield row['text']
    for field in ('direct_statements', 'key_points', 'practical_takeaways', 'boundaries'):
        yield from detail[field]
    for segment in detail['watch_segments']:
        yield segment['title']
        yield segment['reason']


def validate_no_raw_passage(detail: dict, transcript: dict, evidence: dict) -> None:
    sources = [str(row.get('text') or '') for row in transcript['segments']]
    sources.extend(str(row.get('text') or '') for row in evidence['screen_text_timeline']['segments'])
    normalized_sources = [re.sub(r'\s+', '', value) for value in sources]
    for position, text in enumerate(iter_report_texts(detail)):
        normalized = re.sub(r'\s+', '', text)
        for offset in range(max(0, len(normalized) - 59)):
            window = normalized[offset:offset + 60]
            if any(window in source for source in normalized_sources):
                raise ValueError(f'报告文字复制了长段原文：#{position}')


def build_report_evidence(note_id: str, transcript: dict, evidence: dict) -> dict:
    audio_segments = []
    for index, segment in enumerate(transcript['segments']):
        if not isinstance(segment, dict) or not str(segment.get('text') or '').strip():
            raise ValueError(f'MiMo 听觉文字稿分段无效：{note_id}#{index}')
        audio_segments.append({
            'segment_index': index,
            'start_seconds': segment.get('start'),
            'end_seconds': segment.get('end'),
            'text': segment.get('text'),
        })
    visual_anchors = []
    for frame in evidence['analysis']['frames']:
        window_start, window_end = host_watch_window(
            frame['timestamp_seconds'],
            evidence['duration_seconds'],
        )
        visual_anchors.append({
            'anchor_id': visual_anchor_id(frame['index']),
            'index': frame['index'],
            'timestamp_seconds': frame['timestamp_seconds'],
            'host_watch_window': {
                'start': timecode(window_start),
                'end': timecode(window_end),
            },
            'observation': frame['observation'],
            'visible_text': frame['visible_text'],
            'actions': frame['actions'],
            'uncertainty': frame['uncertainty'],
        })
    screen_segments = []
    for index, segment in enumerate(evidence['screen_text_timeline']['segments']):
        if not isinstance(segment, dict) or not str(segment.get('text') or '').strip():
            raise ValueError(f'屏幕文字时间线分段无效：{note_id}#{index}')
        screen_segments.append({
            'segment_index': index,
            'start_seconds': segment.get('start'),
            'end_seconds': segment.get('end'),
            'text': segment.get('text'),
        })
    return {
        'report_text_track': evidence['report_text_track'],
        'audio_segments': audio_segments,
        'overall_visual_summary': evidence['analysis'].get('overall_visual_summary'),
        'visual_anchors': visual_anchors,
        'visual_caveats': evidence['analysis'].get('visual_caveats') or [],
        'screen_text_segments': screen_segments,
    }


def build_final_report_prompt(metadata: dict, report_evidence: dict) -> str:
    contract = {
        'one_line': '视频指出，……（第一个词必须是“视频”，一句具体主旨，不超过 100 字）。',
        'what_it_says': '视频指出，……（第一个词必须是“视频”，具体说明对象、问题、判断、方法、复测和边界）。',
        'path_table': [
            {'label': '2-12 字的内容自适应标签', 'text': '以视频/画面/屏幕文字归因的单句短句。'},
        ],
        'direct_statements': ['每条以视频/画面/屏幕文字/按视频开头的直接证据主张。'],
        'key_points': ['每条以视频/画面/屏幕文字/按视频开头的结构归纳，不得与 direct_statements 重复。'],
        'practical_takeaways': ['每条以视频/画面/屏幕文字/按视频开头，只转述如何操作或复测。'],
        'boundaries': ['每条以视频/画面/屏幕文字/音轨/现有证据/本报告开头，只写适用范围或证据限制。'],
        'watch_segments': [{
            'anchor_id': '从 visual_anchors 原样选择的唯一 anchor_id',
            'title': '具体说明会看到什么的短标题',
            'reason': '以视频/画面/屏幕文字/按视频开头，不超过 56 字，概括该 anchor 的直接可见价值。',
        }],
    }
    rules = (
        'path_table 必须 3-6 项，标签随内容改变；每项只有 label/text。'
        'one_line 和 what_it_says 的第一个词必须是“视频”，不得在“视频”之前写任何主题词。'
        'one_line 只写一个核心机制和一个对应行动，不得并列多个误区后共享“会加重”或“会改善”的谓词。'
        'direct_statements 和 key_points 各 2-5 条且不得重复；practical_takeaways 和 boundaries 各 1-5 条；禁止为了凑数输出第 6 条。'
        'direct_statements、key_points、practical_takeaways 的每条必须以“视频”“画面”“屏幕文字”或“按视频”开头。'
        'boundaries 的每条必须以“视频”“画面”“屏幕文字”“音轨”“现有证据”或“本报告”开头。'
        'boundaries 只能写输入证据确实未提供的事项；不得把正文或 practical_takeaways 已提供的频率、时长、次数、人群或方法又写成“未提供”。'
        '看到论文、期刊或研究截图时，只能表述“视频引用”或“画面显示文献信息”；不得写成系统独立证明、证实或完成文献核验。'
        '不要添加证据未明确表达的“而非”“只能”“禁止”“不适用”等排他性判断。'
        '数值中的“至少”“约”“每次”“每天”“组”“次”必须按证据原样保留，不得互换或丢失。'
        '任何数字、剂量、次数和时长必须逐字存在于输入证据，不得补入常识性参数。'
        '除 path_table.label 和 watch_segments.title 外，每个字符串条目都必须用完整中文句末标点收尾。'
        '任何报告字段都不得从任一单个 audio segment 或 screen_text segment 连续照抄 60 个及以上非空白字符。'
        '遇到长英文论文、期刊或研究标题时，只能写短中文概括，不得连续照抄英文标题。'
        'watch_segments 必须 3-6 个，只能包含 anchor_id/title/reason；anchor_id 必须从 visual_anchors 原样选择。'
        '不得在 watch_segments 中输出 start、end、index、timestamp_seconds、sha256 或 evidence_refs。'
        '必须选择 3-6 个不同视觉 anchor，并避免选择时间过近、会使宿主 5 秒固定窗口重叠的 anchor；输出顺序由宿主按可信时间坐标统一排序。'
        '每个 visual_anchor 已给出 host_watch_window；所选窗口必须互不重叠，尤其不要同时选择尾部发生重叠的相邻窗口。'
        '宿主会在验证选择后绑定最终 start/end 和原始帧 hash；你不得计算、生成或改写这些字段。'
        'title/reason 必须说明选中 anchor 的直接可见价值；reason 必须以“视频”“画面”“屏幕文字”或“按视频”开头。'
        '每个 watch reason 不得超过 56 个字符，文献画面只写简短中文概括。'
        '主文必须结合视觉关键帧，不能只改写音轨。'
        'report_text_track=screen_text 时，屏幕文字和画面是内容主轨，音轨只能用于边界；'
        'report_text_track=mimo_audio 时，语言主张以音轨为准，动作、对象、图表和屏幕文字以视觉证据为准。'
    )
    return (
        '请根据下面的可审计多模态证据生成最终小红书单条深度报告。\n'
        + rules
        + '\n严格 JSON 合同：\n'
        + json.dumps(contract, ensure_ascii=False, indent=2)
        + '\n输入证据：\n'
        + json.dumps({'metadata': metadata, 'evidence': report_evidence}, ensure_ascii=False, indent=2)
    )


def call_qwen_strict_json(
    local_extract_module,
    prompt: str,
    *,
    system_prompt: str,
    response_label: str,
    debug_prefix: str,
    model_id: str,
    api_base: str,
    timeout: int,
    max_tokens: int,
    debug_dir,
) -> dict:
    selected_model = local_extract_module.choose_qwen_model(
        model_id=model_id,
        api_base=api_base,
        urlopen_func=urllib.request.urlopen,
        timeout=min(timeout, 30),
    )
    request_payload = {
        'model': selected_model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0,
        'reasoning_effort': 'none',
        'max_tokens': max_tokens,
        'stream': False,
    }
    if debug_dir:
        atomic_json(debug_dir / f'{debug_prefix}_request.json', {
            'qwen_api_base': local_extract_module.configured_qwen_api_base(api_base),
            'qwen_model': selected_model,
            'timeout': timeout,
            'request': request_payload,
        })
    raw_response = []

    def capture_raw(value: str) -> None:
        raw_response.append(value)

    response = local_extract_module.http_json(
        f'{local_extract_module.configured_qwen_api_base(api_base)}/chat/completions',
        method='POST',
        payload=request_payload,
        urlopen_func=urllib.request.urlopen,
        timeout=timeout,
        raw_response_callback=capture_raw,
    )
    if debug_dir and raw_response:
        temporary = debug_dir / f'{debug_prefix}_raw_response.txt.tmp'
        temporary.write_text(raw_response[-1], encoding='utf-8')
        temporary.chmod(0o600)
        os.replace(temporary, debug_dir / f'{debug_prefix}_raw_response.txt')
    raw_message = local_extract_module.extract_message_text(response)
    try:
        parsed = json.loads(raw_message.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f'Qwen {response_label}不是严格 JSON 对象：{exc}') from exc
    if not isinstance(parsed, dict):
        raise ValueError(f'Qwen {response_label}必须是严格 JSON 对象')
    return parsed


def call_final_qwen_json(
    local_extract_module,
    prompt: str,
    *,
    model_id: str,
    api_base: str,
    timeout: int,
    debug_dir,
) -> dict:
    return call_qwen_strict_json(
        local_extract_module,
        prompt,
        system_prompt=ALBUM_DEEP_SYSTEM_PROMPT,
        response_label='最终报告',
        debug_prefix='final_details',
        model_id=model_id,
        api_base=api_base,
        timeout=timeout,
        max_tokens=4096,
        debug_dir=debug_dir,
    )


def select_overlapping_audio_ref(
    note_id: str,
    transcript: dict,
    *,
    anchor_timestamp: float,
    window_start: int,
    window_end: int,
) -> dict:
    candidates = []
    for index, segment in enumerate(transcript['segments']):
        start = segment.get('start')
        end = segment.get('end')
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
            or float(end) <= float(start)
        ):
            raise ValueError(f'MiMo 听觉文字稿时间坐标无效：{note_id}#{index}')
        overlap = min(float(end), window_end) - max(float(start), window_start)
        if overlap <= 0:
            continue
        contains_anchor = float(start) <= anchor_timestamp <= float(end)
        candidates.append((contains_anchor, overlap, -index, index, start, end))
    if not candidates:
        raise ValueError(f'宿主观看窗口没有可绑定的 MiMo 听觉分段：{note_id}')
    _contains, _overlap, _negative_index, index, start, end = max(candidates)
    return {
        'type': 'audio_segment',
        'segment_index': index,
        'start_seconds': start,
        'end_seconds': end,
    }


def bind_watch_segments(
    note_id: str,
    model_segments,
    *,
    transcript: dict,
    evidence: dict,
    duration_seconds: float,
) -> list[dict]:
    """验证模型的 anchor 选择，再由宿主生成坐标和证据引用。"""
    if not isinstance(model_segments, list) or not 3 <= len(model_segments) <= 6:
        raise ValueError(f'watch_segments 必须是 3 到 6 项：{note_id}')

    anchors = {}
    for frame in evidence['analysis']['frames']:
        index = frame.get('index')
        timestamp = frame.get('timestamp_seconds')
        sha256 = frame.get('sha256')
        anchor_id = visual_anchor_id(index)
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not isinstance(sha256, str)
            or re.fullmatch(r'[0-9a-f]{64}', sha256) is None
        ):
            raise ValueError(f'宿主视觉 anchor 坐标或 hash 无效：{note_id}#{anchor_id}')
        if anchor_id in anchors:
            raise ValueError(f'宿主视觉 anchor 重复：{note_id}#{anchor_id}')
        anchors[anchor_id] = frame

    selected_anchors = set()
    selected = []
    for position, segment in enumerate(model_segments):
        if not isinstance(segment, dict) or set(segment) != MODEL_WATCH_SEGMENT_FIELDS:
            raise ValueError(
                f'watch_segments[{position}] 只能包含 anchor_id/title/reason：{note_id}'
            )
        anchor_id = segment.get('anchor_id')
        if not isinstance(anchor_id, str) or anchor_id != anchor_id.strip() or anchor_id not in anchors:
            raise ValueError(f'watch_segments[{position}].anchor_id 未绑定宿主视觉帧：{note_id}')
        if anchor_id in selected_anchors:
            raise ValueError(f'watch_segments 选择了重复 anchor：{note_id}#{anchor_id}')
        selected_anchors.add(anchor_id)
        frame = anchors[anchor_id]
        timestamp = float(frame['timestamp_seconds'])
        selected.append((timestamp, position, anchor_id, frame, segment))

    watch_titles = set()
    previous_end = -1
    bound_segments = []
    for timestamp, position, anchor_id, frame, segment in sorted(selected):
        start, end = host_watch_window(frame['timestamp_seconds'], duration_seconds)
        if start < previous_end:
            raise ValueError(f'watch_segments anchor 选择导致宿主固定窗口重叠：{note_id}#{anchor_id}')
        previous_end = end

        title = validate_text(
            segment['title'],
            f'watch_segments[{position}].title',
            maximum=24,
            sentence=False,
        )
        reason = validate_text(
            segment['reason'],
            f'watch_segments[{position}].reason',
            minimum=12,
            maximum=56,
        )
        if not reason.startswith(CLAIM_PREFIXES):
            raise ValueError(f'watch_segments[{position}].reason 没有明确证据归因：{note_id}')
        if any(generic in title for generic in GENERIC_WATCH_TITLES):
            raise ValueError(f'watch_segments[{position}].title 过于空泛：{note_id}')
        title_key = normalized_text_key(title)
        if title_key in watch_titles:
            raise ValueError(f'watch_segments 标题重复：{note_id}')
        watch_titles.add(title_key)

        refs = [{
            'type': 'visual_frame',
            'index': frame['index'],
            'timestamp_seconds': frame['timestamp_seconds'],
            'sha256': frame['sha256'],
        }]
        if evidence['report_text_track'] == 'mimo_audio':
            refs.append(select_overlapping_audio_ref(
                note_id,
                transcript,
                anchor_timestamp=timestamp,
                window_start=start,
                window_end=end,
            ))
        bound_segments.append({
            'start': timecode(start),
            'end': timecode(end),
            'title': title,
            'reason': reason,
            'evidence_refs': refs,
        })

    if len(selected_anchors) < 3:
        raise ValueError(f'深度报告观看节点至少必须绑定 3 个不同视觉帧：{note_id}')
    return bound_segments


def validate_final_detail(
    note_id: str,
    payload,
    *,
    transcript: dict,
    evidence: dict,
    duration_seconds: float,
    evidence_contract: dict,
) -> dict:
    if not isinstance(payload, dict) or set(payload) != set(FINAL_DETAIL_FIELDS):
        actual = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise ValueError(f'最终深度报告字段与合同不一致：{note_id}#{actual}')
    detail = deepcopy(payload)
    validate_text(detail['one_line'], 'one_line', maximum=100)
    validate_text(detail['what_it_says'], 'what_it_says', minimum=20, maximum=600)
    if not detail['one_line'].startswith('视频') or not detail['what_it_says'].startswith('视频'):
        raise ValueError(f'主旨和详解必须明确归因于视频：{note_id}')

    path_table = detail['path_table']
    if not isinstance(path_table, list) or not 3 <= len(path_table) <= 6:
        raise ValueError(f'path_table 必须是 3 到 6 项：{note_id}')
    labels = set()
    for position, row in enumerate(path_table):
        if not isinstance(row, dict) or set(row) != {'label', 'text'}:
            raise ValueError(f'path_table[{position}] 只能包含 label/text：{note_id}')
        label = validate_text(row['label'], f'path_table[{position}].label', maximum=12, sentence=False)
        text = validate_text(row['text'], f'path_table[{position}].text', minimum=8, maximum=120)
        if any(mark in text[:-1] for mark in '。！？'):
            raise ValueError(f'path_table[{position}].text 必须是单句短句：{note_id}')
        if not text.startswith(CLAIM_PREFIXES):
            raise ValueError(f'path_table[{position}].text 没有明确证据归因：{note_id}')
        if label in labels:
            raise ValueError(f'path_table 标签重复：{note_id}')
        labels.add(label)
    if any(normalized_text_key(row['text']) == normalized_text_key(detail['what_it_says']) for row in path_table):
        raise ValueError(f'path_table 不得复制 what_it_says：{note_id}')

    direct = validate_string_list(
        detail['direct_statements'],
        'direct_statements',
        minimum=2,
        maximum=5,
        required_prefixes=CLAIM_PREFIXES,
    )
    key_points = validate_string_list(
        detail['key_points'],
        'key_points',
        minimum=2,
        maximum=5,
        required_prefixes=CLAIM_PREFIXES,
    )
    if {normalized_text_key(value) for value in direct} & {normalized_text_key(value) for value in key_points}:
        raise ValueError(f'direct_statements 与 key_points 不得重复：{note_id}')
    validate_string_list(
        detail['practical_takeaways'],
        'practical_takeaways',
        minimum=1,
        maximum=5,
        required_prefixes=CLAIM_PREFIXES,
    )
    validate_string_list(
        detail['boundaries'],
        'boundaries',
        minimum=1,
        maximum=5,
        required_prefixes=BOUNDARY_PREFIXES,
    )

    detail['watch_segments'] = bind_watch_segments(
        note_id,
        detail['watch_segments'],
        transcript=transcript,
        evidence=evidence,
        duration_seconds=duration_seconds,
    )

    validate_no_raw_passage(detail, transcript, evidence)
    detail['evidence_contract'] = deepcopy(evidence_contract)
    return detail


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='复用 WatchBefore 本地 Qwen 调用层，直接生成专辑逐条严格多模态深度报告。'
    )
    parser.add_argument('--items', required=True, type=Path)
    parser.add_argument('--classification', required=True, type=Path)
    parser.add_argument('--transcripts', required=True, type=Path)
    parser.add_argument(
        '--visual-evidence',
        required=True,
        type=Path,
        help='按 note id 保存的 WatchBefore visual_evidence；深度报告必须有 MiMo-VL 完整时轴证据。',
    )
    parser.add_argument('--note-id', action='append', help='只生成指定 note id；可重复。')
    parser.add_argument('--watchbefore-root', required=True, type=Path)
    parser.add_argument('--qwen-model', default='qwen3-30b-a3b-instruct-2507-mlx')
    parser.add_argument('--qwen-api-base', default='http://127.0.0.1:1234/v1')
    parser.add_argument('--qwen-timeout', type=int, default=300)
    parser.add_argument('--debug-dir', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()

    watchbefore_scripts = args.watchbefore_root.resolve() / 'scripts'
    if not (watchbefore_scripts / 'analyzer' / 'local_extract.py').is_file():
        parser.error('--watchbefore-root 不是有效的 WatchBefore 仓库')
    sys.path.insert(0, str(args.watchbefore_root.resolve()))
    from scripts.analyzer import local_extract as watchbefore_local_extract

    items = load_json(args.items)
    classification = load_json(args.classification)
    transcripts = load_json(args.transcripts)
    visual_bundle = load_json(args.visual_evidence)
    if not isinstance(visual_bundle, dict) or not isinstance(visual_bundle.get('items'), dict):
        raise ValueError('--visual-evidence 必须包含 items 对象')
    visual_by_id = visual_bundle['items']
    items_by_id = {str(item.get('id') or ''): item for item in items if isinstance(item, dict)}
    class_by_id = {str(item.get('id') or ''): item for item in classification if isinstance(item, dict)}
    selected_ids = {str(note_id).strip() for note_id in (args.note_id or []) if str(note_id).strip()}
    available_ids = {str(row.get('id') or '').strip() for row in transcripts if isinstance(row, dict)}
    missing_ids = sorted(selected_ids - available_ids)
    if missing_ids:
        raise ValueError('--note-id 不在文字稿中：' + ', '.join(missing_ids))
    selected_transcripts = [
        row for row in transcripts
        if isinstance(row, dict) and (not selected_ids or str(row.get('id') or '').strip() in selected_ids)
    ]
    result = {'contract': 'xiaohongshu.album.watchbrief_details.v2', 'items': {}}

    for position, transcript in enumerate(selected_transcripts, 1):
        note_id = str(transcript.get('id') or '').strip()
        item = items_by_id.get(note_id)
        classified = class_by_id.get(note_id)
        if not item or not classified:
            raise ValueError(f'文字稿条目没有对应的 items/classification：{note_id}')
        evidence = visual_by_id.get(note_id)
        evidence_contract = validate_multimodal_evidence(note_id, transcript, evidence)
        coverage = transcript['coverage']

        raw_segments = transcript.get('segments') or []
        duration = coverage.get('video_duration_seconds') or (raw_segments[-1].get('end') if raw_segments else 0)
        debug_dir = (args.debug_dir / note_id) if args.debug_dir else None
        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            'title': str(item.get('title') or '未命名笔记'),
            'url': f'https://www.xiaohongshu.com/explore/{note_id}',
            'channel': str(item.get('user') or '未知'),
            'duration': timecode(duration),
            'duration_seconds': duration,
        }
        report_evidence = build_report_evidence(note_id, transcript, evidence)
        raw_report = call_final_qwen_json(
            watchbefore_local_extract,
            build_final_report_prompt(metadata, report_evidence),
            model_id=args.qwen_model,
            api_base=args.qwen_api_base,
            timeout=args.qwen_timeout,
            debug_dir=debug_dir,
        )
        detail = validate_final_detail(
            note_id,
            raw_report,
            transcript=transcript,
            evidence=evidence,
            duration_seconds=float(duration),
            evidence_contract=evidence_contract,
        )
        result['items'][note_id] = detail
        atomic_json(args.output, result)
        print(f'[{position}/{len(selected_transcripts)}] {note_id} completed', flush=True)

    if not result['items']:
        atomic_json(args.output, result)
    print(json.dumps({'output': str(args.output), 'detail_count': len(result['items'])}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
