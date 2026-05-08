#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def normalize_board_names(value):
    if isinstance(value, dict):
        value = value.get('boards', [])
    names = []
    for item in value or []:
        if isinstance(item, dict):
            name = item.get('name') or item.get('title')
        else:
            name = item
        name = str(name or '').strip()
        if name and name not in names:
            names.append(name)
    return names


def main():
    parser = argparse.ArgumentParser(description='核对目标专辑体系和已有专辑，输出 created_boards.json。')
    parser.add_argument('taxonomy', help='board_taxonomy.json 或 templates/board_taxonomy.template.json')
    parser.add_argument('existing_boards', help='现有专辑列表 JSON，例如 {"boards":["滑雪"]}')
    parser.add_argument('out', help='created_boards.json 输出路径')
    args = parser.parse_args()

    target = normalize_board_names(load_json(args.taxonomy))
    existing = normalize_board_names(load_json(args.existing_boards))
    existing_set = set(existing)
    confirmed = [name for name in target if name in existing_set]
    missing = [name for name in target if name not in existing_set]
    result = {
        'confirmed': confirmed,
        'created': [],
        'missing': missing,
        'failed': [],
        'action_required': 'Create missing boards manually in Xiaohongshu before running --execute.' if missing else '',
    }
    out = Path(args.out)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'confirmed': len(confirmed), 'missing': len(missing), 'output': str(out)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
