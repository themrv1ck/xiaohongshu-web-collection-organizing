#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from run_reassign_batch import (
    BOARD_LIST_PAGINATION_JS,
    BOARD_VERIFICATION_JS,
    LIVE_API_RESOLVER_JS,
    BrowserRunner,
    parse_browser_job_id,
    poll_browser_job,
    utc_now,
    write_json,
)
from verify_board_membership import MembershipContractError, validate_arc_locator
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)


def build_create_board_job(args: argparse.Namespace) -> str:
    payload = {
        'name': args.name,
        'desc': args.desc,
        'privacy': args.privacy,
        'execute': bool(args.execute),
        'userId': args.user_id,
        'verifyPages': args.verify_pages,
        'expectedTabMarker': args.arc_tab_marker,
        'expectedUrlSubstring': args.arc_expected_url_substring,
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
    if (!node) throw new Error('Xiaohongshu create-board state bridge is missing');
    node.dataset.xhsSkillState = state.done ? (state.ok ? 'ok' : 'error') : 'pending';
    node.textContent = JSON.stringify(state);
  }

  const securityMarkers = [
    '安全验证', '异常访问', '访问异常', '访问过于频繁', '操作过于频繁',
    '请求过于频繁', '网络环境存在风险', '当前环境存在风险', '请完成验证',
    '拖动滑块', 'captcha', 'security verification', 'abnormal access', 'too many requests'
  ];

  function assertContext(error) {
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
    chunk.push([['xhs-create-board-runtime-' + runId], {}, function(req) {
      capturedRequire = req;
    }]);
    if (!capturedRequire) throw new Error('failed to capture Xiaohongshu Rspack require');
    return capturedRequire;
  }

  LIVE_API_RESOLVER_JS

  BOARD_LIST_PAGINATION_JS

  BOARD_VERIFICATION_JS

  function hasExactStringLiteral(source, value) {
    return source.includes('"' + value + '"') ||
      source.includes("'" + value + "'") ||
      source.includes('`' + value + '`');
  }

  function findCreateBoardExport(req) {
    const endpoints = Object.values(XHS_LIVE_API_ENDPOINTS);
    const moduleMatches = Object.keys(req.m).filter((moduleId) => {
      const factory = req.m[moduleId];
      return typeof factory === 'function' &&
        endpoints.every((endpoint) => hasExactEndpointLiteral(factory, endpoint));
    });
    if (moduleMatches.length !== 1) {
      throw new Error('Xiaohongshu board API factory match count must be 1; found ' + moduleMatches.length);
    }
    const functions = collectExportedFunctions(req(moduleMatches[0]));
    const endpoint = '/api/sns/web/v1/board';
    const summary = 'web创建专辑';
    const matches = functions.filter((fn) => {
      const source = Function.prototype.toString.call(fn);
      return hasExactEndpointLiteral(fn, endpoint) &&
        hasExactStringLiteral(source, summary) &&
        /\.\s*post\s*\(/.test(source);
    });
    if (matches.length !== 1) {
      throw new Error('Xiaohongshu create-board export match count must be 1; found ' + matches.length);
    }
    return matches[0];
  }

  function assertOldBoardsUnchanged(before, after) {
    const afterById = new Map(after.boards.map((board) => [board.id, board]));
    for (const board of before.boards) {
      const current = afterById.get(board.id);
      if (!current || current.name !== board.name) {
        throw new Error('existing board identity changed during create-board verification');
      }
    }
  }

  async function loadBoards(api) {
    return loadAllBoardsStrict(api, payload.userId, assertContext);
  }

  async function run() {
    assertContext();
    const req = exposeRspackRequire();
    const api = findApi(req);
    const createBoard = findCreateBoardExport(req);
    const before = await loadBoards(api);
    assertContext();
    const existing = before.boards.filter((board) => board.name === payload.name);
    if (existing.length > 1) throw new Error('multiple boards already use the requested name');
    if (existing.length === 1) {
      if (!payload.execute) {
        return {
          status: 'already_exists',
          writePerformed: false,
          board: existing[0],
          boardCountBefore: before.boardCount,
          boardCountAfter: before.boardCount,
          events: ['preflight:board_already_exists']
        };
      }
      if (Number(existing[0].privacy) !== payload.privacy) {
        throw new Error('existing board privacy does not match the approved value');
      }
      const snapshot = await boardSnapshot(api, existing[0].id, payload.verifyPages, assertContext);
      assertContext();
      if (snapshot.accessibleTotal !== 0 || snapshot.noteIds.length !== 0) {
        throw new Error('existing board with the approved name is not empty');
      }
      return {
        status: 'already_exists',
        writePerformed: false,
        board: existing[0],
        boardCountBefore: before.boardCount,
        boardCountAfter: before.boardCount,
        emptyBoardVerified: true,
        events: ['preflight:board_already_exists', 'verify:existing_board_empty']
      };
    }
    if (!payload.execute) {
      return {
        status: 'planned',
        writePerformed: false,
        board: null,
        boardCountBefore: before.boardCount,
        boardCountAfter: before.boardCount,
        events: ['preflight:name_available', 'dry_run:no_account_changes']
      };
    }

    let writeAttempted = false;
    try {
      assertContext();
      writeAttempted = true;
      await createBoard({
        name: payload.name,
        desc: payload.desc,
        privacy: payload.privacy
      });
      assertContext();
      const after = await loadBoards(api);
      assertContext();
      if (after.boardCount !== before.boardCount + 1) {
        throw new Error('board count did not increase by exactly one');
      }
      assertOldBoardsUnchanged(before, after);
      const created = after.boards.filter((board) => board.name === payload.name);
      if (created.length !== 1) {
        throw new Error('created board name must resolve to exactly one board');
      }
      if (Number(created[0].privacy) !== payload.privacy) {
        throw new Error('created board privacy does not match the requested value');
      }
      const snapshot = await boardSnapshot(api, created[0].id, payload.verifyPages, assertContext);
      assertContext();
      if (snapshot.accessibleTotal !== 0 || snapshot.noteIds.length !== 0) {
        throw new Error('new board is not empty after creation');
      }
      return {
        status: 'created',
        writePerformed: true,
        board: created[0],
        boardCountBefore: before.boardCount,
        boardCountAfter: after.boardCount,
        emptyBoardVerified: true,
        boardVerification: {
          declaredTotal: snapshot.declaredTotal,
          accessibleTotal: snapshot.accessibleTotal,
          countMismatch: snapshot.countMismatch,
          pageCount: snapshot.pageCount
        },
        events: [
          'preflight:name_available',
          'create:called',
          'postflight:board_count_plus_one',
          'postflight:old_boards_unchanged',
          'postflight:new_board_unique',
          'verify:new_board_empty'
        ]
      };
    } catch (error) {
      if (writeAttempted) {
        throw new Error(
          'HIGH_RISK_STATE_UNCERTAIN: create-board write may have been applied; no delete rollback attempted; ' +
          (error && error.message ? error.message : String(error))
        );
      }
      throw error;
    }
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
        .replace('BOARD_LIST_PAGINATION_JS', BOARD_LIST_PAGINATION_JS)
        .replace('BOARD_VERIFICATION_JS', BOARD_VERIFICATION_JS)
        .replace('PAYLOAD_JSON', json.dumps(payload, ensure_ascii=False))
    )


def validate_args(args: argparse.Namespace) -> None:
    validate_arc_locator(args)
    name = str(args.name or '').strip()
    if not name:
        raise MembershipContractError('--name must be non-empty')
    if name != args.name:
        raise MembershipContractError('--name must not contain leading or trailing whitespace')
    if args.privacy not in (0, 1):
        raise MembershipContractError('--privacy must be 0 or 1')


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
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'max_board_creations': 1,
        },
    )
    runner = BrowserRunner('arc', args)
    try:
        run_id = parse_browser_job_id(
            runner.run_javascript(build_create_board_job(args))
        )
        browser_result = poll_browser_job(runner, run_id, args.timeout_sec)
        result = validate_result(browser_result, args.execute)
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
        report = {
            'generated_at': utc_now(),
            'mode': 'execute' if args.execute else 'dry_run',
            'passed': False,
            'name': args.name,
            'error': str(exc),
            'safety_state': str(safety_state),
        }
        write_json(report_path, report)
        raise
    finally:
        runner.close()

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
        description='通过小红书当前前端官方调用严格创建一个专辑；默认 dry-run。'
    )
    parser.add_argument('--name', required=True, help='专辑名称')
    parser.add_argument('--desc', default='', help='专辑描述；默认空')
    parser.add_argument('--privacy', type=int, choices=(0, 1), default=0, help='0 公开，1 私密')
    parser.add_argument('--report', required=True, help='创建报告 JSON')
    parser.add_argument('--execute', action='store_true', help='真实创建；不传只做查重 dry-run')
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--arc-window-id', required=True)
    parser.add_argument('--arc-tab-id', required=True)
    parser.add_argument('--arc-tab-marker', required=True)
    parser.add_argument('--arc-expected-url-substring', required=True)
    parser.add_argument('--verify-pages', type=int, default=100)
    parser.add_argument('--timeout-sec', type=int, default=180)
    parser.add_argument('--safety-state', default='', help='共享安全状态文件；默认与创建报告同目录的 xhs_safety_state.json')
    args = parser.parse_args()
    try:
        report = execute_create_board(args)
    except Exception as exc:
        print(json.dumps({
            'passed': False,
            'error': str(exc),
            'report': args.report,
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
