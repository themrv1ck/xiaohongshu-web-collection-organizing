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
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from extract_visible_items import extract_with_capture_mode
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
    proc = subprocess.run(
        args,
        cwd=str(ROOT),
        env=os.environ.copy(),
        text=True,
        capture_output=True,
    )
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


def login_action(timeout_sec: int) -> Dict[str, Any]:
    require_workbuddy()
    if timeout_sec < 60 or timeout_sec > 900:
        raise RuntimeError('timeout_sec 必须在 60 到 900 秒之间。')
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError('Playwright 尚未安装；先调用 xhs_workbuddy_setup。') from exc

    profile = workbuddy_profile_path()
    last_url = 'https://www.xiaohongshu.com/explore'
    closed_by_user = False
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            headless=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(last_url, wait_until='domcontentloaded', timeout=60000)
            deadline = time.monotonic() + timeout_sec
            while time.monotonic() < deadline:
                pages = context.pages
                if not pages:
                    closed_by_user = True
                    break
                try:
                    last_url = pages[0].url or last_url
                except Exception:
                    closed_by_user = True
                    break
                time.sleep(1)
            if not closed_by_user:
                raise RuntimeError('等待登录超时。请重试，并在登录完成后关闭专用浏览器窗口。')
        finally:
            try:
                context.close()
            except Exception:
                pass
    result = {
        'ok': True,
        'closed_by_user': True,
        'last_url': last_url,
        'profile': str(profile),
        'next_action': '把已登录账号的收藏页或点赞页完整 URL 交给抓取工具。',
        'finished_at': utc_now(),
    }
    write_private_json(plugin_data_dir() / 'last_login.json', result)
    return result


def capture_action(
    run_id: str,
    source: str,
    page_url: str,
    segment_limit: int,
    quick_classify: bool,
) -> Dict[str, Any]:
    require_workbuddy()
    checked_url = validate_xhs_url(page_url, source)
    if not isinstance(segment_limit, int) or isinstance(segment_limit, bool) or not 1 <= segment_limit <= 200:
        raise RuntimeError('segment_limit 必须是 1 到 200 的整数。')
    directory = run_dir_for(run_id, create=True)
    visible = directory / 'visible_items.json'
    manifest = directory / 'crawl_manifest.json'
    safety = resolve_safety_state_path('', visible)
    ensure_active_session(
        safety,
        stage='capture',
        policy={
            'capture_mode': 'passive',
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'workbuddy_exact_url_open': checked_url,
        },
    )
    args = browser_args(checked_url)
    runner = BrowserRunner('playwright', args)
    try:
        result = extract_with_capture_mode(
            runner.eval,
            visible,
            0,
            0,
            manifest,
            source,
            False,
            'passive',
            segment_limit,
            safety,
        )
    finally:
        runner.close()
    result.update({
        'run_id': directory.name,
        'run_dir': str(directory),
        'browser_backend': 'playwright',
        'browser_profile': str(workbuddy_profile_path()),
        'exact_url_opened': checked_url,
    })
    if quick_classify:
        classification = directory / 'classification.json'
        run_command([
            sys.executable,
            str(ROOT / 'scripts/classify_items.py'),
            str(visible),
            str(classification),
            '--skip-ocr',
        ])
        result['classification'] = str(classification)
        result['classification_count'] = len(load_json(classification))
        result['classification_depth'] = 'quick'
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


def prepare_action(
    run_id: str,
    user_id: str,
    page_url: str,
    expected_url_substring: str,
    verify_pages: int,
) -> Dict[str, Any]:
    require_workbuddy()
    directory = run_dir_for(run_id)
    classification = directory / 'classification.json'
    if not classification.is_file():
        raise RuntimeError('缺少 classification.json；必须先完成真实分类，不能用抓取结果冒充分类。')
    if not NOTE_ID_RE.fullmatch(str(user_id or '').strip()):
        raise RuntimeError('user_id 必须是当前账号 URL 中的 24 位十六进制 id。')
    checked_url = validate_xhs_url(page_url, 'custom')
    expected = str(expected_url_substring or '').strip()
    if not expected or expected not in checked_url:
        raise RuntimeError('expected_url_substring 必须是 page_url 中的稳定片段。')
    if not isinstance(verify_pages, int) or isinstance(verify_pages, bool) or not 1 <= verify_pages <= 200:
        raise RuntimeError('verify_pages 必须是 1 到 200 的整数。')

    snapshot = directory / 'board_snapshot.json'
    created = directory / 'created_boards.json'
    report_path = directory / 'run_report.json'
    safety = directory / 'xhs_safety_state.json'
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
    digest = None
    if (
        report.get('mode') == 'dry_run'
        and report.get('ready_for_execute') is True
        and report.get('blockers') == []
    ):
        digest = approval_digest(directory, report)
        write_private_json(directory / 'approval.json', {
            'approval_digest': digest,
            'basis': approval_basis(directory, report),
            'created_at': utc_now(),
        })
    return {
        'ok': proc.returncode == 0,
        'run_id': directory.name,
        'run_dir': str(directory),
        'mode': report.get('mode'),
        'ready_for_execute': report.get('ready_for_execute') is True,
        'blockers': report.get('blockers'),
        'processed': report.get('processed'),
        'approval_digest': digest,
        'report': str(report_path),
        'next_action': (
            '向用户展示每条“当前专辑 → 目标专辑”和移动上限；用户明确确认后才能调用 execute。'
            if digest else
            '硬闸门未通过；停止，不得调用 execute。'
        ),
    }


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

    capture = sub.add_parser('capture')
    capture.add_argument('--run-id', default='')
    capture.add_argument('--source', choices=sorted(ALLOWED_SOURCES), required=True)
    capture.add_argument('--page-url', required=True)
    capture.add_argument('--segment-limit', type=int, default=10)
    capture.add_argument('--quick-classify', action='store_true')

    prepare = sub.add_parser('prepare')
    prepare.add_argument('--run-id', required=True)
    prepare.add_argument('--user-id', required=True)
    prepare.add_argument('--page-url', required=True)
    prepare.add_argument('--expected-url-substring', required=True)
    prepare.add_argument('--verify-pages', type=int, default=100)

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
            result = login_action(args.timeout_sec)
        elif args.action == 'capture':
            result = capture_action(
                args.run_id,
                args.source,
                args.page_url,
                args.segment_limit,
                args.quick_classify,
            )
        elif args.action == 'prepare':
            result = prepare_action(
                args.run_id,
                args.user_id,
                args.page_url,
                args.expected_url_substring,
                args.verify_pages,
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
