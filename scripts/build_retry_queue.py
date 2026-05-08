#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='从 run_report.json 生成 retry_queue.json。')
    parser.add_argument('report', help='run_report.json 路径')
    parser.add_argument('out', help='retry_queue.json 输出路径')
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding='utf-8'))
    retry = []
    for item in report.get('processed', []):
        if item.get('status') in {'failed', 'verification_failed'}:
            retry.append({
                'id': item.get('id'),
                'title': item.get('title'),
                'target_board': item.get('target_board'),
                'reason': item.get('error') or '; '.join(item.get('events', [])),
                'next_action': 'retry_after_fixing_browser_or_board_state',
            })
    for item in report.get('errors', []):
        retry.append({
            'id': item.get('id'),
            'title': item.get('title'),
            'target_board': item.get('target_board'),
            'reason': item.get('error'),
            'next_action': 'retry_after_fixing_browser_or_board_state',
        })
    out = Path(args.out)
    out.write_text(json.dumps(retry, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'retry_count': len(retry), 'output': str(out)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
