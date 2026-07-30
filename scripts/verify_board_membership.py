#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from run_reassign_batch import (
    BOARD_VERIFICATION_JS,
    LIVE_API_RESOLVER_JS,
    BrowserRunner,
    parse_browser_job_id,
    poll_browser_job,
    utc_now,
    write_json,
)
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)


NOTE_ID_RE = re.compile(r'^[0-9a-f]{24}$', re.IGNORECASE)
EXPECTED_SAFE_COUNT = 160
EXPECTED_CROSS_COUNT = 38
EXPECTED_UNASSIGNED_COUNT = 1


class MembershipContractError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_note_id(value: Any, context: str) -> str:
    note_id = str(value or '').strip()
    if not NOTE_ID_RE.fullmatch(note_id):
        raise MembershipContractError(f'{context} must be a 24-character hexadecimal note id')
    return note_id


def baseline_board_maps(
    baseline: Dict[str, Any],
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, str]]:
    boards = baseline.get('boards')
    if not isinstance(boards, list) or not boards:
        raise MembershipContractError('baseline.boards must be a non-empty array')
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    global_occurrences: Dict[str, List[str]] = {}
    for index, board in enumerate(boards):
        if not isinstance(board, dict):
            raise MembershipContractError(f'baseline.boards[{index}] must be an object')
        board_id = require_note_id(board.get('id'), f'baseline.boards[{index}].id')
        name = str(board.get('name') or '').strip()
        if not name:
            raise MembershipContractError(f'baseline.boards[{index}].name must be non-empty')
        if board_id in by_id or name in by_name:
            raise MembershipContractError('baseline board ids and names must be unique')
        note_ids = board.get('note_ids')
        if not isinstance(note_ids, list):
            raise MembershipContractError(f'baseline board {name} note_ids must be an array')
        normalized_ids = [require_note_id(note_id, f'baseline board {name} note_ids') for note_id in note_ids]
        if len(normalized_ids) != len(set(normalized_ids)):
            raise MembershipContractError(f'baseline board {name} contains duplicate note ids')
        normalized = dict(board)
        normalized['id'] = board_id
        normalized['name'] = name
        normalized['note_ids'] = normalized_ids
        by_id[board_id] = normalized
        by_name[name] = normalized
        for note_id in normalized_ids:
            global_occurrences.setdefault(note_id, []).append(board_id)
    duplicates = sorted(note_id for note_id, board_ids in global_occurrences.items() if len(board_ids) != 1)
    if duplicates:
        raise MembershipContractError(f'baseline contains globally duplicated note ids: {duplicates[:5]}')
    note_to_board_id = {
        note_id: board_ids[0]
        for note_id, board_ids in global_occurrences.items()
    }
    return by_id, by_name, note_to_board_id


def validate_input_contract(
    safe_rows: Any,
    cross_rows: Any,
    baseline: Any,
    baseline_sha256: str,
) -> Dict[str, Any]:
    if not isinstance(safe_rows, list) or len(safe_rows) != EXPECTED_SAFE_COUNT:
        raise MembershipContractError(f'safe160 must contain exactly {EXPECTED_SAFE_COUNT} rows')
    if not isinstance(cross_rows, list) or len(cross_rows) != EXPECTED_CROSS_COUNT:
        raise MembershipContractError(f'cross38 must contain exactly {EXPECTED_CROSS_COUNT} rows')
    if not isinstance(baseline, dict):
        raise MembershipContractError('baseline must be an object')
    source = baseline.get('source')
    if baseline.get('mode') != 'read_only' or not isinstance(source, dict) or source.get('writes_performed') is not False:
        raise MembershipContractError('baseline must be a read-only snapshot with writes_performed=false')

    board_by_id, board_by_name, baseline_note_board = baseline_board_maps(baseline)
    safe_by_id: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(safe_rows):
        if not isinstance(row, dict):
            raise MembershipContractError(f'safe160[{index}] must be an object')
        note_id = require_note_id(row.get('id'), f'safe160[{index}].id')
        target = str(row.get('target_board') or '').strip()
        if not target or target not in board_by_name:
            raise MembershipContractError(f'safe160[{index}] target board is missing from baseline: {target!r}')
        if note_id in safe_by_id:
            raise MembershipContractError(f'safe160 contains duplicate id {note_id}')
        safe_by_id[note_id] = row

    baseline_classification = baseline.get('classification')
    if not isinstance(baseline_classification, dict):
        raise MembershipContractError('baseline.classification must be an object')
    baseline_items = baseline_classification.get('items')
    if not isinstance(baseline_items, list) or len(baseline_items) != EXPECTED_SAFE_COUNT:
        raise MembershipContractError('baseline classification must contain exactly 160 items')
    baseline_by_id: Dict[str, Dict[str, Any]] = {}
    status_ids: Dict[str, set] = {
        'already_in_target': set(),
        'in_other_board': set(),
        'not_in_any_board': set(),
    }
    for index, row in enumerate(baseline_items):
        if not isinstance(row, dict):
            raise MembershipContractError(f'baseline.classification.items[{index}] must be an object')
        note_id = require_note_id(row.get('id'), f'baseline.classification.items[{index}].id')
        status = str(row.get('status') or '')
        if status not in status_ids:
            raise MembershipContractError(f'baseline classification has invalid status {status!r}')
        if note_id in baseline_by_id:
            raise MembershipContractError(f'baseline classification contains duplicate id {note_id}')
        if note_id not in safe_by_id:
            raise MembershipContractError(f'baseline classification id is missing from safe160: {note_id}')
        if row.get('target_board') != safe_by_id[note_id].get('target_board'):
            raise MembershipContractError(f'baseline and safe160 target differ for {note_id}')
        row_boards = row.get('boards')
        if not isinstance(row_boards, list):
            raise MembershipContractError(f'baseline classification boards must be an array for {note_id}')
        actual_board_id = baseline_note_board.get(note_id)
        expected_board_ids = [] if actual_board_id is None else [actual_board_id]
        row_board_ids = []
        for ref in row_boards:
            if not isinstance(ref, dict):
                raise MembershipContractError(f'baseline classification board reference is invalid for {note_id}')
            board_id = require_note_id(ref.get('board_id'), f'baseline classification board id for {note_id}')
            board = board_by_id.get(board_id)
            if not board or ref.get('board_name') != board['name']:
                raise MembershipContractError(f'baseline classification board identity is invalid for {note_id}')
            row_board_ids.append(board_id)
        if row_board_ids != expected_board_ids:
            raise MembershipContractError(f'baseline classification board membership is stale for {note_id}')
        target_board_id = board_by_name[row['target_board']]['id']
        if status == 'already_in_target' and actual_board_id != target_board_id:
            raise MembershipContractError(f'baseline already_in_target status is invalid for {note_id}')
        if status == 'in_other_board' and (actual_board_id is None or actual_board_id == target_board_id):
            raise MembershipContractError(f'baseline in_other_board status is invalid for {note_id}')
        if status == 'not_in_any_board' and actual_board_id is not None:
            raise MembershipContractError(f'baseline not_in_any_board status is invalid for {note_id}')
        baseline_by_id[note_id] = row
        status_ids[status].add(note_id)
    if set(safe_by_id) != set(baseline_by_id):
        raise MembershipContractError('baseline classification ids must exactly match safe160 ids')
    if len(status_ids['already_in_target']) != 121 or len(status_ids['in_other_board']) != 38 or len(status_ids['not_in_any_board']) != 1:
        raise MembershipContractError('baseline classification status counts must be 121/38/1')

    cross_by_id: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(cross_rows):
        if not isinstance(row, dict):
            raise MembershipContractError(f'cross38[{index}] must be an object')
        note_id = require_note_id(row.get('id'), f'cross38[{index}].id')
        source_board_id = require_note_id(row.get('source_board_id'), f'cross38[{index}].source_board_id')
        if note_id in cross_by_id:
            raise MembershipContractError(f'cross38 contains duplicate id {note_id}')
        if note_id not in safe_by_id:
            raise MembershipContractError(f'cross38 id is missing from safe160: {note_id}')
        if row.get('target_board') != safe_by_id[note_id].get('target_board'):
            raise MembershipContractError(f'cross38 and safe160 target differ for {note_id}')
        target_board = board_by_name[row['target_board']]
        if source_board_id == target_board['id']:
            raise MembershipContractError(f'cross38 source equals target for {note_id}')
        evidence_hash = str(row.get('membership_evidence_sha256') or '').strip()
        if evidence_hash != baseline_sha256:
            raise MembershipContractError(f'cross38 baseline hash mismatch for {note_id}')
        baseline_row = baseline_by_id[note_id]
        if baseline_note_board.get(note_id) != source_board_id:
            raise MembershipContractError(f'cross38 source board does not match baseline for {note_id}')
        cross_by_id[note_id] = row
    if set(cross_by_id) != status_ids['in_other_board']:
        raise MembershipContractError('cross38 ids must exactly match baseline in_other_board ids')

    unassigned_ids = sorted(status_ids['not_in_any_board'])
    if len(unassigned_ids) != EXPECTED_UNASSIGNED_COUNT:
        raise MembershipContractError('baseline must contain exactly one unassigned item')
    return {
        'safe_by_id': safe_by_id,
        'cross_by_id': cross_by_id,
        'baseline_by_id': baseline_by_id,
        'board_by_id': board_by_id,
        'board_by_name': board_by_name,
        'baseline_note_board': baseline_note_board,
        'unassigned_ids': unassigned_ids,
    }


def build_snapshot_job(user_id: str, verify_pages: int, expected_tab_marker: str, expected_url_substring: str) -> str:
    payload = {
        'userId': user_id,
        'verifyPages': verify_pages,
        'expectedTabMarker': expected_tab_marker,
        'expectedUrlSubstring': expected_url_substring,
    }
    job = r'''
(function() {
  const runId = 'xhs_skill_' + Date.now() + '_' + Math.floor(Math.random() * 1000000);
  const payload = PAYLOAD_JSON;
  const stateNode = document.createElement('div');
  stateNode.id = 'xhs-skill-run-state-' + runId;
  stateNode.hidden = true;
  stateNode.dataset.xhsSkillState = 'pending';
  stateNode.textContent = JSON.stringify({ done: false });
  document.documentElement.appendChild(stateNode);

  const mainWorldJob = function(runId, stateNodeId, payload) {
  function publish(state) {
    const node = document.getElementById(stateNodeId);
    if (!node) throw new Error('Xiaohongshu snapshot state bridge is missing');
    node.dataset.xhsSkillState = state.done ? (state.ok ? 'ok' : 'error') : 'pending';
    node.textContent = JSON.stringify(state);
  }

  const securityMarkers = [
    '安全验证', '异常访问', '访问异常', '访问过于频繁', '操作过于频繁',
    '请求过于频繁', '网络环境存在风险', '当前环境存在风险', '请完成验证',
    '拖动滑块', 'captcha', 'security verification', 'abnormal access', 'too many requests'
  ];

  function assertReadContext(error) {
    const location = String(window.location.href || '');
    const bodyText = (document.body && document.body.innerText) || '';
    const errorText = error && (error.message || error) ? String(error.message || error) : '';
    const haystack = (location + '\n' + bodyText + '\n' + errorText).toLowerCase();
    const marker = securityMarkers.find((value) => haystack.includes(value.toLowerCase()));
    if (marker) throw new Error('SAFETY_BREAKER: Xiaohongshu security challenge detected: ' + marker);
    if (!location.includes('xiaohongshu.com')) throw new Error('current page is not xiaohongshu.com');
    if (payload.expectedTabMarker && window.name !== payload.expectedTabMarker) {
      throw new Error('Arc worker runtime marker no longer matches');
    }
    if (payload.expectedUrlSubstring && !location.includes(payload.expectedUrlSubstring)) {
      throw new Error('Arc worker expected URL no longer matches');
    }
    if (/手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test(bodyText)) {
      throw new Error('current Xiaohongshu page looks logged out');
    }
  }

  function exposeRspackRequire() {
    const chunk = window.webpackChunkxhs_pc_web;
    if (!chunk || typeof chunk.push !== 'function') {
      throw new Error('Rspack runtime not found in Xiaohongshu main world');
    }
    let capturedRequire = null;
    chunk.push([['xhs-snapshot-runtime-' + runId], {}, function(req) { capturedRequire = req; }]);
    if (!capturedRequire) throw new Error('failed to capture Xiaohongshu Rspack require');
    return capturedRequire;
  }

  LIVE_API_RESOLVER_JS

  BOARD_VERIFICATION_JS

  function parseUserBoards(response) {
    if (!response || typeof response !== 'object' || Array.isArray(response)) {
      throw new Error('Xiaohongshu board/user response must be an object');
    }
    if (!Number.isSafeInteger(response.boardCount) || response.boardCount < 0) {
      throw new Error('Xiaohongshu board/user response.boardCount must be a non-negative integer');
    }
    if (!Array.isArray(response.boards) || response.boards.length !== response.boardCount) {
      throw new Error('Xiaohongshu board/user response.boards must match boardCount');
    }
    const ids = new Set();
    const names = new Set();
    return response.boards.map((board, index) => {
      if (!board || typeof board !== 'object' || Array.isArray(board)) {
        throw new Error('Xiaohongshu board/user boards[' + index + '] must be an object');
      }
      const id = typeof board.id === 'string' ? board.id.trim() : '';
      const name = typeof board.name === 'string' ? board.name.trim() : '';
      if (!/^[0-9a-f]{24}$/i.test(id) || !name) {
        throw new Error('Xiaohongshu board/user board id/name contract failed at index ' + index);
      }
      if (ids.has(id) || names.has(name)) {
        throw new Error('Xiaohongshu board/user returned duplicate board id or name');
      }
      ids.add(id);
      names.add(name);
      return { id, name, privacy: board.privacy };
    });
  }

  async function run() {
    assertReadContext();
    const fullApi = findApi(exposeRspackRequire());
    const readApi = Object.freeze({ yC: fullApi.yC, U_: fullApi.U_, Ks: fullApi.Ks });
    assertReadContext();
    const boardResponse = await readApi.yC({
      params: { userId: payload.userId, num: 100, page: 1 }
    });
    assertReadContext();
    const boards = parseUserBoards(boardResponse);
    const rows = [];
    for (const board of boards) {
      assertReadContext();
      const snapshot = await boardSnapshot(readApi, board.id, payload.verifyPages, assertReadContext);
      assertReadContext();
      rows.push({
        id: board.id,
        name: board.name,
        privacy: board.privacy,
        declared_total: snapshot.declaredTotal,
        accessible_unique_count: snapshot.accessibleTotal,
        declared_vs_accessible_delta: snapshot.declaredTotal - snapshot.accessibleTotal,
        page_count: snapshot.pageCount,
        note_ids: snapshot.noteIds
      });
    }
    return { board_count: boardResponse.boardCount, boards: rows };
  }

  Promise.resolve().then(run).then((result) => {
    publish({ done: true, ok: true, result });
  }).catch((error) => {
    publish({ done: true, ok: false, error: error && error.message ? error.message : String(error) });
  });
  };

  const injectedScript = document.createElement('script');
  injectedScript.textContent = '(' + mainWorldJob.toString() + ')(' +
    JSON.stringify(runId) + ',' + JSON.stringify(stateNode.id) + ',' + JSON.stringify(payload) + ');';
  document.documentElement.appendChild(injectedScript);
  injectedScript.remove();
  return runId;
})()
'''
    return (
        job
        .replace('LIVE_API_RESOLVER_JS', LIVE_API_RESOLVER_JS)
        .replace('BOARD_VERIFICATION_JS', BOARD_VERIFICATION_JS)
        .replace('PAYLOAD_JSON', json.dumps(payload, ensure_ascii=False))
    )


def normalize_live_snapshot(result: Any, args: argparse.Namespace) -> Dict[str, Any]:
    if not isinstance(result, dict):
        raise MembershipContractError('browser snapshot result must be an object')
    boards = result.get('boards')
    if not isinstance(boards, list) or result.get('board_count') != len(boards):
        raise MembershipContractError('browser snapshot boards must match board_count')
    normalized_boards = []
    seen_ids = set()
    seen_names = set()
    occurrences: Dict[str, List[Dict[str, str]]] = {}
    within_board_duplicates = []
    for index, board in enumerate(boards):
        if not isinstance(board, dict):
            raise MembershipContractError(f'browser snapshot boards[{index}] must be an object')
        board_id = require_note_id(board.get('id'), f'browser snapshot boards[{index}].id')
        name = str(board.get('name') or '').strip()
        if not name or board_id in seen_ids or name in seen_names:
            raise MembershipContractError('browser snapshot board ids and names must be unique and non-empty')
        seen_ids.add(board_id)
        seen_names.add(name)
        note_ids = board.get('note_ids')
        if not isinstance(note_ids, list):
            raise MembershipContractError(f'browser snapshot board {name} note_ids must be an array')
        normalized_ids = [require_note_id(note_id, f'browser snapshot board {name} note_ids') for note_id in note_ids]
        duplicate_ids = sorted(note_id for note_id in set(normalized_ids) if normalized_ids.count(note_id) > 1)
        if duplicate_ids:
            within_board_duplicates.extend({'board_id': board_id, 'note_id': note_id} for note_id in duplicate_ids)
        accessible_unique_count = len(set(normalized_ids))
        if board.get('accessible_unique_count') != accessible_unique_count:
            raise MembershipContractError(f'browser snapshot board {name} accessible count does not match note_ids')
        declared_total = board.get('declared_total')
        page_count = board.get('page_count')
        if not isinstance(declared_total, int) or isinstance(declared_total, bool) or declared_total < 0:
            raise MembershipContractError(f'browser snapshot board {name} declared_total is invalid')
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
            raise MembershipContractError(f'browser snapshot board {name} page_count is invalid')
        normalized = {
            'id': board_id,
            'name': name,
            'privacy': board.get('privacy'),
            'declared_total': declared_total,
            'accessible_unique_count': accessible_unique_count,
            'declared_vs_accessible_delta': declared_total - accessible_unique_count,
            'page_count': page_count,
            'note_ids': normalized_ids,
        }
        normalized_boards.append(normalized)
        for note_id in normalized_ids:
            occurrences.setdefault(note_id, []).append({'board_id': board_id, 'board_name': name})
    membership = {note_id: refs for note_id, refs in sorted(occurrences.items())}
    duplicate_note_ids = sorted(note_id for note_id, refs in membership.items() if len(refs) > 1)
    multi_board_note_ids = sorted(
        note_id for note_id, refs in membership.items()
        if len({ref['board_id'] for ref in refs}) > 1
    )
    return {
        'generated_at': utc_now(),
        'mode': 'read_only',
        'source': {
            'browser': 'Arc',
            'window_id': args.arc_window_id,
            'tab_id': args.arc_tab_id,
            'tab_marker': args.arc_tab_marker,
            'expected_url_substring': args.arc_expected_url_substring,
            'calls': ['yC (read board/user)', 'U_ (read board detail)', 'Ks (read board/note)'],
            'writes_performed': False,
        },
        'boards': normalized_boards,
        'membership': membership,
        'validation': {
            'board_count': len(normalized_boards),
            'board_names_unique': len(seen_names) == len(normalized_boards),
            'pagination_cursor_invariants_passed': True,
            'accessible_note_occurrences': sum(len(board['note_ids']) for board in normalized_boards),
            'accessible_unique_note_ids_across_boards': len(membership),
            'duplicate_note_ids': duplicate_note_ids,
            'multi_board_note_ids': multi_board_note_ids,
            'within_board_duplicates': within_board_duplicates,
        },
    }


def expected_board_membership(contract: Dict[str, Any]) -> Dict[str, set]:
    expected = {
        board_id: set(board['note_ids'])
        for board_id, board in contract['board_by_id'].items()
    }
    for note_id, row in contract['cross_by_id'].items():
        source_board_id = row['source_board_id']
        target_board_id = contract['board_by_name'][row['target_board']]['id']
        expected[source_board_id].remove(note_id)
        expected[target_board_id].add(note_id)
    for note_id in contract['unassigned_ids']:
        target = contract['safe_by_id'][note_id]['target_board']
        expected[contract['board_by_name'][target]['id']].add(note_id)
    return expected


def verify_final_snapshot(snapshot: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    membership = snapshot['membership']
    snapshot_board_by_id = {board['id']: board for board in snapshot['boards']}
    baseline_identity = {board_id: board['name'] for board_id, board in contract['board_by_id'].items()}
    snapshot_identity = {board_id: board['name'] for board_id, board in snapshot_board_by_id.items()}
    board_identity_matches = snapshot_identity == baseline_identity

    target_mismatches = []
    exactly_once_count = 0
    target_count = 0
    for note_id, row in contract['safe_by_id'].items():
        refs = membership.get(note_id, [])
        if len(refs) == 1:
            exactly_once_count += 1
        if len(refs) == 1 and refs[0]['board_name'] == row['target_board']:
            target_count += 1
        else:
            target_mismatches.append({
                'id': note_id,
                'target_board': row['target_board'],
                'actual_boards': refs,
            })

    source_still_present = []
    for note_id, row in contract['cross_by_id'].items():
        source_board_id = row['source_board_id']
        refs = membership.get(note_id, [])
        if any(ref['board_id'] == source_board_id for ref in refs):
            source_still_present.append({
                'id': note_id,
                'source_board_id': source_board_id,
                'actual_boards': refs,
            })

    unassigned_failures = []
    for note_id in contract['unassigned_ids']:
        target = contract['safe_by_id'][note_id]['target_board']
        refs = membership.get(note_id, [])
        if len(refs) != 1 or refs[0]['board_name'] != target:
            unassigned_failures.append({'id': note_id, 'target_board': target, 'actual_boards': refs})

    expected_sets = expected_board_membership(contract)
    board_membership_mismatches = []
    board_counts = {}
    for board_id, expected_ids in expected_sets.items():
        board = snapshot_board_by_id.get(board_id)
        actual_ids = set(board['note_ids']) if board else set()
        board_counts[contract['board_by_id'][board_id]['name']] = {
            'expected': len(expected_ids),
            'actual': len(actual_ids),
        }
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        if missing or unexpected:
            board_membership_mismatches.append({
                'board_id': board_id,
                'board_name': contract['board_by_id'][board_id]['name'],
                'missing_note_ids': missing,
                'unexpected_note_ids': unexpected,
            })

    duplicate_note_ids = snapshot['validation']['duplicate_note_ids']
    passed = all([
        board_identity_matches,
        target_count == EXPECTED_SAFE_COUNT,
        exactly_once_count == EXPECTED_SAFE_COUNT,
        not source_still_present,
        not unassigned_failures,
        not duplicate_note_ids,
        not board_membership_mismatches,
    ])
    return {
        'generated_at': utc_now(),
        'passed': passed,
        'assertions': {
            'board_identity_matches_baseline': board_identity_matches,
            'target_membership': {'expected': EXPECTED_SAFE_COUNT, 'actual': target_count},
            'classification_items_globally_exactly_once': {
                'expected': EXPECTED_SAFE_COUNT,
                'actual': exactly_once_count,
            },
            'cross_source_absent': {
                'expected': EXPECTED_CROSS_COUNT,
                'actual': EXPECTED_CROSS_COUNT - len(source_still_present),
            },
            'unassigned_now_target': {
                'expected': EXPECTED_UNASSIGNED_COUNT,
                'actual': EXPECTED_UNASSIGNED_COUNT - len(unassigned_failures),
            },
            'global_duplicate_note_ids': {'expected': 0, 'actual': len(duplicate_note_ids)},
            'full_board_membership_matches_expected': not board_membership_mismatches,
        },
        'board_counts': board_counts,
        'mismatches': {
            'target_membership': target_mismatches,
            'source_still_present': source_still_present,
            'unassigned': unassigned_failures,
            'global_duplicate_note_ids': duplicate_note_ids,
            'board_membership': board_membership_mismatches,
        },
    }


def validate_arc_locator(args: argparse.Namespace) -> None:
    required = {
        '--arc-window-id': args.arc_window_id,
        '--arc-tab-id': args.arc_tab_id,
        '--arc-tab-marker': args.arc_tab_marker,
        '--arc-expected-url-substring': args.arc_expected_url_substring,
    }
    missing = [name for name, value in required.items() if not str(value or '').strip()]
    if missing:
        raise MembershipContractError(f'Arc read-only snapshot requires all four locators; missing: {", ".join(missing)}')
    if not isinstance(args.verify_pages, int) or isinstance(args.verify_pages, bool) or args.verify_pages < 1:
        raise MembershipContractError('--verify-pages must be a positive integer')
    require_note_id(args.user_id, '--user-id')
    if not isinstance(args.timeout_sec, int) or isinstance(args.timeout_sec, bool) or args.timeout_sec < 1:
        raise MembershipContractError('--timeout-sec must be a positive integer')


def execute_snapshot_verification(args: argparse.Namespace) -> Dict[str, Any]:
    validate_arc_locator(args)
    safe_path = Path(args.safe160)
    cross_path = Path(args.cross38)
    baseline_path = Path(args.baseline)
    output_path = Path(args.output)
    report_path = Path(args.report)
    safety_state = resolve_safety_state_path(
        getattr(args, 'safety_state', ''),
        output_path,
        predecessors=(safe_path, cross_path, baseline_path),
    )
    ensure_active_session(
        safety_state,
        stage='board_verification',
        policy={
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'read_only': True,
        },
    )
    safe_rows = load_json(safe_path)
    cross_rows = load_json(cross_path)
    baseline = load_json(baseline_path)
    baseline_sha256 = sha256_file(baseline_path)
    contract = validate_input_contract(safe_rows, cross_rows, baseline, baseline_sha256)

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
                stage='board_verification',
                reason_code=reason_code,
                message=message,
            )
        failure_report = {
            'generated_at': utc_now(),
            'passed': False,
            'stage': 'read_only_snapshot',
            'error': str(exc),
            'snapshot': str(output_path),
            'safety_state': str(safety_state),
        }
        write_json(report_path, failure_report)
        raise
    finally:
        runner.close()

    snapshot = normalize_live_snapshot(result, args)
    snapshot['inputs'] = {
        'safe160': str(safe_path),
        'cross38': str(cross_path),
        'baseline': str(baseline_path),
        'baseline_sha256': baseline_sha256,
    }
    write_json(output_path, snapshot)
    report = verify_final_snapshot(snapshot, contract)
    report['inputs'] = snapshot['inputs']
    report['snapshot'] = str(output_path)
    write_json(report_path, report)
    if not report['passed']:
        raise MembershipContractError(f'final board membership verification failed; report: {report_path}')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description='只读抓取小红书全部专辑成员，并严格验收 160 条分类结果。')
    parser.add_argument('--safe160', required=True, help='160 条最终分类 JSON')
    parser.add_argument('--cross38', required=True, help='38 条跨专辑计划 JSON')
    parser.add_argument('--baseline', required=True, help='移动前只读全专辑快照 JSON')
    parser.add_argument('--output', required=True, help='移动后只读全专辑快照输出 JSON')
    parser.add_argument('--report', required=True, help='严格验收报告输出 JSON')
    parser.add_argument('--user-id', required=True, help='当前小红书用户 id')
    parser.add_argument('--arc-window-id', required=True)
    parser.add_argument('--arc-tab-id', required=True)
    parser.add_argument('--arc-tab-marker', required=True)
    parser.add_argument('--arc-expected-url-substring', required=True)
    parser.add_argument('--verify-pages', type=int, default=100)
    parser.add_argument('--timeout-sec', type=int, default=300)
    parser.add_argument('--safety-state', default='', help='共享安全状态文件；默认继承输入旁已有状态，否则使用输出快照同目录的 xhs_safety_state.json')
    args = parser.parse_args()
    try:
        report = execute_snapshot_verification(args)
    except Exception as exc:
        print(json.dumps({'passed': False, 'error': str(exc), 'report': args.report}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({
        'passed': True,
        'snapshot': args.output,
        'report': args.report,
        'assertions': report['assertions'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
