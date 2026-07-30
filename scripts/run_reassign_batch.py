#!/usr/bin/env python3
import argparse
import json
import platform
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from extract_visible_items import arc_js_macos, require_macos_app_running
from xhs_safety import (
    SafetyHaltedError,
    atomic_write_json,
    classify_safety_error,
    default_safety_state_path,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)


LOGIN_MARKERS = ('手机号登录', '登录后推荐', '马上登录即可', '扫码登录', '验证码登录')


LIVE_API_RESOLVER_JS = r'''
const XHS_LIVE_API_ENDPOINTS = Object.freeze({
  LN: '/api/sns/web/v1/note/uncollect',
  B1: '/api/sns/web/v1/note/collect',
  d0: '/api/sns/web/v1/note/move',
  Ks: '/api/sns/web/v1/board/note',
  yC: '/api/sns/web/v1/board/user',
  U_: '/api/sns/web/v1/board/{boardId}'
});

function hasExactEndpointLiteral(fn, endpoint) {
  if (typeof fn !== 'function') return false;
  const source = Function.prototype.toString.call(fn);
  return source.includes('"' + endpoint + '"') ||
    source.includes("'" + endpoint + "'") ||
    source.includes('`' + endpoint + '`');
}

function collectExportedFunctions(moduleExports) {
  const containers = [];
  if (moduleExports !== null && (typeof moduleExports === 'object' || typeof moduleExports === 'function')) {
    containers.push(moduleExports);
    if (moduleExports.default !== null &&
        (typeof moduleExports.default === 'object' || typeof moduleExports.default === 'function') &&
        moduleExports.default !== moduleExports) {
      containers.push(moduleExports.default);
    }
  }
  const functions = [];
  const seen = new Set();
  function add(fn) {
    if (typeof fn === 'function' && !seen.has(fn)) {
      seen.add(fn);
      functions.push(fn);
    }
  }
  for (const container of containers) {
    add(container);
    for (const key of Object.keys(container)) add(container[key]);
  }
  return functions;
}

function uniqueEndpointExport(functions, endpoint, label) {
  const matches = functions.filter((fn) => hasExactEndpointLiteral(fn, endpoint));
  if (matches.length !== 1) {
    throw new Error('Xiaohongshu live API ' + label + ' export match count must be 1; found ' + matches.length);
  }
  return matches[0];
}

function findApi(req) {
  if (typeof req !== 'function' || !req.m || typeof req.m !== 'object') {
    throw new Error('Xiaohongshu Rspack module registry is unavailable');
  }
  const endpoints = Object.values(XHS_LIVE_API_ENDPOINTS);
  const moduleMatches = Object.keys(req.m).filter((moduleId) => {
    const factory = req.m[moduleId];
    return typeof factory === 'function' && endpoints.every((endpoint) => hasExactEndpointLiteral(factory, endpoint));
  });
  if (moduleMatches.length !== 1) {
    throw new Error('Xiaohongshu live API factory match count must be 1; found ' + moduleMatches.length);
  }
  const functions = collectExportedFunctions(req(moduleMatches[0]));
  return Object.freeze({
    LN: uniqueEndpointExport(functions, XHS_LIVE_API_ENDPOINTS.LN, 'LN'),
    B1: uniqueEndpointExport(functions, XHS_LIVE_API_ENDPOINTS.B1, 'B1'),
    d0: uniqueEndpointExport(functions, XHS_LIVE_API_ENDPOINTS.d0, 'd0'),
    Ks: uniqueEndpointExport(functions, XHS_LIVE_API_ENDPOINTS.Ks, 'Ks'),
    yC: uniqueEndpointExport(functions, XHS_LIVE_API_ENDPOINTS.yC, 'yC'),
    U_: uniqueEndpointExport(functions, XHS_LIVE_API_ENDPOINTS.U_, 'U_')
  });
}
'''.strip()


BOARD_TRANSACTION_JS = r'''
class HighRiskStateUncertainError extends Error {
  constructor(message) {
    super('HIGH_RISK_STATE_UNCERTAIN: ' + message);
    this.name = 'HighRiskStateUncertainError';
  }
}

class CrossBoardTransactionError extends Error {
  constructor(message) {
    super(message);
    this.name = 'CrossBoardTransactionError';
  }
}

function errorText(error) {
  return error && error.message ? error.message : String(error);
}

function assertRecoveryIsSafe(assertTransactionSafe, error, events, context) {
  try {
    assertTransactionSafe(error);
  } catch (guardError) {
    events.push('transaction:high_risk_state_uncertain');
    throw new HighRiskStateUncertainError(
      context + '; recovery writes stopped: ' + errorText(guardError)
    );
  }
}

async function rollbackCrossBoardTransaction(
  api, noteId, sourceBoardId, maxPages, events, assertTransactionSafe
) {
  events.push('transaction:rollback');
  events.push('transaction:rollback:start');
  assertRecoveryIsSafe(
    assertTransactionSafe, null, events, 'rollback could not start safely'
  );

  let rollbackEndpointFailure = null;
  try {
    await api.LN({ noteIds: noteId });
    events.push('transaction:rollback:uncollect');
  } catch (error) {
    rollbackEndpointFailure = error;
    events.push('transaction:rollback:uncollect_failed');
    assertRecoveryIsSafe(
      assertTransactionSafe, error, events, 'rollback uncollect state is uncertain'
    );
  }

  assertRecoveryIsSafe(
    assertTransactionSafe, null, events, 'rollback recollect could not start safely'
  );
  try {
    await api.B1({ noteId });
    events.push('transaction:rollback:recollect');
  } catch (error) {
    if (!rollbackEndpointFailure) rollbackEndpointFailure = error;
    events.push('transaction:rollback:recollect_failed');
    assertRecoveryIsSafe(
      assertTransactionSafe, error, events, 'rollback recollect state is uncertain'
    );
  }

  assertRecoveryIsSafe(
    assertTransactionSafe, null, events, 'rollback source move could not start safely'
  );
  try {
    await api.d0({ targetBoardId: sourceBoardId, notesId: noteId });
    events.push('transaction:rollback:move');
  } catch (error) {
    if (!rollbackEndpointFailure) rollbackEndpointFailure = error;
    events.push('transaction:rollback:move_failed');
    assertRecoveryIsSafe(
      assertTransactionSafe, error, events, 'rollback source move state is uncertain'
    );
  }

  assertRecoveryIsSafe(
    assertTransactionSafe, rollbackEndpointFailure, events,
    'rollback source verification could not start safely'
  );
  let sourceSnapshot;
  try {
    sourceSnapshot = await boardSnapshot(api, sourceBoardId, maxPages, assertTransactionSafe);
  } catch (error) {
    events.push('transaction:rollback:source_verify_failed');
    assertRecoveryIsSafe(
      assertTransactionSafe, error, events, 'rollback source verification failed'
    );
    events.push('transaction:rollback:failed');
    events.push('transaction:high_risk_state_uncertain');
    throw new HighRiskStateUncertainError(
      'rollback source verification failed: ' + errorText(error)
    );
  }
  assertRecoveryIsSafe(
    assertTransactionSafe, null, events,
    'rollback source verification completed under an unsafe page state'
  );
  if (!sourceSnapshot.noteIds.includes(noteId)) {
    events.push('transaction:rollback:source_missing');
    events.push('transaction:rollback:failed');
    events.push('transaction:high_risk_state_uncertain');
    throw new HighRiskStateUncertainError(
      'rollback completed without restoring the note to source board ' + sourceBoardId
    );
  }
  events.push('transaction:rollback:source_verified');
  events.push('transaction:rollback:succeeded');
  return sourceSnapshot;
}

async function moveAcrossBoardsTransaction(
  api, noteId, sourceBoardId, targetBoardId, maxPages, events, assertTransactionSafe
) {
  if (!sourceBoardId || sourceBoardId === targetBoardId) {
    throw new Error('cross-board transaction requires different non-empty source and target board ids');
  }
  if (typeof assertTransactionSafe !== 'function') {
    throw new Error('cross-board transaction requires a safety guard');
  }

  assertTransactionSafe();
  const sourceBefore = await boardSnapshot(api, sourceBoardId, maxPages, assertTransactionSafe);
  if (!sourceBefore.noteIds.includes(noteId)) {
    events.push('transaction:preflight:source_missing');
    throw new Error('transaction preflight failed: note is absent from source board');
  }
  events.push('transaction:preflight:source_present');

  assertTransactionSafe();
  const targetBefore = await boardSnapshot(api, targetBoardId, maxPages, assertTransactionSafe);
  if (targetBefore.noteIds.includes(noteId)) {
    events.push('transaction:preflight:target_present');
    throw new Error('transaction preflight failed: note already exists in target board');
  }
  events.push('transaction:preflight:target_absent');
  assertTransactionSafe();

  let transactionFailure = null;
  try {
    await api.LN({ noteIds: noteId });
    events.push('transaction:uncollect');
  } catch (error) {
    transactionFailure = error;
    events.push('transaction:uncollect_failed');
    assertRecoveryIsSafe(
      assertTransactionSafe, error, events, 'initial uncollect state is uncertain'
    );
  }

  assertRecoveryIsSafe(
    assertTransactionSafe, null, events, 'initial recollect could not start safely'
  );
  try {
    await api.B1({ noteId });
    events.push('transaction:recollect');
  } catch (error) {
    if (!transactionFailure) transactionFailure = error;
    events.push('transaction:recollect_failed');
    assertRecoveryIsSafe(
      assertTransactionSafe, error, events, 'initial recollect state is uncertain'
    );
  }

  if (!transactionFailure) {
    let targetStage = 'move';
    try {
      assertRecoveryIsSafe(
        assertTransactionSafe, null, events, 'target move could not start safely'
      );
      await api.d0({ targetBoardId, notesId: noteId });
      events.push('transaction:move');
      targetStage = 'verify';
      assertRecoveryIsSafe(
        assertTransactionSafe, null, events, 'target verification could not start safely'
      );
      const targetSnapshot = await boardSnapshot(api, targetBoardId, maxPages, assertTransactionSafe);
      assertRecoveryIsSafe(
        assertTransactionSafe, null, events,
        'target verification completed under an unsafe page state'
      );
      if (!targetSnapshot.noteIds.includes(noteId)) {
        events.push('transaction:target_missing');
        throw new Error('note not found in target board after cross-board move');
      }
      events.push('transaction:target_verified');
      return { sourceBefore, targetBefore, targetSnapshot };
    } catch (error) {
      if (error && error.name === 'HighRiskStateUncertainError') throw error;
      transactionFailure = error;
      events.push(
        targetStage === 'move' ? 'transaction:move_failed' : 'transaction:target_verify_failed'
      );
      assertRecoveryIsSafe(
        assertTransactionSafe, error, events, 'target move or verification state is uncertain'
      );
    }
  }

  await rollbackCrossBoardTransaction(
    api, noteId, sourceBoardId, maxPages, events, assertTransactionSafe
  );
  throw new CrossBoardTransactionError(
    'cross-board transaction failed; source rollback verified: ' + errorText(transactionFailure)
  );
}
'''.strip()


BOARD_VERIFICATION_JS = r'''
const XHS_BOARD_IMAGE_FORMATS = 'jpg,webp,avif';

function parseBoardNotesPage(response) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) {
    throw new Error('Xiaohongshu board/note response must be an object');
  }
  if (!Array.isArray(response.notes)) {
    throw new Error('Xiaohongshu board/note response.notes must be an array');
  }
  if (typeof response.cursor !== 'string') {
    throw new Error('Xiaohongshu board/note response.cursor must be a string');
  }
  if (typeof response.hasMore !== 'boolean') {
    throw new Error('Xiaohongshu board/note response.hasMore must be a boolean');
  }
  const noteIds = response.notes.map((note, index) => {
    if (!note || typeof note !== 'object' || Array.isArray(note) ||
        typeof note.noteId !== 'string' || !note.noteId.trim()) {
      throw new Error('Xiaohongshu board/note notes[' + index + '].noteId must be a non-empty string');
    }
    return note.noteId.trim();
  });
  return { noteIds, cursor: response.cursor, hasMore: response.hasMore };
}

function parseBoardDetail(response, boardId) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) {
    throw new Error('Xiaohongshu board detail response must be an object');
  }
  if (typeof response.id !== 'string' || response.id !== boardId) {
    throw new Error('Xiaohongshu board detail response.id does not match target board');
  }
  if (!Number.isSafeInteger(response.total) || response.total < 0) {
    throw new Error('Xiaohongshu board detail response.total must be a non-negative integer');
  }
  return { total: response.total };
}

async function boardSnapshot(api, boardId, maxPages, assertSafe) {
  if (!Number.isSafeInteger(maxPages) || maxPages < 1) {
    throw new Error('board verification maxPages must be a positive integer');
  }
  const check = typeof assertSafe === 'function' ? assertSafe : function() {};
  check();
  const detailResponse = await api.U_({
    params: { imageFormats: XHS_BOARD_IMAGE_FORMATS },
    resourceParams: { boardId }
  });
  check();
  const detail = parseBoardDetail(detailResponse, boardId);
  const noteIds = [];
  const seenCursors = new Set(['']);
  let cursor = '';
  let pageCount = 0;
  while (true) {
    if (pageCount >= maxPages) {
      throw new Error('Xiaohongshu board/note pagination exceeded maxPages before completion');
    }
    check();
    const pageResponse = await api.Ks({
      params: { boardId, num: 30, cursor, imageFormats: XHS_BOARD_IMAGE_FORMATS }
    });
    check();
    const page = parseBoardNotesPage(pageResponse);
    noteIds.push(...page.noteIds);
    pageCount += 1;
    if (!page.hasMore) break;
    if (!page.cursor) {
      throw new Error('Xiaohongshu board/note hasMore=true with an empty cursor');
    }
    if (seenCursors.has(page.cursor)) {
      throw new Error('Xiaohongshu board/note hasMore=true with a repeated cursor');
    }
    seenCursors.add(page.cursor);
    cursor = page.cursor;
  }
  const uniqueNoteIds = Array.from(new Set(noteIds));
  if (uniqueNoteIds.length !== noteIds.length) {
    throw new Error('Xiaohongshu board/note pagination returned duplicate noteId values');
  }
  return {
    noteIds: uniqueNoteIds,
    declaredTotal: detail.total,
    accessibleTotal: uniqueNoteIds.length,
    countMismatch: detail.total !== uniqueNoteIds.length,
    pageCount
  };
}
'''.strip()


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path: Path, data: Any) -> None:
    atomic_write_json(path, data)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def osascript(script: str) -> str:
    proc = subprocess.run(['osascript'], input=script, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or 'osascript failed')
    return proc.stdout.strip()


def chrome_js(js: str) -> str:
    require_macos_app_running('Google Chrome')
    script = (
        'tell application "Google Chrome"\n'
        'tell active tab of front window\n'
        f'execute javascript {json.dumps(js)}\n'
        'end tell\n'
        'end tell\n'
    )
    return osascript(script)


def safari_js(js: str) -> str:
    require_macos_app_running('Safari')
    with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8', delete=False) as fh:
        fh.write(js)
        js_path = fh.name
    try:
        script = (
            f'set jsSource to read POSIX file {json.dumps(js_path)} as «class utf8»\n'
            'tell application "Safari"\n'
            'do JavaScript jsSource in current tab of front window\n'
            'end tell\n'
        )
        return osascript(script)
    finally:
        Path(js_path).unlink(missing_ok=True)


def parse_js_json(raw: str) -> Any:
    value: Any = (raw or '').strip()
    if not value:
        return None
    for _ in range(2):
        if not isinstance(value, str):
            break
        value = json.loads(value)
    return value


def parse_browser_job_id(raw: Any) -> str:
    value = str(raw or '').strip()
    for _ in range(2):
        if len(value) < 2 or value[0] != '"' or value[-1] != '"':
            break
        decoded = json.loads(value)
        if not isinstance(decoded, str):
            raise RuntimeError('browser job id is not a string')
        value = decoded.strip()
    parts = value.split('_')
    if len(parts) != 4 or parts[:2] != ['xhs', 'skill'] or not parts[2].isdigit() or not parts[3].isdigit():
        raise RuntimeError('browser returned an invalid Xiaohongshu job id')
    return value


class BrowserRunner:
    def __init__(self, backend: str, args: argparse.Namespace):
        self.backend = backend
        self.args = args
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        if backend == 'playwright':
            self._open_playwright()

    def _open_playwright(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError('Playwright Python 未安装。先运行：python -m pip install playwright && python -m playwright install chromium') from exc
        self.playwright = sync_playwright().start()
        close_context = True
        if self.args.cdp_url:
            self.browser = self.playwright.chromium.connect_over_cdp(self.args.cdp_url)
            self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
            close_context = False
        else:
            profile_dir = Path(self.args.user_data_dir or Path.home() / '.xhs-skill-browser-profile')
            profile_dir.mkdir(parents=True, exist_ok=True)
            launch_args: Dict[str, Any] = {'headless': self.args.headless}
            if self.args.channel and self.args.channel != 'chromium':
                launch_args['channel'] = self.args.channel
            self.context = self.playwright.chromium.launch_persistent_context(str(profile_dir), **launch_args)
        self.close_context = close_context
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        if self.args.url:
            self.page.goto(self.args.url, wait_until='domcontentloaded', timeout=60000)
        self.page.wait_for_load_state('domcontentloaded', timeout=60000)

    def eval(self, js: str) -> str:
        if self.backend == 'arc':
            return arc_js_macos(
                js,
                tab_marker=getattr(self.args, 'arc_tab_marker', ''),
                window_id=getattr(self.args, 'arc_window_id', ''),
                tab_id=getattr(self.args, 'arc_tab_id', ''),
                expected_url_substring=getattr(self.args, 'arc_expected_url_substring', ''),
            )
        if self.backend == 'chrome':
            return chrome_js(js)
        if self.backend == 'safari':
            return safari_js(js)
        return self.page.evaluate(js)

    def close(self) -> None:
        if self.backend != 'playwright':
            return
        try:
            if self.close_context and self.context:
                self.context.close()
            elif self.browser:
                self.browser.close()
        finally:
            if self.playwright:
                self.playwright.stop()


def choose_backend(value: str) -> str:
    if value != 'auto':
        return value
    raise RuntimeError('真实执行必须显式指定 --browser arc、safari、chrome 或 playwright；禁止自动选择外部浏览器。')


def normalize_classification(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for index, item in enumerate(items):
        note_id = str(item.get('id') or '').strip()
        target_board = str(item.get('target_board') or '').strip()
        exclude_reason = str(item.get('exclude_reason') or '').strip()
        excluded = bool(item.get('excluded')) or bool(exclude_reason)
        normalized.append({
            'id': note_id,
            'title': item.get('title') or '',
            'target_board': target_board,
            'confidence': item.get('confidence') or '',
            'review_state': item.get('review_state') or '',
            'excluded': excluded,
            'exclude_reason': exclude_reason,
            'source_board': item.get('source_board') or '',
            'source_board_id': str(item.get('source_board_id') or '').strip(),
            'source_lists': item.get('source_lists') or ([item.get('source_primary')] if item.get('source_primary') else []),
            'source_primary': item.get('source_primary') or ((item.get('source_lists') or [''])[0] if isinstance(item.get('source_lists'), list) and item.get('source_lists') else ''),
            'source_index': index,
        })
    return normalized


def initial_report(classification: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    return {
        'started_at': utc_now(),
        'mode': mode,
        'visible_count': len(classification),
        'processed': [],
        'errors': [],
        'missing_boards': [],
        'board_counts_before': {},
        'board_counts_after': {},
        'board_count_checks': {},
    }


def successful_processed_ids(report: Dict[str, Any]) -> set:
    return {str(row.get('id') or '') for row in report.get('processed', []) if row.get('status') == 'success' and row.get('id')}


def filter_classification_for_resume(classification: List[Dict[str, Any]], previous_report: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if previous_report.get('safety_state') == 'security_halted' or previous_report.get('security_halted') is True:
        raise SafetyHaltedError(
            'resume 拒绝：旧报告记录了安全停机。请先由用户完成平台处理，再使用新的安全状态文件开启新会话。'
        )
    for row in previous_report.get('processed', []):
        if isinstance(row, dict) and row.get('status') == 'security_halted':
            raise SafetyHaltedError(
                'resume 拒绝：旧报告有 security_halted 条目。请先由用户完成平台处理，再开启新会话。'
            )
    success_rows: Dict[str, List[Dict[str, Any]]] = {}
    for row in previous_report.get('processed', []):
        note_id = str(row.get('id') or '').strip()
        if row.get('status') == 'success' and note_id:
            success_rows.setdefault(note_id, []).append(row)
    pending: List[Dict[str, Any]] = []
    preserved: List[Dict[str, Any]] = []
    for item in classification:
        note_id = str(item.get('id') or '').strip()
        rows = success_rows.get(note_id, [])
        if rows:
            current_target = str(item.get('target_board') or '').strip()
            previous_targets = {str(row.get('target_board') or '').strip() for row in rows}
            if previous_targets != {current_target}:
                raise RuntimeError(
                    f'resume 拒绝：已成功条目 {note_id} 的旧目标专辑 '
                    f'{sorted(previous_targets)} 与当前目标专辑 {current_target!r} 不一致'
                )
            preserved.append(rows[-1])
        else:
            pending.append(item)
    return pending, preserved


def merge_report_chunk(report: Dict[str, Any], chunk: Dict[str, Any]) -> None:
    report.setdefault('processed', []).extend(chunk.get('processed', []))
    report.setdefault('errors', []).extend(chunk.get('errors', []))
    missing = report.setdefault('missing_boards', [])
    for board in chunk.get('missing_boards', []):
        if board and board not in missing:
            missing.append(board)
    report.setdefault('board_counts_before', {}).update(chunk.get('board_counts_before', {}))
    report.setdefault('board_counts_after', {}).update(chunk.get('board_counts_after', {}))
    report.setdefault('board_count_checks', {}).update(chunk.get('board_count_checks', {}))


def append_dry_run(report: Dict[str, Any], item: Dict[str, Any], allow_low_confidence: bool) -> None:
    status = 'planned'
    events = ['dry_run:no_account_changes']
    error = ''
    if item.get('excluded') or item.get('exclude_reason'):
        status = 'skipped'
        events = ['skip:existing_board_excluded', 'dry_run:no_account_changes']
        error = item.get('exclude_reason') or 'user_kept_existing_boards'
    elif not item['id']:
        status = 'failed'
        error = 'missing note id'
    elif not item['target_board']:
        status = 'needs_review'
        error = 'missing target_board'
    elif item['confidence'] == 'low' and not allow_low_confidence:
        status = 'needs_review'
        error = 'low confidence classification; rerun with --allow-low-confidence after review'
    report['processed'].append({
        'id': item['id'],
        'title': item['title'],
        'target_board': item['target_board'],
        'status': status,
        'attempt': 0,
        'events': events,
        'error': error,
        'source_board': item.get('source_board', ''),
        'source_board_id': item.get('source_board_id', ''),
        'source_lists': item.get('source_lists', []),
        'source_primary': item.get('source_primary', ''),
        'exclude_reason': item.get('exclude_reason', ''),
    })
    if status == 'failed':
        report['errors'].append(report['processed'][-1])


def build_browser_job(items: List[Dict[str, Any]], args: argparse.Namespace) -> str:
    payload = {
        'items': items,
        'allowLowConfidence': args.allow_low_confidence,
        'verifyPages': args.verify_pages,
        'userId': args.user_id or '',
        'expectedTabMarker': getattr(args, 'arc_tab_marker', '') or '',
        'expectedUrlSubstring': getattr(args, 'arc_expected_url_substring', '') or '',
    }
    browser_job = r"""
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
    if (!node) throw new Error('Xiaohongshu job state bridge is missing');
    node.dataset.xhsSkillState = state.done ? (state.ok ? 'ok' : 'error') : 'pending';
    node.textContent = JSON.stringify(state);
  }

  function textOf(value) {
    if (value === undefined || value === null) return '';
    return String(value).trim();
  }

  const securityMarkers = [
    '安全验证', '异常访问', '访问异常', '访问过于频繁', '操作过于频繁',
    '请求过于频繁', '网络环境存在风险', '当前环境存在风险', '请完成验证',
    '拖动滑块', 'captcha', 'security verification', 'abnormal access', 'too many requests'
  ];

  class SecurityChallengeError extends Error {
    constructor(marker) {
      super('SAFETY_BREAKER: Xiaohongshu security challenge detected: ' + marker);
      this.name = 'SecurityChallengeError';
    }
  }

  class ExecutePageBindingError extends Error {
    constructor(message) {
      super(message);
      this.name = 'ExecutePageBindingError';
    }
  }

  function securityMarkerIn(value) {
    const haystack = textOf(value).toLowerCase();
    return securityMarkers.find((marker) => haystack.includes(marker.toLowerCase())) || '';
  }

  function assertNoSecurityChallenge(error) {
    const location = String(window.location.href || '');
    const bodyText = (document.body && document.body.innerText) || '';
    const marker = securityMarkerIn(location) || securityMarkerIn(bodyText) || securityMarkerIn(error && (error.message || error));
    if (marker) throw new SecurityChallengeError(marker);
  }

  function assertExpectedExecutePage() {
    const location = String(window.location.href || '');
    if (!location.includes('xiaohongshu.com')) {
      throw new ExecutePageBindingError('current page is not xiaohongshu.com: ' + location);
    }
    if (payload.expectedTabMarker && window.name !== payload.expectedTabMarker) {
      throw new ExecutePageBindingError('Arc worker runtime marker no longer matches');
    }
    if (payload.expectedUrlSubstring && !location.includes(payload.expectedUrlSubstring)) {
      throw new ExecutePageBindingError('Arc worker expected URL no longer matches');
    }
  }

  function normalizeBoard(board) {
    const id = textOf(board.id || board.boardId || board.board_id);
    const name = textOf(board.name || board.title);
    const totalRaw = board.total ?? board.noteCount ?? board.note_count ?? board.notesCount;
    const total = Number.isFinite(Number(totalRaw)) ? Number(totalRaw) : null;
    return { id, name, total };
  }

  function flattenBoards(value, out) {
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach((entry) => flattenBoards(entry, out));
      return;
    }
    if (typeof value !== 'object') return;
    const maybe = normalizeBoard(value);
    if (maybe.id && maybe.name) out.push(maybe);
    for (const key of ['boards', 'list', 'items']) {
      if (Array.isArray(value[key])) flattenBoards(value[key], out);
    }
    if (value.data && typeof value.data === 'object') flattenBoards(value.data, out);
  }

  function uniqueBoards(boards) {
    const seen = new Set();
    const result = [];
    for (const board of boards) {
      const key = board.id + '|' + board.name;
      if (!seen.has(key)) {
        seen.add(key);
        result.push(board);
      }
    }
    return result;
  }

  function boardsFromInitialState() {
    const out = [];
    const state = window.__INITIAL_STATE__;
    const data = state && state.board && state.board.boardListData;
    if (data && typeof data === 'object') {
      Object.keys(data).forEach((key) => flattenBoards(data[key], out));
    }
    return uniqueBoards(out);
  }

  function exposeRspackRequire() {
    const chunk = window.webpackChunkxhs_pc_web;
    if (!chunk || typeof chunk.push !== 'function') {
      throw new Error('Rspack runtime not found in Xiaohongshu main world');
    }
    let capturedRequire = null;
    chunk.push([['xhs-skill-runtime-' + runId], {}, function(req) {
      capturedRequire = req;
    }]);
    if (!capturedRequire) throw new Error('failed to capture Xiaohongshu Rspack require');
    return capturedRequire;
  }

  LIVE_API_RESOLVER_JS

  async function boardsFromApi(api) {
    if (!payload.userId || typeof api.yC !== 'function') return [];
    const response = await api.yC({ params: { userId: payload.userId, num: 100, page: 1 } });
    const out = [];
    flattenBoards(response, out);
    return uniqueBoards(out);
  }

  BOARD_VERIFICATION_JS

  BOARD_TRANSACTION_JS

  async function run() {
    const location = String(window.location.href || '');
    const bodyText = (document.body && document.body.innerText) || '';
    assertNoSecurityChallenge();
    assertExpectedExecutePage();
    if (/手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test(bodyText)) {
      throw new Error('current Xiaohongshu page looks logged out');
    }
    const req = exposeRspackRequire();
    const api = findApi(req);
    let boards = boardsFromInitialState();
    if (!boards.length) boards = await boardsFromApi(api);
    if (!boards.length) throw new Error('no boards found; open your Xiaohongshu profile/favorites page first');
    const boardByName = {};
    for (const board of boards) boardByName[board.name] = board;
    const boardCountsBefore = {};
    const boardCountsAfter = {};
    const boardCountChecks = {};
    const processed = [];
    const errors = [];
    const missingBoards = [];

    for (const item of payload.items) {
      const events = [];
      const row = {
        id: item.id,
        title: item.title || '',
        target_board: item.target_board || '',
        status: 'pending',
        attempt: 1,
        events,
        error: '',
        verified: false,
        source_board: item.source_board || '',
        source_board_id: item.source_board_id || '',
        exclude_reason: item.exclude_reason || ''
      };
      try {
        if (item.excluded || item.exclude_reason) {
          row.status = 'skipped';
          row.error = item.exclude_reason || 'user_kept_existing_boards';
          events.push('skip:existing_board_excluded');
          processed.push(row);
          continue;
        }
        if (!item.id) throw new Error('missing note id');
        if (!item.target_board) {
          row.status = 'needs_review';
          row.error = 'missing target_board';
          events.push('skip:missing_target_board');
          processed.push(row);
          continue;
        }
        if (item.confidence === 'low' && !payload.allowLowConfidence) {
          row.status = 'needs_review';
          row.error = 'low confidence classification; review before executing';
          events.push('skip:low_confidence');
          processed.push(row);
          continue;
        }
        const board = boardByName[item.target_board];
        if (!board) {
          row.status = 'failed';
          row.error = 'target board not found: ' + item.target_board;
          events.push('board:missing:' + item.target_board);
          if (!missingBoards.includes(item.target_board)) missingBoards.push(item.target_board);
          processed.push(row);
          errors.push(row);
          continue;
        }
        if (board.total !== null) boardCountsBefore[board.name] = board.total;
        events.push('board:FOUND:' + board.name);
        const useCrossBoardTransaction = Boolean(item.source_board_id) &&
          item.source_board_id !== board.id;
        let snapshot;
        if (useCrossBoardTransaction) {
          const assertTransactionSafe = function(error) {
            assertNoSecurityChallenge(error);
            assertExpectedExecutePage();
          };
          const transaction = await moveAcrossBoardsTransaction(
            api,
            item.id,
            item.source_board_id,
            board.id,
            payload.verifyPages,
            events,
            assertTransactionSafe
          );
          snapshot = transaction.targetSnapshot;
        } else {
          assertNoSecurityChallenge();
          assertExpectedExecutePage();
          await api.d0({ targetBoardId: board.id, notesId: item.id });
          assertNoSecurityChallenge();
          assertExpectedExecutePage();
          events.push('note_move:CALLED');
          snapshot = await boardSnapshot(api, board.id, payload.verifyPages, assertNoSecurityChallenge);
        }
        boardCountsAfter[board.name] = snapshot.accessibleTotal;
        boardCountChecks[board.name] = {
          declared_total: snapshot.declaredTotal,
          accessible_total: snapshot.accessibleTotal,
          count_mismatch: snapshot.countMismatch,
          page_count: snapshot.pageCount
        };
        if (snapshot.countMismatch) events.push('verify:board_count_mismatch');
        if (snapshot.noteIds.includes(item.id)) {
          row.status = 'success';
          row.verified = true;
          events.push('verify:note_present');
        } else {
          row.status = 'verification_failed';
          row.error = 'note not found in target board after move';
          events.push('verify:note_missing');
          errors.push(row);
        }
        processed.push(row);
      } catch (error) {
        if (error && error.name === 'HighRiskStateUncertainError') {
          row.status = 'failed';
          row.error = error.message;
          events.push('error:' + row.error);
          processed.push(row);
          errors.push(row);
          break;
        }
        if (error && (error.name === 'SecurityChallengeError' || error.name === 'ExecutePageBindingError')) throw error;
        assertNoSecurityChallenge(error);
        row.status = 'failed';
        row.error = error && error.message ? error.message : String(error);
        events.push('error:' + row.error);
        processed.push(row);
        errors.push(row);
      }
    }
    return {
      processed,
      errors,
      missing_boards: missingBoards,
      board_counts_before: boardCountsBefore,
      board_counts_after: boardCountsAfter,
      board_count_checks: boardCountChecks
    };
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
"""
    return (
        browser_job
        .replace('LIVE_API_RESOLVER_JS', LIVE_API_RESOLVER_JS)
        .replace('BOARD_VERIFICATION_JS', BOARD_VERIFICATION_JS)
        .replace('BOARD_TRANSACTION_JS', BOARD_TRANSACTION_JS)
        .replace('PAYLOAD_JSON', json.dumps(payload, ensure_ascii=False))
    )


def poll_browser_job(runner: BrowserRunner, run_id: str, timeout_sec: int) -> Dict[str, Any]:
    deadline = time.time() + timeout_sec
    state_node_id = 'xhs-skill-run-state-' + run_id
    poll_js = r'''
(function() {
  const node = document.getElementById(STATE_NODE_ID);
  if (!node) return JSON.stringify(null);
  const state = JSON.parse(node.textContent || '{"done":false}');
  if (node.dataset.xhsSkillState === 'ok' || node.dataset.xhsSkillState === 'error') node.remove();
  return JSON.stringify(state);
})()
'''.replace('STATE_NODE_ID', json.dumps(state_node_id))
    while time.time() < deadline:
        state = parse_js_json(runner.eval(poll_js))
        if state is None:
            raise SafetyHaltedError('browser job state bridge disappeared; 已停止以免在未知页面状态下继续写入')
        if state and state.get('done'):
            if state.get('ok'):
                return state.get('result') or {}
            message = state.get('error') or 'browser job failed'
            if classify_safety_error(message):
                raise SafetyHaltedError(str(message))
            raise RuntimeError(message)
        time.sleep(1)
    raise TimeoutError('browser job timed out')


def record_security_halt(
    report: Dict[str, Any],
    *,
    safety_state: Path,
    item: Optional[Dict[str, Any]],
    error: object,
    existing_row: Optional[Dict[str, Any]] = None,
) -> None:
    classified = classify_safety_error(error)
    reason_code, message = classified or ('security_challenge', str(error))
    mark_security_halted(
        safety_state,
        stage='move',
        reason_code=reason_code,
        message=message,
    )
    row = existing_row
    if row is None:
        row = {
            'id': str((item or {}).get('id') or ''),
            'title': (item or {}).get('title') or '',
            'target_board': (item or {}).get('target_board') or '',
            'status': 'security_halted',
            'attempt': 1,
            'events': ['safety:security_halted'],
            'error': message,
            'verified': False,
            'source_board': (item or {}).get('source_board') or '',
            'source_board_id': (item or {}).get('source_board_id') or '',
        }
        report.setdefault('processed', []).append(row)
    else:
        row['status'] = 'security_halted'
        row['error'] = message
        events = row.setdefault('events', [])
        if 'safety:security_halted' not in events:
            events.append('safety:security_halted')
    if row not in report.setdefault('errors', []):
        report['errors'].append(row)
    report['safety_state'] = 'security_halted'
    report['security_halted'] = True
    report['safety_halt'] = {
        'reason_code': reason_code,
        'message': message,
        'next_action': 'manual_complete_platform_verification_then_start_new_session',
        'state_file': str(safety_state),
    }
    report['updated_at'] = utc_now()


def move_session_limit(args: argparse.Namespace) -> int:
    value = getattr(args, 'max_moves_per_session', None)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 200:
        raise RuntimeError('--max-moves-per-session 必须明确指定为 1 到 200 的整数；不会默认移动全部条目。')
    return value


def execute_batch(classification: List[Dict[str, Any]], report: Dict[str, Any], args: argparse.Namespace, report_path: Path) -> None:
    backend = choose_backend(args.browser)
    arc_selector = {
        '--arc-window-id': str(getattr(args, 'arc_window_id', '') or '').strip(),
        '--arc-tab-id': str(getattr(args, 'arc_tab_id', '') or '').strip(),
        '--arc-tab-marker': str(getattr(args, 'arc_tab_marker', '') or '').strip(),
        '--arc-expected-url-substring': str(getattr(args, 'arc_expected_url_substring', '') or '').strip(),
    }
    if backend == 'arc':
        missing = [name for name, value in arc_selector.items() if not value]
        if missing:
            raise RuntimeError(
                'Arc 真实执行必须提供稳定的 window id + tab id + window.name 标记 + 预期页面片段；'
                f'缺少：{", ".join(missing)}'
            )
    inter_item_delay_sec = float(getattr(args, 'inter_item_delay_sec', 5.0))
    if inter_item_delay_sec < 0:
        raise RuntimeError('--inter-item-delay-sec 不能小于 0')
    verify_pages = getattr(args, 'verify_pages', 10)
    if not isinstance(verify_pages, int) or isinstance(verify_pages, bool) or verify_pages < 1:
        raise RuntimeError('--verify-pages 必须是大于 0 的整数')
    session_limit = move_session_limit(args)
    safety_state = resolve_safety_state_path(getattr(args, 'safety_state', ''), report_path)
    ensure_active_session(
        safety_state,
        stage='move',
        policy={
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'max_moves_per_session': session_limit,
        },
    )
    report['safety_state_file'] = str(safety_state)
    report['move_session_limit'] = session_limit
    planned_items = classification[:session_limit]
    remaining_count = len(classification) - len(planned_items)
    if not planned_items:
        report['session_status'] = 'completed'
        report['updated_at'] = utc_now()
        write_json(report_path, report)
        return
    runner = BrowserRunner(backend, args)
    try:
        for index, item in enumerate(planned_items):
            if index > 0 and inter_item_delay_sec > 0:
                time.sleep(inter_item_delay_sec)
            try:
                run_id = parse_browser_job_id(runner.eval(build_browser_job([item], args)))
                result = poll_browser_job(runner, run_id, args.timeout_sec)
            except Exception as exc:
                if isinstance(exc, SafetyHaltedError) or classify_safety_error(exc):
                    record_security_halt(report, safety_state=safety_state, item=item, error=exc)
                    write_json(report_path, report)
                    raise SafetyHaltedError(
                        '已检测到安全验证、执行页绑定丢失或未知写入状态；已落盘并停止本次移动。'
                    ) from exc
                row = {
                    'id': str(item.get('id') or ''),
                    'title': item.get('title') or '',
                    'target_board': item.get('target_board') or '',
                    'status': 'failed',
                    'attempt': 1,
                    'events': ['error:stopped_before_next_item'],
                    'error': str(exc),
                    'verified': False,
                    'source_board': item.get('source_board') or '',
                    'source_board_id': item.get('source_board_id') or '',
                    'source_lists': item.get('source_lists', []),
                    'source_primary': item.get('source_primary', ''),
                    'exclude_reason': item.get('exclude_reason', ''),
                }
                report.setdefault('processed', []).append(row)
                report.setdefault('errors', []).append(row)
                report['session_status'] = 'stopped_on_error'
                report['updated_at'] = utc_now()
                write_json(report_path, report)
                raise
            merge_report_chunk(report, result)
            report['updated_at'] = utc_now()
            write_json(report_path, report)
            chunk_errors = result.get('errors', [])
            if chunk_errors:
                first = chunk_errors[0]
                if classify_safety_error(first.get('error') or first.get('status') or ''):
                    record_security_halt(
                        report,
                        safety_state=safety_state,
                        item=item,
                        error=first.get('error') or first.get('status') or '',
                        existing_row=first,
                    )
                    write_json(report_path, report)
                    raise SafetyHaltedError('浏览器返回安全异常；已落盘并停止本次移动。')
                raise RuntimeError(
                    '批次已在首个错误后停止，且已先写入报告：'
                    f'id={first.get("id") or "unknown"}, '
                    f'error={first.get("error") or first.get("status") or "unknown"}'
                )
        if remaining_count:
            report['session_status'] = 'move_limit_reached'
            report['remaining_count'] = remaining_count
            report['next_action'] = '本次移动上限已到；请人工检查结果后，再明确开启新的移动会话。'
        else:
            report['session_status'] = 'completed'
        report['updated_at'] = utc_now()
        write_json(report_path, report)
    finally:
        runner.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='批量移动小红书收藏到 classification.json 指定专辑。默认 dry-run，不改账号。')
    parser.add_argument('classification', help='classification.json 路径')
    parser.add_argument('report', nargs='?', default='run_report.json', help='run_report.json 输出路径')
    parser.add_argument('--execute', action='store_true', help='真实移动收藏；不传则只生成计划')
    parser.add_argument('--browser', choices=['auto', 'arc', 'chrome', 'safari', 'playwright'], default='auto', help='执行浏览器后端；真实执行必须显式指定，禁止 auto')
    parser.add_argument('--allow-low-confidence', action='store_true', help='允许移动 low confidence 条目；默认要求人工复核')
    parser.add_argument('--verify-pages', type=int, default=10, help='每个目标专辑最多翻页核验次数')
    parser.add_argument('--timeout-sec', type=int, default=300, help='浏览器执行最长等待秒数')
    parser.add_argument('--inter-item-delay-sec', type=float, default=5.0, help='真实执行时两条移动之间的固定等待秒数；默认 5，测试可设 0')
    parser.add_argument('--max-moves-per-session', type=int, default=None, help='真实执行必填：本次最多移动多少条，范围 1 到 200；达到上限后不会自动续跑')
    parser.add_argument('--safety-state', default='', help='共享安全状态文件；默认继承 classification.json 旁已有状态，否则使用 run_report.json 同目录的 xhs_safety_state.json')
    parser.add_argument('--arc-window-id', default='', help='Arc 真实执行必填：工作窗口的 Arc AppleScript 唯一 id（不是会变的 window index）')
    parser.add_argument('--arc-tab-id', default='', help='Arc 真实执行必填：工作标签页的 Arc AppleScript 唯一 id')
    parser.add_argument('--arc-tab-marker', default='', help='Arc 真实执行必填：预先单独写入工作标签页 window.name 的唯一标记；执行器只核验，不自动设置')
    parser.add_argument('--arc-expected-url-substring', default='', help='Arc 真实执行必填：预期收藏/专辑页 URL 的稳定片段；执行和轮询每次都重新核对')
    parser.add_argument('--user-id', default='', help='可选：当页面 state 没有专辑列表时，用当前账号 user id 查询专辑')
    parser.add_argument('--url', default=None, help='Playwright 模式下可选：打开指定小红书页面')
    parser.add_argument('--channel', default='chromium', help='Playwright channel：chrome、msedge、chromium；默认使用 Playwright 自带 chromium')
    parser.add_argument('--user-data-dir', default=None, help='Playwright 持久化浏览器资料目录')
    parser.add_argument('--cdp-url', default=None, help='连接已启动 Chrome/Edge 的 CDP 地址')
    parser.add_argument('--headless', action='store_true', help='Playwright 新开浏览器时使用 headless；登录场景通常不要开启')
    parser.add_argument('--resume', action='store_true', help='读取已有 run_report.json，跳过已经 success 且核验过的条目')
    args = parser.parse_args()

    classification = normalize_classification(load_json(args.classification))
    mode = 'execute' if args.execute else 'dry_run'
    report_path = Path(args.report)
    if not args.safety_state:
        args.safety_state = str(
            resolve_safety_state_path(
                None,
                report_path,
                predecessors=(Path(args.classification),),
            )
        )
    report = initial_report(classification, mode)
    if args.resume and report_path.exists():
        previous = load_json(str(report_path))
        classification, preserved = filter_classification_for_resume(classification, previous)
        report['resumed_from'] = str(report_path)
        report['processed'] = preserved
        report['visible_count'] = len(classification) + len(preserved)
        report['skipped_success_count'] = len(preserved)

    if args.execute:
        execute_batch(classification, report, args, report_path)
    else:
        for item in classification:
            append_dry_run(report, item, args.allow_low_confidence)
            report['updated_at'] = utc_now()
            write_json(report_path, report)

    report['finished_at'] = utc_now()
    write_json(report_path, report)
    print(json.dumps({
        'mode': report['mode'],
        'processed_count': len(report['processed']),
        'error_count': len(report['errors']),
        'missing_boards': report['missing_boards'],
        'report': str(report_path),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
