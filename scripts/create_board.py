#!/usr/bin/env python3
"""Create one Xiaohongshu album through the visible Arc form."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from run_reassign_batch import utc_now, write_json
from verify_board_membership import MembershipContractError, validate_arc_locator
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)
from xhs_visible_ui import (
    ArcVisibleUiSession,
    build_album_list_snapshot_js,
    build_open_create_modal_js,
    create_visible_board,
)


def build_create_board_job(args: argparse.Namespace) -> str:
    """Return only a visible-page DOM action; never resolve a private module/API."""
    validate_args(args)
    if args.execute:
        return build_open_create_modal_js(args.user_id, args.arc_tab_marker)
    return build_album_list_snapshot_js(args.user_id, args.arc_tab_marker)


def validate_args(args: argparse.Namespace) -> None:
    validate_arc_locator(args)
    name = str(args.name or '').strip()
    if not name:
        raise MembershipContractError('--name must be non-empty')
    if name != args.name:
        raise MembershipContractError('--name must not contain leading or trailing whitespace')
    if args.privacy not in (0, 1):
        raise MembershipContractError('--privacy must be 0 or 1')
    if not isinstance(args.timeout_sec, int) or isinstance(args.timeout_sec, bool) or args.timeout_sec < 1:
        raise MembershipContractError('--timeout-sec must be a positive integer')


def validate_result(result: Any, execute: bool) -> Dict[str, Any]:
    if not isinstance(result, dict):
        raise MembershipContractError('create-board browser result must be an object')
    status = result.get('status')
    allowed = {'planned', 'already_exists', 'created'}
    if status not in allowed:
        raise MembershipContractError(f'create-board returned invalid status: {status!r}')
    if execute and status == 'planned':
        raise MembershipContractError('execute mode cannot return planned')
    if not execute and status == 'created':
        raise MembershipContractError('dry-run must not create a board')
    board = result.get('board')
    if status in {'already_exists', 'created'}:
        if not isinstance(board, dict):
            raise MembershipContractError('create-board result is missing board identity')
        board_id = str(board.get('id') or '').strip()
        board_name = str(board.get('name') or '').strip()
        if len(board_id) != 24 or any(ch not in '0123456789abcdefABCDEF' for ch in board_id):
            raise MembershipContractError('create-board result has invalid board id')
        if not board_name:
            raise MembershipContractError('create-board result has empty board name')
    if execute and status in {'already_exists', 'created'} and result.get('emptyBoardVerified') is not True:
        raise MembershipContractError('created or pre-existing planned board was not verified empty')
    return result


def execute_create_board(args: argparse.Namespace) -> Dict[str, Any]:
    validate_args(args)
    report_path = Path(args.report)
    safety_state = resolve_safety_state_path(getattr(args, 'safety_state', ''), report_path)
    ensure_active_session(
        safety_state,
        stage='create_board',
        policy={
            'browser': 'Arc',
            'visible_ui_only': True,
            'auto_scroll': True,
            'auto_navigation': True,
            'auto_retry': False,
            'max_board_creations': 1,
        },
    )
    session = ArcVisibleUiSession(
        args.arc_window_id,
        args.arc_tab_id,
        args.arc_tab_marker,
        args.user_id,
    )
    try:
        result = validate_result(
            create_visible_board(
                session,
                name=args.name,
                description=args.desc,
                privacy=args.privacy,
                execute=bool(args.execute),
            ),
            bool(args.execute),
        )
    except Exception as exc:
        classified = classify_safety_error(exc)
        if isinstance(exc, SafetyHaltedError) or classified:
            reason_code, message = classified or ('security_challenge', str(exc))
            mark_security_halted(
                safety_state,
                stage='create_board',
                reason_code=reason_code,
                message=message,
            )
        write_json(report_path, {
            'generated_at': utc_now(),
            'mode': 'execute' if args.execute else 'dry_run',
            'passed': False,
            'name': args.name,
            'error': str(exc),
            'safety_state': str(safety_state),
        })
        raise

    report = {
        'generated_at': utc_now(),
        'mode': 'execute' if args.execute else 'dry_run',
        'passed': True,
        'name': args.name,
        'desc': args.desc,
        'privacy': args.privacy,
        **result,
        'safety_state': str(safety_state),
    }
    write_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description='通过当前回合明确授权的 Arc 正式页面创建一个小红书专辑。'
    )
    parser.add_argument('--name', required=True, help='专辑名称')
    parser.add_argument('--desc', default='', help='专辑描述；默认空')
    parser.add_argument('--privacy', type=int, choices=(0, 1), default=0, help='0 公开，1 私密')
    parser.add_argument('--report', required=True, help='创建报告 JSON')
    parser.add_argument('--execute', action='store_true', help='确认后通过可见表单提交创建；不带此参数只读预检')
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--arc-window-id', required=True)
    parser.add_argument('--arc-tab-id', required=True)
    parser.add_argument('--arc-tab-marker', required=True)
    parser.add_argument('--arc-expected-url-substring', required=True)
    parser.add_argument('--verify-pages', type=int, default=100)
    parser.add_argument('--timeout-sec', type=int, default=180)
    parser.add_argument('--safety-state', default='', help='共享安全状态文件；默认与创建报告同目录')
    args = parser.parse_args()
    try:
        report = execute_create_board(args)
    except Exception as exc:
        print(json.dumps({'passed': False, 'error': str(exc), 'report': args.report}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
