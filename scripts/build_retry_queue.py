#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def is_security_halted(item, report):
    if item.get('status') == 'security_halted':
        return True
    if report.get('safety_state') == 'security_halted' or report.get('security_halted') is True:
        return True
    text = ' '.join([
        str(item.get('error') or ''),
        '; '.join(str(event) for event in item.get('events', []) if isinstance(event, str)),
    ]).lower()
    return any(marker in text for marker in (
        'safety_breaker', 'securitychallengeerror', '安全验证', '异常访问',
        '访问过于频繁', 'security verification', 'high_risk_state_uncertain',
        'arc worker runtime marker', 'state bridge is missing',
    ))


def main():
    parser = argparse.ArgumentParser(description='从 run_report.json 生成 retry_queue.json。')
    parser.add_argument('report', help='run_report.json 路径')
    parser.add_argument('out', help='retry_queue.json 输出路径')
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding='utf-8'))
    retry = []
    seen = set()

    def add_item(item):
        if item.get('status') not in {'failed', 'verification_failed', 'security_halted'}:
            return
        reason = item.get('error') or '; '.join(item.get('events', []))
        key = (item.get('id'), item.get('target_board'), reason)
        if key in seen:
            return
        seen.add(key)
        halted = is_security_halted(item, report)
        retry.append({
            'id': item.get('id'),
            'title': item.get('title'),
            'target_board': item.get('target_board'),
            'reason': reason,
            'retry_eligible': not halted,
            'next_action': (
                'manual_complete_platform_verification_then_start_new_session'
                if halted else 'review_failure_then_start_new_session'
            ),
        })

    for item in report.get('processed', []):
        add_item(item)
    for item in report.get('errors', []):
        add_item(item)
    out = Path(args.out)
    out.write_text(json.dumps(retry, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'retry_count': len(retry), 'output': str(out)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
