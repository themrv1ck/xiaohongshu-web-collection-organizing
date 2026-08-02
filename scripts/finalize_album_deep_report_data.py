#!/usr/bin/env python3
"""Validate and finalize one arbitrary Xiaohongshu deep-report dataset."""

import argparse
import hashlib
import json
import math
import os
import re
from copy import deepcopy
from pathlib import Path


DETAILS_CONTRACT = 'xiaohongshu.album.watchbrief_details.v3'
INPUT_DETAILS_CONTRACT = 'xiaohongshu.album.watchbrief_details.v2'
VISUAL_BUNDLE_CONTRACT = 'xiaohongshu.album.visual_evidence.v1'
VISUAL_EVIDENCE_VERSION = 'watchbrief_v5.visual_evidence.v1'
VIDEO_EVIDENCE_BASIS = 'mimo_audio_plus_mimo_vl_full_timeline'
IMAGE_EVIDENCE_BASIS = 'complete_image_ocr'
SHA256_RE = re.compile(r'[0-9a-f]{64}')


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def stable_sha256(value) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def require_sha256(value, label: str) -> str:
    text = str(value or '').strip()
    if SHA256_RE.fullmatch(text) is None:
        raise ValueError(f'{label} 不是有效 sha256')
    return text


def require_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} 不是数值')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f'{label} 不是有限数值')
    return result


def index_rows(rows, label: str) -> tuple[list[str], dict[str, dict]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f'{label} 必须是非空数组')
    order = []
    indexed = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f'{label}[{position}] 必须是对象')
        note_id = str(row.get('id') or '').strip()
        if not note_id:
            raise ValueError(f'{label}[{position}] 缺少 id')
        if note_id in indexed:
            raise ValueError(f'{label} 包含重复 id：{note_id}')
        order.append(note_id)
        indexed[note_id] = row
    return order, indexed


def bundle_items(value, label: str) -> dict[str, dict]:
    if not isinstance(value, dict) or not isinstance(value.get('items'), dict):
        raise ValueError(f'{label} 必须包含 items 对象')
    result = {}
    for note_id, row in value['items'].items():
        normalized_id = str(note_id).strip()
        if not normalized_id or not isinstance(row, dict):
            raise ValueError(f'{label}.items 包含无效条目：{note_id}')
        result[normalized_id] = row
    return result


def require_exact_ids(actual, expected, label: str) -> None:
    actual_set = set(actual)
    expected_set = set(expected)
    if actual_set != expected_set:
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        raise ValueError(f'{label} id 不完整：missing={missing}, extra={extra}')


def validate_detail(note_id: str, detail: dict, label: str) -> dict:
    required_text = ('one_line', 'what_it_says')
    required_lists = (
        'path_table',
        'direct_statements',
        'key_points',
        'practical_takeaways',
        'boundaries',
        'watch_segments',
    )
    for field in required_text:
        if not str(detail.get(field) or '').strip():
            raise ValueError(f'{label} 缺少 {field}：{note_id}')
    for field in required_lists:
        value = detail.get(field)
        if not isinstance(value, list):
            raise ValueError(f'{label}.{field} 必须是数组：{note_id}')
    for field in ('direct_statements', 'key_points', 'practical_takeaways', 'boundaries'):
        if not detail[field] or any(not str(value).strip() for value in detail[field]):
            raise ValueError(f'{label}.{field} 缺少有效内容：{note_id}')
    return deepcopy(detail)


def validate_visual_evidence(note_id: str, evidence: dict) -> dict:
    if not isinstance(evidence, dict):
        raise ValueError(f'深度视频缺少视觉证据：{note_id}')
    evidence_hash = require_sha256(
        evidence.get('visual_evidence_hash'),
        f'视觉证据 hash：{note_id}',
    )
    hash_material = deepcopy(evidence)
    hash_material.pop('visual_evidence_hash', None)
    if stable_sha256(hash_material) != evidence_hash:
        raise ValueError(f'视觉证据 hash 不一致：{note_id}')
    if evidence.get('evidence_version') != VISUAL_EVIDENCE_VERSION:
        raise ValueError(f'视觉证据版本无效：{note_id}')
    prompt_version = str(evidence.get('prompt_version') or '').strip()
    if not prompt_version:
        raise ValueError(f'视觉证据缺少 prompt_version：{note_id}')
    provider = evidence.get('provider')
    if not isinstance(provider, dict) or provider.get('provider') != 'mimo-vl-mlx':
        raise ValueError(f'视觉证据不是 MiMo-VL：{note_id}')
    for field in ('model', 'version'):
        if not str(provider.get(field) or '').strip():
            raise ValueError(f'视觉 provider 缺少 {field}：{note_id}')
    require_sha256(evidence.get('video_sha256'), f'视频 hash：{note_id}')
    duration = require_number(evidence.get('duration_seconds'), f'视频时长：{note_id}')
    if duration <= 0:
        raise ValueError(f'视频时长必须大于零：{note_id}')

    audio = evidence.get('audio_evidence')
    if not isinstance(audio, dict) or audio.get('provider') != 'mimo_audio':
        raise ValueError(f'视觉证据缺少 MiMo 听觉证据：{note_id}')
    segments = audio.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError(f'MiMo 听觉证据缺少分段：{note_id}')
    for position, segment in enumerate(segments):
        if not isinstance(segment, dict) or not str(segment.get('text') or '').strip():
            raise ValueError(f'MiMo 听觉分段无效：{note_id}#{position}')
        start = require_number(segment.get('start'), f'听觉分段 start：{note_id}#{position}')
        end = require_number(segment.get('end'), f'听觉分段 end：{note_id}#{position}')
        if start < 0 or end <= start:
            raise ValueError(f'MiMo 听觉分段时间无效：{note_id}#{position}')
    transcript_hash = require_sha256(
        evidence.get('transcript_sha256'),
        f'听觉文字稿 hash：{note_id}',
    )
    if stable_sha256(segments) != transcript_hash:
        raise ValueError(f'听觉文字稿 hash 不一致：{note_id}')
    if audio.get('transcript_sha256') != transcript_hash:
        raise ValueError(f'MiMo 听觉证据没有绑定文字稿：{note_id}')

    sampling = evidence.get('sampling')
    if not isinstance(sampling, dict):
        raise ValueError(f'视觉证据缺少 sampling：{note_id}')
    if sampling.get('includes_start') is not True or sampling.get('includes_end') is not True:
        raise ValueError(f'视觉证据未覆盖视频首尾：{note_id}')
    timestamps = sampling.get('timestamps_seconds')
    frames = evidence.get('frames')
    analysis = evidence.get('analysis')
    analyzed_frames = analysis.get('frames') if isinstance(analysis, dict) else None
    if (
        not isinstance(timestamps, list)
        or not isinstance(frames, list)
        or not frames
        or not isinstance(analyzed_frames, list)
        or len(timestamps) != len(frames)
        or len(frames) != len(analyzed_frames)
    ):
        raise ValueError(f'视觉证据完整时轴帧不完整：{note_id}')
    numeric_timestamps = [
        require_number(value, f'采样时间：{note_id}#{position}')
        for position, value in enumerate(timestamps)
    ]
    if numeric_timestamps[0] != 0.0 or numeric_timestamps[-1] != duration:
        raise ValueError(f'视觉证据未严格覆盖视频首尾：{note_id}')
    for position, (timestamp, frame, analyzed) in enumerate(
        zip(numeric_timestamps, frames, analyzed_frames)
    ):
        if not isinstance(frame, dict) or not isinstance(analyzed, dict):
            raise ValueError(f'视觉帧格式无效：{note_id}#{position}')
        frame_hash = require_sha256(frame.get('sha256'), f'视觉帧 hash：{note_id}#{position}')
        expected = (position, timestamp, frame_hash)
        actual = (frame.get('index'), frame.get('timestamp_seconds'), frame.get('sha256'))
        analyzed_actual = (
            analyzed.get('index'),
            analyzed.get('timestamp_seconds'),
            analyzed.get('sha256'),
        )
        if expected != actual or expected != analyzed_actual:
            raise ValueError(f'视觉帧 index/timestamp/hash 未严格绑定：{note_id}#{position}')
        expected_endpoint = 'start' if position == 0 else 'end' if position == len(frames) - 1 else ''
        if str(frame.get('endpoint') or '') != expected_endpoint:
            raise ValueError(f'视觉首尾帧标记无效：{note_id}#{position}')
        if (
            not isinstance(analyzed.get('observation'), str)
            or not isinstance(analyzed.get('visible_text'), list)
            or not isinstance(analyzed.get('actions'), list)
            or not isinstance(analyzed.get('uncertainty'), str)
        ):
            raise ValueError(f'视觉帧分析字段无效：{note_id}#{position}')
    if not str((analysis or {}).get('overall_visual_summary') or '').strip():
        raise ValueError(f'视觉分析缺少整体总结：{note_id}')

    screen_track = evidence.get('screen_text_timeline')
    screen_segments = screen_track.get('segments') if isinstance(screen_track, dict) else None
    if (
        not isinstance(screen_track, dict)
        or screen_track.get('verbatim_visible_text') is not True
        or screen_track.get('includes_start') is not True
        or screen_track.get('includes_end') is not True
        or not str(screen_track.get('provider') or '').strip()
        or not isinstance(screen_segments, list)
    ):
        raise ValueError(f'视觉证据缺少逐字屏幕文字时间线：{note_id}')
    if (
        not isinstance(screen_track.get('text_detected'), bool)
        or screen_track['text_detected'] != bool(screen_segments)
    ):
        raise ValueError(f'视觉证据屏幕文字状态不一致：{note_id}')
    report_text_track = str(evidence.get('report_text_track') or '').strip()
    if report_text_track not in {'mimo_audio', 'screen_text'}:
        raise ValueError(f'视觉证据未指定主内容文字轨：{note_id}')
    has_independent_screen_sampling = report_text_track == 'screen_text'
    if has_independent_screen_sampling and not screen_segments:
        raise ValueError(f'视觉证据把空屏幕文字轨设为主内容：{note_id}')
    if has_independent_screen_sampling:
        require_sha256(
            evidence.get('multimodal_transcript_sha256'),
            f'多模态文字稿 hash：{note_id}',
        )
    frame_hashes = {frame['sha256'] for frame in frames}
    final_frame_hash = frames[-1]['sha256']
    for position, segment in enumerate(screen_segments):
        if not isinstance(segment, dict) or not str(segment.get('text') or '').strip():
            raise ValueError(f'屏幕文字分段无效：{note_id}#{position}')
        start = require_number(segment.get('start'), f'屏幕文字 start：{note_id}#{position}')
        end = require_number(segment.get('end'), f'屏幕文字 end：{note_id}#{position}')
        sample_hash = require_sha256(
            segment.get('sample_frame_sha256'),
            f'屏幕文字帧 hash：{note_id}#{position}',
        )
        is_final_point_observation = (
            start == duration
            and end == duration
            and sample_hash == final_frame_hash
        )
        if (
            start < 0
            or end < start
            or end > duration
            or (end == start and not is_final_point_observation)
        ):
            raise ValueError(f'屏幕文字分段时间无效：{note_id}#{position}')
        if sample_hash not in frame_hashes and not has_independent_screen_sampling:
            raise ValueError(f'屏幕文字没有绑定当前帧：{note_id}#{position}')
    return {
        'basis': VIDEO_EVIDENCE_BASIS,
        'transcript_sha256': transcript_hash,
        'visual_evidence_sha256': evidence_hash,
        'visual_provider': 'mimo-vl-mlx',
        'visual_prompt_version': prompt_version,
        'frame_count': len(frames),
        'screen_text_segment_count': len(screen_segments),
        'report_text_track': report_text_track,
        'analysis_model': str(provider['model']),
        'analysis_provider_version': str(provider['version']),
    }


def validate_video_detail_contract(note_id: str, detail: dict, evidence_contract: dict) -> dict:
    result = validate_detail(note_id, detail, '视频 detail')
    contract = result.get('evidence_contract')
    if not isinstance(contract, dict) or contract.get('basis') != VIDEO_EVIDENCE_BASIS:
        raise ValueError(f'视频 detail 证据 basis 无效：{note_id}')
    required_matches = (
        'transcript_sha256',
        'visual_evidence_sha256',
        'visual_provider',
        'visual_prompt_version',
        'frame_count',
        'screen_text_segment_count',
    )
    for field in required_matches:
        if contract.get(field) != evidence_contract[field]:
            raise ValueError(f'视频 detail 证据合同不一致：{note_id}#{field}')
    if 'report_text_track' in contract and contract['report_text_track'] != evidence_contract['report_text_track']:
        raise ValueError(f'视频 detail 证据合同不一致：{note_id}#report_text_track')
    return result


def merge_detail_bundles(detail_bundles) -> dict[str, dict]:
    if not isinstance(detail_bundles, list) or not detail_bundles:
        raise ValueError('至少需要一份 details bundle')
    merged = {}
    for position, bundle in enumerate(detail_bundles):
        label = f'details bundle[{position}]'
        if not isinstance(bundle, dict) or bundle.get('contract') != INPUT_DETAILS_CONTRACT:
            raise ValueError(f'{label} 合同无效')
        rows = bundle_items(bundle, label)
        duplicates = sorted(set(rows) & set(merged))
        if duplicates:
            raise ValueError(f'{label} 包含重复 id：{duplicates}')
        merged.update(rows)
    return merged


def validate_image_ocr(image_id: str, entry: dict) -> tuple[dict, dict]:
    if not isinstance(entry, dict) or str(entry.get('id') or '').strip() != image_id:
        raise ValueError(f'OCR 结果没有绑定图文：{image_id}')
    if entry.get('status') != 'ok' or entry.get('image_set_complete') is not True:
        raise ValueError(f'图文 OCR 未完整成功：{image_id}')
    counts = {}
    for field in ('image_count_declared', 'image_count_available', 'image_count_processed'):
        value = entry.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f'图文 OCR 图片数无效：{image_id}#{field}')
        counts[field] = value
    if len(set(counts.values())) != 1:
        raise ValueError(
            f'图文 OCR 声明、可用和已处理图片数必须一致：{image_id}#{counts}'
        )
    image_count = counts['image_count_declared']
    images = entry.get('images')
    if not isinstance(images, list) or len(images) != image_count:
        raise ValueError(f'图文 OCR 图片证据数量不一致：{image_id}')
    provider = str(entry.get('ocr_provider') or '').strip()
    if not provider:
        raise ValueError(f'图文 OCR 缺少 provider：{image_id}')
    image_set_hash = require_sha256(entry.get('image_set_sha256'), f'图文集合 hash：{image_id}')
    run_fingerprint = require_sha256(entry.get('ocr_run_fingerprint'), f'图文 OCR 指纹：{image_id}')
    for position, image in enumerate(images):
        if not isinstance(image, dict) or image.get('image_index') != position:
            raise ValueError(f'图文 OCR 图片序号无效：{image_id}#{position}')
        if image.get('status') != 'ok' or not isinstance(image.get('ocr_text'), str):
            raise ValueError(f'图文 OCR 图片未成功：{image_id}#{position}')
        if str(image.get('ocr_provider') or '').strip() != provider:
            raise ValueError(f'图文 OCR provider 不一致：{image_id}#{position}')
        require_sha256(image.get('image_sha256'), f'图文图片 hash：{image_id}#{position}')
        require_sha256(image.get('source_url_sha256'), f'图文来源 hash：{image_id}#{position}')
    contract = {
        'basis': IMAGE_EVIDENCE_BASIS,
        'image_set_sha256': image_set_hash,
        'image_count': image_count,
        'ocr_provider': provider,
        'ocr_run_fingerprint': run_fingerprint,
    }
    return entry, contract


def finalize_dataset(
    items,
    classification,
    detail_bundles,
    ocr_results,
    visual_evidence,
) -> tuple[dict, list[dict]]:
    item_order, _ = index_rows(items, 'items')
    _, classification_by_id = index_rows(classification, 'classification')
    require_exact_ids(classification_by_id, item_order, 'classification')
    video_ids = [
        note_id
        for note_id in item_order
        if str(classification_by_id[note_id].get('content_type') or '').strip() == 'video'
    ]
    image_ids = [
        note_id
        for note_id in item_order
        if str(classification_by_id[note_id].get('content_type') or '').strip() == 'image'
    ]
    supported_ids = set(video_ids) | set(image_ids)
    unknown_ids = [note_id for note_id in item_order if note_id not in supported_ids]
    if unknown_ids:
        raise ValueError(f'深度报告只支持明确的视频或图文类型：{unknown_ids}')

    details_by_id = merge_detail_bundles(detail_bundles)
    require_exact_ids(details_by_id, item_order, 'details bundles')

    if video_ids:
        if (
            not isinstance(visual_evidence, dict)
            or visual_evidence.get('contract') != VISUAL_BUNDLE_CONTRACT
        ):
            raise ValueError('visual evidence bundle 合同无效')
        visual_by_id = bundle_items(visual_evidence, 'visual evidence bundle')
    else:
        visual_by_id = {}
    require_exact_ids(visual_by_id, video_ids, 'visual evidence bundle')
    validated_visual = {
        note_id: validate_visual_evidence(note_id, visual_by_id[note_id])
        for note_id in video_ids
    }

    if image_ids:
        _, ocr_by_id = index_rows(ocr_results, 'ocr_results')
    else:
        ocr_by_id = {}
    require_exact_ids(ocr_by_id, image_ids, 'ocr_results')
    validated_ocr = {
        note_id: validate_image_ocr(note_id, ocr_by_id[note_id])
        for note_id in image_ids
    }

    final_details_by_id = {}
    for note_id in item_order:
        raw_detail = details_by_id[note_id]
        if note_id in validated_visual:
            final_details_by_id[note_id] = validate_video_detail_contract(
                note_id,
                raw_detail,
                validated_visual[note_id],
            )
            continue
        final_image_detail = validate_detail(note_id, raw_detail, '图文 detail')
        final_image_detail['evidence_contract'] = validated_ocr[note_id][1]
        final_details_by_id[note_id] = final_image_detail

    final_classification = []
    for note_id in item_order:
        row = deepcopy(classification_by_id[note_id])
        detail = final_details_by_id[note_id]
        if note_id in validated_ocr:
            ocr_entry, image_contract = validated_ocr[note_id]
            row.update({
                'content_type': 'image',
                'confidence': 'high',
                'classification_basis': 'metadata_and_ocr',
                'ocr_status': 'ok',
                'ocr_provider': ocr_entry['ocr_provider'],
                'ocr_run_fingerprint': ocr_entry['ocr_run_fingerprint'],
                'ocr_image_count': image_contract['image_count'],
                'ocr_image_set_complete': True,
                'ocr_image_set_sha256': ocr_entry['image_set_sha256'],
                'content_summary': detail['one_line'],
                'review_state': 'verified_ocr',
            })
        else:
            evidence = visual_by_id[note_id]
            provider = evidence['provider']
            row.update({
                'content_type': 'video',
                'confidence': 'high',
                'classification_basis': 'video_content',
                'video_analysis_status': 'success',
                'video_analysis_basis': 'full_timeline_visual_with_transcript',
                'visual_status': 'analyzed',
                'visual_reason_code': '',
                'visual_evidence_sha256': evidence['visual_evidence_hash'],
                'analysis_provider': provider['provider'],
                'analysis_model': provider['model'],
                'analysis_provider_version': provider['version'],
                'content_summary': detail['one_line'],
                'review_state': 'multimodal_verified',
            })
        final_classification.append(row)

    return {
        'contract': DETAILS_CONTRACT,
        'items': final_details_by_id,
    }, final_classification


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='严格校验并合并任意规模的小红书深度专辑报告数据。'
    )
    parser.add_argument('--items', required=True, type=Path)
    parser.add_argument('--classification', required=True, type=Path)
    parser.add_argument(
        '--detail-bundle',
        required=True,
        action='append',
        type=Path,
        help='可重复传入；所有 bundle 的 note id 合并后必须精确覆盖专辑',
    )
    parser.add_argument(
        '--ocr-results',
        type=Path,
        help='专辑包含图文时必填，且必须精确覆盖全部图文 note id',
    )
    parser.add_argument(
        '--visual-evidence',
        type=Path,
        help='专辑包含视频时必填，且必须精确覆盖全部视频 note id',
    )
    parser.add_argument('--output-details', required=True, type=Path)
    parser.add_argument('--output-classification', required=True, type=Path)
    args = parser.parse_args()
    if args.output_details.resolve() == args.output_classification.resolve():
        parser.error('两份输出不能写入同一路径')

    details, final_classification = finalize_dataset(
        load_json(args.items),
        load_json(args.classification),
        [load_json(path) for path in args.detail_bundle],
        load_json(args.ocr_results) if args.ocr_results else [],
        load_json(args.visual_evidence) if args.visual_evidence else None,
    )
    atomic_json(args.output_details, details)
    atomic_json(args.output_classification, final_classification)
    video_count = sum(
        row.get('content_type') == 'video'
        for row in final_classification
    )
    image_count = sum(
        row.get('content_type') == 'image'
        for row in final_classification
    )
    print(json.dumps({
        'details_output': str(args.output_details),
        'classification_output': str(args.output_classification),
        'item_count': len(details['items']),
        'video_count': video_count,
        'image_count': image_count,
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
