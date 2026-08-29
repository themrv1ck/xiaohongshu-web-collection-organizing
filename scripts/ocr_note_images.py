#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from video_content_common import normalize_content_type
from collection_scope import validate_scope_input
from archive_exclusion import combine_archived_note_maps
from xhs_ocr_common import load_json, perform_ocr_for_items


def main():
    parser = argparse.ArgumentParser(description='对小红书图文笔记的封面和全部内页图片逐张执行 OCR。')
    parser.add_argument('src', help='image_items.json 路径')
    parser.add_argument('out', nargs='?', default='ocr_results.json', help='ocr_results.json 输出路径')
    parser.add_argument('--cache-dir', default=None, help='OCR 下载缓存目录')
    parser.add_argument('--ocr-timeout-sec', type=int, default=20, help='单张图片下载超时时间')
    parser.add_argument('--force', action='store_true', help='忽略已有 OCR 结果，强制重跑')
    parser.add_argument('--provider', choices=['auto', 'swift', 'tesseract', 'easyocr'], default='auto', help='OCR 后端：macOS 默认 Swift Vision；Windows 默认 Tesseract')
    parser.add_argument('--tesseract-lang', default='chi_sim', help='Tesseract 语言包；默认使用已检测的 chi_sim，可显式传 chi_sim+eng')
    parser.add_argument('--collection-scope', default='', help='可选 collection_scope.json；提供时强制校验完整 note ID 范围')
    parser.add_argument('--archive-registry', action='append', default=[], help='已确认归档基线或 existing boards inventory；可重复传入，命中 ID 不执行 OCR')
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    items = load_json(src)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('image_items.json 必须是对象数组')
    scope_user_id = ''
    if str(args.collection_scope or '').strip():
        scope = validate_scope_input(args.collection_scope, items, stage='OCR 输入')
        scope_user_id = str((scope.get('page_binding') or {}).get('user_id') or '')
    archived_note_map = combine_archived_note_maps(
        args.archive_registry,
        expected_user_id=scope_user_id or None,
    )
    archived_input_ids = {
        str(item.get('id') or '').strip()
        for item in items
        if str(item.get('id') or '').strip() in archived_note_map
    }
    unarchived_items = [
        item for item in items
        if str(item.get('id') or '').strip() not in archived_note_map
    ]
    image_items = [
        item for item in unarchived_items
        if normalize_content_type(item.get('content_type') or item.get('note_type') or item.get('type')) == 'image'
    ]
    results = perform_ocr_for_items(
        image_items,
        out,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        timeout_sec=args.ocr_timeout_sec,
        force=args.force,
        provider=args.provider,
        tesseract_lang=args.tesseract_lang,
    )
    ok = sum(1 for entry in results if entry.get('status') == 'ok')
    incomplete = sum(1 for entry in results if entry.get('status') == 'incomplete_image_set')
    failed = len(results) - ok - incomplete
    print(json.dumps({
        'image_note_count': len(image_items),
        'skipped_non_image_count': len(unarchived_items) - len(image_items),
        'ok': ok,
        'incomplete_image_set': incomplete,
        'failed': failed,
        'archived_excluded': len(archived_input_ids),
        'output': str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
