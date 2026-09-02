#!/usr/bin/env python3
"""Collect one new note into an existing album through visible Arc controls."""

import argparse
import json
import sys
from pathlib import Path

from run_reassign_batch import utc_now, write_json
from verify_board_membership import MembershipContractError, require_note_id, validate_arc_locator
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)
from xhs_visible_ui import ArcVisibleUiSession, collect_new_note_into_board


def validate_args(args: argparse.Namespace) -> None:
    validate_arc_locator(args)
    require_note_id(args.note_id, '--note-id')
    target = str(args.target_board or '').strip()
    if not target or target != args.target_board:
        raise MembershipContractError('--target-board 不能为空或带首尾空格')
    if not isinstance(args.timeout_sec, int) or isinstance(args.timeout_sec, bool) or args.timeout_sec < 1:
        raise MembershipContractError('--timeout-sec 必须是大于 0 的整数')


def execute_collect(args: argparse.Namespace) -> dict:
    validate_args(args)
    report_path = Path(args.report)
    safety_state = resolve_safety_state_path(getattr(args, 'safety_state', ''), report_path)
    ensure_active_session(
        safety_state,
        stage='collect_new_note',
        policy={
            'browser': 'Arc',
            'visible_ui_only': True,
            'auto_scroll': True,
            'auto_navigation': True,
            'auto_retry': False,
            'historical_collected_notes_protected': True,
            'max_note_writes': 1,
        },
    )
    session = ArcVisibleUiSession(
        args.arc_window_id,
        args.arc_tab_id,
        args.arc_tab_marker,
        args.user_id,
    )
    try:
        result = collect_new_note_into_board(
            session,
            note_id=args.note_id,
            target_board=args.target_board,
            execute=bool(args.execute),
        )
    except Exception as exc:
        classified = classify_safety_error(exc)
        if isinstance(exc, SafetyHaltedError) or classified:
            reason_code, message = classified or ('security_challenge', str(exc))
            mark_security_halted(
                safety_state,
                stage='collect_new_note',
                reason_code=reason_code,
                message=message,
            )
        write_json(report_path, {
            'generated_at': utc_now(),
            'mode': 'execute' if args.execute else 'dry_run',
            'passed': False,
            'note_id': args.note_id,
            'target_board': args.target_board,
            'error': str(exc),
            'safety_state': str(safety_state),
        })
        raise
    report = {
        'generated_at': utc_now(),
        'mode': 'execute' if args.execute else 'dry_run',
        'passed': True,
        **result,
        'safety_state': str(safety_state),
    }
    write_json(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description='通过 Arc 正式页面收藏一条尚未收藏的笔记，并立即选择已有专辑。'
    )
    parser.add_argument('--note-id', required=True)
    parser.add_argument('--target-board', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--execute', action='store_true', help='不带此参数时只读预检')
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--arc-window-id', required=True)
    parser.add_argument('--arc-tab-id', required=True)
    parser.add_argument('--arc-tab-marker', required=True)
    parser.add_argument('--arc-expected-url-substring', required=True)
    parser.add_argument('--verify-pages', type=int, default=100, help='严格结构校验参数；保留与 Arc 定位合同一致')
    parser.add_argument('--timeout-sec', type=int, default=180)
    parser.add_argument('--safety-state', default='')
    args = parser.parse_args()
    try:
        report = execute_collect(args)
    except Exception as exc:
        print(json.dumps({'passed': False, 'error': str(exc), 'report': args.report}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
