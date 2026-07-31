#!/usr/bin/env python3
"""WorkBuddy MCP 与现有 Skill 脚本之间的窄接口。

只暴露固定工作流，不提供任意命令执行。所有浏览器阶段都由
workbuddy_runtime.py 强制进入独立的 Playwright Chromium profile。
"""

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from extract_visible_items import (
    normalize_source_label,
    read_stable_items_snapshot,
    validate_capture_page,
)
from run_reassign_batch import BrowserRunner
from workbuddy_runtime import (
    apply_workbuddy_browser_policy,
    is_workbuddy_host,
    workbuddy_profile_path,
    workbuddy_runtime_status,
)
from xhs_safety import ensure_active_session, resolve_safety_state_path


ROOT = Path(__file__).resolve().parents[1]
RUN_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
NOTE_ID_RE = re.compile(r'^[0-9a-f]{24}$', re.IGNORECASE)
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
ALLOWED_SOURCES = {'collection', 'liked', 'custom'}
LOGIN_SOURCES = {'collection', 'liked'}
PROFILE_LOCK_NAMES = ('SingletonLock', 'SingletonSocket', 'SingletonCookie')
MAX_CAPTURE_BATCH_SIZE = 200
DEFAULT_CAPTURE_BATCH_SIZE = 200
DEFAULT_CAPTURE_PAUSE_MINUTES = 3
MAX_WORKBUDDY_SCROLLS = 5000
WORKBUDDY_RESET_TOP_JS = r"""
(async () => {
  window.scrollTo(0, 0);
  await new Promise(resolve => requestAnimationFrame(
    () => requestAnimationFrame(resolve)
  ));
  await new Promise(resolve => setTimeout(resolve, 100));
  return "ok";
})()
"""
WORKBUDDY_SCROLL_AND_SETTLE_JS = r"""
(async () => {
  const pageState = () => {
    const indexes = Array.from(
      document.querySelectorAll('section.note-item, .note-item, [data-note-id]')
    ).map(node => Number(node.getAttribute('data-index')))
      .filter(Number.isInteger);
    return {
      scrollY: window.scrollY,
      scrollHeight: document.documentElement.scrollHeight,
      maxPageIndex: indexes.length ? Math.max(...indexes) : null
    };
  };
  const before = pageState();
  window.scrollBy(0, Math.max(800, Math.floor(window.innerHeight * 0.8)));
  const deadline = Date.now() + 2500;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 100));
    const after = pageState();
    if (
      after.scrollY !== before.scrollY
      || after.scrollHeight !== before.scrollHeight
      || after.maxPageIndex !== before.maxPageIndex
    ) {
      await new Promise(resolve => setTimeout(resolve, 100));
      return JSON.stringify(after);
    }
  }
  return JSON.stringify(pageState());
})()
"""
OWN_PROFILE_LINK_JS = r"""
() => {
  const links = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'));
  const own = links.find((link) => (link.textContent || '').trim() === '我');
  if (!own) return '';
  const href = own.getAttribute('href') || '';
  return new URL(href, window.location.origin).href;
}
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_private_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.chmod(0o600)
    os.replace(temp, path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def plugin_data_dir() -> Path:
    raw = str(os.environ.get('CODEBUDDY_PLUGIN_DATA') or '').strip()
    if not raw:
        raise RuntimeError('缺少 CODEBUDDY_PLUGIN_DATA；只能从 WorkBuddy Plugin 调用此桥接器。')
    data_dir = Path(raw).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def require_workbuddy() -> None:
    if not is_workbuddy_host():
        raise RuntimeError('此入口只允许由 WorkBuddy Plugin 调用。')
    workbuddy_profile_path()


def validate_run_id(value: str, *, create: bool = False) -> str:
    run_id = str(value or '').strip()
    if not run_id and create:
        run_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    if not RUN_ID_RE.fullmatch(run_id) or '..' in run_id:
        raise RuntimeError('run_id 只能包含字母、数字、点、下划线和连字符，长度 1 到 64。')
    return run_id


def run_dir_for(run_id: str, *, create: bool = False) -> Path:
    checked = validate_run_id(run_id, create=create)
    directory = plugin_data_dir() / 'runs' / checked
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    elif not directory.is_dir():
        raise RuntimeError(f'运行目录不存在：{checked}')
    return directory


def validate_xhs_url(value: str, source: str = 'custom') -> str:
    raw = str(value or '').strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not (host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com')):
        raise RuntimeError('page_url 必须是 https://*.xiaohongshu.com 页面。')
    if source not in ALLOWED_SOURCES:
        raise RuntimeError(f'不支持的来源：{source}')
    tab = (parse_qs(parsed.query).get('tab') or [''])[0].lower()
    if source == 'collection' and tab != 'fav':
        raise RuntimeError('收藏范围必须提供带 tab=fav 的个人收藏页 URL。')
    if source == 'liked' and tab not in {'liked', 'like'}:
        raise RuntimeError('点赞范围必须提供带 tab=liked 的个人点赞页 URL。')
    return raw


def profile_lock_paths(profile: Path) -> List[Path]:
    return [
        profile / name
        for name in PROFILE_LOCK_NAMES
        if (profile / name).exists() or (profile / name).is_symlink()
    ]


def require_profile_available() -> None:
    profile = workbuddy_profile_path()
    busy = profile_lock_paths(profile)
    if busy:
        names = ', '.join(path.name for path in busy)
        raise RuntimeError(
            f'WorkBuddy 专用浏览器仍在使用同一登录目录（{names}）。'
            '插件已停止，未创建运行产物；请结束上一条浏览器任务后重试。'
        )


def wait_for_profile_release(profile: Path, timeout_sec: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not profile_lock_paths(profile):
            return
        time.sleep(0.1)
    busy = ', '.join(path.name for path in profile_lock_paths(profile))
    raise RuntimeError(
        f'WorkBuddy 专用浏览器退出后未释放登录目录（{busy}）。'
        '插件已停止，不能继续抓取。'
    )


def profile_user_id(value: str) -> str:
    parsed = urlparse(str(value or '').strip())
    match = re.fullmatch(r'/user/profile/([0-9a-fA-F]{24})/?', parsed.path)
    return match.group(1) if match else ''


def target_page_url(user_id: str, source: str) -> str:
    if source not in LOGIN_SOURCES:
        raise RuntimeError('登录入口只接受 collection 或 liked。')
    tab = 'fav' if source == 'collection' else 'liked'
    return (
        f'https://www.xiaohongshu.com/user/profile/{user_id}'
        f'?tab={tab}&subTab=note'
    )


def metadata_quality(items: Any) -> Dict[str, int]:
    rows = items if isinstance(items, list) else []
    usable = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        if any([
            str(item.get('title') or '').strip(),
            str(item.get('desc') or '').strip(),
            str(item.get('card_text') or '').strip(),
            str(item.get('user') or '').strip(),
            item.get('tags') if isinstance(item.get('tags'), list) else [],
        ]):
            usable += 1
    return {
        'item_count': len(rows),
        'usable_item_count': usable,
        'unusable_item_count': len(rows) - usable,
    }


def _workbuddy_page_state(
    data: Dict[str, Any],
    seen_positions: Dict[int, str],
    seen_count: int,
) -> Dict[str, Any]:
    scroll_y = data.get('scrollY')
    inner_height = data.get('innerHeight')
    scroll_height = data.get('scrollHeight')
    at_bottom = bool(
        all(
            isinstance(value, (int, float))
            for value in (scroll_y, inner_height, scroll_height)
        )
        and scroll_y + inner_height >= scroll_height - 50
    )
    positions = sorted(seen_positions)
    return {
        'location': data.get('location'),
        'title': data.get('title'),
        'scrollY': scroll_y,
        'innerHeight': inner_height,
        'scrollHeight': scroll_height,
        'declaredItemCount': data.get('declaredItemCount'),
        'at_bottom': at_bottom,
        'page_position_min': positions[0] if positions else None,
        'page_position_max': positions[-1] if positions else None,
        'page_position_count': len(positions),
        'seen_count': seen_count,
    }


def _write_workbuddy_group(
    directory: Path,
    segment_index: int,
    rows: List[Dict[str, Any]],
    *,
    batch_size: int,
    pause_minutes: int,
    crawl_complete: bool,
    page: Dict[str, Any],
    safety_state: Path,
) -> Dict[str, Any]:
    output = directory / f'visible_items.segment-{segment_index:03d}.json'
    manifest = directory / f'crawl_manifest.segment-{segment_index:03d}.json'
    if output.exists() or manifest.exists():
        raise RuntimeError(f'第 {segment_index} 组产物已存在，拒绝覆盖。')
    write_private_json(output, rows)
    payload = {
        'capture_mode': 'workbuddy_segmented',
        'segment_index': segment_index,
        'batch_size': batch_size,
        'pause_minutes': pause_minutes,
        'item_count': len(rows),
        'crawl_complete': crawl_complete,
        'stopped_reason': (
            'collection_complete' if crawl_complete else 'batch_size_reached'
        ),
        'auto_scroll': True,
        'auto_navigation': False,
        'auto_retry': False,
        'auto_continue_after_pause': True,
        'browser_session_reused': True,
        'output': str(output),
        'manifest': str(manifest),
        'page': page,
        'safety_state': str(safety_state),
        'completed_at': utc_now(),
    }
    write_private_json(manifest, payload)
    return payload


def capture_workbuddy_groups(
    js_eval,
    directory: Path,
    source: str,
    batch_size: int,
    pause_minutes: int,
    safety_state: Path,
) -> Dict[str, Any]:
    """Read a WorkBuddy list in durable groups without reopening the browser."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CAPTURE_BATCH_SIZE:
        raise RuntimeError('batch_size 必须是 1 到 200 的整数。')
    if not isinstance(pause_minutes, int) or isinstance(pause_minutes, bool) or pause_minutes < 1:
        raise RuntimeError('pause_minutes 必须是大于 0 的整数。')

    directory = Path(directory)
    safety_state = Path(safety_state)
    source_label = normalize_source_label(source)
    ensure_active_session(
        safety_state,
        stage='capture',
        policy={
            'capture_mode': 'workbuddy_segmented',
            'controller': 'workbuddy_plugin',
            'batch_size': batch_size,
            'pause_minutes': pause_minutes,
            'auto_scroll': True,
            'auto_navigation': False,
            'auto_retry': False,
            'auto_continue_after_pause': True,
            'browser_session_reused': True,
        },
    )

    seen: Dict[str, Dict[str, Any]] = {}
    seen_positions: Dict[int, str] = {}
    pending: List[Dict[str, Any]] = []
    committed: List[Dict[str, Any]] = []
    segment_manifests: List[Dict[str, Any]] = []
    bottom_signature = None
    bottom_stable_reads = 0
    last_page: Dict[str, Any] = {}
    crawl_complete = False

    js_eval(WORKBUDDY_RESET_TOP_JS)
    for scroll_index in range(MAX_WORKBUDDY_SCROLLS + 1):
        data, stability_checks = read_stable_items_snapshot(js_eval)
        validate_capture_page(data, safety_state)
        observed = [
            item for item in list(data.get('items') or [])
            if isinstance(item, dict) and item.get('id')
        ]
        observed.sort(key=lambda item: (
            item.get('page_index')
            if isinstance(item.get('page_index'), int)
            else MAX_WORKBUDDY_SCROLLS * 1000
        ))
        for item in observed:
            note_id = str(item.get('id') or '').strip()
            page_index = item.get('page_index')
            if isinstance(page_index, int) and page_index >= 0:
                previous_id = seen_positions.get(page_index)
                if previous_id and previous_id != note_id:
                    raise RuntimeError(
                        f'页面位置 {page_index} 对应的笔记发生变化，已停止以避免错位。'
                    )
                seen_positions[page_index] = note_id
            if note_id in seen:
                current = seen[note_id]
                for key, value in item.items():
                    if (
                        value not in (None, '', [], {})
                        and current.get(key) in (None, '', [], {})
                    ):
                        current[key] = value
                continue
            row = dict(item)
            row['source_lists'] = [source_label]
            row['source_primary'] = source_label
            seen[note_id] = row
            pending.append(row)

        last_page = _workbuddy_page_state(data, seen_positions, len(seen))
        if last_page['at_bottom']:
            signature = (
                last_page['scrollY'],
                last_page['scrollHeight'],
                last_page['page_position_max'],
                last_page['seen_count'],
            )
            bottom_stable_reads = (
                bottom_stable_reads + 1 if signature == bottom_signature else 1
            )
            bottom_signature = signature
        else:
            bottom_signature = None
            bottom_stable_reads = 0

        declared = last_page.get('declaredItemCount')
        declared_end_reached = bool(
            isinstance(declared, int)
            and not isinstance(declared, bool)
            and (
                (
                    isinstance(last_page['page_position_max'], int)
                    and last_page['page_position_max'] + 1 >= declared
                )
                or (not seen_positions and len(seen) >= declared)
            )
        )
        started_from_top = (
            not seen_positions or last_page['page_position_min'] == 0
        )
        crawl_complete = bool(
            last_page['at_bottom']
            and started_from_top
            and (declared_end_reached or bottom_stable_reads >= 2)
        )

        while len(pending) >= batch_size:
            group = pending[:batch_size]
            del pending[:batch_size]
            final_group = crawl_complete and not pending
            segment = _write_workbuddy_group(
                directory,
                len(segment_manifests) + 1,
                group,
                batch_size=batch_size,
                pause_minutes=pause_minutes,
                crawl_complete=final_group,
                page=last_page,
                safety_state=safety_state,
            )
            segment_manifests.append(segment)
            committed.extend(group)
            write_private_json(directory / 'visible_items.json', committed)
            if pending or not crawl_complete:
                time.sleep(pause_minutes * 60)

        write_private_json(directory / 'capture_progress.json', {
            'capture_mode': 'workbuddy_segmented',
            'batch_size': batch_size,
            'pause_minutes': pause_minutes,
            'captured_count': len(seen),
            'committed_count': len(committed),
            'pending_count': len(pending),
            'segment_count': len(segment_manifests),
            'scroll_count': scroll_index,
            'dom_stability_checks': stability_checks,
            'crawl_complete': crawl_complete,
            'page': last_page,
            'updated_at': utc_now(),
        })
        if crawl_complete:
            break
        js_eval(WORKBUDDY_SCROLL_AND_SETTLE_JS)
    else:
        raise RuntimeError(
            f'达到 {MAX_WORKBUDDY_SCROLLS} 次滚动上限仍未到列表末尾，已停止。'
        )

    if pending:
        segment = _write_workbuddy_group(
            directory,
            len(segment_manifests) + 1,
            pending,
            batch_size=batch_size,
            pause_minutes=pause_minutes,
            crawl_complete=True,
            page=last_page,
            safety_state=safety_state,
        )
        segment_manifests.append(segment)
        committed.extend(pending)
        pending = []
        write_private_json(directory / 'visible_items.json', committed)
    elif segment_manifests and not segment_manifests[-1]['crawl_complete']:
        final_segment = dict(segment_manifests[-1])
        final_segment['crawl_complete'] = True
        final_segment['stopped_reason'] = 'collection_complete'
        write_private_json(Path(final_segment['manifest']), final_segment)
        segment_manifests[-1] = final_segment
    if not (directory / 'visible_items.json').exists():
        write_private_json(directory / 'visible_items.json', committed)

    positions = sorted(seen_positions)
    missing_positions = (
        sorted(set(range(positions[0], positions[-1] + 1)) - set(positions))
        if positions else []
    )
    warnings = []
    declared = last_page.get('declaredItemCount')
    if isinstance(declared, int) and not isinstance(declared, bool) and declared != len(committed):
        warnings.append({
            'code': 'declared_count_mismatch',
            'declared_count': declared,
            'accessible_count': len(committed),
        })
    if missing_positions:
        warnings.append({
            'code': 'missing_page_positions',
            'count': len(missing_positions),
            'sample': missing_positions[:20],
        })

    aggregate_manifest = directory / 'crawl_manifest.json'
    write_private_json(aggregate_manifest, {
        'capture_mode': 'workbuddy_segmented',
        'source': source_label,
        'batch_size': batch_size,
        'pause_minutes': pause_minutes,
        'item_count': len(committed),
        'segment_count': len(segment_manifests),
        'crawl_complete': True,
        'stopped_reason': 'collection_complete',
        'browser_session_reused': True,
        'page': last_page,
        'warnings': warnings,
        'segments': [
            {
                'segment_index': item['segment_index'],
                'item_count': item['item_count'],
                'output': item['output'],
                'manifest': item['manifest'],
            }
            for item in segment_manifests
        ],
        'safety_state': str(safety_state),
    })
    return {
        'count': len(committed),
        'newly_seen_count': len(committed),
        'existing_count': 0,
        'source': source_label,
        'output': str(directory / 'visible_items.json'),
        'manifest': str(aggregate_manifest),
        'page': last_page,
        'capture_mode': 'workbuddy_segmented',
        'batch_size': batch_size,
        'pause_minutes': pause_minutes,
        'segment_count': len(segment_manifests),
        'crawl_complete': True,
        'stopped_reason': 'collection_complete',
        'warnings': warnings,
        'safety_state': str(safety_state),
    }


def browser_args(url: str = '') -> argparse.Namespace:
    args = argparse.Namespace(
        browser='auto',
        channel='chromium',
        user_data_dir=None,
        cdp_url=None,
        headless=False,
        url=url or None,
        arc_window_id='',
        arc_tab_id='',
        arc_tab_marker='',
        arc_expected_url_substring='',
        expected_url_substring='',
    )
    apply_workbuddy_browser_policy('auto', args)
    return args


def run_command(args: List[str], *, allow_failure: bool = False) -> subprocess.CompletedProcess:
    popen_kwargs: Dict[str, Any] = {
        'cwd': str(ROOT),
        'env': os.environ.copy(),
        'text': True,
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
    }
    if os.name == 'nt':
        popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs['start_new_session'] = True
    child = subprocess.Popen(args, **popen_kwargs)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def terminate_child_tree() -> None:
        if child.poll() is not None:
            return
        if os.name == 'nt':
            subprocess.run(
                ['taskkill', '/PID', str(child.pid), '/T', '/F'],
                text=True,
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def cancel_command(_signum, _frame) -> None:
        terminate_child_tree()
        raise RuntimeError('WorkBuddy 固定工作流已取消；子进程和专用浏览器已关闭。')

    signal.signal(signal.SIGTERM, cancel_command)
    try:
        stdout, stderr = child.communicate()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
    proc = subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
    if proc.returncode != 0 and not allow_failure:
        details = proc.stderr.strip() or proc.stdout.strip() or f'exit={proc.returncode}'
        raise RuntimeError(f'固定工作流命令失败：{details}')
    return proc


def playwright_probe() -> Dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {
            'python_package_ready': False,
            'chromium_ready': False,
            'install_required': True,
        }
    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            ready = executable.is_file()
            return {
                'python_package_ready': True,
                'chromium_ready': ready,
                'chromium_executable': str(executable),
                'install_required': not ready,
            }
    except Exception as exc:
        return {
            'python_package_ready': True,
            'chromium_ready': False,
            'install_required': True,
            'probe_error': str(exc),
        }


def status_action() -> Dict[str, Any]:
    require_workbuddy()
    return {
        'ok': True,
        'runtime': workbuddy_runtime_status(),
        'dependencies': playwright_probe(),
        'runs_dir': str(plugin_data_dir() / 'runs'),
        'checked_at': utc_now(),
    }


def setup_action() -> Dict[str, Any]:
    require_workbuddy()
    data_dir = plugin_data_dir()
    venv_dir = data_dir / 'python-venv'
    if sys.prefix != str(venv_dir):
        run_command([sys.executable, '-m', 'venv', str(venv_dir)])
    python = venv_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    requirements = ROOT / 'requirements-workbuddy.txt'
    run_command([
        str(python), '-m', 'pip', 'install',
        '--disable-pip-version-check',
        '--requirement', str(requirements),
    ])
    run_command([str(python), '-m', 'playwright', 'install', 'chromium'])
    marker = {
        'installed_at': utc_now(),
        'python': str(python),
        'requirements_sha256': sha256_file(requirements),
    }
    write_private_json(data_dir / 'setup.json', marker)
    return {
        'ok': True,
        'installed': True,
        'restart_mcp_required': False,
        **marker,
    }


def login_action(timeout_sec: int, source: str) -> Dict[str, Any]:
    require_workbuddy()
    if timeout_sec < 60 or timeout_sec > 900:
        raise RuntimeError('timeout_sec 必须在 60 到 900 秒之间。')
    if source not in LOGIN_SOURCES:
        raise RuntimeError('source 必须是 collection 或 liked。')
    require_profile_available()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError('Playwright 尚未安装；先调用 xhs_workbuddy_setup。') from exc

    profile = workbuddy_profile_path()
    selected_url = ''
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            headless=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            for stale_page in list(context.pages):
                if stale_page is not page:
                    stale_page.close()
            page.goto(
                'https://www.xiaohongshu.com/explore',
                wait_until='domcontentloaded',
                timeout=60000,
            )
            deadline = time.monotonic() + timeout_sec
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise RuntimeError(
                        '专用浏览器被提前关闭。无需手动关闭窗口；'
                        '请重试并只完成小红书登录。'
                    )
                own_profile = str(page.evaluate(OWN_PROFILE_LINK_JS) or '').strip()
                user_id = profile_user_id(own_profile)
                if user_id:
                    selected_url = target_page_url(user_id, source)
                    page.goto(
                        selected_url,
                        wait_until='domcontentloaded',
                        timeout=60000,
                    )
                    validate_xhs_url(page.url or selected_url, source)
                    break
                time.sleep(1)
            if not selected_url:
                raise RuntimeError(
                    '等待登录超时。请在专用浏览器完成小红书登录；'
                    '插件会自动进入所选范围并关闭窗口。'
                )
        finally:
            try:
                context.close()
            except Exception:
                pass
    wait_for_profile_release(profile)
    result = {
        'ok': True,
        'source': source,
        'target_page_url': selected_url,
        'completion_reason': 'authenticated_target_page_detected',
        'browser_closed_by_tool': True,
        'profile': str(profile),
        'next_action': (
            '直接把 target_page_url 传给 xhs_workbuddy_capture；'
            '不要让用户关闭窗口或复制 URL。'
        ),
        'finished_at': utc_now(),
    }
    write_private_json(plugin_data_dir() / 'last_login.json', result)
    return result


def capture_action(
    run_id: str,
    source: str,
    page_url: str,
    batch_size: int,
    pause_minutes: int,
) -> Dict[str, Any]:
    require_workbuddy()
    checked_url = validate_xhs_url(page_url, source)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CAPTURE_BATCH_SIZE:
        raise RuntimeError('batch_size 必须是 1 到 200 的整数。')
    if not isinstance(pause_minutes, int) or isinstance(pause_minutes, bool) or pause_minutes < 1:
        raise RuntimeError('pause_minutes 必须是大于 0 的整数。')
    require_profile_available()
    args = browser_args(checked_url)
    runner = None
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def cancel_capture(_signum, _frame):
        raise RuntimeError('WorkBuddy 抓取已取消；正在关闭本轮专用浏览器。')

    signal.signal(signal.SIGTERM, cancel_capture)
    try:
        runner = BrowserRunner('playwright', args)
        directory = run_dir_for(run_id, create=True)
        visible = directory / 'visible_items.json'
        safety = resolve_safety_state_path('', visible)
        ensure_active_session(
            safety,
            stage='capture',
            policy={
                'capture_mode': 'workbuddy_segmented',
                'controller': 'workbuddy_plugin',
                'batch_size': batch_size,
                'pause_minutes': pause_minutes,
                'auto_scroll': True,
                'auto_navigation': False,
                'auto_retry': False,
                'auto_continue_after_pause': True,
                'browser_session_reused': True,
                'workbuddy_exact_url_open': checked_url,
            },
        )
        result = capture_workbuddy_groups(
            runner.eval,
            directory,
            source,
            batch_size,
            pause_minutes,
            safety,
        )
        quality = metadata_quality(load_json(visible))
        result['metadata_quality'] = quality
        if quality['item_count'] > 0 and quality['usable_item_count'] == 0:
            raise RuntimeError(
                '抓取到了笔记 ID，但标题、作者和卡片文字全部为空；'
                '页面结构已变化，已停止分类，不能生成空的整理方案。'
            )
    finally:
        try:
            if runner is not None:
                runner.close()
        finally:
            try:
                if runner is not None:
                    wait_for_profile_release(workbuddy_profile_path())
            finally:
                signal.signal(signal.SIGTERM, previous_sigterm)
    result.update({
        'run_id': directory.name,
        'run_dir': str(directory),
        'browser_backend': 'playwright',
        'browser_profile': str(workbuddy_profile_path()),
        'exact_url_opened': checked_url,
    })
    result['classification_required'] = True
    result['next_action'] = (
        '先调用不带 classification 的 prepare，只读取得本次账号真实已有专辑；'
        '再根据 visible_items.json 和该专辑清单逐条分类。'
    )
    return result


def approval_basis(directory: Path, report: Dict[str, Any]) -> Dict[str, Any]:
    classification = directory / 'classification.json'
    snapshot = directory / 'board_snapshot.json'
    created = directory / 'created_boards.json'
    for path in (classification, snapshot, created):
        if not path.is_file():
            raise RuntimeError(f'缺少审批输入：{path.name}')
    planned = [
        {
            'id': str(row.get('id') or ''),
            'target_board': str(row.get('target_board') or ''),
            'source_board_id': str(row.get('source_board_id') or ''),
            'membership_state': str(row.get('membership_state') or ''),
            'status': str(row.get('status') or ''),
        }
        for row in report.get('processed', [])
    ]
    return {
        'classification_sha256': sha256_file(classification),
        'board_snapshot_sha256': sha256_file(snapshot),
        'created_boards_sha256': sha256_file(created),
        'mode': report.get('mode'),
        'ready_for_execute': report.get('ready_for_execute'),
        'blockers': report.get('blockers'),
        'planned': planned,
    }


def approval_digest(directory: Path, report: Dict[str, Any]) -> str:
    encoded = json.dumps(
        approval_basis(directory, report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def validate_workbuddy_snapshot_binding(
    snapshot: Any,
    user_id: str,
    expected_url_substring: str,
) -> List[str]:
    if not isinstance(snapshot, dict) or snapshot.get('mode') != 'read_only':
        raise RuntimeError('board_snapshot.json 不是本次只读专辑清单。')
    source = snapshot.get('source')
    if not isinstance(source, dict):
        raise RuntimeError('board_snapshot.json 缺少来源绑定。')
    if (
        source.get('browser') != 'playwright'
        or source.get('writes_performed') is not False
        or str(source.get('user_id') or '') != user_id
        or str(source.get('expected_url_substring') or '') != expected_url_substring
    ):
        raise RuntimeError('board_snapshot.json 与本次 WorkBuddy 账号或页面绑定不一致。')
    boards = snapshot.get('boards')
    if not isinstance(boards, list):
        raise RuntimeError('board_snapshot.json 的 boards 必须是数组。')
    names: List[str] = []
    for index, board in enumerate(boards):
        if not isinstance(board, dict):
            raise RuntimeError(f'board_snapshot.json 的 boards[{index}] 必须是对象。')
        name = str(board.get('name') or '').strip()
        if not name:
            raise RuntimeError(f'board_snapshot.json 的 boards[{index}] 缺少名称。')
        if name in names:
            raise RuntimeError(f'board_snapshot.json 包含重复专辑名：{name}')
        names.append(name)
    return names


def write_workbuddy_classification(
    directory: Path,
    classification_rows: Any,
    allowed_boards: List[str],
) -> Dict[str, Any]:
    """Validate model classifications against the real capture and persist them."""
    directory = Path(directory)
    visible_path = directory / 'visible_items.json'
    if not visible_path.is_file():
        raise RuntimeError('缺少 visible_items.json；不能生成脱离真实抓取的分类。')
    visible_rows = load_json(visible_path)
    if not isinstance(visible_rows, list):
        raise RuntimeError('visible_items.json 顶层必须是数组。')
    visible_by_id: Dict[str, Dict[str, Any]] = {}
    visible_order: List[str] = []
    for item in visible_rows:
        if not isinstance(item, dict):
            raise RuntimeError('visible_items.json 每一项必须是对象。')
        note_id = str(item.get('id') or '').strip()
        if not note_id or note_id in visible_by_id:
            raise RuntimeError('visible_items.json 含空 ID 或重复 ID。')
        visible_by_id[note_id] = item
        visible_order.append(note_id)

    if not isinstance(classification_rows, list):
        raise RuntimeError('classification 必须是数组。')
    allowed_board_set = set(allowed_boards)
    supplied: Dict[str, Dict[str, Any]] = {}
    for row in classification_rows:
        if not isinstance(row, dict):
            raise RuntimeError('classification 每一项必须是对象。')
        note_id = str(row.get('id') or '').strip()
        if note_id not in visible_by_id:
            raise RuntimeError(f'分类 ID 不属于本次抓取：{note_id or "<empty>"}')
        if note_id in supplied:
            raise RuntimeError(f'classification 包含重复 ID：{note_id}')
        supplied[note_id] = row
    missing_ids = [note_id for note_id in visible_order if note_id not in supplied]
    if missing_ids:
        raise RuntimeError(
            f'classification 未覆盖本次抓取的 {len(missing_ids)} 条笔记。'
        )

    taxonomy: List[str] = []
    normalized: List[Dict[str, Any]] = []
    for note_id in visible_order:
        source = visible_by_id[note_id]
        proposal = supplied[note_id]
        target = str(proposal.get('target_board') or '').strip()
        if target and target not in allowed_board_set:
            raise RuntimeError(
                f'分类目标不属于本次真实已有专辑：{note_id} target_board={target!r}'
            )
        confidence = str(proposal.get('confidence') or 'low').strip().lower()
        if confidence not in {'low', 'medium', 'high'}:
            raise RuntimeError(f'分类置信度无效：{note_id} confidence={confidence!r}')
        raw_reason = proposal.get('reason') or []
        if not isinstance(raw_reason, list):
            raise RuntimeError(f'分类 reason 必须是数组：{note_id}')
        reason = [str(value).strip() for value in raw_reason if str(value).strip()]
        review_state = str(proposal.get('review_state') or '').strip()
        if not review_state:
            review_state = 'classified' if target else 'pending'
        if target and target not in taxonomy:
            taxonomy.append(target)
        row = dict(source)
        row.update({
            'target_board': target,
            'confidence': confidence,
            'reason': reason,
            'review_state': review_state,
            'classification_basis': 'workbuddy_model_real_content',
            'main_topic': str(proposal.get('main_topic') or '').strip(),
            'content_summary': str(proposal.get('content_summary') or '').strip(),
        })
        normalized.append(row)

    taxonomy_path = directory / 'board_taxonomy.json'
    classification_path = directory / 'classification.json'
    write_private_json(taxonomy_path, {'boards': taxonomy})
    write_private_json(classification_path, normalized)
    return {
        'classification': str(classification_path),
        'classification_count': len(normalized),
        'taxonomy_path': str(taxonomy_path),
        'taxonomy': taxonomy,
    }


def prepare_action(
    run_id: str,
    user_id: str,
    page_url: str,
    expected_url_substring: str,
    verify_pages: int,
    classification_rows: Any = None,
) -> Dict[str, Any]:
    require_workbuddy()
    directory = run_dir_for(run_id)
    if not NOTE_ID_RE.fullmatch(str(user_id or '').strip()):
        raise RuntimeError('user_id 必须是当前账号 URL 中的 24 位十六进制 id。')
    user_id = str(user_id).strip()
    checked_url = validate_xhs_url(page_url, 'custom')
    expected = str(expected_url_substring or '').strip()
    if not expected or expected not in checked_url:
        raise RuntimeError('expected_url_substring 必须是 page_url 中的稳定片段。')
    if not isinstance(verify_pages, int) or isinstance(verify_pages, bool) or not 1 <= verify_pages <= 200:
        raise RuntimeError('verify_pages 必须是 1 到 200 的整数。')

    snapshot = directory / 'board_snapshot.json'
    classification = directory / 'classification.json'
    created = directory / 'created_boards.json'
    report_path = directory / 'run_report.json'
    safety = directory / 'xhs_safety_state.json'
    approval_path = directory / 'approval.json'
    approval_path.unlink(missing_ok=True)

    if classification_rows is None:
        require_profile_available()
        run_command([
            sys.executable,
            str(ROOT / 'scripts/capture_board_snapshot.py'),
            str(snapshot),
            '--browser', 'playwright',
            '--user-id', user_id,
            '--expected-url-substring', expected,
            '--verify-pages', str(verify_pages),
            '--safety-state', str(safety),
            '--url', checked_url,
        ])
        existing_board_names = validate_workbuddy_snapshot_binding(
            load_json(snapshot),
            user_id,
            expected,
        )
        has_existing_boards = bool(existing_board_names)
        return {
            'ok': True,
            'phase': 'board_inventory',
            'run_id': directory.name,
            'run_dir': str(directory),
            'board_snapshot': str(snapshot),
            'existing_board_names': existing_board_names,
            'existing_board_count': len(existing_board_names),
            'classification_required': has_existing_boards,
            'ready_for_execute': False,
            'blockers': [] if has_existing_boards else ['no_existing_boards'],
            'approval_digest': None,
            'next_action': (
                '只允许从 existing_board_names 中为本次真实 note id 选择目标专辑；'
                '没有准确匹配时 target_board 留空，再带完整 classification 调用 prepare。'
                if has_existing_boards
                else '当前账号没有真实已有专辑；停止，不得生成或移动到预设类别。'
            ),
        }

    if not snapshot.is_file():
        raise RuntimeError(
            '缺少本次只读专辑清单；必须先调用不带 classification 的 prepare。'
        )
    existing_board_names = validate_workbuddy_snapshot_binding(
        load_json(snapshot),
        user_id,
        expected,
    )
    if not existing_board_names:
        raise RuntimeError('当前账号没有真实已有专辑；不能生成移动计划。')
    classification_context = write_workbuddy_classification(
        directory,
        classification_rows,
        existing_board_names,
    )
    run_command([
        sys.executable,
        str(ROOT / 'scripts/build_created_boards.py'),
        str(classification),
        str(snapshot),
        str(created),
    ])
    proc = run_command([
        sys.executable,
        str(ROOT / 'scripts/run_reassign_batch.py'),
        str(classification),
        str(report_path),
        '--board-snapshot', str(snapshot),
        '--created-boards', str(created),
        '--safety-state', str(safety),
    ], allow_failure=True)
    if not report_path.is_file():
        details = proc.stderr.strip() or proc.stdout.strip() or f'exit={proc.returncode}'
        raise RuntimeError(f'dry-run 未生成 run_report.json：{details}')
    report = load_json(report_path)
    planned_move_count = sum(
        1
        for row in report.get('processed', [])
        if isinstance(row, dict) and row.get('status') == 'planned'
    )
    digest = None
    if (
        report.get('mode') == 'dry_run'
        and report.get('ready_for_execute') is True
        and report.get('blockers') == []
        and planned_move_count > 0
    ):
        digest = approval_digest(directory, report)
        write_private_json(approval_path, {
            'approval_digest': digest,
            'basis': approval_basis(directory, report),
            'created_at': utc_now(),
        })
    result = {
        'ok': proc.returncode == 0,
        'phase': 'dry_run',
        'run_id': directory.name,
        'run_dir': str(directory),
        'mode': report.get('mode'),
        'ready_for_execute': report.get('ready_for_execute') is True,
        'blockers': report.get('blockers'),
        'warnings': report.get('warnings', []),
        'board_validation_status': report.get('board_validation_status'),
        'membership_validation_status': report.get('membership_validation_status'),
        'planned_move_count': planned_move_count,
        'processed': report.get('processed'),
        'approval_digest': digest,
        'report': str(report_path),
        'next_action': (
            '向用户展示每条“当前专辑 → 目标专辑”和移动上限；用户明确确认后才能调用 execute。'
            if digest
            else (
                '没有可执行移动；展示已在正确专辑和待人工复核的条目，不得调用 execute。'
                if (
                    report.get('mode') == 'dry_run'
                    and report.get('ready_for_execute') is True
                    and report.get('blockers') == []
                )
                else '硬闸门未通过；停止，不得调用 execute。'
            )
        ),
    }
    result.update(classification_context)
    return result


def execute_action(
    run_id: str,
    user_id: str,
    page_url: str,
    expected_url_substring: str,
    approval: str,
    max_moves: int,
) -> Dict[str, Any]:
    require_workbuddy()
    directory = run_dir_for(run_id)
    report_path = directory / 'run_report.json'
    if not report_path.is_file():
        raise RuntimeError('缺少 run_report.json；必须先完成 prepare。')
    report = load_json(report_path)
    if (
        report.get('mode') != 'dry_run'
        or report.get('ready_for_execute') is not True
        or report.get('blockers') != []
    ):
        raise RuntimeError('执行被拒：run_report.json 不是可执行 dry-run。')
    expected_approval = approval_digest(directory, report)
    provided = str(approval or '').strip().lower()
    if not SHA256_RE.fullmatch(provided) or provided != expected_approval:
        raise RuntimeError('执行被拒：approval_digest 不匹配，分类或专辑证据已改变。')
    if not isinstance(max_moves, int) or isinstance(max_moves, bool) or not 1 <= max_moves <= 200:
        raise RuntimeError('max_moves_per_session 必须是用户确认的 1 到 200 整数。')
    if not NOTE_ID_RE.fullmatch(str(user_id or '').strip()):
        raise RuntimeError('user_id 必须是当前账号 URL 中的 24 位十六进制 id。')
    checked_url = validate_xhs_url(page_url, 'custom')
    expected = str(expected_url_substring or '').strip()
    if not expected or expected not in checked_url:
        raise RuntimeError('expected_url_substring 必须是 page_url 中的稳定片段。')
    require_profile_available()

    classification = directory / 'classification.json'
    snapshot = directory / 'board_snapshot.json'
    created = directory / 'created_boards.json'
    safety = directory / 'xhs_safety_state.json'
    proc = run_command([
        sys.executable,
        str(ROOT / 'scripts/run_reassign_batch.py'),
        str(classification),
        str(report_path),
        '--board-snapshot', str(snapshot),
        '--created-boards', str(created),
        '--execute',
        '--browser', 'playwright',
        '--user-id', user_id,
        '--expected-url-substring', expected,
        '--max-moves-per-session', str(max_moves),
        '--safety-state', str(safety),
        '--url', checked_url,
    ], allow_failure=True)
    if not report_path.is_file():
        raise RuntimeError('执行未生成 run_report.json。')
    final_report = load_json(report_path)
    return {
        'ok': proc.returncode == 0 and not final_report.get('errors'),
        'run_id': directory.name,
        'mode': final_report.get('mode'),
        'session_status': final_report.get('session_status'),
        'processed': final_report.get('processed'),
        'errors': final_report.get('errors'),
        'report': str(report_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='WorkBuddy 小红书 Skill 固定工作流桥接器。')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    sub.add_parser('setup')

    login = sub.add_parser('login')
    login.add_argument('--timeout-sec', type=int, default=600)
    login.add_argument('--source', choices=sorted(LOGIN_SOURCES), required=True)

    capture = sub.add_parser('capture')
    capture.add_argument('--run-id', default='')
    capture.add_argument('--source', choices=sorted(ALLOWED_SOURCES), required=True)
    capture.add_argument('--page-url', required=True)
    capture.add_argument('--batch-size', type=int, default=DEFAULT_CAPTURE_BATCH_SIZE)
    capture.add_argument('--pause-minutes', type=int, default=DEFAULT_CAPTURE_PAUSE_MINUTES)

    prepare = sub.add_parser('prepare')
    prepare.add_argument('--run-id', required=True)
    prepare.add_argument('--user-id', required=True)
    prepare.add_argument('--page-url', required=True)
    prepare.add_argument('--expected-url-substring', required=True)
    prepare.add_argument('--verify-pages', type=int, default=100)
    prepare.add_argument('--classification-stdin', action='store_true')

    execute = sub.add_parser('execute')
    execute.add_argument('--run-id', required=True)
    execute.add_argument('--user-id', required=True)
    execute.add_argument('--page-url', required=True)
    execute.add_argument('--expected-url-substring', required=True)
    execute.add_argument('--approval-digest', required=True)
    execute.add_argument('--max-moves-per-session', type=int, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.action == 'status':
            result = status_action()
        elif args.action == 'setup':
            result = setup_action()
        elif args.action == 'login':
            result = login_action(args.timeout_sec, args.source)
        elif args.action == 'capture':
            result = capture_action(
                args.run_id,
                args.source,
                args.page_url,
                args.batch_size,
                args.pause_minutes,
            )
        elif args.action == 'prepare':
            classification_rows = None
            if args.classification_stdin:
                payload = json.load(sys.stdin)
                if not isinstance(payload, dict) or 'classification' not in payload:
                    raise RuntimeError('stdin 必须提供包含 classification 的 JSON 对象。')
                classification_rows = payload['classification']
            result = prepare_action(
                args.run_id,
                args.user_id,
                args.page_url,
                args.expected_url_substring,
                args.verify_pages,
                classification_rows,
            )
        else:
            result = execute_action(
                args.run_id,
                args.user_id,
                args.page_url,
                args.expected_url_substring,
                args.approval_digest,
                args.max_moves_per_session,
            )
    except Exception as exc:
        print(json.dumps({
            'ok': False,
            'error': str(exc),
            'action': args.action,
        }, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
