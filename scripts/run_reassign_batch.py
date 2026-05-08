#!/usr/bin/env python3
import argparse
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


LOGIN_MARKERS = ('手机号登录', '登录后推荐', '马上登录即可', '扫码登录', '验证码登录')


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def osascript(script: str) -> str:
    proc = subprocess.run(['osascript'], input=script, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or 'osascript failed')
    return proc.stdout.strip()


def chrome_js(js: str) -> str:
    script = (
        'tell application "Google Chrome"\n'
        'tell active tab of front window\n'
        f'execute javascript {json.dumps(js)}\n'
        'end tell\n'
        'end tell\n'
    )
    return osascript(script)


def safari_js(js: str) -> str:
    script = (
        'tell application "Safari"\n'
        f'do JavaScript {json.dumps(js)} in current tab of front window\n'
        'end tell\n'
    )
    return osascript(script)


def parse_js_json(raw: str) -> Any:
    raw = (raw or '').strip()
    if not raw:
        return None
    return json.loads(raw)


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
    return 'chrome' if platform.system() == 'Darwin' else 'playwright'


def normalize_classification(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for index, item in enumerate(items):
        note_id = str(item.get('id') or '').strip()
        target_board = str(item.get('target_board') or '').strip()
        normalized.append({
            'id': note_id,
            'title': item.get('title') or '',
            'target_board': target_board,
            'confidence': item.get('confidence') or '',
            'review_state': item.get('review_state') or '',
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
    }


def append_dry_run(report: Dict[str, Any], item: Dict[str, Any], allow_low_confidence: bool) -> None:
    status = 'planned'
    events = ['dry_run:no_account_changes']
    error = ''
    if not item['id']:
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
    })
    if status == 'failed':
        report['errors'].append(report['processed'][-1])


def build_browser_job(items: List[Dict[str, Any]], args: argparse.Namespace) -> str:
    payload = {
        'items': items,
        'allowLowConfidence': args.allow_low_confidence,
        'verifyPages': args.verify_pages,
        'userId': args.user_id or '',
    }
    return r"""
(function() {
  const runId = 'xhs_skill_' + Date.now() + '_' + Math.floor(Math.random() * 1000000);
  const payload = PAYLOAD_JSON;
  window.__xhsSkillRuns = window.__xhsSkillRuns || {};
  window.__xhsSkillRuns[runId] = { done: false };

  function textOf(value) {
    if (value === undefined || value === null) return '';
    return String(value).trim();
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

  function exposeWebpackRequire() {
    if (window.__xhsSkillReq) return window.__xhsSkillReq;
    const chunk = window.webpackChunkxhs_pc_web;
    if (!chunk || typeof chunk.push !== 'function') {
      throw new Error('webpack runtime not found on current Xiaohongshu page');
    }
    chunk.push([[Math.floor(Math.random() * 1000000000)], {}, function(req) {
      window.__xhsSkillReq = req;
    }]);
    if (!window.__xhsSkillReq) throw new Error('failed to expose webpack runtime');
    return window.__xhsSkillReq;
  }

  function findApi(req) {
    const cache = req.c || {};
    for (const key of Object.keys(cache)) {
      const exported = cache[key] && cache[key].exports;
      const candidates = [exported, exported && exported.default];
      for (const api of candidates) {
        if (api && typeof api.d0 === 'function' && typeof api.Ks === 'function') {
          return api;
        }
      }
    }
    throw new Error('Xiaohongshu board API module not found');
  }

  async function boardsFromApi(api) {
    if (!payload.userId || typeof api.yC !== 'function') return [];
    const response = await api.yC({ params: { userId: payload.userId, num: 100, page: 1 } });
    const out = [];
    flattenBoards(response, out);
    return uniqueBoards(out);
  }

  function collectNotes(value, out) {
    if (!value) return;
    if (Array.isArray(value)) {
      for (const entry of value) collectNotes(entry, out);
      return;
    }
    if (typeof value !== 'object') return;
    const noteId = textOf(value.noteId || value.id || value.note_id);
    if (noteId) out.push(noteId);
    for (const key of ['notes', 'items', 'list']) {
      if (Array.isArray(value[key])) collectNotes(value[key], out);
    }
    if (value.data && typeof value.data === 'object') collectNotes(value.data, out);
  }

  function extractCursor(value) {
    if (!value || typeof value !== 'object') return '';
    const roots = [value, value.data, value.data && value.data.data];
    for (const root of roots) {
      if (!root || typeof root !== 'object') continue;
      const cursor = textOf(root.cursor || root.nextCursor || root.next_cursor);
      if (cursor) return cursor;
    }
    return '';
  }

  function extractTotal(value) {
    if (!value || typeof value !== 'object') return null;
    const roots = [value, value.data, value.data && value.data.data];
    for (const root of roots) {
      if (!root || typeof root !== 'object') continue;
      const total = root.total ?? root.noteCount ?? root.note_count;
      if (Number.isFinite(Number(total))) return Number(total);
    }
    return null;
  }

  async function boardSnapshot(api, boardId, pages) {
    const noteIds = [];
    let cursor = '';
    let total = null;
    for (let page = 0; page < pages; page += 1) {
      const response = await api.Ks({ params: { boardId, num: 30, cursor } });
      collectNotes(response, noteIds);
      const responseTotal = extractTotal(response);
      if (responseTotal !== null) total = responseTotal;
      const next = extractCursor(response);
      if (!next || next === cursor) break;
      cursor = next;
    }
    return { noteIds: Array.from(new Set(noteIds)), total };
  }

  async function run() {
    const location = String(window.location.href || '');
    const bodyText = (document.body && document.body.innerText) || '';
    if (!location.includes('xiaohongshu.com')) {
      throw new Error('current page is not xiaohongshu.com: ' + location);
    }
    if (/手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test(bodyText)) {
      throw new Error('current Xiaohongshu page looks logged out');
    }
    const req = exposeWebpackRequire();
    const api = findApi(req);
    let boards = boardsFromInitialState();
    if (!boards.length) boards = await boardsFromApi(api);
    if (!boards.length) throw new Error('no boards found; open your Xiaohongshu profile/favorites page first');
    const boardByName = {};
    for (const board of boards) boardByName[board.name] = board;
    const boardCountsBefore = {};
    const boardCountsAfter = {};
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
        verified: false
      };
      try {
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
        await api.d0({ targetBoardId: board.id, notesId: item.id });
        events.push('note_move:CALLED');
        const snapshot = await boardSnapshot(api, board.id, payload.verifyPages);
        if (snapshot.total !== null) boardCountsAfter[board.name] = snapshot.total;
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
        row.status = 'failed';
        row.error = error && error.message ? error.message : String(error);
        events.push('error:' + row.error);
        processed.push(row);
        errors.push(row);
      }
    }
    return { processed, errors, missing_boards: missingBoards, board_counts_before: boardCountsBefore, board_counts_after: boardCountsAfter };
  }

  run().then((result) => {
    window.__xhsSkillRuns[runId] = { done: true, ok: true, result };
  }).catch((error) => {
    window.__xhsSkillRuns[runId] = { done: true, ok: false, error: error && error.message ? error.message : String(error) };
  });
  return runId;
})()
""".replace('PAYLOAD_JSON', json.dumps(payload, ensure_ascii=False))


def poll_browser_job(runner: BrowserRunner, run_id: str, timeout_sec: int) -> Dict[str, Any]:
    deadline = time.time() + timeout_sec
    poll_js = 'JSON.stringify((window.__xhsSkillRuns && window.__xhsSkillRuns[%s]) || null)' % json.dumps(run_id)
    while time.time() < deadline:
        state = parse_js_json(runner.eval(poll_js))
        if state and state.get('done'):
            if state.get('ok'):
                return state.get('result') or {}
            raise RuntimeError(state.get('error') or 'browser job failed')
        time.sleep(1)
    raise TimeoutError('browser job timed out')


def execute_batch(classification: List[Dict[str, Any]], report: Dict[str, Any], args: argparse.Namespace) -> None:
    backend = choose_backend(args.browser)
    runner = BrowserRunner(backend, args)
    try:
        run_id = runner.eval(build_browser_job(classification, args))
        result = poll_browser_job(runner, str(run_id), args.timeout_sec)
        report['processed'] = result.get('processed', [])
        report['errors'] = result.get('errors', [])
        report['missing_boards'] = result.get('missing_boards', [])
        report['board_counts_before'] = result.get('board_counts_before', {})
        report['board_counts_after'] = result.get('board_counts_after', {})
    finally:
        runner.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='批量移动小红书收藏到 classification.json 指定专辑。默认 dry-run，不改账号。')
    parser.add_argument('classification', help='classification.json 路径')
    parser.add_argument('report', nargs='?', default='run_report.json', help='run_report.json 输出路径')
    parser.add_argument('--execute', action='store_true', help='真实移动收藏；不传则只生成计划')
    parser.add_argument('--browser', choices=['auto', 'chrome', 'safari', 'playwright'], default='auto', help='执行浏览器后端')
    parser.add_argument('--allow-low-confidence', action='store_true', help='允许移动 low confidence 条目；默认要求人工复核')
    parser.add_argument('--verify-pages', type=int, default=10, help='每个目标专辑最多翻页核验次数')
    parser.add_argument('--timeout-sec', type=int, default=300, help='浏览器执行最长等待秒数')
    parser.add_argument('--user-id', default='', help='可选：当页面 state 没有专辑列表时，用当前账号 user id 查询专辑')
    parser.add_argument('--url', default=None, help='Playwright 模式下可选：打开指定小红书页面')
    parser.add_argument('--channel', default='chrome', help='Playwright channel：chrome、msedge、chromium')
    parser.add_argument('--user-data-dir', default=None, help='Playwright 持久化浏览器资料目录')
    parser.add_argument('--cdp-url', default=None, help='连接已启动 Chrome/Edge 的 CDP 地址')
    parser.add_argument('--headless', action='store_true', help='Playwright 新开浏览器时使用 headless；登录场景通常不要开启')
    args = parser.parse_args()

    classification = normalize_classification(load_json(args.classification))
    mode = 'execute' if args.execute else 'dry_run'
    report = initial_report(classification, mode)
    report_path = Path(args.report)

    if args.execute:
        execute_batch(classification, report, args)
    else:
        for item in classification:
            append_dry_run(report, item, args.allow_low_confidence)

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
