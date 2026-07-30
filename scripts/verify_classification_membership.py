#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from verify_board_membership import (
    BrowserRunner,
    MembershipContractError,
    build_snapshot_job,
    load_json,
    normalize_live_snapshot,
    parse_browser_job_id,
    poll_browser_job,
    require_note_id,
    utc_now,
    validate_arc_locator,
    write_json,
)
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)


def build_classification_scope(classification: Any) -> Dict[str, Any]:
    if not isinstance(classification, list):
        raise MembershipContractError('classification must be an array')

    safe_videos: List[Dict[str, str]] = []
    unresolved_videos: List[Dict[str, Any]] = []
    seen_video_ids = set()
    video_count = 0

    for index, row in enumerate(classification):
        if not isinstance(row, dict):
            raise MembershipContractError(f'classification[{index}] must be an object')
        if str(row.get('content_type') or '').strip() != 'video':
            continue

        video_count += 1
        note_id = require_note_id(row.get('id'), f'classification[{index}].id')
        if note_id in seen_video_ids:
            raise MembershipContractError(f'classification contains duplicate video id {note_id}')
        seen_video_ids.add(note_id)

        target = str(row.get('target_board') or '').strip()
        confidence = str(row.get('confidence') or '').strip()
        review_state = str(row.get('review_state') or '').strip()
        unresolved_reasons = []
        if not target:
            unresolved_reasons.append('empty_target')
        if confidence == 'low':
            unresolved_reasons.append('low_confidence')
        if review_state.endswith('needs_review'):
            unresolved_reasons.append('needs_review')
        if unresolved_reasons:
            unresolved_videos.append({
                'id': note_id,
                'target_board': target,
                'confidence': confidence,
                'review_state': review_state,
                'reasons': unresolved_reasons,
            })

        is_safe = all([
            str(row.get('classification_basis') or '').strip() == 'video_content',
            str(row.get('video_analysis_status') or '').strip() == 'success',
            review_state == 'video_content_classified',
            bool(target),
            confidence != 'low',
        ])
        if is_safe:
            safe_videos.append({'id': note_id, 'target_board': target})

    return {
        'counts': {
            'classification_rows': len(classification),
            'video_rows': video_count,
            'safe_video_rows': len(safe_videos),
            'unresolved_video_rows': len(unresolved_videos),
        },
        'safe_videos': safe_videos,
        'unresolved_videos': unresolved_videos,
    }


def verify_classification_membership(snapshot: Dict[str, Any], scope: Dict[str, Any]) -> Dict[str, Any]:
    board_names = {board['name'] for board in snapshot['boards']}
    required_board_names = {row['target_board'] for row in scope['safe_videos']}
    missing_target_boards = sorted(required_board_names - board_names)

    membership = snapshot['membership']
    exactly_once_count = 0
    in_target_count = 0
    membership_mismatches = []
    for row in scope['safe_videos']:
        refs = membership.get(row['id'], [])
        if len(refs) == 1:
            exactly_once_count += 1
        if len(refs) == 1 and refs[0]['board_name'] == row['target_board']:
            in_target_count += 1
        else:
            membership_mismatches.append({
                'id': row['id'],
                'target_board': row['target_board'],
                'actual_boards': refs,
            })

    safe_count = len(scope['safe_videos'])
    duplicate_note_ids = snapshot['validation']['duplicate_note_ids']
    passed = all([
        not missing_target_boards,
        exactly_once_count == safe_count,
        in_target_count == safe_count,
        not duplicate_note_ids,
    ])
    return {
        'generated_at': utc_now(),
        'mode': 'read_only',
        'passed': passed,
        'classification_counts': scope['counts'],
        'assertions': {
            'target_boards_exist': {
                'expected': len(required_board_names),
                'actual': len(required_board_names) - len(missing_target_boards),
            },
            'safe_videos_globally_exactly_once': {
                'expected': safe_count,
                'actual': exactly_once_count,
            },
            'safe_videos_in_target_board': {
                'expected': safe_count,
                'actual': in_target_count,
            },
            'global_duplicate_note_ids': {
                'expected': 0,
                'actual': len(duplicate_note_ids),
            },
        },
        'unresolved_videos': scope['unresolved_videos'],
        'mismatches': {
            'missing_target_boards': missing_target_boards,
            'safe_video_membership': membership_mismatches,
            'global_duplicate_note_ids': duplicate_note_ids,
        },
    }


def execute_verification(args: argparse.Namespace) -> Dict[str, Any]:
    validate_arc_locator(args)
    classification_path = Path(args.classification)
    snapshot_path = Path(args.snapshot)
    report_path = Path(args.report)
    safety_state = resolve_safety_state_path(
        getattr(args, 'safety_state', ''),
        snapshot_path,
        predecessors=(classification_path,),
    )
    ensure_active_session(
        safety_state,
        stage='classification_verification',
        policy={
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'read_only': True,
        },
    )
    scope = build_classification_scope(load_json(classification_path))

    runner = BrowserRunner('arc', args)
    try:
        job = build_snapshot_job(
            args.user_id,
            args.verify_pages,
            args.arc_tab_marker,
            args.arc_expected_url_substring,
        )
        run_id = parse_browser_job_id(runner.eval(job))
        result = poll_browser_job(runner, run_id, args.timeout_sec)
    except Exception as exc:
        classified = classify_safety_error(exc)
        if isinstance(exc, SafetyHaltedError) or classified:
            reason_code, message = classified or ('security_challenge', str(exc))
            mark_security_halted(
                safety_state,
                stage='classification_verification',
                reason_code=reason_code,
                message=message,
            )
        write_json(report_path, {
            'generated_at': utc_now(),
            'mode': 'read_only',
            'passed': False,
            'stage': 'read_only_snapshot',
            'error': str(exc),
            'classification_counts': scope['counts'],
            'unresolved_videos': scope['unresolved_videos'],
            'snapshot': str(snapshot_path),
            'safety_state': str(safety_state),
        })
        raise
    finally:
        runner.close()

    snapshot = normalize_live_snapshot(result, args)
    snapshot['inputs'] = {'classification': str(classification_path)}
    write_json(snapshot_path, snapshot)

    report = verify_classification_membership(snapshot, scope)
    report['inputs'] = snapshot['inputs']
    report['snapshot'] = str(snapshot_path)
    write_json(report_path, report)
    if not report['passed']:
        raise MembershipContractError(
            f'classification membership verification failed; report: {report_path}'
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description='只读抓取小红书全部专辑，并验收完整 classification 中的安全视频。'
    )
    parser.add_argument('--classification', required=True, help='完整 classification JSON')
    parser.add_argument('--snapshot', required=True, help='只读全专辑快照输出 JSON')
    parser.add_argument('--report', required=True, help='成员关系验收报告输出 JSON')
    parser.add_argument('--user-id', required=True, help='当前小红书用户 id')
    parser.add_argument('--arc-window-id', required=True)
    parser.add_argument('--arc-tab-id', required=True)
    parser.add_argument('--arc-tab-marker', required=True)
    parser.add_argument('--arc-expected-url-substring', required=True)
    parser.add_argument('--verify-pages', type=int, default=100)
    parser.add_argument('--timeout-sec', type=int, default=300)
    parser.add_argument('--safety-state', default='', help='共享安全状态文件；默认继承 classification 旁已有状态，否则使用输出快照同目录的 xhs_safety_state.json')
    args = parser.parse_args()
    try:
        report = execute_verification(args)
    except Exception as exc:
        print(json.dumps({
            'passed': False,
            'error': str(exc),
            'report': args.report,
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({
        'passed': True,
        'snapshot': args.snapshot,
        'report': args.report,
        'classification_counts': report['classification_counts'],
        'assertions': report['assertions'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
