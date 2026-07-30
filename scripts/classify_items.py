#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from xhs_ocr_common import infer_board, load_json, load_taxonomy, perform_ocr_for_items, write_json
from video_content_common import normalize_content_type


def load_existing_inventory(path):
    if not path:
        return {}
    data = load_json(Path(path))
    note_to_board = data.get('note_to_board', {}) if isinstance(data, dict) else {}
    if not isinstance(note_to_board, dict):
        note_to_board = {}
    return {str(note_id): str(board) for note_id, board in note_to_board.items() if note_id and board}


def load_video_analysis(path):
    if not path:
        return {}
    data = load_json(Path(path))
    rows = data if isinstance(data, list) else data.get('items', []) if isinstance(data, dict) else []
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('video_analysis.json 每一项都必须是对象')
        note_id = str(row.get('id') or '').strip()
        if not note_id:
            raise ValueError('video_analysis.json 包含缺少 ID 的条目')
        if note_id in result:
            raise ValueError(f'video_analysis.json 包含重复 ID：{note_id}')
        result[note_id] = row
    return result

def main():
    parser = argparse.ArgumentParser(description='基于元数据和可选的图文全图片 OCR 结果生成 classification.json。')
    parser.add_argument('src', help='image_items.json 路径；关闭 OCR 时也可直接使用 visible_items.json')
    parser.add_argument('out', help='classification.json 输出路径')
    parser.add_argument('--taxonomy', default=None, help='board_taxonomy.json 路径')
    parser.add_argument('--ocr-results', default=None, help='ocr_results.json 路径；未提供时自动生成')
    parser.add_argument('--cache-dir', default=None, help='OCR 下载缓存目录')
    parser.add_argument('--ocr-timeout-sec', type=int, default=20, help='OCR 图片下载超时时间')
    parser.add_argument('--skip-ocr', action='store_true', help='跳过 OCR，只使用已有元数据做分类')
    parser.add_argument('--force-ocr', action='store_true', help='忽略已有 OCR 结果，强制重跑')
    parser.add_argument('--existing-boards-inventory', default=None, help='existing_boards_inventory.json 路径；默认排除已在已有专辑中的笔记')
    parser.add_argument('--include-existing-boards', action='store_true', help='纳入已有专辑内容；默认不纳入')
    parser.add_argument('--classify-video-by-content', action='store_true', help='视频只采用合格转写和用户选择的分析 provider，不使用简介兜底')
    parser.add_argument('--video-analysis', help='video_analysis.json 路径；开启视频内容分类时必须提供')
    parser.add_argument('--require-visual-analysis', action='store_true', help='要求每条成功视频均已完成完整时轴画面分析')
    parser.add_argument('--allow-partial-video-analysis', action='store_true', help='只用于显式抽样测试；正式全量分类不得开启')
    args = parser.parse_args()

    if args.classify_video_by_content and not args.video_analysis:
        parser.error('--classify-video-by-content 必须同时提供 --video-analysis')
    if args.require_visual_analysis and not args.classify_video_by_content:
        parser.error('--require-visual-analysis 必须与 --classify-video-by-content 同时使用')

    src = Path(args.src)
    out = Path(args.out)
    items = load_json(src)
    boards = load_taxonomy(Path(args.taxonomy)) if args.taxonomy else load_taxonomy(None)
    existing_note_to_board = load_existing_inventory(args.existing_boards_inventory)
    video_analysis_map = load_video_analysis(args.video_analysis)
    if args.classify_video_by_content:
        video_ids = {
            str(item.get('id') or '').strip()
            for item in items
            if normalize_content_type(item.get('content_type') or item.get('note_type') or item.get('type')) == 'video'
        }
        analysis_ids = set(video_analysis_map)
        extra_ids = sorted(analysis_ids - video_ids)
        missing_ids = sorted(video_ids - analysis_ids)
        if extra_ids:
            parser.error('video_analysis.json 包含当前视频输入之外的 ID：' + ', '.join(extra_ids))
        if missing_ids and not args.allow_partial_video_analysis:
            parser.error(
                f'video_analysis.json 尚未完成：缺少 {len(missing_ids)} 条视频；'
                '只有显式抽样测试才能传 --allow-partial-video-analysis'
            )
        valid_success_modes = {
            ('transcript_only', 'not_enabled'),
            ('full_timeline_visual', 'analyzed'),
            ('full_timeline_visual_with_transcript', 'analyzed'),
        }
        for note_id, analysis_row in video_analysis_map.items():
            if analysis_row.get('status') != 'success':
                continue
            mode = (
                str(analysis_row.get('analysis_basis') or ''),
                str(analysis_row.get('visual_status') or ''),
            )
            if mode not in valid_success_modes:
                parser.error(f'video_analysis.json 的成功项缺少有效分析模式：{note_id} mode={mode}')
            if args.require_visual_analysis and mode[0] == 'transcript_only':
                parser.error(f'视觉模块已开启，但视频尚未完成完整时轴画面分析：{note_id}')

    ocr_map = {}
    ocr_output = None
    if not args.skip_ocr:
        ocr_output = Path(args.ocr_results) if args.ocr_results else out.parent / 'ocr_results.json'
        ocr_items = [
            item for item in items
            if normalize_content_type(item.get('content_type') or item.get('note_type') or item.get('type')) == 'image'
        ]
        ocr_results = perform_ocr_for_items(
            ocr_items,
            ocr_output,
            cache_dir=Path(args.cache_dir) if args.cache_dir else None,
            timeout_sec=args.ocr_timeout_sec,
            force=args.force_ocr,
        )
        ocr_map = {
            str(entry.get('id')): entry
            for entry in ocr_results
            if isinstance(entry, dict) and entry.get('id')
        }

    result = []
    for item in items:
        ocr_entry = ocr_map.get(str(item.get('id') or ''))
        item_id = item.get('id')
        content_type = normalize_content_type(item.get('content_type') or item.get('note_type') or item.get('type'))
        analysis = video_analysis_map.get(str(item_id))
        classification_basis = 'metadata_only'
        main_topic = ''
        content_summary = ''
        video_analysis_status = ''
        video_analysis_basis = ''
        visual_status = ''
        analysis_provider = ''
        analysis_model = ''
        analysis_provider_version = ''
        if args.classify_video_by_content and content_type == 'video':
            classification_basis = 'video_content'
            video_analysis_status = str((analysis or {}).get('status') or 'missing')
            video_analysis_basis = str((analysis or {}).get('analysis_basis') or '')
            visual_status = str((analysis or {}).get('visual_status') or '')
            analysis_provider = str((analysis or {}).get('analysis_provider') or '')
            analysis_model = str((analysis or {}).get('analysis_model') or '')
            analysis_provider_version = str((analysis or {}).get('analysis_provider_version') or '')
            target = str((analysis or {}).get('target_board') or '')
            if analysis and analysis.get('status') == 'success' and (not target or target in boards):
                board = target
                confidence = str(analysis.get('confidence') or 'low')
                reason = [str(value) for value in (analysis.get('reason') or []) if str(value).strip()]
                review_state = 'video_content_classified' if board else 'video_content_needs_review'
                main_topic = str(analysis.get('main_topic') or '')
                content_summary = str(analysis.get('content_summary') or '')
            else:
                board = ''
                confidence = 'low'
                reason = [str((analysis or {}).get('reason_code') or 'video_content_unavailable')]
                review_state = 'video_content_unavailable'
        elif args.classify_video_by_content and content_type == 'unknown':
            classification_basis = 'content_type_required'
            board = ''
            confidence = 'low'
            reason = ['content_type_unknown']
            review_state = 'content_type_needs_review'
        elif content_type == 'image' and not args.skip_ocr:
            invalid_ocr_reason = ''
            if not ocr_entry:
                invalid_ocr_reason = 'ocr_missing'
            elif ocr_entry.get('status') != 'ok':
                invalid_ocr_reason = f"ocr_{str(ocr_entry.get('status') or 'missing')}"
            elif ocr_entry.get('image_set_complete') is not True:
                invalid_ocr_reason = 'ocr_incomplete_image_set'
            elif not str(ocr_entry.get('ocr_run_fingerprint') or ''):
                invalid_ocr_reason = 'ocr_run_fingerprint_missing'
            if invalid_ocr_reason:
                classification_basis = 'image_ocr_incomplete'
                board = ''
                confidence = 'low'
                reason = [invalid_ocr_reason]
                review_state = 'image_ocr_incomplete'
            else:
                classification_basis = 'metadata_and_ocr'
                board, confidence, reason, review_state = infer_board(item, ocr_entry, boards)
        else:
            board, confidence, reason, review_state = infer_board(item, None, boards)
        if args.skip_ocr or content_type != 'image':
            ocr_status = 'skipped'
        else:
            ocr_status = str((ocr_entry or {}).get('status') or 'missing')
        ocr_image_evidence = []
        for image in (ocr_entry or {}).get('images', []):
            if not isinstance(image, dict):
                continue
            ocr_image_evidence.append({
                'image_index': image.get('image_index'),
                'status': image.get('status'),
                'ocr_text': image.get('ocr_text', ''),
                'ocr_confidence': image.get('ocr_confidence'),
                'image_sha256': image.get('image_sha256', ''),
                'source_url_sha256': image.get('source_url_sha256', ''),
                'error': image.get('error', ''),
            })
        classification_ocr_fingerprint = ''
        if (
            content_type == 'image'
            and (ocr_entry or {}).get('status') == 'ok'
            and (ocr_entry or {}).get('image_set_complete') is True
        ):
            classification_ocr_fingerprint = str((ocr_entry or {}).get('ocr_run_fingerprint') or '')
        row = {
            'id': item.get('id'),
            'title': item.get('title'),
            'target_board': board,
            'confidence': confidence,
            'reason': reason,
            'review_state': review_state,
            'content_type': content_type,
            'classification_basis': classification_basis,
            'video_analysis_status': video_analysis_status,
            'video_analysis_basis': video_analysis_basis,
            'visual_status': visual_status,
            'analysis_provider': analysis_provider,
            'analysis_model': analysis_model,
            'analysis_provider_version': analysis_provider_version,
            'main_topic': main_topic,
            'content_summary': content_summary,
            'ocr_status': ocr_status,
            'ocr_confidence': (ocr_entry or {}).get('ocr_confidence'),
            'ocr_text': (ocr_entry or {}).get('ocr_text', ''),
            'ocr_run_fingerprint': classification_ocr_fingerprint,
            'ocr_image_count': (ocr_entry or {}).get('image_count_processed', 0),
            'ocr_image_set_complete': (ocr_entry or {}).get('image_set_complete', False),
            'ocr_image_evidence': ocr_image_evidence,
            'source_lists': item.get('source_lists') or ([item.get('source_primary')] if item.get('source_primary') else []),
            'source_primary': item.get('source_primary') or ((item.get('source_lists') or [''])[0] if isinstance(item.get('source_lists'), list) and item.get('source_lists') else ''),
        }
        if args.existing_boards_inventory and not args.include_existing_boards:
            source_board = existing_note_to_board.get(str(item_id))
            if source_board:
                row['excluded'] = True
                row['exclude_reason'] = 'user_kept_existing_boards'
                row['source_board'] = source_board
                row['target_board'] = ''
        result.append(row)

    write_json(out, result)
    print(json.dumps({
        'count': len(result),
        'ocr_output': str(ocr_output) if ocr_output else None,
        'output': str(out),
    }, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
