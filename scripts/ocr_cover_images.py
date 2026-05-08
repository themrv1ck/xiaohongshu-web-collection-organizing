#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from xhs_ocr_common import load_json, perform_ocr_for_items

def main():
    parser = argparse.ArgumentParser(description='对小红书可见条目封面图批量执行 OCR。')
    parser.add_argument('src', help='visible_items.json 路径')
    parser.add_argument('out', nargs='?', default='ocr_results.json', help='ocr_results.json 输出路径')
    parser.add_argument('--cache-dir', default=None, help='OCR 下载缓存目录')
    parser.add_argument('--ocr-timeout-sec', type=int, default=20, help='图片下载超时时间')
    parser.add_argument('--force', action='store_true', help='忽略已有 OCR 结果，强制重跑')
    parser.add_argument('--provider', choices=['auto', 'swift', 'tesseract', 'easyocr'], default='auto', help='OCR 后端：macOS 默认 swift Vision；Windows 推荐 tesseract 或 easyocr')
    parser.add_argument('--tesseract-lang', default='chi_sim+eng', help='Tesseract 语言包，例如 chi_sim+eng 或 eng')
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    items = load_json(src)
    results = perform_ocr_for_items(
        items,
        out,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        timeout_sec=args.ocr_timeout_sec,
        force=args.force,
        provider=args.provider,
        tesseract_lang=args.tesseract_lang,
    )
    ok = sum(1 for entry in results if entry.get('status') == 'ok')
    print(json.dumps({'count': len(results), 'ok': ok, 'output': str(out)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
