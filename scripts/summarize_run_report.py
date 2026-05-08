#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='汇总 run_report.json。')
    parser.add_argument('report', help='run_report.json 路径')
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding='utf-8'))
    statuses = Counter(item.get('status', 'unknown') for item in report.get('processed', []))
    summary = {
        'mode': report.get('mode'),
        'visible_count': report.get('visible_count'),
        'processed_count': len(report.get('processed', [])),
        'status_counts': dict(sorted(statuses.items())),
        'error_count': len(report.get('errors', [])),
        'missing_boards': report.get('missing_boards', []),
        'board_counts_before': report.get('board_counts_before', {}),
        'board_counts_after': report.get('board_counts_after', {}),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
