#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from run_reassign_batch import (
    BrowserRunner,
    choose_backend,
    parse_browser_job_id,
    poll_browser_job,
    write_json,
)
from verify_board_membership import (
    MembershipContractError,
    build_snapshot_job,
    normalize_live_snapshot,
    require_note_id,
    validate_arc_locator,
)
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)


def validate_args(args: argparse.Namespace) -> str:
    backend = choose_backend(args.browser, args)
    require_note_id(args.user_id, '--user-id')
    if not str(args.expected_url_substring or '').strip():
        raise MembershipContractError('--expected-url-substring 不能为空')
    if not isinstance(args.verify_pages, int) or isinstance(args.verify_pages, bool) or args.verify_pages < 1:
        raise MembershipContractError('--verify-pages 必须是大于 0 的整数')
    if not isinstance(args.timeout_sec, int) or isinstance(args.timeout_sec, bool) or args.timeout_sec < 1:
        raise MembershipContractError('--timeout-sec 必须是大于 0 的整数')
    if backend == 'arc':
        args.arc_expected_url_substring = args.expected_url_substring
        validate_arc_locator(args)
    return backend


def capture_snapshot(args: argparse.Namespace) -> dict:
    backend = validate_args(args)
    output_path = Path(args.output)
    safety_state = resolve_safety_state_path(
        args.safety_state,
        output_path,
    )
    ensure_active_session(
        safety_state,
        stage='board_snapshot',
        policy={
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'read_only': True,
        },
    )
    runner = BrowserRunner(backend, args)
    try:
        job = build_snapshot_job(
            args.user_id,
            args.verify_pages,
            args.arc_tab_marker if backend == 'arc' else '',
            args.expected_url_substring,
        )
        run_id = parse_browser_job_id(runner.eval(job))
        result = poll_browser_job(runner, run_id, args.timeout_sec)
    except Exception as exc:
        classified = classify_safety_error(exc)
        if isinstance(exc, SafetyHaltedError) or classified:
            reason_code, message = classified or ('security_challenge', str(exc))
            mark_security_halted(
                safety_state,
                stage='board_snapshot',
                reason_code=reason_code,
                message=message,
            )
        raise
    finally:
        runner.close()

    snapshot = normalize_live_snapshot(result, args)
    snapshot['source'].update({
        'browser': backend,
        'user_id': args.user_id,
        'expected_url_substring': args.expected_url_substring,
        'verify_pages': args.verify_pages,
        'safety_state': str(safety_state),
    })
    count_mismatch_boards = [
        board['name']
        for board in snapshot['boards']
        if board['declared_vs_accessible_delta'] != 0
    ]
    snapshot['validation']['count_mismatch_boards'] = count_mismatch_boards
    snapshot['validation']['display_count_consistent'] = not count_mismatch_boards
    snapshot['validation']['full_membership_complete'] = all([
        snapshot['validation']['pagination_cursor_invariants_passed'],
        not snapshot['validation']['within_board_duplicates'],
        snapshot['validation']['display_count_consistent'],
    ])
    write_json(output_path, snapshot)
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description='通过用户本轮明确授权的小红书前端，只读抓取全部专辑及完整成员关系。'
    )
    parser.add_argument('output', help='board_snapshot.json 输出路径')
    parser.add_argument('--browser', required=True, choices=['arc', 'chrome', 'safari', 'playwright'])
    parser.add_argument('--user-id', required=True, help='当前小红书账号 user id')
    parser.add_argument('--expected-url-substring', required=True, help='当前授权页面 URL 必须包含的稳定片段')
    parser.add_argument('--verify-pages', type=int, default=100)
    parser.add_argument('--timeout-sec', type=int, default=300)
    parser.add_argument('--safety-state', default='')
    parser.add_argument('--arc-window-id', default='')
    parser.add_argument('--arc-tab-id', default='')
    parser.add_argument('--arc-tab-marker', default='')
    parser.add_argument('--arc-expected-url-substring', default='')
    parser.add_argument('--url', default=None)
    parser.add_argument('--channel', default='chromium')
    parser.add_argument('--user-data-dir', default=None)
    parser.add_argument('--cdp-url', default=None)
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    try:
        snapshot = capture_snapshot(args)
    except Exception as exc:
        print(json.dumps({
            'passed': False,
            'error': str(exc),
            'output': args.output,
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({
        'passed': True,
        'output': args.output,
        'board_count': snapshot['validation']['board_count'],
        'full_membership_complete': snapshot['validation']['full_membership_complete'],
        'count_mismatch_boards': snapshot['validation']['count_mismatch_boards'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
