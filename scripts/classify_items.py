#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from xhs_ocr_common import infer_board, load_json, load_taxonomy, perform_ocr_for_items, write_json


def load_existing_inventory(path):
    if not path:
        return {}
    data = load_json(Path(path))
    note_to_board = data.get('note_to_board', {}) if isinstance(data, dict) else {}
    if not isinstance(note_to_board, dict):
        note_to_board = {}
    return {str(note_id): str(board) for note_id, board in note_to_board.items() if note_id and board}

def main():
    parser = argparse.ArgumentParser(description='基于元数据 + 封面 OCR 结果生成 classification.json。')
    parser.add_argument('src', help='visible_items.json 路径')
    parser.add_argument('out', help='classification.json 输出路径')
    parser.add_argument('--taxonomy', default=None, help='board_taxonomy.json 路径')
    parser.add_argument('--ocr-results', default=None, help='ocr_results.json 路径；未提供时自动生成')
    parser.add_argument('--cache-dir', default=None, help='OCR 下载缓存目录')
    parser.add_argument('--ocr-timeout-sec', type=int, default=20, help='OCR 图片下载超时时间')
    parser.add_argument('--skip-ocr', action='store_true', help='跳过 OCR，只使用已有元数据做分类')
    parser.add_argument('--force-ocr', action='store_true', help='忽略已有 OCR 结果，强制重跑')
    parser.add_argument('--existing-boards-inventory', default=None, help='existing_boards_inventory.json 路径；默认排除已在已有专辑中的笔记')
    parser.add_argument('--include-existing-boards', action='store_true', help='纳入已有专辑内容；默认不纳入')
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    items = load_json(src)
    boards = load_taxonomy(Path(args.taxonomy)) if args.taxonomy else load_taxonomy(None)
    existing_note_to_board = load_existing_inventory(args.existing_boards_inventory)

    ocr_map = {}
    ocr_output = None
    if not args.skip_ocr:
        ocr_output = Path(args.ocr_results) if args.ocr_results else out.parent / 'ocr_results.json'
        ocr_results = perform_ocr_for_items(
            items,
            ocr_output,
            cache_dir=Path(args.cache_dir) if args.cache_dir else None,
            timeout_sec=args.ocr_timeout_sec,
            force=args.force_ocr,
        )
        ocr_map = {entry.get('id'): entry for entry in ocr_results if isinstance(entry, dict) and entry.get('id')}

    result = []
    for item in items:
        ocr_entry = ocr_map.get(item.get('id'))
        board, confidence, reason, review_state = infer_board(item, ocr_entry, boards)
        item_id = item.get('id')
        row = {
            'id': item.get('id'),
            'title': item.get('title'),
            'target_board': board,
            'confidence': confidence,
            'reason': reason,
            'review_state': review_state,
            'ocr_status': (ocr_entry or {}).get('status', 'skipped' if args.skip_ocr else 'missing'),
            'ocr_confidence': (ocr_entry or {}).get('ocr_confidence'),
            'ocr_text': (ocr_entry or {}).get('ocr_text', ''),
            'ocr_image_url': (ocr_entry or {}).get('image_url') or item.get('cover_image_url', ''),
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
