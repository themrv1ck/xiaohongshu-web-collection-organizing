#!/usr/bin/env python3
import argparse
import hashlib
import json
import platform
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from browser_page_runtime import run_page_javascript
from collection_scope import validate_scope_input, validate_scope_snapshot
from extract_visible_items import arc_js_macos, osascript, require_macos_app_running
from xhs_safety import (
    SafetyHaltedError,
    atomic_write_json,
    classify_safety_error,
    default_safety_state_path,
    ensure_active_session,
    is_security_halted,
    load_safety_state,
    mark_security_halted,
    redact_persisted_errors,
    resolve_safety_state_path,
)
from workbuddy_runtime import apply_workbuddy_browser_policy, is_workbuddy_host
from archive_rules import UNCERTAIN_BOARD_NAME
from archive_exclusion import protected_note_map_from_snapshot
from xhs_visible_ui import (
    ArcVisibleUiSession,
    VisibleUiContractError,
    build_assign_collected_note_js,
    build_collect_into_board_js,
    build_find_and_open_note_card_js,
    build_note_collect_probe_js,
    read_visible_album_list,
    read_visible_board,
    capture_visible_album_snapshot,
    source_tab_for_item,
    validate_new_collection_transition,
)


LOGIN_MARKERS = ('手机号登录', '登录后推荐', '马上登录即可', '扫码登录', '验证码登录')
NOTE_ID_RE = re.compile(r'^[0-9a-f]{24}$', re.IGNORECASE)


class ExecutionPreflightError(RuntimeError):
    pass


def load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: Any) -> None:
    atomic_write_json(path, redact_persisted_errors(data))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


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
        self.backend = apply_workbuddy_browser_policy(backend, args)
        self.args = args
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.close_context = True
        if self.backend == 'playwright':
            self._open_playwright()

    def _open_playwright(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError('Playwright Python 未安装。先运行：python -m pip install playwright && python -m playwright install chromium') from exc
        self.playwright = sync_playwright().start()
        try:
            if self.args.cdp_url:
                self.browser = self.playwright.chromium.connect_over_cdp(self.args.cdp_url)
                self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
                self.close_context = False
            else:
                profile_dir = Path(self.args.user_data_dir or Path.home() / '.xhs-skill-browser-profile')
                profile_dir.mkdir(parents=True, exist_ok=True)
                launch_args: Dict[str, Any] = {'headless': self.args.headless}
                if self.args.channel and self.args.channel != 'chromium':
                    launch_args['channel'] = self.args.channel
                self.context = self.playwright.chromium.launch_persistent_context(str(profile_dir), **launch_args)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            if self.args.url:
                self.page.goto(self.args.url, wait_until='domcontentloaded', timeout=60000)
            self.page.wait_for_load_state('domcontentloaded', timeout=60000)
        except Exception:
            self.close()
            raise

    def run_javascript(self, js: str) -> str:
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
        return run_page_javascript(self.page, js)

    def navigate_xhs(self, path: str, query: Optional[Dict[str, str]] = None) -> None:
        clean_path = str(path or '').strip()
        if not clean_path.startswith('/') or '..' in clean_path:
            raise VisibleUiContractError('小红书目标路径无效')
        from urllib.parse import urlencode
        target = 'https://www.xiaohongshu.com' + clean_path
        query_string = urlencode(dict(query or {}))
        if query_string:
            target += '?' + query_string
        parsed = urlparse(target)
        if parsed.scheme != 'https' or parsed.hostname != 'www.xiaohongshu.com':
            raise VisibleUiContractError('只允许导航到小红书正式站')
        if {'xsec_token', 'xsec_source', 'sign', 'signature'}.intersection(parse_qs(parsed.query)):
            raise VisibleUiContractError('导航目标不得包含会话或签名参数')

        if self.backend == 'arc':
            ArcVisibleUiSession(
                str(getattr(self.args, 'arc_window_id', '') or ''),
                str(getattr(self.args, 'arc_tab_id', '') or ''),
                str(getattr(self.args, 'arc_tab_marker', '') or ''),
                str(getattr(self.args, 'user_id', '') or ''),
            ).navigate(clean_path, query)
            return
        if self.backend == 'playwright':
            self.page.goto(target, wait_until='domcontentloaded', timeout=60000)
            return

        self.run_javascript(
            "window.location.assign(" + json.dumps(target, ensure_ascii=False) + "); true;"
        )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                state = parse_js_json(self.run_javascript(
                    "JSON.stringify({url:String(window.location.href||''),ready:document.readyState,"
                    "title:String(document.title||''),body:String((document.body&&document.body.innerText)||'').slice(0,500)});"
                ))
            except Exception:
                time.sleep(0.2)
                continue
            text_value = ' '.join(str(state.get(key) or '') for key in ('url', 'title', 'body'))
            classified = classify_safety_error(text_value)
            if classified:
                raise SafetyHaltedError(classified[1])
            if state.get('url') == target and state.get('ready') in {'interactive', 'complete'}:
                return
            time.sleep(0.2)
        raise VisibleUiContractError('正式页面导航未在截止时间内完成')

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


class BrowserVisibleUiSession:
    """Duck-typed visible UI session for the already authorized BrowserRunner."""

    def __init__(self, runner: BrowserRunner, user_id: str, tab_marker: str = ''):
        self.runner = runner
        self.browser_name = str(getattr(runner, 'backend', 'test'))
        self.user_id = str(user_id or '').strip().lower()
        self.tab_marker = str(tab_marker or '').strip()
        if not NOTE_ID_RE.fullmatch(self.user_id):
            raise VisibleUiContractError('user id 不是 24 位十六进制 id')

    def run_json(self, script: str) -> Any:
        return parse_js_json(self.runner.run_javascript(script))

    def navigate(self, path: str, query: Optional[Dict[str, str]] = None) -> None:
        self.runner.navigate_xhs(path, query)

    def wait_for(self, script: str, *, timeout_sec: float = 20.0) -> Any:
        deadline = time.monotonic() + timeout_sec
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                value = self.run_json(script)
                if value:
                    return value
            except Exception as exc:
                last_error = exc
                if classify_safety_error(exc):
                    raise
            time.sleep(0.2)
        if last_error:
            raise VisibleUiContractError(f'正式页面未在截止时间内就绪：{last_error}') from last_error
        raise VisibleUiContractError('正式页面未在截止时间内就绪')


def choose_backend(value: str, args: argparse.Namespace = None) -> str:
    if args is not None:
        value = apply_workbuddy_browser_policy(value, args)
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


def confidence_allows_move(item: Dict[str, Any], allow_low_confidence: bool) -> bool:
    """The fixed review album is the only low-confidence target allowed directly."""
    return (
        str(item.get('target_board') or '').strip() == UNCERTAIN_BOARD_NAME
        or item.get('confidence') != 'low'
        or allow_low_confidence
    )


def initial_report(classification: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
    return {
        'started_at': utc_now(),
        'mode': mode,
        'ready_for_execute': False,
        'blockers': [],
        'warnings': [],
        'collection_write_notice': '已收藏的待归档笔记需取消一次再重新收藏；会改变收藏排序，中途失败可能留下未收藏或未归入状态。须明确同意 --allow-recollect，受 Skill 归档保护的笔记不操作。',
        'board_validation_status': 'not_checked',
        'membership_validation_status': 'not_checked',
        'visible_count': len(classification),
        'processed': [],
        'errors': [],
        'missing_boards': None,
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
    for key in ('board_list_count', 'board_list_page_count'):
        if key not in chunk:
            continue
        previous = report.get(key)
        current = chunk[key]
        if previous is not None and previous != current:
            raise RuntimeError(
                f'{key} changed between move batches: expected {previous}, got {current}'
            )
        report[key] = current
    report.setdefault('processed', []).extend(chunk.get('processed', []))
    report.setdefault('errors', []).extend(chunk.get('errors', []))
    missing = report.get('missing_boards')
    if missing is None:
        missing = []
        report['missing_boards'] = missing
    for board in chunk.get('missing_boards', []):
        if board and board not in missing:
            missing.append(board)
    report.setdefault('board_counts_before', {}).update(chunk.get('board_counts_before', {}))
    report.setdefault('board_counts_after', {}).update(chunk.get('board_counts_after', {}))
    report.setdefault('board_count_checks', {}).update(chunk.get('board_count_checks', {}))


def append_classification_preview(
    report: Dict[str, Any],
    item: Dict[str, Any],
    allow_low_confidence: bool,
) -> None:
    status = 'preview_only'
    events = ['preview:no_account_changes', 'preflight:not_run']
    error = ''
    if item.get('excluded') or item.get('exclude_reason'):
        status = 'skipped'
        events = ['skip:existing_board_excluded', 'preview:no_account_changes', 'preflight:not_run']
        error = item.get('exclude_reason') or 'skill_archived_board_member_protected'
    elif not item['id']:
        status = 'failed'
        error = 'missing note id'
    elif not item['target_board']:
        status = 'needs_review'
        error = 'missing target_board'
    elif not confidence_allows_move(item, allow_low_confidence):
        status = 'needs_review'
        error = 'low confidence classification; review before membership preflight'
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
        'membership_state': 'not_checked',
        'archive_lifecycle_state': 'not_checked',
        'source_lists': item.get('source_lists', []),
        'source_primary': item.get('source_primary', ''),
        'exclude_reason': item.get('exclude_reason', ''),
    })
    if status == 'failed':
        report['errors'].append(report['processed'][-1])


def _normalize_string_list(value: Any, field: str) -> List[str]:
    if not isinstance(value, list):
        raise ExecutionPreflightError(f'{field} must be an array')
    result = []
    for entry in value:
        text = str(entry or '').strip()
        if text and text not in result:
            result.append(text)
    return result


def prepare_write_preflight(
    classification: List[Dict[str, Any]],
    board_snapshot: Any,
    created_boards: Any,
    *,
    allow_low_confidence: bool,
    allow_planned_board_creation: bool = False,
    archive_registry: Any = None,
) -> Dict[str, Any]:
    blockers: List[str] = []
    warnings: List[str] = []
    if not isinstance(board_snapshot, dict):
        raise ExecutionPreflightError('board_snapshot must be an object')
    source = board_snapshot.get('source')
    validation = board_snapshot.get('validation')
    boards = board_snapshot.get('boards')
    if (
        board_snapshot.get('mode') != 'read_only'
        or not isinstance(source, dict)
        or source.get('writes_performed') is not False
    ):
        blockers.append('board_snapshot_not_read_only')
    if not isinstance(validation, dict):
        blockers.append('board_snapshot_validation_missing')
        validation = {}
    if validation.get('pagination_cursor_invariants_passed') is not True:
        blockers.append('board_pagination_incomplete')
    if validation.get('board_names_unique') is False:
        blockers.append('board_names_not_unique')
    if validation.get('within_board_duplicates'):
        blockers.append('within_board_duplicates')
    if validation.get('full_membership_complete') is not True:
        blockers.append('full_membership_incomplete')
    if not isinstance(boards, list):
        raise ExecutionPreflightError('board_snapshot.boards must be an array')

    board_by_name: Dict[str, Dict[str, str]] = {}
    board_by_id: Dict[str, Dict[str, str]] = {}
    membership: Dict[str, List[Dict[str, str]]] = {}
    board_counts: Dict[str, int] = {}
    board_count_checks: Dict[str, Dict[str, Any]] = {}
    for index, board in enumerate(boards):
        if not isinstance(board, dict):
            raise ExecutionPreflightError(f'board_snapshot.boards[{index}] must be an object')
        board_id = str(board.get('id') or '').strip()
        board_name = str(board.get('name') or '').strip()
        note_ids = board.get('note_ids')
        if not NOTE_ID_RE.fullmatch(board_id) or not board_name or not isinstance(note_ids, list):
            raise ExecutionPreflightError(f'board_snapshot.boards[{index}] has invalid id/name/note_ids')
        if board_id in board_by_id or board_name in board_by_name:
            raise ExecutionPreflightError('board snapshot board ids and names must be unique')
        normalized_board = {'id': board_id, 'name': board_name}
        board_by_id[board_id] = normalized_board
        board_by_name[board_name] = normalized_board
        normalized_note_ids = []
        for note_id_value in note_ids:
            note_id = str(note_id_value or '').strip()
            if not NOTE_ID_RE.fullmatch(note_id):
                raise ExecutionPreflightError(f'board {board_name} contains invalid note id')
            normalized_note_ids.append(note_id)
            membership.setdefault(note_id, []).append({
                'board_id': board_id,
                'board_name': board_name,
            })
        if len(normalized_note_ids) != len(set(normalized_note_ids)):
            blockers.append(f'within_board_duplicates:{board_name}')
        declared_total = board.get('declared_total')
        accessible_total = len(set(normalized_note_ids))
        if declared_total != accessible_total:
            if 'full_membership_incomplete' not in blockers:
                blockers.append('full_membership_incomplete')
            blockers.append(f'board_count_mismatch:{board_name}')
        board_counts[board_name] = accessible_total
        board_count_checks[board_name] = {
            'declared_total': declared_total,
            'accessible_total': accessible_total,
            'count_mismatch': declared_total != accessible_total,
            'page_count': board.get('page_count'),
        }

    snapshot_user_id = str(source.get('user_id') or '').strip().lower() if isinstance(source, dict) else ''
    protected_note_to_board = protected_note_map_from_snapshot(
        board_snapshot,
        archive_registry,
        expected_user_id=snapshot_user_id,
    ) if archive_registry is not None else {}

    if not isinstance(created_boards, dict):
        raise ExecutionPreflightError('created_boards must be an object')
    confirmed_boards = set(_normalize_string_list(created_boards.get('confirmed'), 'created_boards.confirmed'))
    declared_missing = set(_normalize_string_list(created_boards.get('missing'), 'created_boards.missing'))
    planned_rows = created_boards.get('planned', [])
    if not isinstance(planned_rows, list):
        raise ExecutionPreflightError('created_boards.planned must be an array')
    planned_boards: List[Dict[str, Any]] = []
    planned_board_names = set()
    for index, entry in enumerate(planned_rows):
        if not isinstance(entry, dict):
            raise ExecutionPreflightError(
                f'created_boards.planned[{index}] must be an object'
            )
        name = str(entry.get('name') or '').strip()
        privacy = entry.get('privacy')
        if not name or privacy not in {0, 1} or isinstance(privacy, bool):
            raise ExecutionPreflightError(
                f'created_boards.planned[{index}] has invalid name/privacy'
            )
        if name in planned_board_names:
            raise ExecutionPreflightError('created_boards.planned names must be unique')
        if name in confirmed_boards or name in board_by_name or name in declared_missing:
            raise ExecutionPreflightError(
                f'planned board conflicts with inventory state: {name}'
            )
        planned_board_names.add(name)
        planned_boards.append({'name': name, 'privacy': privacy})
    if planned_boards and not allow_planned_board_creation:
        raise ExecutionPreflightError(
            'planned board creation requires explicit allow_planned_board_creation'
        )
    if not boards and not planned_boards:
        raise ExecutionPreflightError('board_snapshot.boards must be a non-empty array')

    resolved_items: List[Dict[str, Any]] = []
    required_targets = set()
    missing_targets = set()
    membership_counts = {
        'skill_archived_board_member_protected': 0,
        'not_in_any_board': 0,
        'needs_review': 0,
        'excluded': 0,
    }
    for index, item in enumerate(classification):
        resolved = dict(item)
        resolved['membership_state'] = 'not_required'
        resolved['archive_lifecycle_state'] = 'not_required'
        note_id = str(item.get('id') or '').strip()
        if NOTE_ID_RE.fullmatch(note_id):
            unique_refs = {
                (ref['board_id'], ref['board_name'])
                for ref in membership.get(note_id, [])
            }
            protected_board = protected_note_to_board.get(note_id)
            if protected_board:
                sorted_refs = sorted(unique_refs)
                if not sorted_refs or protected_board not in {ref[1] for ref in sorted_refs}:
                    raise ExecutionPreflightError(
                        f'归档登记与本轮专辑快照不一致：{note_id}'
                    )
                resolved['membership_state'] = 'skill_archived_board_member_protected'
                resolved['archive_lifecycle_state'] = 'first_archive_confirmed'
                resolved['excluded'] = True
                resolved['exclude_reason'] = 'skill_archived_board_member_protected'
                resolved['source_board'] = ' | '.join(ref[1] for ref in sorted_refs)
                resolved['source_board_id'] = (
                    sorted_refs[0][0] if len(sorted_refs) == 1 else ''
                )
                membership_counts['skill_archived_board_member_protected'] += 1
                resolved_items.append(resolved)
                continue
            if unique_refs:
                sorted_refs = sorted(unique_refs)
                if len(sorted_refs) != 1:
                    resolved['membership_state'] = 'unarchived_multiple_boards'
                    resolved['archive_lifecycle_state'] = 'first_archive_pending'
                    resolved['source_board'] = ' | '.join(ref[1] for ref in sorted_refs)
                    resolved['source_board_id'] = ''
                    blockers.append(f'unarchived_note_in_multiple_boards:{note_id}')
                    resolved_items.append(resolved)
                    continue
                resolved['membership_state'] = 'unarchived_board_member'
                resolved['archive_lifecycle_state'] = 'first_archive_pending'
                resolved['source_board'] = sorted_refs[0][1]
                resolved['source_board_id'] = sorted_refs[0][0]
                membership_counts['not_in_any_board'] += 1
            else:
                resolved['membership_state'] = 'not_in_any_board'
                resolved['archive_lifecycle_state'] = 'first_archive_pending'
                resolved['source_board'] = ''
                resolved['source_board_id'] = ''
                membership_counts['not_in_any_board'] += 1

        actionable = all([
            not item.get('excluded'),
            not item.get('exclude_reason'),
            bool(item.get('id')),
            bool(item.get('target_board')),
            confidence_allows_move(item, allow_low_confidence),
        ])
        if not actionable:
            membership_counts['excluded' if item.get('excluded') or item.get('exclude_reason') else 'needs_review'] += 1
            resolved_items.append(resolved)
            continue

        target_board = str(item.get('target_board') or '').strip()
        if not NOTE_ID_RE.fullmatch(note_id):
            blockers.append(f'invalid_note_id:{index}')
            resolved_items.append(resolved)
            continue
        target = board_by_name.get(target_board)
        target_is_planned = (
            allow_planned_board_creation and target_board in planned_board_names
        )
        if target_is_planned:
            resolved['target_board_state'] = 'planned'
            required_targets.add(target_board)
            resolved_items.append(resolved)
            continue
        if not target or target_board not in confirmed_boards or target_board in declared_missing:
            missing_targets.add(target_board)
            resolved_items.append(resolved)
            continue
        required_targets.add(target_board)
        resolved_items.append(resolved)

    for target in sorted(required_targets):
        if (
            target not in planned_board_names
            and (
                target not in board_by_name
                or target not in confirmed_boards
                or target in declared_missing
            )
        ):
            missing_targets.add(target)
    blockers.extend(f'missing_target_board:{name}' for name in sorted(missing_targets))
    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    ready = not blockers
    return {
        'ready_for_execute': ready,
        'blockers': blockers,
        'warnings': warnings,
        'board_validation_status': (
            'verified_with_warnings'
            if ready and warnings
            else 'verified' if ready else 'blocked'
        ),
        'membership_validation_status': 'verified' if ready else 'blocked',
        'missing_boards': sorted(missing_targets),
        'planned_board_creations': planned_boards,
        'required_target_boards': sorted(required_targets),
        'membership_counts': membership_counts,
        'resolved_items': resolved_items,
        'board_counts_before': {
            name: board_counts[name]
            for name in sorted(required_targets)
            if name in board_counts
        },
        'board_count_checks': {
            name: board_count_checks[name]
            for name in sorted(required_targets)
            if name in board_count_checks
        },
        'snapshot_source': source if isinstance(source, dict) else {},
    }


def write_binding_blockers(snapshot_source: Any, args: argparse.Namespace) -> List[str]:
    if not isinstance(snapshot_source, dict):
        return ['snapshot_source_missing']
    blockers = []
    try:
        backend = choose_backend(args.browser, args)
    except RuntimeError:
        return ['browser_not_explicit']
    snapshot_browser = str(snapshot_source.get('browser') or '').strip().lower()
    if snapshot_browser != backend:
        blockers.append('snapshot_browser_changed')
    snapshot_user_id = str(snapshot_source.get('user_id') or '').strip()
    current_user_id = str(getattr(args, 'user_id', '') or '').strip()
    if not NOTE_ID_RE.fullmatch(snapshot_user_id) or current_user_id != snapshot_user_id:
        blockers.append('snapshot_user_changed')
    snapshot_url = str(snapshot_source.get('expected_url_substring') or '').strip()
    current_url = str(
        getattr(args, 'expected_url_substring', '')
        or getattr(args, 'arc_expected_url_substring', '')
        or ''
    ).strip()
    if not snapshot_url or current_url != snapshot_url:
        blockers.append('snapshot_page_binding_changed')
    live_page_binding = str(snapshot_source.get('live_page_binding') or '').strip()
    live_account_user_id = str(snapshot_source.get('live_account_user_id') or '').strip()
    if backend == 'playwright' or live_page_binding or live_account_user_id:
        if live_page_binding != current_url:
            blockers.append('snapshot_live_page_binding_changed')
        if live_account_user_id != current_user_id:
            blockers.append('snapshot_live_account_changed')
    snapshot_verify_pages = snapshot_source.get('verify_pages')
    current_verify_pages = getattr(args, 'verify_pages', None)
    if (
        not isinstance(snapshot_verify_pages, int)
        or isinstance(snapshot_verify_pages, bool)
        or snapshot_verify_pages < 1
        or current_verify_pages != snapshot_verify_pages
    ):
        blockers.append('snapshot_verify_pages_changed')
    snapshot_safety_state = str(snapshot_source.get('safety_state') or '').strip()
    current_safety_state = str(getattr(args, 'safety_state', '') or '').strip()
    if (
        not snapshot_safety_state
        or not current_safety_state
        or Path(snapshot_safety_state).resolve() != Path(current_safety_state).resolve()
    ):
        blockers.append('snapshot_safety_session_changed')
    return blockers


def expected_profile_binding(
    args: argparse.Namespace,
    *,
    required: bool = True,
) -> Optional[Dict[str, str]]:
    raw = str(
        getattr(args, 'expected_url_substring', '')
        or getattr(args, 'arc_expected_url_substring', '')
        or ''
    ).strip()
    parsed = urlparse(raw)
    match = re.fullmatch(r'/user/profile/([0-9a-fA-F]{24})/?', parsed.path)
    tab = (parse_qs(parsed.query).get('tab') or [''])[0].strip().lower()
    user_id = str(getattr(args, 'user_id', '') or '').strip().lower()
    host = (parsed.hostname or '').lower()
    if (
        parsed.scheme != 'https'
        or not (host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com'))
        or not match
        or match.group(1).lower() != user_id
        or tab not in {'fav', 'liked', 'like'}
    ):
        if required:
            raise RuntimeError('执行页必须绑定当前账号的精确 profile 列表与 tab。')
        return None
    return {
        'origin': f'{parsed.scheme}://{parsed.netloc}',
        'path': parsed.path.rstrip('/'),
        'tab': tab,
        'user_id': user_id,
    }


def build_write_binding_probe(args: argparse.Namespace) -> str:
    payload = expected_profile_binding(args, required=True)
    return r"""
JSON.stringify((() => {
  const payload = PAYLOAD_JSON;
  const bodyText = (document.body && document.body.innerText) || '';
  const securityMarkers = [
    '安全验证', '异常访问', '访问异常', '当前请求异常', '300031', 'website-login/error',
    '访问过于频繁', '操作过于频繁',
    '请求过于频繁', '网络环境存在风险', '当前环境存在风险', '请完成验证',
    '拖动滑块', 'captcha', 'security verification', 'abnormal access',
    'too many requests'
  ];
  const securityText = `${window.location.origin}${window.location.pathname}\n${bodyText}`.toLowerCase();
  const marker = securityMarkers.find(value => securityText.includes(value.toLowerCase())) || '';
  if (marker) return {ok: false, code: 'security_challenge', marker};
  if (/手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test(bodyText)) {
    return {ok: false, code: 'login_required'};
  }
  const current = new URL(window.location.href);
  const currentTab = String(current.searchParams.get('tab') || '').trim().toLowerCase();
  if (
    current.origin !== payload.origin
    || current.pathname.replace(/\/$/, '') !== payload.path
    || currentTab !== payload.tab
  ) return {ok: false, code: 'page_binding_mismatch'};
  const own = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'))
    .find(link => (link.textContent || '').trim() === '我');
  if (!own) return {ok: false, code: 'account_binding_unavailable'};
  const ownUrl = new URL(own.getAttribute('href') || '', window.location.origin);
  const ownMatch = ownUrl.pathname.match(/^\/user\/profile\/([0-9a-fA-F]{24})\/?$/);
  if (!ownMatch || ownMatch[1].toLowerCase() !== payload.user_id) {
    return {ok: false, code: 'account_binding_mismatch'};
  }
  return {
    ok: true,
    page_binding: `${current.origin}${current.pathname}?tab=${currentTab}`,
    user_id: ownMatch[1].toLowerCase()
  };
})())
""".replace('PAYLOAD_JSON', json.dumps(payload, ensure_ascii=False))


def validate_write_live_binding(runner: 'BrowserRunner', args: argparse.Namespace) -> Dict[str, Any]:
    raw = runner.run_javascript(build_write_binding_probe(args))
    value = raw
    for _ in range(2):
        if not isinstance(value, str):
            break
        value = json.loads(value)
    if not isinstance(value, dict) or value.get('ok') is not True:
        code = str(value.get('code') or 'execute_page_binding_invalid') if isinstance(value, dict) else 'execute_page_binding_invalid'
        if code == 'security_challenge':
            raise SafetyHaltedError('执行前检测到小红书安全验证，已停止。')
        raise RuntimeError(code)
    return value


def append_dry_run(report: Dict[str, Any], item: Dict[str, Any], allow_low_confidence: bool) -> None:
    status = 'planned'
    events = ['dry_run:no_account_changes']
    error = ''
    if item.get('excluded') or item.get('exclude_reason'):
        status = 'skipped'
        events = ['skip:existing_board_excluded', 'dry_run:no_account_changes']
        error = item.get('exclude_reason') or 'skill_archived_board_member_protected'
    elif not item['id']:
        status = 'failed'
        error = 'missing note id'
    elif not item['target_board']:
        status = 'needs_review'
        error = 'missing target_board'
    elif not confidence_allows_move(item, allow_low_confidence):
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
        'membership_state': item.get('membership_state', ''),
        'archive_lifecycle_state': item.get('archive_lifecycle_state', ''),
        'source_lists': item.get('source_lists', []),
        'source_primary': item.get('source_primary', ''),
        'exclude_reason': item.get('exclude_reason', ''),
    })
    if status == 'failed':
        report['errors'].append(report['processed'][-1])


def build_browser_job(items: List[Dict[str, Any]], args: argparse.Namespace) -> str:
    """Build one visible-detail-page assignment; never search titles or touch runtime modules."""
    if len(items) != 1:
        raise RuntimeError('可见页面归档每次必须且只能处理一条笔记')
    item = items[0]
    note_id = str(item.get('id') or '').strip().lower()
    target_board = str(item.get('target_board') or '').strip()
    if not NOTE_ID_RE.fullmatch(note_id):
        raise RuntimeError('移动笔记缺少有效的 24 位 note id')
    if not target_board:
        raise RuntimeError('移动笔记缺少目标专辑')
    if (
        item.get('excluded') or item.get('exclude_reason')
        or
        item.get('membership_state') not in {'not_in_any_board', 'unarchived_board_member'}
        or item.get('archive_lifecycle_state') != 'first_archive_pending'
    ):
        raise RuntimeError('只允许首次归档尚未被本 Skill 登记保护的笔记')
    source_tab = source_tab_for_item(item)
    user_id = str(getattr(args, 'user_id', '') or '').strip()
    marker = str(getattr(args, 'arc_tab_marker', '') or '').strip()
    timeout_ms = min(10000, int(float(getattr(args, 'timeout_sec', 30)) * 1000) - 1000)
    collected = item.get('_visible_collected')
    if collected is False:
        if source_tab != 'liked':
            raise RuntimeError('收藏列表中的笔记却显示未收藏，已停止以免切换错误状态')
        script = build_collect_into_board_js(
            user_id,
            marker,
            note_id=note_id,
            target_board=target_board,
            timeout_ms=timeout_ms,
        )
    else:
        script = build_assign_collected_note_js(
            user_id,
            marker,
            note_id=note_id,
            target_board=target_board,
            allow_recollect=getattr(args, 'allow_recollect', False) is True,
            timeout_ms=timeout_ms,
        )
    return (
        '/* membership_state:not_in_any_board; '
        'archive_lifecycle_state:first_archive_pending; exact_note_id_only */\n'
        + script
    )


def open_exact_source_note(
    session: BrowserVisibleUiSession,
    item: Dict[str, Any],
    *,
    timeout_sec: float,
) -> Dict[str, Any]:
    note_id = str(item.get('id') or '').strip().lower()
    source_tab = source_tab_for_item(item)
    session.navigate(f'/user/profile/{session.user_id}', {'tab': source_tab})
    deadline = time.monotonic() + timeout_sec
    from xhs_visible_ui import build_source_page_ready_js
    session.wait_for(
        build_source_page_ready_js(session.user_id, session.tab_marker, source_tab),
        timeout_sec=min(20.0, timeout_sec),
    )
    last_count = -1
    last_scroll = -1
    while time.monotonic() < deadline:
        result = session.run_json(build_find_and_open_note_card_js(
            session.user_id,
            session.tab_marker,
            note_id=note_id,
            source_tab=source_tab,
        ))
        if result.get('found') is True and result.get('clicked') is True:
            probe = session.wait_for(
                build_note_collect_probe_js(session.user_id, session.tab_marker, note_id),
                timeout_sec=min(20.0, max(1.0, deadline - time.monotonic())),
            )
            return dict(probe)
        count = int(result.get('visible_card_count') or 0)
        scroll_after = int(result.get('scroll_y_after') or 0)
        if result.get('at_bottom') is True and count == last_count and scroll_after == last_scroll:
            break
        last_count = count
        last_scroll = scroll_after
        time.sleep(0.2)
    raise VisibleUiContractError(
        f'在{source_tab}列表完整滚动到底后仍未找到精确 note id：{note_id}'
    )


def read_target_board_state(
    session: BrowserVisibleUiSession,
    target_board: str,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    albums = read_visible_album_list(session)
    matches = [row for row in albums['boards'] if row.get('name') == target_board]
    if len(matches) != 1:
        raise VisibleUiContractError('目标专辑必须在完整可见专辑列表中唯一存在')
    return albums, read_visible_board(session, matches[0])


def validate_live_assignment_membership(session, item):
    """Re-prove the approved source before a potentially destructive toggle.

    Even one new membership is a change: do not uncollect a note the user has
    since put in another (possibly protected) album. Full reader fails on gaps.
    """
    snapshot = capture_visible_album_snapshot(session)
    note_id = str(item.get('id') or '').lower()
    actual = {board['id'] for board in snapshot['boards'] if note_id in board['note_ids']}
    source = str(item.get('source_board_id') or '').lower()
    expected = {source} if source else set()
    if actual != expected:
        raise VisibleUiContractError('笔记实时专辑关系已变化；不会取消收藏，必须重新生成计划')
    return snapshot


def successful_visible_assignment_chunk(
    item: Dict[str, Any],
    result: Dict[str, Any],
    before_albums: Dict[str, Any],
    before_board: Dict[str, Any],
    after_albums: Dict[str, Any],
    after_board: Dict[str, Any],
    before_source_board: Optional[Dict[str, Any]] = None,
    after_source_board: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    note_id = str(item.get('id') or '').strip().lower()
    validate_new_collection_transition(before_board, after_board, note_id)
    before_identity = {(row['id'], row['name']) for row in before_albums['boards']}
    after_identity = {(row['id'], row['name']) for row in after_albums['boards']}
    if before_albums['declared_board_count'] != after_albums['declared_board_count']:
        raise VisibleUiContractError('归档后专辑总数发生变化')
    if before_identity != after_identity:
        raise VisibleUiContractError('归档后专辑 id/名称集合发生变化')
    if before_source_board is not None:
        if after_source_board is None:
            raise VisibleUiContractError('跨专辑归档后缺少原专辑回读')
        before_source_ids = set(before_source_board.get('note_ids') or [])
        after_source_ids = set(after_source_board.get('note_ids') or [])
        if note_id not in before_source_ids:
            raise VisibleUiContractError('跨专辑归档前原专辑没有该笔记')
        if (
            after_source_board.get('declared_total') != before_source_board.get('declared_total') - 1
            or after_source_ids != before_source_ids - {note_id}
        ):
            raise VisibleUiContractError('跨专辑归档后原专辑没有精确减少该笔记')
    row = {
        'id': note_id,
        'title': item.get('title') or '',
        'target_board': item.get('target_board') or '',
        'status': 'success',
        'attempt': 1,
        'events': list(result.get('events') or []) + ['verify:exact_member_append'],
        'error': '',
        'verified': True,
        'visible_confirmation': result.get('visible_confirmation') or '',
        'source_board': '',
        'source_board_id': '',
        'membership_state': 'skill_archived_board_member_protected',
        'archive_lifecycle_state': 'first_archive_confirmed',
        'source_lists': item.get('source_lists', []),
        'source_primary': item.get('source_primary', ''),
        'exclude_reason': '',
    }
    target = row['target_board']
    return {
        'board_list_count': after_albums['declared_board_count'],
        'board_list_page_count': after_albums['page_count'],
        'processed': [row],
        'errors': [],
        'missing_boards': [],
        'board_counts_before': {target: before_board['declared_total']},
        'board_counts_after': {target: after_board['declared_total']},
        'board_count_checks': {target: {
            'before': before_board['declared_total'],
            'after': after_board['declared_total'],
            'expected_delta': 1,
            'passed': True,
        }},
    }

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
        state = parse_js_json(runner.run_javascript(poll_js))
        if state is None:
            raise SafetyHaltedError('browser job state bridge disappeared; 已停止以免在未知页面状态下继续写入')
        if state and state.get('done'):
            if state.get('ok'):
                return state.get('result') or {}
            message = state.get('error') or 'browser job failed'
            if classify_safety_error(message):
                error = SafetyHaltedError(str(message))
                error.ui_state = state
                raise error
            raise RuntimeError(message)
        time.sleep(1)
    raise SafetyHaltedError('HIGH_RISK_STATE_UNCERTAIN: browser job timed out after dispatch; do not retry')


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
            'membership_state': (item or {}).get('membership_state') or '',
            'archive_lifecycle_state': (item or {}).get('archive_lifecycle_state') or '',
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
    if isinstance(getattr(error, 'ui_state', None), dict):
        row['visible_ui_state'] = error.ui_state
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


def is_ready_move(item: Dict[str, Any], allow_low_confidence: bool) -> bool:
    status = str(item.get('status') or '').strip()
    if status and status != 'planned':
        return False
    return all((
        not item.get('excluded'),
        not item.get('exclude_reason'),
        item.get('membership_state') in {'not_in_any_board', 'unarchived_board_member'},
        item.get('archive_lifecycle_state') == 'first_archive_pending',
        bool(str(item.get('id') or '').strip()),
        bool(str(item.get('target_board') or '').strip()),
        str(item.get('source_board') or '').strip() != str(item.get('target_board') or '').strip(),
        confidence_allows_move(item, allow_low_confidence),
    ))


def is_ready_finalize(item: Dict[str, Any], allow_low_confidence: bool) -> bool:
    status = str(item.get('status') or '').strip()
    return all((
        not status or status == 'planned',
        not item.get('excluded'),
        not item.get('exclude_reason'),
        item.get('membership_state') == 'unarchived_board_member',
        item.get('archive_lifecycle_state') == 'first_archive_pending',
        bool(str(item.get('id') or '').strip()),
        bool(str(item.get('target_board') or '').strip()),
        str(item.get('source_board') or '').strip() == str(item.get('target_board') or '').strip(),
        confidence_allows_move(item, allow_low_confidence),
    ))


def apply_batch(
    classification: List[Dict[str, Any]],
    report: Dict[str, Any],
    args: argparse.Namespace,
    report_path: Path,
    commit_callback: Optional[Callable[[], None]] = None,
    post_commit_callback: Optional[Callable[['BrowserRunner'], None]] = None,
    completion_callback: Optional[Callable[[BrowserVisibleUiSession, Dict[str, Any]], None]] = None,
) -> None:
    backend = choose_backend(args.browser, args)
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
    move_policy = {
        'auto_scroll': True,
        'auto_navigation': True,
        'auto_retry': False,
        'visible_ui_only': True,
        'exact_note_id_only': True,
        'max_moves_per_session': session_limit,
    }
    if commit_callback is None:
        ensure_active_session(safety_state, stage='move', policy=move_policy)
    elif is_security_halted(load_safety_state(safety_state)):
        raise SafetyHaltedError('此前会话已安全停机，不能执行移动。')
    report['safety_state_file'] = str(safety_state)
    report['move_session_limit'] = session_limit
    executable_items = [
        item
        for item in classification
        if is_ready_move(item, args.allow_low_confidence)
    ]
    finalize_items = [
        item
        for item in classification
        if is_ready_finalize(item, args.allow_low_confidence)
    ]
    planned_items = executable_items[:session_limit]
    remaining_count = len(executable_items) - len(planned_items)
    if not planned_items and not finalize_items:
        report['session_status'] = 'completed'
        report['updated_at'] = utc_now()
        write_json(report_path, report)
        return
    for item in planned_items:
        if source_tab_for_item(item) == 'fav' and getattr(args, 'allow_recollect', False) is not True:
            report.update({'mode': 'execute_blocked', 'ready_for_execute': False,
                           'blockers': ['recollect_consent_required'], 'session_status': 'blocked'})
            write_json(report_path, report)
            raise RuntimeError('已有收藏归档需要明确同意取消后重新收藏（--allow-recollect）；尚未打开浏览器')
        build_browser_job([item], args)
    runner = BrowserRunner(backend, args)
    try:
        validate_write_live_binding(runner, args)
        session = BrowserVisibleUiSession(
            runner,
            str(getattr(args, 'user_id', '') or ''),
            str(getattr(args, 'arc_tab_marker', '') or '') if backend == 'arc' else '',
        )
        if commit_callback is not None:
            commit_callback()
            ensure_active_session(safety_state, stage='move', policy=move_policy)
        if post_commit_callback is not None:
            try:
                post_commit_callback(runner)
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
                    report['security_halted'] = True
                    report['safety_halt'] = {
                        'reason_code': reason_code,
                        'message': message,
                        'state_file': str(safety_state),
                    }
                report['session_status'] = 'stopped_on_board_creation_error'
                report['board_creation_error'] = str(exc)
                report['updated_at'] = utc_now()
                write_json(report_path, report)
                raise
        for item in finalize_items:
            albums, board = read_target_board_state(
                session,
                str(item.get('target_board') or '').strip(),
            )
            note_id = str(item.get('id') or '').strip().lower()
            if note_id not in set(board.get('note_ids') or []):
                raise VisibleUiContractError(
                    f'待登记笔记不在声明的目标专辑中：{note_id}'
                )
            row = {
                'id': note_id,
                'title': item.get('title') or '',
                'target_board': item.get('target_board') or '',
                'status': 'already_in_target',
                'attempt': 0,
                'events': ['verify:already_in_target', 'archive:first_archive_confirmed'],
                'error': '',
                'verified': True,
                'source_board': item.get('source_board') or '',
                'source_board_id': item.get('source_board_id') or '',
                'membership_state': 'skill_archived_board_member_protected',
                'archive_lifecycle_state': 'first_archive_confirmed',
                'source_lists': item.get('source_lists', []),
                'source_primary': item.get('source_primary', ''),
                'exclude_reason': '',
            }
            report.setdefault('processed', []).append(row)
            report['board_list_count'] = albums['declared_board_count']
            report['board_list_page_count'] = albums['page_count']
            report['updated_at'] = utc_now()
            write_json(report_path, report)
        for index, item in enumerate(planned_items):
            if index > 0 and inter_item_delay_sec > 0:
                time.sleep(inter_item_delay_sec)
            try:
                validate_live_assignment_membership(session, item)
                before_albums, before_board = read_target_board_state(
                    session,
                    str(item.get('target_board') or '').strip(),
                )
                before_source_board = None
                source_board_id = str(item.get('source_board_id') or '').strip().lower()
                if source_board_id:
                    source_matches = [
                        row for row in before_albums['boards']
                        if str(row.get('id') or '').strip().lower() == source_board_id
                    ]
                    if len(source_matches) != 1:
                        raise VisibleUiContractError('原专辑必须在完整专辑列表中唯一存在')
                    before_source_board = read_visible_board(session, source_matches[0])
                probe = open_exact_source_note(
                    session,
                    item,
                    timeout_sec=float(args.timeout_sec),
                )
                source_tab = source_tab_for_item(item)
                if probe.get('collected') is False and source_tab != 'liked':
                    raise VisibleUiContractError(
                        '收藏列表中的笔记显示未收藏，已停止以免误触收藏状态'
                    )
                if probe.get('collected') is True and getattr(args, 'allow_recollect', False) is not True:
                    raise VisibleUiContractError('该笔记已收藏，缺少取消后重新收藏授权；保持零写入')
                executable_item = dict(item)
                executable_item['_visible_collected'] = probe.get('collected') is True
                intent = {
                    'id': item['id'],
                    'operation': 'uncollect_recollect_then_join' if executable_item['_visible_collected'] else 'collect_then_join',
                    'status': 'dispatching',
                }
                report.setdefault('write_intents', []).append(intent)
                write_json(report_path, report)
                try:
                    run_id = parse_browser_job_id(
                        runner.run_javascript(build_browser_job([executable_item], args))
                    )
                    result = poll_browser_job(runner, run_id, args.timeout_sec)
                except Exception as dispatch_exc:
                    intent['status'] = 'state_uncertain'
                    error = SafetyHaltedError('HIGH_RISK_STATE_UNCERTAIN: 可见收藏/归入任务已下发；禁止自动重试；' + str(dispatch_exc))
                    error.ui_state = getattr(dispatch_exc, 'ui_state', None)
                    raise error from dispatch_exc
                try:
                    after_albums, after_board = read_target_board_state(
                        session,
                        str(item.get('target_board') or '').strip(),
                    )
                    after_source_board = None
                    if source_board_id:
                        source_matches = [
                            row for row in after_albums['boards']
                            if str(row.get('id') or '').strip().lower() == source_board_id
                        ]
                        if len(source_matches) != 1:
                            raise VisibleUiContractError('归档后无法唯一找回原专辑')
                        after_source_board = read_visible_board(session, source_matches[0])
                    result = successful_visible_assignment_chunk(
                        item,
                        result,
                        before_albums,
                        before_board,
                        after_albums,
                        after_board,
                        before_source_board,
                        after_source_board,
                    )
                    intent['status'] = 'verified'
                except Exception as verify_exc:
                    intent['status'] = 'state_uncertain'
                    raise RuntimeError(
                        'HIGH_RISK_STATE_UNCERTAIN: 已点击目标专辑，但完整成员回读未通过；'
                        + str(verify_exc)
                    ) from verify_exc
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
                    'membership_state': item.get('membership_state') or '',
                    'archive_lifecycle_state': item.get('archive_lifecycle_state') or '',
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
            if completion_callback is not None:
                completion_callback(session, report)
        report['updated_at'] = utc_now()
        write_json(report_path, report)
    finally:
        runner.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description='生成分类预览、严格 dry-run，并在显式授权后通过正式可见页面执行归档。'
    )
    parser.add_argument('classification', help='classification.json 路径')
    parser.add_argument('report', nargs='?', default='run_report.json', help='run_report.json 输出路径')
    parser.add_argument(
        '--execute',
        action='store_true',
        help='通过用户在当前回合明确授权的浏览器正式可见页面执行已确认归档',
    )
    parser.add_argument('--browser', choices=['auto', 'arc', 'chrome', 'safari', 'playwright'], default='auto', help='执行浏览器后端；真实执行必须显式指定，禁止 auto')
    parser.add_argument('--allow-low-confidence', action='store_true', help='允许移动 low confidence 条目；默认要求人工复核')
    parser.add_argument('--allow-recollect', action='store_true', help='仅用户明确同意后：待归档的已收藏笔记取消一次再重新收藏，以打开加入专辑；会改变收藏排序，中途失败可能留下未收藏状态')
    parser.add_argument('--verify-pages', type=int, default=10, help='每个目标专辑最多翻页核验次数')
    parser.add_argument('--timeout-sec', type=int, default=300, help='浏览器执行最长等待秒数')
    parser.add_argument('--inter-item-delay-sec', type=float, default=5.0, help='真实执行时两条移动之间的固定等待秒数；默认 5，测试可设 0')
    parser.add_argument('--max-moves-per-session', type=int, default=None, help='真实执行必填：本次最多移动多少条，范围 1 到 200；达到上限后不会自动续跑')
    parser.add_argument('--safety-state', default='', help='共享安全状态文件；默认继承 classification.json 旁已有状态，否则使用 run_report.json 同目录的 xhs_safety_state.json')
    parser.add_argument('--arc-window-id', default='', help='Arc 真实执行必填：工作窗口的 Arc AppleScript 唯一 id（不是会变的 window index）')
    parser.add_argument('--arc-tab-id', default='', help='Arc 真实执行必填：工作标签页的 Arc AppleScript 唯一 id')
    parser.add_argument('--arc-tab-marker', default='', help='Arc 真实执行必填：预先单独写入工作标签页 window.name 的唯一标记；执行器只核验，不自动设置')
    parser.add_argument('--arc-expected-url-substring', default='', help='Arc 真实执行必填：预期收藏/专辑页 URL 的稳定片段；执行和轮询每次都重新核对')
    parser.add_argument('--expected-url-substring', default='', help='执行页 URL 必须包含的稳定片段；真实执行时必须与只读专辑快照绑定一致')
    parser.add_argument('--user-id', default='', help='真实执行时必填且必须与只读专辑快照账号一致；也用于页面 state 没有专辑列表时查询专辑')
    parser.add_argument('--url', default=None, help='Playwright 模式下可选：打开指定小红书页面')
    parser.add_argument('--channel', default='chromium', help='Playwright channel：chrome、msedge、chromium；默认使用 Playwright 自带 chromium')
    parser.add_argument('--user-data-dir', default=None, help='Playwright 持久化浏览器资料目录')
    parser.add_argument('--cdp-url', default=None, help='连接已启动 Chrome/Edge 的 CDP 地址')
    parser.add_argument('--headless', action='store_true', help='Playwright 新开浏览器时使用 headless；登录场景通常不要开启')
    parser.add_argument('--resume', action='store_true', help='读取已有 run_report.json，跳过已经 success 且核验过的条目')
    parser.add_argument('--board-snapshot', default='', help='capture_board_snapshot.py 生成的只读全专辑快照；与 --created-boards 同时提供后才生成可执行 dry-run')
    parser.add_argument('--created-boards', default='', help='build_created_boards.py 生成的目标专辑核验结果；与 --board-snapshot 同时提供')
    parser.add_argument('--allow-planned-board-creation', action='store_true', help='仅供受信 WorkBuddy dry-run：允许审批证据中声明待创建专辑')
    parser.add_argument('--collection-scope', default='', help='可选 collection_scope.json；提供时强制校验完整分类范围及专辑快照页面绑定')
    parser.add_argument('--archive-registry', default='', help='可选：上一轮由本 Skill 回读确认后生成的 v2 归档登记；只有其中专辑的当前成员受保护')
    parser.add_argument('--archive-output', default='', help='直接 execute 必填：写入新的不可覆盖 Skill 归档登记；同目录生成 post snapshot')
    args = parser.parse_args()

    if args.execute and is_workbuddy_host():
        raise SystemExit(
            'WorkBuddy 中禁止直接运行 --execute；必须通过 xhs_workbuddy_execute 的签名审批通路。'
        )
    if args.execute and args.allow_planned_board_creation:
        raise SystemExit(
            '--allow-planned-board-creation 只能用于受信 WorkBuddy dry-run；'
            '真实执行必须由插件在同一会话中创建并核验专辑。'
        )
    classification = normalize_classification(load_json(args.classification))
    scope_path = str(args.collection_scope or '').strip()
    if scope_path:
        validate_scope_input(scope_path, classification, stage='归档分类输入')
    has_snapshot = bool(str(args.board_snapshot or '').strip())
    has_created_boards = bool(str(args.created_boards or '').strip())
    has_preflight_inputs = has_snapshot and has_created_boards
    if has_snapshot != has_created_boards:
        raise SystemExit('--board-snapshot 与 --created-boards 必须同时提供')
    mode = 'execute' if args.execute else ('dry_run' if has_preflight_inputs else 'classification_preview')
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
    if has_preflight_inputs:
        snapshot_path = Path(args.board_snapshot)
        created_boards_path = Path(args.created_boards)
        snapshot_data = load_json(str(snapshot_path))
        if scope_path:
            validate_scope_snapshot(scope_path, snapshot_data)
        preflight = prepare_write_preflight(
            classification,
            snapshot_data,
            load_json(str(created_boards_path)),
            allow_low_confidence=args.allow_low_confidence,
            allow_planned_board_creation=args.allow_planned_board_creation,
            archive_registry=(
                load_json(str(Path(args.archive_registry)))
                if str(args.archive_registry or '').strip()
                else None
            ),
        )
        classification = preflight.pop('resolved_items')
        report.update(preflight)
        report['board_snapshot'] = str(snapshot_path)
        report['board_snapshot_sha256'] = sha256_file(snapshot_path)
        report['created_boards'] = str(created_boards_path)
        report['created_boards_sha256'] = sha256_file(created_boards_path)
        report['classification_sha256'] = sha256_file(Path(args.classification))
        if str(args.archive_registry or '').strip():
            report['archive_registry'] = str(Path(args.archive_registry))
            report['archive_registry_sha256'] = sha256_file(Path(args.archive_registry))
        if scope_path:
            report['collection_scope'] = scope_path
            report['collection_scope_sha256'] = sha256_file(Path(scope_path))
        if report['blockers']:
            report['mode'] = 'execute_blocked' if args.execute else 'dry_run_blocked'
            report['finished_at'] = utc_now()
            write_json(report_path, report)
            print(json.dumps({
                'mode': report['mode'],
                'ready_for_execute': False,
                'blockers': report['blockers'],
                'report': str(report_path),
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            raise SystemExit(1)
        if args.execute:
            binding_blockers = write_binding_blockers(report.get('snapshot_source'), args)
            if binding_blockers:
                report['ready_for_execute'] = False
                report['blockers'] = binding_blockers
                report['mode'] = 'execute_blocked'
                report['finished_at'] = utc_now()
                write_json(report_path, report)
                print(json.dumps({
                    'mode': report['mode'],
                    'ready_for_execute': False,
                    'blockers': report['blockers'],
                    'report': str(report_path),
                }, ensure_ascii=False, indent=2), file=sys.stderr)
                raise SystemExit(1)
    else:
        report['blockers'] = [
            'board_validation_not_run',
            'membership_validation_not_run',
        ]
        if args.execute:
            report['mode'] = 'execute_blocked'
            report['finished_at'] = utc_now()
            write_json(report_path, report)
            print(json.dumps({
                'mode': report['mode'],
                'ready_for_execute': False,
                'blockers': report['blockers'],
                'report': str(report_path),
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            raise SystemExit(1)
    if args.resume and report_path.exists():
        previous = load_json(str(report_path))
        classification, preserved = filter_classification_for_resume(classification, previous)
        report['resumed_from'] = str(report_path)
        report['processed'] = preserved
        report['visible_count'] = len(classification) + len(preserved)
        report['skipped_success_count'] = len(preserved)

    if args.execute:
        if not str(args.archive_output or '').strip():
            report['mode'] = 'execute_blocked'
            report['ready_for_execute'] = False
            report['blockers'] = ['archive_output_required']
            report['finished_at'] = utc_now()
            write_json(report_path, report)
            raise SystemExit('--execute 必须提供 --archive-output；只有完成实时回读并写入归档登记后才算完成')
        archive_output = Path(args.archive_output)
        if archive_output.exists():
            raise SystemExit(f'拒绝覆盖已有归档登记：{archive_output}')
        post_snapshot_path = archive_output.with_name(archive_output.stem + '.post_board_snapshot.json')
        if post_snapshot_path.exists():
            raise SystemExit(f'拒绝覆盖已有 post snapshot：{post_snapshot_path}')

        def write_direct_archive(session: BrowserVisibleUiSession, current_report: Dict[str, Any]) -> None:
            from build_archived_notes_registry import build_registry
            snapshot = capture_visible_album_snapshot(session)
            snapshot['generated_at'] = utc_now()
            snapshot['source'].update({
                'browser': choose_backend(args.browser, args),
                'user_id': args.user_id,
                'expected_url_substring': args.expected_url_substring or args.arc_expected_url_substring,
                'verify_pages': args.verify_pages,
                'writes_performed': False,
            })
            write_json(post_snapshot_path, snapshot)
            previous = (
                load_json(str(Path(args.archive_registry)))
                if str(args.archive_registry or '').strip()
                else None
            )
            registry = build_registry(
                current_report,
                snapshot,
                user_id=args.user_id,
                previous=previous,
            )
            registry['source_sha256'] = {
                'run_report_before_registry': hashlib.sha256(
                    json.dumps(current_report, ensure_ascii=False, sort_keys=True).encode('utf-8')
                ).hexdigest(),
                'post_board_snapshot': sha256_file(post_snapshot_path),
                'previous_registry': (
                    sha256_file(Path(args.archive_registry))
                    if str(args.archive_registry or '').strip()
                    else ''
                ),
            }
            write_json(archive_output, registry)
            current_report['post_board_snapshot'] = str(post_snapshot_path)
            current_report['archive_registry'] = str(archive_output)
            current_report['archived_board_count'] = registry['archived_board_count']
            current_report['confirmed_archived_count'] = registry['confirmed_archived_count']

        apply_batch(
            classification,
            report,
            args,
            report_path,
            completion_callback=write_direct_archive,
        )
    elif not has_preflight_inputs:
        for item in classification:
            append_classification_preview(report, item, args.allow_low_confidence)
            report['updated_at'] = utc_now()
            write_json(report_path, report)
    else:
        for item in classification:
            append_dry_run(report, item, args.allow_low_confidence)
            report['updated_at'] = utc_now()
            write_json(report_path, report)

    report['finished_at'] = utc_now()
    write_json(report_path, report)
    print(json.dumps({
        'mode': report['mode'],
        'ready_for_execute': report['ready_for_execute'],
        'blockers': report['blockers'],
        'processed_count': len(report['processed']),
        'error_count': len(report['errors']),
        'missing_boards': report['missing_boards'],
        'report': str(report_path),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
