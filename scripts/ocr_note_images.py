#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from video_content_common import normalize_content_type
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
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    items = load_json(src)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('image_items.json 必须是对象数组')
    image_items = [
        item for item in items
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
        'skipped_non_image_count': len(items) - len(image_items),
        'ok': ok,
        'incomplete_image_set': incomplete,
        'failed': failed,
        'output': str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
