#!/usr/bin/env python3
"""WorkBuddy MCP 与现有 Skill 脚本之间的窄接口。

只暴露固定工作流，不提供任意命令执行。所有浏览器阶段都由
workbuddy_runtime.py 强制进入独立的 Playwright 受管浏览器 profile。
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

from extract_visible_items import (
    normalize_source_label,
    read_stable_items_snapshot,
    validate_capture_page,
)
from create_board import (
    build_create_board_job,
    validate_result as validate_create_board_result,
)
from run_reassign_batch import (
    BrowserRunner,
    apply_batch,
    write_binding_blockers,
    initial_report,
    normalize_classification,
    parse_browser_job_id,
    poll_browser_job,
    prepare_write_preflight,
    validate_write_live_binding,
)
from video_content_common import (
    normalize_content_type,
    redact_sensitive_text as redact_content_secret,
)
from workbuddy_runtime import (
    apply_workbuddy_browser_policy,
    find_windows_edge_executable,
    is_workbuddy_host,
    workbuddy_browser_channel,
    workbuddy_profile_path,
    workbuddy_runtime_status,
)
from xhs_ocr_common import (
    detect_ocr_provider,
    file_sha256,
    image_set_sha256,
    image_url_from_value,
    ocr_run_fingerprint,
    resolve_image_files,
    resolve_image_urls,
    reusable_ocr_entry,
    supported_image_bytes,
)
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    halt_if_safety_error,
    mark_security_halted,
    resolve_safety_state_path,
)
from archive_rules import (
    UNCERTAIN_BOARD_NAME,
    apply_uncertain_assignment,
)


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
DEFAULT_DETAIL_REQUEST_INTERVAL_SECONDS = 1.5
MAX_WORKBUDDY_SCROLLS = 5000
TRUSTED_EVIDENCE_SCHEMA = 'xhs_workbuddy_trusted_evidence_v1'
MCP_LAUNCH_ATTESTATION_SCHEMA = 'xhs_workbuddy_launch_attestation_v1'
MCP_LAUNCH_KEY_FD = 3
MCP_EXECUTE_READY_FD = 4
MCP_EXECUTE_COMMIT_FD = 5
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

WORKBUDDY_PERSISTED_ITEM_KEYS = (
    'id', 'title', 'user', 'desc', 'tags', 'card_text',
    'content_type', 'content_type_source', 'first_seen', 'page_index',
    'source_lists', 'source_primary',
)
_MCP_EXECUTE_CAPABILITY = object()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )


def read_mcp_launch_key(fd: int = MCP_LAUNCH_KEY_FD) -> bytes:
    chunks = []
    total = 0
    try:
        while total <= 32:
            chunk = os.read(fd, 33 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as exc:
        raise RuntimeError('mcp_launch_attestation_fd_missing') from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    key = b''.join(chunks)
    if not key:
        raise RuntimeError('mcp_launch_attestation_fd_missing')
    if len(key) != 32:
        raise RuntimeError('mcp_launch_attestation_key_invalid')
    return key


def verify_mcp_launch_attestation(
    action: str,
    args: List[str],
    payload: Any,
    *,
    key_fd: int = MCP_LAUNCH_KEY_FD,
) -> None:
    """Require a per-launch capability delivered outside argv/env/stdin."""
    key = read_mcp_launch_key(key_fd)
    if not isinstance(payload, dict):
        raise RuntimeError('mcp_launch_attestation_missing')
    attestation = payload.get('launch_attestation')
    trusted_evidence = payload.get('trusted_evidence')
    if not isinstance(attestation, dict) or not isinstance(trusted_evidence, dict):
        raise RuntimeError('mcp_launch_attestation_missing')
    if attestation.get('schema') != MCP_LAUNCH_ATTESTATION_SCHEMA:
        raise RuntimeError('mcp_launch_attestation_invalid')
    nonce = str(attestation.get('nonce') or '')
    signature = str(attestation.get('signature') or '')
    if (
        not re.fullmatch(r'[A-Za-z0-9_-]{24}', nonce)
        or not re.fullmatch(r'[A-Za-z0-9_-]{43}', signature)
    ):
        raise RuntimeError('mcp_launch_attestation_invalid')
    try:
        provided = base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4))
    except (ValueError, TypeError) as exc:
        raise RuntimeError('mcp_launch_attestation_invalid') from exc
    basis = {
        'schema': MCP_LAUNCH_ATTESTATION_SCHEMA,
        'nonce': nonce,
        'action': str(action),
        'args': [str(value) for value in args],
        'trusted_evidence': trusted_evidence,
    }
    expected = hmac.new(
        key,
        canonical_json(basis).encode('utf-8'),
        hashlib.sha256,
    ).digest()
    if len(provided) != len(expected) or not hmac.compare_digest(provided, expected):
        raise RuntimeError('mcp_launch_attestation_invalid')


def await_mcp_execute_commit(
    *,
    ready_fd: int = MCP_EXECUTE_READY_FD,
    commit_fd: int = MCP_EXECUTE_COMMIT_FD,
) -> None:
    """Tell MCP local preflight passed, then wait for receipt consumption."""
    try:
        os.write(ready_fd, b'READY\n')
    except OSError as exc:
        raise RuntimeError('mcp_execute_ready_fd_missing') from exc
    finally:
        try:
            os.close(ready_fd)
        except OSError:
            pass
    try:
        decision = os.read(commit_fd, 16)
    except OSError as exc:
        raise RuntimeError('mcp_execute_commit_fd_missing') from exc
    finally:
        try:
            os.close(commit_fd)
        except OSError:
            pass
    if decision != b'COMMIT\n':
        raise RuntimeError('mcp_execute_commit_missing')
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
WORKBUDDY_DETAIL_HREFS_JS = r"""
JSON.stringify(Array.from(
  document.querySelectorAll('section.note-item, .note-item, [data-note-id]')
).map(section => {
  const anchor = section.querySelector('a[href*="/explore/"]');
  if (!anchor) return null;
  const href = anchor.href || anchor.getAttribute('href') || '';
  const match = href.match(/\/explore\/([a-f0-9]{24})(?:[/?#]|$)/i);
  if (!match) return null;
  return {id: match[1], href: new URL(href, window.location.origin).href};
}).filter(Boolean))
"""
WORKBUDDY_DETAIL_STATE_JS = r"""
noteId => {
  const unwrap = value => {
    let current = value;
    for (let index = 0; index < 5; index += 1) {
      if (current && typeof current === 'object' && current.__v_isRef === true) {
        current = current._value !== undefined ? current._value : current._rawValue;
      } else break;
    }
    return current;
  };
  const bodyText = (document.body && document.body.innerText) || '';
  const securityText = `${window.location.origin}${window.location.pathname}\n${bodyText}`.toLowerCase();
  const securityMarkers = [
    '安全验证', '异常访问', '访问异常', '访问过于频繁', '操作过于频繁',
    '请求过于频繁', '网络环境存在风险', '当前环境存在风险', '请完成验证',
    '拖动滑块', 'captcha', 'security verification', 'abnormal access',
    'too many requests'
  ];
  let noteData = null;
  let stateSource = '';
  const setup = unwrap(window.__SETUP_SERVER_STATE__);
  const setupPage = unwrap(setup && setup.LAUNCHER_SSR_STORE_PAGE_DATA);
  const setupNote = unwrap(setupPage && setupPage.noteData);
  if (setupNote && typeof setupNote === 'object') {
    noteData = unwrap(setupNote[noteId]) || setupNote;
    stateSource = 'setup_server_state';
  }
  if (!noteData || typeof noteData !== 'object') {
    const initial = unwrap(window.__INITIAL_STATE__);
    const detailMap = unwrap(initial && initial.note && initial.note.noteDetailMap);
    const entry = unwrap(detailMap && detailMap[noteId]);
    const detailNote = unwrap(entry && (entry.note || entry.noteData));
    if (detailNote && typeof detailNote === 'object') {
      noteData = detailNote;
      stateSource = 'initial_state_note_detail_map';
    }
  }
  const imageUrl = value => {
    const current = unwrap(value);
    if (typeof current === 'string') return current;
    if (!current || typeof current !== 'object') return '';
    for (const key of ['urlDefault', 'url', 'urlPre', 'src']) {
      if (typeof current[key] === 'string' && current[key]) return current[key];
    }
    const infoList = unwrap(current.infoList || current.info_list);
    if (Array.isArray(infoList)) {
      for (const scene of ['WB_DFT', 'WB_PRV', 'WB_WM']) {
        const found = infoList.find(info => info && (
          info.imageScene || info.image_scene
        ) === scene && info.url);
        if (found) return found.url;
      }
      const fallback = infoList.find(info => info && info.url);
      if (fallback) return fallback.url;
    }
    return '';
  };
  const rawImages = unwrap(noteData && noteData.imageList);
  return {
    location: `${window.location.origin}${window.location.pathname}`,
    title: document.title,
    loginRequired: /手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test(bodyText),
    securityMarker: securityMarkers.find(marker => securityText.includes(marker.toLowerCase())) || '',
    stateSource,
    noteData: noteData && typeof noteData === 'object' ? {
      noteId: String(noteData.noteId || noteData.id || ''),
      type: String(noteData.type || ''),
      imageList: Array.isArray(rawImages) ? rawImages.map(imageUrl) : null
    } : null
  };
}
"""


class WorkBuddyDetailError(RuntimeError):
    """Stable detail failure that never includes a transient URL or token."""

    def __init__(self, note_id: str, reason_code: str):
        self.note_id = str(note_id or '')
        self.reason_code = str(reason_code or 'detail_failed')
        super().__init__(f'{self.reason_code}:{self.note_id}')


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_private_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.chmod(0o600)
    os.replace(temp, path)


def write_private_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    try:
        temp.write_bytes(data)
        temp.chmod(0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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


def trusted_artifact_names(stage: str, organizing_depth: str) -> List[str]:
    names = [
        'visible_items.json',
        'crawl_manifest.json',
        'xhs_safety_state.json',
    ]
    if organizing_depth == 'light':
        names.extend(['image_items.json', 'ocr_results.json'])
    if stage in {'inventory', 'plan'}:
        names.append('board_snapshot.json')
    if stage == 'plan':
        names.extend([
            'classification.json',
            'created_boards.json',
            'run_report.json',
            'approval.json',
        ])
    return sorted(names)


def validate_trusted_evidence(
    directory: Path,
    trusted_evidence: Any,
    *,
    expected_stage: str,
    expected_user_id: str,
    expected_page_url: str,
) -> Dict[str, Any]:
    """Recheck the MCP-private receipt hashes before any browser can start."""
    if not isinstance(trusted_evidence, dict):
        raise RuntimeError('trusted_evidence_missing')
    if trusted_evidence.get('schema') != TRUSTED_EVIDENCE_SCHEMA:
        raise RuntimeError('trusted_evidence_schema_invalid')
    if trusted_evidence.get('stage') != expected_stage:
        raise RuntimeError('trusted_evidence_stage_mismatch')
    if str(trusted_evidence.get('run_id') or '') != directory.name:
        raise RuntimeError('trusted_evidence_run_mismatch')
    if not str(trusted_evidence.get('receipt_id') or '').strip():
        raise RuntimeError('trusted_evidence_receipt_missing')
    bindings = trusted_evidence.get('bindings')
    if not isinstance(bindings, dict):
        raise RuntimeError('trusted_evidence_bindings_invalid')
    organizing_depth = str(bindings.get('organizing_depth') or '').strip()
    source = str(bindings.get('source') or '').strip()
    expected_tab = (
        parse_qs(urlparse(str(expected_page_url or '')).query).get('tab')
        or ['']
    )[0].strip().lower()
    source_tab_valid = (
        (source == 'collection' and expected_tab == 'fav')
        or (source == 'liked' and expected_tab in {'liked', 'like'})
    )
    recorded_page_binding = str(bindings.get('page_binding') or '').strip()
    if (
        str(bindings.get('user_id') or '').strip().lower()
        != str(expected_user_id or '').strip().lower()
        or not source_tab_valid
        or recorded_page_binding != page_origin_path(expected_page_url)
        or page_origin_path(recorded_page_binding) != recorded_page_binding
        or organizing_depth not in {'quick', 'light'}
        or source not in LOGIN_SOURCES
    ):
        raise RuntimeError('trusted_evidence_binding_mismatch')
    artifacts = trusted_evidence.get('artifacts')
    expected_names = trusted_artifact_names(expected_stage, organizing_depth)
    if not isinstance(artifacts, dict) or sorted(artifacts) != expected_names:
        raise RuntimeError('trusted_evidence_artifacts_invalid')
    if directory.is_symlink():
        raise RuntimeError('trusted_evidence_run_directory_unsafe')
    resolved_directory = directory.resolve(strict=True)
    runs_root = (plugin_data_dir() / 'runs').resolve(strict=True)
    try:
        resolved_directory.relative_to(runs_root)
    except ValueError as exc:
        raise RuntimeError('trusted_evidence_run_path_escape') from exc
    for name in expected_names:
        expected = artifacts.get(name)
        if not isinstance(expected, dict):
            raise RuntimeError(f'trusted_evidence_artifact_invalid:{name}')
        expected_sha = str(expected.get('sha256') or '').strip().lower()
        expected_size = expected.get('size')
        if (
            not SHA256_RE.fullmatch(expected_sha)
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise RuntimeError(f'trusted_evidence_artifact_invalid:{name}')
        artifact = directory / name
        if artifact.is_symlink() or not artifact.is_file():
            raise RuntimeError(f'trusted_evidence_artifact_unsafe:{name}')
        resolved_artifact = artifact.resolve(strict=True)
        if resolved_artifact.parent != resolved_directory:
            raise RuntimeError(f'trusted_evidence_artifact_path_escape:{name}')
        if artifact.stat().st_size != expected_size or sha256_file(artifact) != expected_sha:
            raise RuntimeError(f'trusted_evidence_changed:{name}')
    return {
        'stage': expected_stage,
        'bindings': dict(bindings),
        'artifacts': dict(artifacts),
    }


def load_trusted_json_snapshot(
    directory: Path,
    trusted_evidence: Dict[str, Any],
    name: str,
) -> Any:
    """Read one already-bound artifact once and verify the exact bytes in memory."""
    record = trusted_evidence.get('artifacts', {}).get(name)
    if not isinstance(record, dict):
        raise RuntimeError(f'trusted_evidence_artifact_invalid:{name}')
    flags = os.O_RDONLY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(directory / name, flags)
    except OSError as exc:
        raise RuntimeError(f'trusted_evidence_artifact_unsafe:{name}') from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f'trusted_evidence_artifact_unsafe:{name}')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            data = handle.read()
    finally:
        os.close(fd)
    expected_size = record.get('size')
    expected_sha = str(record.get('sha256') or '').strip().lower()
    if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_sha:
        raise RuntimeError(f'trusted_evidence_changed:{name}')
    try:
        return json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'trusted_evidence_json_invalid:{name}') from exc


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


def page_origin_path(value: str) -> str:
    parsed = urlparse(str(value or '').strip())
    tab = (parse_qs(parsed.query).get('tab') or [''])[0].strip().lower()
    base = f'{parsed.scheme}://{parsed.netloc}{parsed.path}'
    return f'{base}?tab={tab}' if tab else base


def _is_xhs_https_url(value: str) -> bool:
    parsed = urlparse(str(value or '').strip())
    host = (parsed.hostname or '').lower()
    return bool(
        parsed.scheme == 'https'
        and (host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com'))
    )


def _capture_url_tab(value: str) -> str:
    return (
        (parse_qs(urlparse(str(value or '')).query).get('tab') or [''])[0]
        .strip()
        .lower()
    )


def _halt_capture_binding(
    safety_state: Path,
    *,
    reason_code: str,
    message: str,
    error_code: str,
) -> None:
    mark_security_halted(
        safety_state,
        stage='capture',
        reason_code=reason_code,
        message=message,
    )
    raise SafetyHaltedError(error_code)


def _validate_workbuddy_capture_binding(
    js_eval,
    data: Dict[str, Any],
    expected_page_url: str,
    source: str,
    safety_state: Path,
) -> None:
    """Bind every list snapshot to the authorized page and logged-in account."""
    if source not in LOGIN_SOURCES:
        return

    expected_user_id = profile_user_id(expected_page_url)
    expected_tab = _capture_url_tab(expected_page_url)
    live_url = str(data.get('location') or '').strip()
    live_user_id = profile_user_id(live_url)
    live_tab = _capture_url_tab(live_url)
    if (
        not expected_user_id
        or not expected_tab
        or not _is_xhs_https_url(live_url)
        or live_user_id != expected_user_id
        or live_tab != expected_tab
    ):
        _halt_capture_binding(
            safety_state,
            reason_code='page_binding_lost',
            message='抓取页已离开用户授权的账号或列表范围。',
            error_code='capture_page_binding_lost',
        )

    try:
        own_profile_url = str(js_eval(OWN_PROFILE_LINK_JS) or '').strip()
    except Exception as exc:
        mark_security_halted(
            safety_state,
            stage='capture',
            reason_code='account_binding_unavailable',
            message='无法从前端“我”入口核验当前登录账号。',
        )
        raise SafetyHaltedError(
            'capture_account_binding_unavailable'
        ) from exc
    own_user_id = profile_user_id(own_profile_url)
    if not _is_xhs_https_url(own_profile_url) or not own_user_id:
        _halt_capture_binding(
            safety_state,
            reason_code='account_binding_unavailable',
            message='前端“我”入口未提供可验证的当前账号。',
            error_code='capture_account_binding_unavailable',
        )
    if own_user_id != expected_user_id:
        _halt_capture_binding(
            safety_state,
            reason_code='account_binding_mismatch',
            message='当前登录账号与用户授权的收藏页账号不一致。',
            error_code='capture_account_binding_mismatch',
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


def redact_sensitive_text(value: object) -> str:
    """Remove transient page credentials before an error crosses the bridge."""
    return redact_content_secret(value)[:1000]


def _redact_model_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [_redact_model_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _redact_model_value(item)
            for key, item in value.items()
        }
    return value


def sanitize_workbuddy_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Keep classification metadata while dropping all navigational/image URLs."""
    sanitized = {
        key: _redact_model_value(item.get(key))
        for key in WORKBUDDY_PERSISTED_ITEM_KEYS
        if key in item
    }
    note_id = str(sanitized.get('id') or '').strip()
    if note_id:
        sanitized['id'] = note_id
    return sanitized


def _parse_workbuddy_detail_href_rows(raw: Any) -> List[Dict[str, str]]:
    value = raw
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise WorkBuddyDetailError('', 'detail_href_snapshot_invalid') from exc
    if not isinstance(value, list):
        raise WorkBuddyDetailError('', 'detail_href_snapshot_invalid')
    rows: List[Dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        note_id = str(entry.get('id') or '').strip()
        href = str(entry.get('href') or '').strip()
        if not NOTE_ID_RE.fullmatch(note_id):
            continue
        parsed = urlparse(href)
        host = (parsed.hostname or '').lower()
        path_match = re.fullmatch(
            rf'/explore/{re.escape(note_id)}/?',
            parsed.path,
            flags=re.IGNORECASE,
        )
        if (
            parsed.scheme != 'https'
            or not (host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com'))
            or not path_match
        ):
            continue
        rows.append({'id': note_id, 'href': href})
    return rows


def collect_workbuddy_detail_hrefs(
    js_eval,
    observed_ids: Set[str],
    href_sink: Dict[str, str],
) -> None:
    """Collect raw card hrefs in memory; callers must never persist the sink."""
    for row in _parse_workbuddy_detail_href_rows(js_eval(WORKBUDDY_DETAIL_HREFS_JS)):
        note_id = row['id']
        if note_id in observed_ids:
            href_sink[note_id] = row['href']


def _detail_status_item(
    item: Dict[str, Any],
    status: str,
    reason_code: str = '',
) -> Dict[str, Any]:
    output = sanitize_workbuddy_item(item)
    item_type = normalize_content_type(item.get('content_type'))
    output['image_files'] = []
    output['image_file_sha256'] = []
    output['image_count'] = 0 if item_type == 'video' else None
    output['image_urls_complete'] = False
    output['image_list_source'] = ''
    output['image_enrichment_status'] = status
    output['image_enrichment_error'] = reason_code
    return output


def _validate_workbuddy_detail_snapshot(
    snapshot: Any,
    note_id: str,
    safety_state: Path,
) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise WorkBuddyDetailError(note_id, 'detail_state_invalid')
    security_marker = str(snapshot.get('securityMarker') or '').strip()
    if security_marker:
        mark_security_halted(
            safety_state,
            stage='image_enrichment',
            reason_code='security_challenge',
            message=f'小红书详情页出现安全提示：{security_marker}',
        )
        raise SafetyHaltedError(f'image_enrichment_security_halted:{note_id}')
    if snapshot.get('loginRequired'):
        mark_security_halted(
            safety_state,
            stage='image_enrichment',
            reason_code='login_required',
            message='WorkBuddy 专用浏览器详情页需要重新登录。',
        )
        raise SafetyHaltedError(f'image_enrichment_login_required:{note_id}')

    parsed = urlparse(str(snapshot.get('location') or ''))
    host = (parsed.hostname or '').lower()
    path_matches = bool(re.fullmatch(
        rf'/explore/{re.escape(note_id)}/?',
        parsed.path,
        flags=re.IGNORECASE,
    ))
    if (
        parsed.scheme != 'https'
        or not (host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com'))
        or not path_matches
    ):
        mark_security_halted(
            safety_state,
            stage='image_enrichment',
            reason_code='page_binding_lost',
            message=f'详情页未保持在已授权笔记：{note_id}',
        )
        raise SafetyHaltedError(f'image_enrichment_page_binding_lost:{note_id}')

    state_source = str(snapshot.get('stateSource') or '')
    if state_source not in {'setup_server_state', 'initial_state_note_detail_map'}:
        raise WorkBuddyDetailError(note_id, 'detail_state_missing')
    note_data = snapshot.get('noteData')
    if not isinstance(note_data, dict):
        raise WorkBuddyDetailError(note_id, 'detail_note_data_missing')
    returned_id = str(note_data.get('noteId') or '').strip()
    if returned_id != note_id:
        raise WorkBuddyDetailError(note_id, 'detail_note_id_mismatch')
    detail_type = normalize_content_type(note_data.get('type'))
    if detail_type == 'unknown':
        raise WorkBuddyDetailError(note_id, 'detail_content_type_missing')
    raw_images = note_data.get('imageList')
    if detail_type == 'image':
        if not isinstance(raw_images, list) or not raw_images:
            raise WorkBuddyDetailError(note_id, 'detail_image_list_missing')
        image_urls = [image_url_from_value(value) for value in raw_images]
        if any(not url for url in image_urls):
            raise WorkBuddyDetailError(note_id, 'detail_image_url_missing')
    else:
        image_urls = []
    return {
        'content_type': detail_type,
        'image_urls': image_urls,
        'state_source': state_source,
    }


def _enrich_item_from_workbuddy_detail(
    item: Dict[str, Any],
    detail: Dict[str, Any],
    image_files: Optional[List[str]] = None,
    image_file_sha256: Optional[List[str]] = None,
) -> Dict[str, Any]:
    enriched = sanitize_workbuddy_item(item)
    detail_type = detail['content_type']
    source = detail['state_source']
    enriched['content_type'] = detail_type
    enriched['content_type_source'] = (
        'workbuddy_authenticated_frontend.noteData.type'
    )
    enriched['detail_state_source'] = source
    if detail_type == 'video':
        enriched.update({
            'image_files': [],
            'image_file_sha256': [],
            'image_count': 0,
            'image_urls_complete': False,
            'image_list_source': '',
            'image_enrichment_status': 'not_applicable',
            'image_enrichment_error': '',
        })
        return enriched
    image_files = list(image_files or [])
    image_file_sha256 = list(image_file_sha256 or [])
    if (
        not image_files
        or len(image_files) != len(detail['image_urls'])
        or len(image_file_sha256) != len(image_files)
    ):
        raise WorkBuddyDetailError(str(item.get('id') or ''), 'detail_image_download_incomplete')
    enriched.update({
        'image_files': image_files,
        'image_file_sha256': image_file_sha256,
        'image_count': len(image_files),
        'image_urls_complete': True,
        'image_list_source': (
            'workbuddy_authenticated_frontend.noteData.imageList.local_copy'
        ),
        'image_enrichment_status': 'ok',
        'image_enrichment_error': '',
    })
    return enriched


def _image_suffix(data: bytes) -> str:
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    if len(data) >= 12 and data[4:8] == b'ftyp':
        return '.heic'
    return '.img'


def download_workbuddy_authenticated_images(
    runner: BrowserRunner,
    note_id: str,
    image_urls: List[str],
    directory: Path,
) -> tuple[List[str], List[str]]:
    """Download with the live BrowserContext; never persist signed source URLs."""
    files: List[str] = []
    hashes: List[str] = []
    created: List[Path] = []
    try:
        for index, image_url in enumerate(image_urls):
            parsed = urlparse(str(image_url or '').strip())
            host = (parsed.hostname or '').lower()
            if (
                parsed.scheme != 'https'
                or not (
                    host == 'xiaohongshu.com'
                    or host.endswith('.xiaohongshu.com')
                    or host == 'xhscdn.com'
                    or host.endswith('.xhscdn.com')
                )
            ):
                raise WorkBuddyDetailError(note_id, 'detail_image_host_invalid')
            response = runner.context.request.get(image_url, timeout=60000)
            try:
                if not response.ok:
                    raise WorkBuddyDetailError(note_id, 'detail_image_download_failed')
                data = response.body()
            finally:
                try:
                    response.dispose()
                except Exception:
                    pass
            if not supported_image_bytes(data):
                raise WorkBuddyDetailError(note_id, 'detail_image_bytes_invalid')
            digest = hashlib.sha256(data).hexdigest()
            relative = Path('authenticated_images') / (
                f'{note_id}-{index:03d}-{digest[:12]}{_image_suffix(data)}'
            )
            destination = directory / relative
            if destination.exists():
                raise WorkBuddyDetailError(note_id, 'detail_image_file_exists')
            write_private_bytes(destination, data)
            created.append(destination)
            files.append(relative.as_posix())
            hashes.append(digest)
    except WorkBuddyDetailError:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        for path in created:
            path.unlink(missing_ok=True)
        raise WorkBuddyDetailError(
            note_id,
            'detail_image_download_failed',
        ) from exc
    return files, hashes


def enrich_workbuddy_image_items(
    runner: BrowserRunner,
    items: List[Dict[str, Any]],
    detail_hrefs: Dict[str, str],
    batch_size: int,
    pause_minutes: int,
    output: Path,
    safety_state: Path,
    *,
    request_interval: float = DEFAULT_DETAIL_REQUEST_INTERVAL_SECONDS,
) -> Dict[str, Any]:
    """Use the capture context for authenticated detail reads, never a new profile."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CAPTURE_BATCH_SIZE:
        raise RuntimeError('图文详情 batch_size 必须是 1 到 200 的整数。')
    if not isinstance(pause_minutes, int) or isinstance(pause_minutes, bool) or pause_minutes < 1:
        raise RuntimeError('图文详情 pause_minutes 必须是大于 0 的整数。')
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise RuntimeError('visible_items.json 必须是对象数组。')
    note_ids = [str(item.get('id') or '').strip() for item in items]
    if any(not NOTE_ID_RE.fullmatch(note_id) for note_id in note_ids):
        raise RuntimeError('visible_items.json 含非法 note id，已停止详情补齐。')
    if len(note_ids) != len(set(note_ids)):
        raise RuntimeError('visible_items.json 含重复 note id，已停止详情补齐。')

    # 列表 content_type 只是 observed 线索；必须逐条用详情 noteData.type 确认，
    # 否则被列表误标为 video 的真实图文会绕过 OCR。
    candidates = list(items)
    candidate_ids = [str(item['id']) for item in candidates]
    missing_hrefs = [note_id for note_id in candidate_ids if note_id not in detail_hrefs]
    if missing_hrefs:
        missing_set = set(missing_hrefs)
        candidate_set = set(candidate_ids)
        rows = []
        for item in items:
            note_id = str(item.get('id') or '')
            if note_id in missing_set:
                rows.append(_detail_status_item(item, 'error', 'detail_href_missing'))
            elif note_id in candidate_set:
                rows.append(_detail_status_item(
                    item,
                    'not_requested_after_failure',
                    'detail_href_set_incomplete',
                ))
            else:
                rows.append(_detail_status_item(item, 'not_applicable'))
        write_private_json(output, rows)
        return {
            'requested': 0,
            'succeeded': 0,
            'failed': len(missing_hrefs),
            'ready_for_ocr': False,
            'blockers': ['detail_href_set_incomplete'],
        }

    ensure_active_session(
        safety_state,
        stage='image_enrichment',
        policy={
            'controller': 'workbuddy_plugin',
            'browser_backend': 'playwright',
            'same_capture_context': True,
            'detail_requests_enabled': True,
            'detail_batch_size': batch_size,
            'detail_pause_minutes': pause_minutes,
            'detail_group_count': (
                (len(candidates) + batch_size - 1) // batch_size
                if candidates else 0
            ),
            'raw_detail_hrefs_persisted': False,
            'auto_retry': False,
        },
    )
    results_by_id: Dict[str, Dict[str, Any]] = {}
    requested = 0
    succeeded = 0
    failed = 0
    blocker = ''
    detail_page = runner.context.new_page()
    try:
        for index, item in enumerate(candidates):
            note_id = str(item['id'])
            requested += 1
            try:
                detail_page.goto(
                    detail_hrefs[note_id],
                    wait_until='domcontentloaded',
                    timeout=60000,
                )
                snapshot = detail_page.evaluate(WORKBUDDY_DETAIL_STATE_JS, note_id)
                detail = _validate_workbuddy_detail_snapshot(
                    snapshot,
                    note_id,
                    safety_state,
                )
                if detail['content_type'] == 'image':
                    image_files, image_hashes = download_workbuddy_authenticated_images(
                        runner,
                        note_id,
                        list(detail['image_urls']),
                        output.parent,
                    )
                else:
                    image_files, image_hashes = [], []
                results_by_id[note_id] = _enrich_item_from_workbuddy_detail(
                    item,
                    detail,
                    image_files,
                    image_hashes,
                )
                detail['image_urls'].clear()
                succeeded += 1
            except SafetyHaltedError:
                results_by_id[note_id] = _detail_status_item(
                    item,
                    'security_blocked',
                    'security_halted',
                )
                for remaining in candidates[index + 1:]:
                    remaining_id = str(remaining['id'])
                    results_by_id[remaining_id] = _detail_status_item(
                        remaining,
                        'not_requested_after_security_block',
                        'security_halted',
                    )
                write_private_json(
                    output,
                    [results_by_id[str(row['id'])] for row in items],
                )
                raise
            except WorkBuddyDetailError as exc:
                results_by_id[note_id] = _detail_status_item(
                    item,
                    'error',
                    exc.reason_code,
                )
                failed += 1
                blocker = exc.reason_code
                for remaining in candidates[index + 1:]:
                    remaining_id = str(remaining['id'])
                    results_by_id[remaining_id] = _detail_status_item(
                        remaining,
                        'not_requested_after_failure',
                        blocker,
                    )
                break
            except Exception as exc:
                safe_error = redact_sensitive_text(exc)
                if halt_if_safety_error(
                    safety_state,
                    stage='image_enrichment',
                    error=safe_error,
                ):
                    results_by_id[note_id] = _detail_status_item(
                        item,
                        'security_blocked',
                        'security_halted',
                    )
                    for remaining in candidates[index + 1:]:
                        remaining_id = str(remaining['id'])
                        results_by_id[remaining_id] = _detail_status_item(
                            remaining,
                            'not_requested_after_security_block',
                            'security_halted',
                        )
                    write_private_json(
                        output,
                        [results_by_id[str(row['id'])] for row in items],
                    )
                    raise SafetyHaltedError(
                        f'image_enrichment_security_halted:{note_id}'
                    ) from None
                results_by_id[note_id] = _detail_status_item(
                    item,
                    'error',
                    'detail_navigation_failed',
                )
                failed += 1
                blocker = 'detail_navigation_failed'
                for remaining in candidates[index + 1:]:
                    remaining_id = str(remaining['id'])
                    results_by_id[remaining_id] = _detail_status_item(
                        remaining,
                        'not_requested_after_failure',
                        blocker,
                    )
                break
            write_private_json(
                output,
                [
                    results_by_id.get(
                        str(row['id']),
                        _detail_status_item(row, 'pending'),
                    )
                    for row in items
                ],
            )
            if index + 1 < len(candidates):
                if (index + 1) % batch_size == 0:
                    time.sleep(pause_minutes * 60)
                elif request_interval > 0:
                    time.sleep(request_interval)
    finally:
        try:
            detail_page.close()
        except Exception:
            pass

    rows = [
        results_by_id.get(
            str(item['id']),
            _detail_status_item(item, 'not_requested_after_failure', blocker),
        )
        for item in items
    ]
    write_private_json(output, rows)
    image_rows = [
        row for row in rows
        if normalize_content_type(row.get('content_type')) == 'image'
    ]
    complete = all(
        row.get('image_enrichment_status') == 'ok'
        and row.get('image_urls_complete') is True
        and isinstance(row.get('image_count'), int)
        and row.get('image_count') == len(resolve_image_files(row))
        and row.get('image_count') > 0
        for row in image_rows
    )
    ready_for_ocr = not blocker and complete and succeeded == len(candidates)
    return {
        'requested': requested,
        'succeeded': succeeded,
        'failed': failed,
        'detail_group_count': (
            (len(candidates) + batch_size - 1) // batch_size
            if candidates else 0
        ),
        'ready_for_ocr': ready_for_ocr,
        'blockers': [blocker] if blocker else ([] if ready_for_ocr else ['image_set_incomplete']),
    }


def validate_workbuddy_local_image_contract(
    row: Dict[str, Any],
    directory: Path,
) -> tuple[List[str], List[str]]:
    files = resolve_image_files(row)
    hashes = row.get('image_file_sha256')
    if not isinstance(hashes, list) or len(hashes) != len(files) or not files:
        raise RuntimeError(f'图文本地图片清单无效：{row.get("id") or "unknown"}')
    references: List[str] = []
    normalized_hashes: List[str] = []
    root = directory.resolve(strict=True)
    image_root = (directory / 'authenticated_images').resolve(strict=True)
    image_root.relative_to(root)
    for relative_value, expected_value in zip(files, hashes):
        relative = Path(relative_value)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('图文本地图片路径越界。')
        unresolved = directory / relative
        if unresolved.is_symlink() or not unresolved.is_file():
            raise RuntimeError('图文本地图片缺失或为符号链接。')
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(image_root)
        expected = str(expected_value or '').strip().lower()
        if not SHA256_RE.fullmatch(expected) or file_sha256(resolved) != expected:
            raise RuntimeError('图文本地图片哈希不一致。')
        references.append(f'sha256:{expected}')
        normalized_hashes.append(expected)
    return references, normalized_hashes


def run_workbuddy_ocr(directory: Path, image_items: Path) -> Dict[str, Any]:
    ocr_results = directory / 'ocr_results.json'
    provider = detect_ocr_provider('auto')
    tesseract_lang = 'chi_sim'
    expected_fingerprint = ocr_run_fingerprint(
        provider,
        tesseract_lang,
        ROOT / 'scripts' / 'ocr_image.swift.txt',
    )
    if provider == 'none':
        return {
            'ocr_results': str(ocr_results),
            'ocr_ok': 0,
            'ocr_failed': sum(
                1
                for row in load_json(image_items)
                if normalize_content_type(row.get('content_type')) == 'image'
            ),
            'ready_for_classification': False,
            'blockers': ['ocr_provider_unavailable'],
            'ocr_provider': provider,
            'ocr_tesseract_lang': tesseract_lang,
            'ocr_expected_fingerprint': expected_fingerprint,
        }
    proc = run_command(
        [
            sys.executable,
            str(ROOT / 'scripts' / 'ocr_note_images.py'),
            str(image_items),
            str(ocr_results),
            '--provider',
            provider,
        ],
        allow_failure=True,
    )
    image_rows = [
        row for row in load_json(image_items)
        if normalize_content_type(row.get('content_type')) == 'image'
    ]
    expected_ids = [str(row.get('id') or '') for row in image_rows]
    expected_counts = {
        str(row.get('id') or ''): row.get('image_count')
        for row in image_rows
    }
    expected_sources: Dict[str, List[str]] = {}
    expected_source_hashes: Dict[str, List[str]] = {}
    try:
        for row in image_rows:
            note_id = str(row.get('id') or '')
            references, hashes = validate_workbuddy_local_image_contract(
                row,
                directory,
            )
            expected_sources[note_id] = references
            expected_source_hashes[note_id] = hashes
    except Exception:
        return {
            'ocr_results': str(ocr_results),
            'ocr_ok': 0,
            'ocr_failed': len(expected_ids),
            'ready_for_classification': False,
            'blockers': ['authenticated_image_contract_invalid'],
            'ocr_provider': provider,
            'ocr_tesseract_lang': tesseract_lang,
            'ocr_expected_fingerprint': expected_fingerprint,
        }
    if proc.returncode != 0 or not ocr_results.is_file():
        return {
            'ocr_results': str(ocr_results),
            'ocr_ok': 0,
            'ocr_failed': len(expected_ids),
            'ready_for_classification': False,
            'blockers': ['ocr_process_failed'],
            'ocr_provider': provider,
            'ocr_tesseract_lang': tesseract_lang,
            'ocr_expected_fingerprint': expected_fingerprint,
        }
    try:
        rows = load_json(ocr_results)
    except Exception:
        rows = None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return {
            'ocr_results': str(ocr_results),
            'ocr_ok': 0,
            'ocr_failed': len(expected_ids),
            'ready_for_classification': False,
            'blockers': ['ocr_results_invalid'],
            'ocr_provider': provider,
            'ocr_tesseract_lang': tesseract_lang,
            'ocr_expected_fingerprint': expected_fingerprint,
        }
    result_ids = [str(row.get('id') or '') for row in rows]
    valid_by_id = {
        str(row.get('id') or ''): row
        for row in rows
        if isinstance(row, dict) and row.get('id')
    }
    valid_ids = {
        note_id
        for note_id in expected_ids
        if note_id in valid_by_id
        and expected_counts[note_id] == len(expected_sources[note_id])
        and reusable_ocr_entry(
            valid_by_id[note_id],
            expected_sources[note_id],
            image_set_sha256(expected_sources[note_id]),
            expected_fingerprint,
            expected_source_hashes[note_id],
        )
    }
    valid = (
        len(result_ids) == len(set(result_ids))
        and result_ids == expected_ids
        and valid_ids == set(expected_ids)
    )
    ocr_ok = len(valid_ids)
    return {
        'ocr_results': str(ocr_results),
        'ocr_ok': ocr_ok,
        'ocr_failed': len(expected_ids) - ocr_ok,
        'ready_for_classification': valid,
        'blockers': [] if valid else ['ocr_results_incomplete'],
        'ocr_provider': provider,
        'ocr_tesseract_lang': tesseract_lang,
        'ocr_expected_fingerprint': expected_fingerprint,
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
        'location': page_origin_path(str(data.get('location') or '')),
        'title': redact_sensitive_text(data.get('title')),
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
    detail_href_sink: Optional[Dict[str, str]] = None,
    *,
    expected_page_url: str,
) -> Dict[str, Any]:
    """Read a WorkBuddy list in durable groups without reopening the browser."""
    if batch_size != DEFAULT_CAPTURE_BATCH_SIZE:
        raise RuntimeError('WorkBuddy 每组固定读取 200 条。')
    if pause_minutes != DEFAULT_CAPTURE_PAUSE_MINUTES:
        raise RuntimeError('WorkBuddy 非末组之间固定暂停 3 分钟。')

    directory = Path(directory)
    safety_state = Path(safety_state)
    source_label = normalize_source_label(source)
    checked_expected_page_url = validate_xhs_url(expected_page_url, source)
    if source in LOGIN_SOURCES and not profile_user_id(checked_expected_page_url):
        raise RuntimeError('收藏/点赞抓取必须绑定已授权的个人主页。')
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
    note_positions: Dict[str, int] = {}
    position_contract_blockers: Set[str] = set()
    pending: List[Dict[str, Any]] = []
    committed: List[Dict[str, Any]] = []
    segment_manifests: List[Dict[str, Any]] = []
    bottom_signature = None
    bottom_stable_reads = 0
    last_page: Dict[str, Any] = {}
    crawl_complete = False
    declared_count_missing = False
    observed_declared_counts: Set[int] = set()

    js_eval(WORKBUDDY_RESET_TOP_JS)
    for scroll_index in range(MAX_WORKBUDDY_SCROLLS + 1):
        data, stability_checks = read_stable_items_snapshot(js_eval)
        validate_capture_page(data, safety_state)
        _validate_workbuddy_capture_binding(
            js_eval,
            data,
            checked_expected_page_url,
            source,
            safety_state,
        )
        observed_declared = data.get('declaredItemCount')
        if (
            isinstance(observed_declared, int)
            and not isinstance(observed_declared, bool)
            and observed_declared >= 0
        ):
            observed_declared_counts.add(observed_declared)
        else:
            declared_count_missing = True
        observed = [
            item for item in list(data.get('items') or [])
            if isinstance(item, dict) and item.get('id')
        ]
        if detail_href_sink is not None:
            collect_workbuddy_detail_hrefs(
                js_eval,
                {str(item.get('id') or '') for item in observed},
                detail_href_sink,
            )
        observed.sort(key=lambda item: (
            item.get('page_index')
            if isinstance(item.get('page_index'), int)
            else MAX_WORKBUDDY_SCROLLS * 1000
        ))
        for item in observed:
            note_id = str(item.get('id') or '').strip()
            page_index = item.get('page_index')
            if not NOTE_ID_RE.fullmatch(note_id):
                raise RuntimeError('页面条目缺少合法 note id，已停止以避免错位。')
            if (
                not isinstance(page_index, int)
                or isinstance(page_index, bool)
                or page_index < 0
            ):
                position_contract_blockers.add('invalid_page_positions')
            else:
                previous_id = seen_positions.get(page_index)
                if previous_id and previous_id != note_id:
                    position_contract_blockers.add('page_position_conflict')
                previous_position = note_positions.get(note_id)
                if previous_position is not None and previous_position != page_index:
                    position_contract_blockers.add('note_position_conflict')
                if not previous_id:
                    seen_positions[page_index] = note_id
                if previous_position is None:
                    note_positions[note_id] = page_index
            safe_item = sanitize_workbuddy_item(item)
            if note_id in seen:
                current = seen[note_id]
                for key, value in safe_item.items():
                    if (
                        value not in (None, '', [], {})
                        and current.get(key) in (None, '', [], {})
                    ):
                        current[key] = value
                continue
            row = safe_item
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
        crawl_complete = bool(
            last_page['at_bottom']
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

    warnings = []
    blockers: List[str] = []
    for code in sorted(position_contract_blockers):
        warnings.append({'code': code})
        blockers.append(code)
    declared = last_page.get('declaredItemCount')
    declared_available = bool(
        not declared_count_missing
        and isinstance(declared, int)
        and not isinstance(declared, bool)
        and declared >= 0
        and observed_declared_counts == {declared}
    )
    if not declared_available:
        warnings.append({'code': 'declared_count_unavailable'})
        blockers.append('declared_count_unavailable')
    elif declared != len(committed):
        warnings.append({
            'code': 'declared_count_mismatch',
            'declared_count': declared,
            'accessible_count': len(committed),
        })
        blockers.append('declared_count_mismatch')
    if declared_available:
        expected_positions = set(range(declared))
        actual_positions = set(seen_positions)
        missing_positions = sorted(expected_positions - actual_positions)
        unexpected_positions = sorted(actual_positions - expected_positions)
        if missing_positions:
            warnings.append({
                'code': 'missing_page_positions',
                'count': len(missing_positions),
                'sample': missing_positions[:20],
            })
            blockers.append('missing_page_positions')
        if unexpected_positions:
            warnings.append({
                'code': 'unexpected_page_positions',
                'count': len(unexpected_positions),
                'sample': unexpected_positions[:20],
            })
            blockers.append('unexpected_page_positions')

    coverage_complete = not blockers
    stopped_reason = (
        'collection_complete'
        if coverage_complete
        else 'capture_coverage_incomplete'
    )
    if segment_manifests:
        final_segment = dict(segment_manifests[-1])
        final_segment['crawl_complete'] = coverage_complete
        final_segment['stopped_reason'] = stopped_reason
        final_segment['blockers'] = blockers
        write_private_json(Path(final_segment['manifest']), final_segment)
        segment_manifests[-1] = final_segment
    progress_path = directory / 'capture_progress.json'
    progress = load_json(progress_path)
    progress.update({
        'crawl_complete': coverage_complete,
        'ready_for_classification': coverage_complete,
        'stopped_reason': stopped_reason,
        'blockers': blockers,
        'warnings': warnings,
        'updated_at': utc_now(),
    })
    write_private_json(progress_path, progress)

    aggregate_manifest = directory / 'crawl_manifest.json'
    write_private_json(aggregate_manifest, {
        'capture_mode': 'workbuddy_segmented',
        'source': source_label,
        'batch_size': batch_size,
        'pause_minutes': pause_minutes,
        'item_count': len(committed),
        'segment_count': len(segment_manifests),
        'crawl_complete': coverage_complete,
        'ready_for_classification': coverage_complete,
        'stopped_reason': stopped_reason,
        'blockers': blockers,
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
        'crawl_complete': coverage_complete,
        'ready_for_classification': coverage_complete,
        'stopped_reason': stopped_reason,
        'blockers': blockers,
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


def execute_planned_board_creations(
    runner: BrowserRunner,
    planned_boards: List[Dict[str, Any]],
    execute_args: argparse.Namespace,
    report: Dict[str, Any],
) -> None:
    results: List[Dict[str, Any]] = []
    for planned in planned_boards:
        raw_result: Any = None
        try:
            validate_write_live_binding(runner, execute_args)
            create_args = argparse.Namespace(
                name=planned['name'],
                desc='',
                privacy=planned['privacy'],
                execute=True,
                user_id=execute_args.user_id,
                verify_pages=execute_args.verify_pages,
                timeout_sec=execute_args.timeout_sec,
                arc_tab_marker='',
                arc_expected_url_substring=execute_args.expected_url_substring,
            )
            run_id = parse_browser_job_id(
                runner.run_javascript(build_create_board_job(create_args))
            )
            raw_result = poll_browser_job(
                runner, run_id, execute_args.timeout_sec
            )
            result = validate_create_board_result(raw_result, True)
            validate_write_live_binding(runner, execute_args)
        except Exception as exc:
            prior_or_current_write = bool(results) or bool(
                isinstance(raw_result, dict)
                and raw_result.get('writePerformed') is True
            )
            if prior_or_current_write and not classify_safety_error(exc):
                raise RuntimeError(
                    'HIGH_RISK_STATE_UNCERTAIN: board creation batch stopped '
                    'after an account write; no rollback attempted; ' + str(exc)
                ) from exc
            raise
        results.append({
            'name': planned['name'],
            'privacy': planned['privacy'],
            'status': result['status'],
            'board': result['board'],
            'empty_board_verified': result.get('emptyBoardVerified') is True,
            'write_performed': result.get('writePerformed') is True,
        })
        report['board_creations'] = list(results)
    report['board_creations'] = results


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
            'browser_channel': workbuddy_browser_channel(),
            'chromium_ready': False,
            'browser_download_required': (
                workbuddy_browser_channel() != 'msedge'
            ),
            'install_required': True,
        }
    channel = workbuddy_browser_channel()
    if channel == 'msedge':
        edge = find_windows_edge_executable()
        return {
            'python_package_ready': True,
            'browser_channel': channel,
            'edge_ready': edge is not None,
            'edge_executable': str(edge) if edge else None,
            'browser_download_required': False,
            'install_required': edge is None,
        }
    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            ready = executable.is_file()
            return {
                'python_package_ready': True,
                'browser_channel': channel,
                'chromium_ready': ready,
                'chromium_executable': str(executable),
                'browser_download_required': not ready,
                'install_required': not ready,
            }
    except Exception as exc:
        return {
            'python_package_ready': True,
            'browser_channel': channel,
            'chromium_ready': False,
            'browser_download_required': True,
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
    channel = workbuddy_browser_channel()
    if channel == 'msedge':
        edge = find_windows_edge_executable()
        if edge is None:
            raise RuntimeError(
                '未检测到 Microsoft Edge；请先安装系统 Edge，再重新运行 setup。'
            )
    else:
        run_command([str(python), '-m', 'playwright', 'install', 'chromium'])
    marker = {
        'installed_at': utc_now(),
        'python': str(python),
        'requirements_sha256': sha256_file(requirements),
        'browser_channel': channel,
        'browser_executable': str(edge) if channel == 'msedge' else None,
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
    managed_args = browser_args()
    launch_args: Dict[str, Any] = {'headless': False}
    if managed_args.channel != 'chromium':
        launch_args['channel'] = managed_args.channel
    selected_url = ''
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            **launch_args,
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
    organizing_depth: str,
    report_requested: bool = False,
) -> Dict[str, Any]:
    require_workbuddy()
    organizing_depth = str(organizing_depth or '').strip().lower()
    if organizing_depth not in {'quick', 'light'}:
        raise RuntimeError(
            'WorkBuddy Plugin 当前只支持 quick 或 light；'
            'deep 需要视频语音和完整时轴画面证据，尚未接入，已在打开浏览器前停止。'
        )
    image_ocr_enabled = organizing_depth == 'light'
    if not isinstance(report_requested, bool):
        raise RuntimeError('report_requested 必须是布尔值。')
    if organizing_depth == 'quick' and report_requested:
        raise RuntimeError('快速整理不生成专辑报告；只有轻度或深度整理会询问。')
    supplied_url = validate_xhs_url(page_url, source)
    captured_user_id = profile_user_id(supplied_url)
    if source in LOGIN_SOURCES and not captured_user_id:
        raise RuntimeError('WorkBuddy 收藏/点赞抓取必须绑定当前账号的 profile URL。')
    checked_url = target_page_url(captured_user_id, source)
    if batch_size != DEFAULT_CAPTURE_BATCH_SIZE:
        raise RuntimeError('WorkBuddy 每组固定读取 200 条。')
    if pause_minutes != DEFAULT_CAPTURE_PAUSE_MINUTES:
        raise RuntimeError('WorkBuddy 非末组之间固定暂停 3 分钟。')
    require_profile_available()
    args = browser_args(checked_url)
    runner = None
    detail_hrefs: Dict[str, str] = {}
    detail_result = {
        'requested': 0,
        'succeeded': 0,
        'failed': 0,
        'detail_group_count': 0,
        'ready_for_ocr': False,
        'blockers': [],
    }
    capture_ready_for_classification = False
    capture_blockers: List[str] = []
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def cancel_capture(_signum, _frame):
        raise RuntimeError('WorkBuddy 抓取已取消；正在关闭本轮专用浏览器。')

    signal.signal(signal.SIGTERM, cancel_capture)
    try:
        runner = BrowserRunner('playwright', args)
        directory = run_dir_for(run_id, create=True)
        visible = directory / 'visible_items.json'
        image_items = directory / 'image_items.json'
        ocr_results = directory / 'ocr_results.json'
        if image_ocr_enabled and (image_items.exists() or ocr_results.exists()):
            raise RuntimeError('本次 OCR 产物已存在，拒绝覆盖；请使用新的 run_id。')
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
                'organizing_depth': organizing_depth,
                'image_ocr_enabled': bool(image_ocr_enabled),
                'report_requested': report_requested,
                'detail_batch_size': batch_size if image_ocr_enabled else 0,
                'detail_pause_minutes': pause_minutes if image_ocr_enabled else 0,
            },
        )
        result = capture_workbuddy_groups(
            runner.run_javascript,
            directory,
            source,
            batch_size,
            pause_minutes,
            safety,
            detail_hrefs if image_ocr_enabled else None,
            expected_page_url=checked_url,
        )
        capture_ready_for_classification = (
            result.get('ready_for_classification') is True
        )
        capture_blockers = list(result.get('blockers') or [])
        quality = metadata_quality(load_json(visible))
        result['metadata_quality'] = quality
        if quality['item_count'] > 0 and quality['usable_item_count'] == 0:
            raise RuntimeError(
                '抓取到了笔记 ID，但标题、作者和卡片文字全部为空；'
                '页面结构已变化，已停止分类，不能生成空的整理方案。'
            )
        if image_ocr_enabled and capture_ready_for_classification:
            detail_result = enrich_workbuddy_image_items(
                runner,
                load_json(visible),
                detail_hrefs,
                batch_size,
                pause_minutes,
                image_items,
                safety,
            )
    finally:
        detail_hrefs.clear()
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
        'browser_closed_by_tool': True,
    })
    if not capture_ready_for_classification:
        ocr_result = {
            'ocr_results': None,
            'ocr_ok': 0,
            'ocr_failed': 0,
            'ready_for_classification': False,
            'blockers': capture_blockers,
            'ocr_provider': None,
            'ocr_tesseract_lang': None,
            'ocr_expected_fingerprint': None,
        }
    elif image_ocr_enabled and detail_result['ready_for_ocr']:
        ocr_result = run_workbuddy_ocr(directory, image_items)
    elif image_ocr_enabled:
        ocr_result = {
            'ocr_results': str(ocr_results),
            'ocr_ok': 0,
            'ocr_failed': 0,
            'ready_for_classification': False,
            'blockers': list(detail_result['blockers']),
            'ocr_provider': None,
            'ocr_tesseract_lang': None,
            'ocr_expected_fingerprint': None,
        }
    else:
        ocr_result = {
            'ocr_results': None,
            'ocr_ok': 0,
            'ocr_failed': 0,
            'ready_for_classification': True,
            'blockers': [],
            'ocr_provider': None,
            'ocr_tesseract_lang': None,
            'ocr_expected_fingerprint': None,
        }
    classification_blockers = list(dict.fromkeys(
        capture_blockers + list(ocr_result['blockers'])
    ))
    ready_for_classification = bool(
        capture_ready_for_classification
        and ocr_result['ready_for_classification']
        and not classification_blockers
    )
    result.update({
        'image_ocr_enabled': bool(image_ocr_enabled),
        'report_requested': report_requested,
        'organizing_depth': organizing_depth,
        'image_items': (
            str(image_items)
            if image_ocr_enabled and image_items.is_file()
            else None
        ),
        'ocr_results': ocr_result['ocr_results'],
        'requested': detail_result['requested'],
        'succeeded': detail_result['succeeded'],
        'failed': detail_result['failed'],
        'ocr_ok': ocr_result['ocr_ok'],
        'ocr_failed': ocr_result['ocr_failed'],
        'ready_for_classification': ready_for_classification,
        'capture_blockers': capture_blockers,
        'image_ocr_blockers': list(ocr_result['blockers']),
        'blockers': classification_blockers,
    })
    result['classification_required'] = True
    if result['ready_for_classification']:
        result['next_action'] = (
            '先调用不带 classification 的 prepare，只读取得本次账号真实已有专辑；'
            '再根据本次 OCR 证据（若已开启）和该专辑清单逐条分类。'
        )
    else:
        result['next_action'] = (
            '抓取覆盖、图文详情补齐或 OCR 未完整通过，必须停止；'
            '不得改用封面 OCR 或元数据分类继续。'
        )
    manifest_path = directory / 'crawl_manifest.json'
    if not manifest_path.is_file():
        raise RuntimeError('抓取未生成 crawl_manifest.json；不能建立分类证据。')
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise RuntimeError('crawl_manifest.json 必须是对象。')
    manifest.update({
        'capture_source': source,
        'capture_user_id': captured_user_id,
        'capture_page_binding': page_origin_path(checked_url),
        'capture_tab': (parse_qs(urlparse(checked_url).query).get('tab') or [''])[0],
        'visible_items': str(visible),
        'visible_items_sha256': sha256_file(visible),
        'organizing_depth': organizing_depth,
        'image_ocr_enabled': bool(image_ocr_enabled),
        'detail_batch_size': batch_size if image_ocr_enabled else 0,
        'detail_pause_minutes': pause_minutes if image_ocr_enabled else 0,
        'detail_group_count': detail_result.get('detail_group_count', 0),
        'image_items': (
            str(image_items)
            if image_ocr_enabled and image_items.is_file()
            else None
        ),
        'image_items_sha256': (
            sha256_file(image_items)
            if image_ocr_enabled and image_items.is_file()
            else None
        ),
        'ocr_results': ocr_result['ocr_results'],
        'ocr_results_sha256': (
            sha256_file(ocr_results)
            if image_ocr_enabled and ocr_results.is_file()
            else None
        ),
        'ocr_provider': ocr_result.get('ocr_provider'),
        'ocr_tesseract_lang': ocr_result.get('ocr_tesseract_lang'),
        'ocr_expected_fingerprint': ocr_result.get('ocr_expected_fingerprint'),
        'ready_for_classification': result['ready_for_classification'],
        'capture_blockers': result['capture_blockers'],
        'image_ocr_blockers': result['image_ocr_blockers'],
        'classification_blockers': result['blockers'],
        'evidence_completed_at': utc_now(),
    })
    write_private_json(manifest_path, manifest)
    result['manifest'] = str(manifest_path)
    return result


def validate_workbuddy_capture_evidence(
    directory: Path,
    *,
    expected_user_id: str = '',
    expected_page_url: str = '',
    expected_url_substring: str = '',
) -> Dict[str, Any]:
    """Bind classification to one complete WorkBuddy capture and OCR decision."""
    directory = Path(directory)
    visible_path = directory / 'visible_items.json'
    manifest_path = directory / 'crawl_manifest.json'
    if not visible_path.is_file() or not manifest_path.is_file():
        raise RuntimeError(
            '缺少 visible_items.json 或 crawl_manifest.json；请重新完成本次 WorkBuddy 抓取。'
        )
    manifest = load_json(manifest_path)
    visible_rows = load_json(visible_path)
    if not isinstance(manifest, dict):
        raise RuntimeError('crawl_manifest.json 必须是对象。')
    if (
        manifest.get('capture_mode') != 'workbuddy_segmented'
        or manifest.get('crawl_complete') is not True
    ):
        raise RuntimeError('WorkBuddy 抓取尚未完整结束，不能进入分类。')
    if not isinstance(visible_rows, list) or any(
        not isinstance(row, dict) for row in visible_rows
    ):
        raise RuntimeError('visible_items.json 必须是对象数组。')
    visible_ids = [str(row.get('id') or '').strip() for row in visible_rows]
    if (
        any(not NOTE_ID_RE.fullmatch(note_id) for note_id in visible_ids)
        or len(visible_ids) != len(set(visible_ids))
    ):
        raise RuntimeError('visible_items.json 含非法或重复 note id。')
    if manifest.get('item_count') != len(visible_rows):
        raise RuntimeError('crawl_manifest.json 与 visible_items.json 数量不一致。')
    recorded_visible = str(manifest.get('visible_items') or '').strip()
    if (
        not recorded_visible
        or Path(recorded_visible).resolve() != visible_path.resolve()
        or manifest.get('visible_items_sha256') != sha256_file(visible_path)
    ):
        raise RuntimeError('visible_items.json 与抓取完成时的证据不一致。')
    capture_source = str(manifest.get('capture_source') or '').strip()
    captured_user_id = str(manifest.get('capture_user_id') or '').strip()
    captured_page_binding = str(manifest.get('capture_page_binding') or '').strip()
    captured_tab = str(manifest.get('capture_tab') or '').strip().lower()
    if (
        capture_source not in ALLOWED_SOURCES
        or not NOTE_ID_RE.fullmatch(captured_user_id)
        or profile_user_id(captured_page_binding) != captured_user_id
        or (
            capture_source == 'collection' and captured_tab != 'fav'
        )
        or (
            capture_source == 'liked' and captured_tab not in {'liked', 'like'}
        )
    ):
        raise RuntimeError('crawl_manifest.json 缺少有效的抓取账号或页面绑定。')
    if expected_user_id and captured_user_id != expected_user_id:
        raise RuntimeError('抓取账号与本次专辑账号不一致。')
    if expected_page_url and captured_page_binding != page_origin_path(expected_page_url):
        raise RuntimeError('抓取页面与本次 prepare/execute 页面不一致。')
    if expected_url_substring and expected_url_substring not in captured_page_binding:
        raise RuntimeError('抓取页面与 expected_url_substring 不一致。')
    image_ocr_enabled = manifest.get('image_ocr_enabled')
    report_requested = manifest.get('report_requested')
    organizing_depth = str(manifest.get('organizing_depth') or '').strip()
    if (
        organizing_depth not in {'quick', 'light'}
        or not isinstance(image_ocr_enabled, bool)
        or not isinstance(report_requested, bool)
        or image_ocr_enabled is not (organizing_depth == 'light')
        or (organizing_depth == 'quick' and report_requested)
    ):
        raise RuntimeError('crawl_manifest.json 缺少一致的 WorkBuddy 整理档位。')
    blockers = manifest.get('image_ocr_blockers')
    if manifest.get('ready_for_classification') is not True or blockers != []:
        raise RuntimeError('本次内容证据未通过，禁止改用元数据继续分类。')

    evidence: Dict[str, Any] = {
        'image_ocr_enabled': image_ocr_enabled,
        'organizing_depth': organizing_depth,
        'report_requested': report_requested,
        'classification_basis': (
            'workbuddy_authenticated_frontend_ocr'
            if image_ocr_enabled
            else 'workbuddy_metadata'
        ),
        'visible_count': len(visible_rows),
        'visible_ids': visible_ids,
        'visible_by_id': {
            str(row.get('id')): row for row in visible_rows
        },
        'capture_source': capture_source,
        'capture_user_id': captured_user_id,
        'capture_page_binding': captured_page_binding,
        'visible_items_sha256': sha256_file(visible_path),
        'crawl_manifest_sha256': sha256_file(manifest_path),
        'image_items_sha256': None,
        'ocr_results_sha256': None,
        'image_by_id': {},
        'ocr_by_id': {},
        'image_note_count': 0,
    }
    if not image_ocr_enabled:
        return evidence

    image_items_path = directory / 'image_items.json'
    ocr_results_path = directory / 'ocr_results.json'
    if not image_items_path.is_file() or not ocr_results_path.is_file():
        raise RuntimeError('图文 OCR 已开启，但缺少 image_items.json 或 ocr_results.json。')
    for key, expected_path in (
        ('image_items', image_items_path),
        ('ocr_results', ocr_results_path),
    ):
        recorded = str(manifest.get(key) or '').strip()
        if not recorded or Path(recorded).resolve() != expected_path.resolve():
            raise RuntimeError(f'crawl_manifest.json 的 {key} 与本次运行目录不一致。')
    if manifest.get('image_items_sha256') != sha256_file(image_items_path):
        raise RuntimeError('image_items.json 已在抓取完成后发生变化。')
    if manifest.get('ocr_results_sha256') != sha256_file(ocr_results_path):
        raise RuntimeError('ocr_results.json 已在 OCR 完成后发生变化。')
    ocr_provider = str(manifest.get('ocr_provider') or '').strip()
    ocr_tesseract_lang = str(manifest.get('ocr_tesseract_lang') or '').strip()
    expected_fingerprint = str(
        manifest.get('ocr_expected_fingerprint') or ''
    ).strip()
    recomputed_fingerprint = ocr_run_fingerprint(
        ocr_provider,
        ocr_tesseract_lang,
        ROOT / 'scripts' / 'ocr_image.swift.txt',
    )
    if (
        ocr_provider not in {'swift', 'tesseract', 'easyocr'}
        or not ocr_tesseract_lang
        or not SHA256_RE.fullmatch(expected_fingerprint)
        or expected_fingerprint != recomputed_fingerprint
    ):
        raise RuntimeError('crawl_manifest.json 的 OCR provider 或运行指纹无效。')

    image_rows = load_json(image_items_path)
    ocr_rows = load_json(ocr_results_path)
    if not isinstance(image_rows, list) or any(
        not isinstance(row, dict) for row in image_rows
    ):
        raise RuntimeError('image_items.json 必须是对象数组。')
    image_ids = [str(row.get('id') or '').strip() for row in image_rows]
    if image_ids != visible_ids:
        raise RuntimeError('image_items.json 与本次真实抓取 ID 或顺序不一致。')

    image_by_id = {str(row.get('id')): row for row in image_rows}
    metadata_keys = (
        'id', 'title', 'user', 'desc', 'tags', 'card_text',
        'source_lists', 'source_primary', 'first_seen', 'page_index',
    )
    for visible_row, image_row in zip(visible_rows, image_rows):
        if any(
            visible_row.get(key) != image_row.get(key)
            for key in metadata_keys
            if key in visible_row or key in image_row
        ):
            raise RuntimeError(
                f'image_items.json 与抓取元数据不一致：{visible_row.get("id")}'
            )
    authoritative_image_ids: List[str] = []
    authoritative_sources: Dict[str, List[str]] = {}
    authoritative_source_hashes: Dict[str, List[str]] = {}
    for row in image_rows:
        note_id = str(row.get('id') or '')
        content_type = normalize_content_type(row.get('content_type'))
        if content_type not in {'image', 'video'}:
            raise RuntimeError(f'详情未确认笔记类型：{note_id}')
        if (
            row.get('content_type_source')
            != 'workbuddy_authenticated_frontend.noteData.type'
            or row.get('detail_state_source')
            not in {'setup_server_state', 'initial_state_note_detail_map'}
        ):
            raise RuntimeError(f'笔记类型缺少登录态详情来源：{note_id}')
        if content_type != 'image':
            if not (
                row.get('image_enrichment_status') == 'not_applicable'
                and row.get('image_count') == 0
                and resolve_image_files(row) == []
                and resolve_image_urls(row) == []
            ):
                raise RuntimeError(f'视频详情证据无效：{note_id}')
            continue
        references, source_hashes = validate_workbuddy_local_image_contract(
            row,
            directory,
        )
        count = row.get('image_count')
        if not (
            row.get('image_enrichment_status') == 'ok'
            and row.get('image_urls_complete') is True
            and row.get('image_list_source')
            == 'workbuddy_authenticated_frontend.noteData.imageList.local_copy'
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count > 0
            and count == len(references)
            and resolve_image_urls(row) == []
        ):
            raise RuntimeError(f'图文完整图片证据无效：{note_id}')
        authoritative_image_ids.append(note_id)
        authoritative_sources[note_id] = references
        authoritative_source_hashes[note_id] = source_hashes

    if not isinstance(ocr_rows, list) or any(
        not isinstance(row, dict) for row in ocr_rows
    ):
        raise RuntimeError('ocr_results.json 必须是对象数组。')
    ocr_ids = [str(row.get('id') or '').strip() for row in ocr_rows]
    if ocr_ids != authoritative_image_ids or len(ocr_ids) != len(set(ocr_ids)):
        raise RuntimeError('ocr_results.json 未精确覆盖本次全部图文笔记。')
    ocr_by_id = {str(row.get('id')): row for row in ocr_rows}
    for note_id in authoritative_image_ids:
        entry = ocr_by_id[note_id]
        references = authoritative_sources[note_id]
        if (
            not reusable_ocr_entry(
                entry,
                references,
                image_set_sha256(references),
                expected_fingerprint,
                authoritative_source_hashes[note_id],
            )
        ):
            raise RuntimeError(f'OCR 完整性、图片哈希或运行指纹无效：{note_id}')

    evidence.update({
        'image_items_sha256': sha256_file(image_items_path),
        'ocr_results_sha256': sha256_file(ocr_results_path),
        'ocr_provider': ocr_provider,
        'ocr_tesseract_lang': ocr_tesseract_lang,
        'ocr_expected_fingerprint': expected_fingerprint,
        'image_by_id': image_by_id,
        'ocr_by_id': ocr_by_id,
        'image_note_count': len(authoritative_image_ids),
    })
    return evidence


def capture_evidence_summary(evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'capture_source': evidence['capture_source'],
        'capture_user_id': evidence['capture_user_id'],
        'capture_page_binding': evidence['capture_page_binding'],
        'image_ocr_enabled': evidence['image_ocr_enabled'],
        'organizing_depth': evidence['organizing_depth'],
        'report_requested': evidence['report_requested'],
        'classification_basis': evidence['classification_basis'],
        'visible_count': evidence['visible_count'],
        'image_note_count': evidence['image_note_count'],
        'visible_items_sha256': evidence['visible_items_sha256'],
        'crawl_manifest_sha256': evidence['crawl_manifest_sha256'],
        'image_items_sha256': evidence['image_items_sha256'],
        'ocr_results_sha256': evidence['ocr_results_sha256'],
        'ocr_provider': evidence.get('ocr_provider'),
        'ocr_tesseract_lang': evidence.get('ocr_tesseract_lang'),
        'ocr_expected_fingerprint': evidence.get('ocr_expected_fingerprint'),
    }


def workbuddy_classification_inputs(
    evidence: Dict[str, Any],
    protected_note_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Return only unassigned model inputs, without URLs, paths, or credentials."""
    protected = protected_note_ids or set()
    safe_keys = (
        'id', 'title', 'user', 'desc', 'tags', 'card_text',
        'source_lists', 'source_primary', 'first_seen', 'page_index',
    )
    result: List[Dict[str, Any]] = []
    for note_id in evidence['visible_ids']:
        if note_id in protected:
            continue
        source = evidence.get('image_by_id', {}).get(
            note_id,
            evidence['visible_by_id'][note_id],
        )
        content_type = normalize_content_type(source.get('content_type'))
        row = {
            key: _redact_model_value(source.get(key))
            for key in safe_keys
            if key in source
        }
        row['content_type'] = content_type
        if evidence.get('image_ocr_enabled') and content_type == 'image':
            ocr_entry = evidence.get('ocr_by_id', {}).get(note_id) or {}
            row.update({
                'classification_basis': 'workbuddy_authenticated_frontend_ocr',
                'ocr_status': 'ok',
                'ocr_text': redact_sensitive_text(ocr_entry.get('ocr_text')),
                'ocr_confidence': ocr_entry.get('ocr_confidence'),
                'ocr_run_fingerprint': str(
                    ocr_entry.get('ocr_run_fingerprint') or ''
                ),
            })
        else:
            row.update({
                'classification_basis': 'workbuddy_metadata',
                'ocr_status': (
                    'skipped_by_user'
                    if content_type == 'image'
                    else 'not_applicable'
                ),
                'ocr_text': '',
                'ocr_confidence': None,
                'ocr_run_fingerprint': '',
            })
        result.append(row)
    return result


def validate_max_moves_per_session(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 200:
        raise RuntimeError('max_moves_per_session 必须是用户确认的 1 到 200 整数。')
    return value


def validate_verify_pages(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 200:
        raise RuntimeError('verify_pages 必须是 1 到 200 的整数。')
    return value


def validate_proposed_board_plan(
    proposed_board_names: Any,
    new_board_privacy: Any,
    existing_board_names: List[str],
) -> List[Dict[str, Any]]:
    if proposed_board_names is None:
        proposed_board_names = []
    if not isinstance(proposed_board_names, list):
        raise RuntimeError('proposed_board_names 必须是数组。')
    if len(proposed_board_names) > 20:
        raise RuntimeError('一次最多提议创建 20 个专辑。')
    existing = set(existing_board_names)
    names: List[str] = []
    for index, value in enumerate(proposed_board_names):
        name = str(value or '')
        if not name or name != name.strip() or any(ord(ch) < 32 for ch in name):
            raise RuntimeError(
                f'proposed_board_names[{index}] 必须是无首尾空白的非空名称。'
            )
        if name in existing:
            raise RuntimeError(f'提议专辑已真实存在：{name}')
        if name in names:
            raise RuntimeError(f'提议专辑名称重复：{name}')
        names.append(name)
    if not names:
        if new_board_privacy is not None:
            raise RuntimeError('没有提议新专辑时不得传 new_board_privacy。')
        return []
    privacy_map = {'public': 0, 'private': 1}
    privacy_key = str(new_board_privacy or '').strip().lower()
    if privacy_key not in privacy_map:
        raise RuntimeError(
            '提议创建专辑时必须明确 new_board_privacy=public 或 private。'
        )
    return [
        {'name': name, 'privacy': privacy_map[privacy_key]}
        for name in names
    ]


def approval_basis(
    directory: Path,
    report: Dict[str, Any],
    max_moves: int,
    verify_pages: Optional[int] = None,
) -> Dict[str, Any]:
    max_moves = validate_max_moves_per_session(max_moves)
    classification = directory / 'classification.json'
    snapshot = directory / 'board_snapshot.json'
    created = directory / 'created_boards.json'
    for path in (classification, snapshot, created):
        if not path.is_file():
            raise RuntimeError(f'缺少审批输入：{path.name}')
    snapshot_payload = load_json(snapshot)
    snapshot_source = (
        snapshot_payload.get('source')
        if isinstance(snapshot_payload, dict)
        else None
    )
    if not isinstance(snapshot_source, dict):
        raise RuntimeError('board_snapshot.json 缺少来源绑定。')
    snapshot_verify_pages = validate_verify_pages(
        snapshot_source.get('verify_pages')
    )
    if verify_pages is None:
        verify_pages = snapshot_verify_pages
    else:
        verify_pages = validate_verify_pages(verify_pages)
        if verify_pages != snapshot_verify_pages:
            raise RuntimeError('verify_pages 与只读专辑快照不一致。')
    planned = [
        {
            'id': str(row.get('id') or ''),
            'target_board': str(row.get('target_board') or ''),
            'source_board_id': str(row.get('source_board_id') or ''),
            'membership_state': str(row.get('membership_state') or ''),
            'archive_lifecycle_state': str(row.get('archive_lifecycle_state') or ''),
            'status': str(row.get('status') or ''),
        }
        for row in report.get('processed', [])
    ]
    evidence = validate_workbuddy_capture_evidence(directory)
    return {
        'classification_sha256': sha256_file(classification),
        'board_snapshot_sha256': sha256_file(snapshot),
        'created_boards_sha256': sha256_file(created),
        'content_evidence': capture_evidence_summary(evidence),
        'mode': report.get('mode'),
        'ready_for_execute': report.get('ready_for_execute'),
        'blockers': report.get('blockers'),
        'max_moves_per_session': max_moves,
        'verify_pages': verify_pages,
        'planned': planned,
    }


def approval_digest(
    directory: Path,
    report: Dict[str, Any],
    max_moves: int,
    verify_pages: Optional[int] = None,
) -> str:
    encoded = json.dumps(
        approval_basis(directory, report, max_moves, verify_pages),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def validate_workbuddy_snapshot_binding(
    snapshot: Any,
    user_id: str,
    expected_url_substring: str,
    verify_pages: int,
) -> List[str]:
    verify_pages = validate_verify_pages(verify_pages)
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
        or str(source.get('live_page_binding') or '') != expected_url_substring
        or str(source.get('live_account_user_id') or '') != user_id
        or source.get('verify_pages') != verify_pages
    ):
        raise RuntimeError(
            'board_snapshot.json 与本次 WorkBuddy 账号、页面或 verify_pages 绑定不一致。'
        )
    validation = snapshot.get('validation')
    if (
        not isinstance(validation, dict)
        or validation.get('pagination_cursor_invariants_passed') is not True
        or validation.get('board_names_unique') is not True
        or bool(validation.get('within_board_duplicates'))
        or validation.get('full_membership_complete') is not True
    ):
        raise RuntimeError('board_snapshot.json 未证明全部专辑成员完整可读。')
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


def snapshot_existing_note_to_board(snapshot: Any) -> Dict[str, str]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('boards'), list):
        raise RuntimeError('board_snapshot.json 缺少完整专辑成员。')
    note_to_boards: Dict[str, List[str]] = {}
    for index, board in enumerate(snapshot['boards']):
        if not isinstance(board, dict):
            raise RuntimeError(f'board_snapshot.json 的 boards[{index}] 必须是对象。')
        board_name = str(board.get('name') or '').strip()
        note_ids = board.get('note_ids')
        if not board_name or not isinstance(note_ids, list):
            raise RuntimeError(f'board_snapshot.json 的 boards[{index}] 缺少名称或成员。')
        for value in note_ids:
            note_id = str(value or '').strip()
            if not note_id:
                raise RuntimeError(f'board_snapshot.json 的 boards[{index}] 包含空笔记 ID。')
            board_names = note_to_boards.setdefault(note_id, [])
            if board_name not in board_names:
                board_names.append(board_name)
    return {
        note_id: ' | '.join(sorted(board_names))
        for note_id, board_names in note_to_boards.items()
    }


def write_workbuddy_classification(
    directory: Path,
    classification_rows: Any,
    allowed_boards: List[str],
    content_evidence: Optional[Dict[str, Any]] = None,
    protected_note_to_board: Optional[Dict[str, str]] = None,
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

    protected_note_to_board = protected_note_to_board or {}
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
        if note_id in protected_note_to_board:
            raise RuntimeError(f'分类包含已归档保护笔记：{note_id}')
        if note_id in supplied:
            raise RuntimeError(f'classification 包含重复 ID：{note_id}')
        supplied[note_id] = row
    missing_ids = [
        note_id for note_id in visible_order
        if note_id not in protected_note_to_board and note_id not in supplied
    ]
    if missing_ids:
        raise RuntimeError(
            f'classification 未覆盖本次抓取的 {len(missing_ids)} 条笔记。'
        )

    taxonomy: List[str] = []
    normalized: List[Dict[str, Any]] = []
    for note_id in visible_order:
        source = visible_by_id[note_id]
        protected_source_board = protected_note_to_board.get(note_id, '')
        proposal = supplied.get(note_id) or {}
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
        evidence = content_evidence or {
            'image_ocr_enabled': False,
            'classification_basis': 'workbuddy_metadata',
            'image_by_id': {},
            'ocr_by_id': {},
        }
        evidence_source = evidence.get('image_by_id', {}).get(note_id, source)
        content_type = normalize_content_type(evidence_source.get('content_type'))
        safe_source_keys = (
            'id', 'title', 'user', 'desc', 'tags', 'card_text',
            'content_type', 'content_type_source', 'source_lists',
            'source_primary', 'first_seen', 'page_index',
        )
        row = {
            key: _redact_model_value(evidence_source.get(key))
            for key in safe_source_keys
            if key in evidence_source
        }
        ocr_entry = evidence.get('ocr_by_id', {}).get(note_id)
        if protected_source_board:
            ocr_fields = {
                'ocr_status': 'skipped_archived',
                'ocr_text': '',
                'ocr_confidence': None,
                'ocr_run_fingerprint': '',
                'ocr_image_count': 0,
                'ocr_image_set_complete': False,
                'ocr_image_evidence': [],
            }
        elif evidence.get('image_ocr_enabled') and content_type == 'image':
            ocr_images = [
                {
                    'image_index': image.get('image_index'),
                    'status': image.get('status'),
                    'ocr_text': redact_sensitive_text(image.get('ocr_text')),
                    'ocr_confidence': image.get('ocr_confidence'),
                    'image_sha256': image.get('image_sha256', ''),
                    'source_image_sha256': image.get('source_image_sha256', ''),
                    'error': image.get('error', ''),
                }
                for image in (ocr_entry or {}).get('images', [])
                if isinstance(image, dict)
            ]
            ocr_fields = {
                'ocr_status': 'ok',
                'ocr_text': redact_sensitive_text((ocr_entry or {}).get('ocr_text')),
                'ocr_confidence': (ocr_entry or {}).get('ocr_confidence'),
                'ocr_run_fingerprint': str(
                    (ocr_entry or {}).get('ocr_run_fingerprint') or ''
                ),
                'ocr_image_count': (ocr_entry or {}).get('image_count_processed', 0),
                'ocr_image_set_complete': True,
                'ocr_image_evidence': ocr_images,
            }
        else:
            ocr_fields = {
                'ocr_status': (
                    'skipped_by_user'
                    if not evidence.get('image_ocr_enabled') and content_type == 'image'
                    else 'not_applicable'
                ),
                'ocr_text': '',
                'ocr_confidence': None,
                'ocr_run_fingerprint': '',
                'ocr_image_count': 0,
                'ocr_image_set_complete': False,
                'ocr_image_evidence': [],
            }
        row.update({
            'target_board': target,
            'confidence': confidence,
            'reason': _redact_model_value(reason),
            'review_state': redact_sensitive_text(review_state),
            'classification_basis': (
                'existing_board_membership_snapshot'
                if protected_source_board
                else 'workbuddy_authenticated_frontend_ocr'
                if evidence.get('image_ocr_enabled') and content_type == 'image'
                else 'workbuddy_metadata'
            ),
            'main_topic': redact_sensitive_text(proposal.get('main_topic')),
            'content_summary': redact_sensitive_text(proposal.get('content_summary')),
            'archive_lifecycle_state': (
                'first_archive_confirmed'
                if protected_source_board
                else 'first_archive_pending'
            ),
        })
        row.update(ocr_fields)
        if not protected_source_board:
            row = apply_uncertain_assignment(row)
            normalized_target = str(row.get('target_board') or '').strip()
            if normalized_target not in allowed_board_set:
                raise RuntimeError(
                    f'分类目标不属于本次真实已有或待创建专辑：'
                    f'{note_id} target_board={normalized_target!r}'
                )
            if normalized_target not in taxonomy:
                taxonomy.append(normalized_target)
        if protected_source_board:
            row.update({
                'target_board': '',
                'confidence': 'low',
                'reason': ['existing_board_member_protected'],
                'review_state': 'existing_board_member_protected',
                'excluded': True,
                'exclude_reason': 'existing_board_member_protected',
                'source_board': protected_source_board,
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
    max_moves: Optional[int] = None,
    trusted_evidence: Any = None,
    proposed_board_names: Any = None,
    new_board_privacy: Any = None,
) -> Dict[str, Any]:
    require_workbuddy()
    directory = run_dir_for(run_id)
    if not NOTE_ID_RE.fullmatch(str(user_id or '').strip()):
        raise RuntimeError('user_id 必须是当前账号 URL 中的 24 位十六进制 id。')
    user_id = str(user_id).strip()
    checked_url = page_origin_path(validate_xhs_url(page_url, 'custom'))
    supplied_expected = str(expected_url_substring or '').strip()
    if supplied_expected and supplied_expected not in checked_url:
        raise RuntimeError('expected_url_substring 与账号列表 URL 不一致。')
    expected = checked_url
    if profile_user_id(checked_url) != user_id:
        raise RuntimeError('page_url 的 profile id 与 user_id 不一致。')
    verify_pages = validate_verify_pages(verify_pages)
    if classification_rows is not None:
        max_moves = validate_max_moves_per_session(max_moves)

    expected_receipt_stage = (
        'capture' if classification_rows is None else 'inventory'
    )
    validate_trusted_evidence(
        directory,
        trusted_evidence,
        expected_stage=expected_receipt_stage,
        expected_user_id=user_id,
        expected_page_url=checked_url,
    )

    snapshot = directory / 'board_snapshot.json'
    classification = directory / 'classification.json'
    created = directory / 'created_boards.json'
    report_path = directory / 'run_report.json'
    safety = directory / 'xhs_safety_state.json'
    approval_path = directory / 'approval.json'
    approval_path.unlink(missing_ok=True)
    content_evidence = validate_workbuddy_capture_evidence(
        directory,
        expected_user_id=user_id,
        expected_page_url=checked_url,
        expected_url_substring=expected,
    )
    content_summary = capture_evidence_summary(content_evidence)

    if classification_rows is None:
        validate_trusted_evidence(
            directory,
            trusted_evidence,
            expected_stage='capture',
            expected_user_id=user_id,
            expected_page_url=checked_url,
        )
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
        snapshot_payload = load_json(snapshot)
        existing_board_names = validate_workbuddy_snapshot_binding(
            snapshot_payload,
            user_id,
            expected,
            verify_pages,
        )
        protected_note_to_board = snapshot_existing_note_to_board(snapshot_payload)
        classification_inputs = workbuddy_classification_inputs(
            content_evidence,
            set(protected_note_to_board),
        )
        return {
            'ok': True,
            'phase': 'board_inventory',
            'run_id': directory.name,
            'run_dir': str(directory),
            'board_snapshot': str(snapshot),
            'existing_board_names': existing_board_names,
            'existing_board_count': len(existing_board_names),
            'classification_required': True,
            'board_creation_required': not bool(existing_board_names),
            'board_creation_available': True,
            'classification_input_count': len(classification_inputs),
            'classification_inputs': classification_inputs,
            'protected_existing_board_member_count': len(
                set(content_evidence['visible_ids']) & set(protected_note_to_board)
            ),
            'verify_pages': verify_pages,
            'ready_for_execute': False,
            'blockers': [],
            'approval_digest': None,
            'content_evidence': content_summary,
            'next_action': (
                '只允许根据 classification_inputs 分类，禁止读取原始 image_items/ocr_results。'
                '优先从 existing_board_names 选择；确需新专辑时，根据本次真实内容提议'
                ' proposed_board_names，不得使用插件预设类别，并明确 new_board_privacy。'
                '再带完整 classification 和用户将确认的 max_moves_per_session 调用 prepare。'
            ),
        }

    if not snapshot.is_file():
        raise RuntimeError(
            '缺少本次只读专辑清单；必须先调用不带 classification 的 prepare。'
        )
    snapshot_payload = load_json(snapshot)
    existing_board_names = validate_workbuddy_snapshot_binding(
        snapshot_payload,
        user_id,
        expected,
        verify_pages,
    )
    effective_proposed_board_names = proposed_board_names
    if isinstance(classification_rows, list) and any(
        isinstance(row, dict)
        and str(row.get('target_board') or '').strip() in {'', UNCERTAIN_BOARD_NAME}
        for row in classification_rows
    ):
        if effective_proposed_board_names is None:
            effective_proposed_board_names = []
        if not isinstance(effective_proposed_board_names, list):
            raise RuntimeError('proposed_board_names 必须是数组。')
        if (
            UNCERTAIN_BOARD_NAME not in existing_board_names
            and UNCERTAIN_BOARD_NAME not in effective_proposed_board_names
        ):
            effective_proposed_board_names = [
                *effective_proposed_board_names,
                UNCERTAIN_BOARD_NAME,
            ]
    planned_boards = validate_proposed_board_plan(
        effective_proposed_board_names,
        new_board_privacy,
        existing_board_names,
    )
    if not existing_board_names and not planned_boards:
        raise RuntimeError(
            '当前账号没有已有专辑；必须基于本次真实内容提议专辑名称并明确隐私。'
        )
    allowed_board_names = existing_board_names + [
        row['name'] for row in planned_boards
    ]
    classification_context = write_workbuddy_classification(
        directory,
        classification_rows,
        allowed_board_names,
        content_evidence,
        snapshot_existing_note_to_board(snapshot_payload),
    )
    used_targets = set(classification_context['taxonomy'])
    unused_plans = [
        row['name'] for row in planned_boards if row['name'] not in used_targets
    ]
    if unused_plans:
        raise RuntimeError(
            '不得创建没有任何真实条目使用的专辑：' + ', '.join(unused_plans)
        )
    run_command([
        sys.executable,
        str(ROOT / 'scripts/build_created_boards.py'),
        str(classification),
        str(snapshot),
        str(created),
    ])
    generated_created = load_json(created)
    if not isinstance(generated_created, dict):
        raise RuntimeError('created_boards.json 格式无效。')
    expected_missing = {row['name'] for row in planned_boards}
    actual_missing = set(generated_created.get('missing') or [])
    if actual_missing != expected_missing:
        raise RuntimeError(
            '待创建专辑与只读清单核验结果不一致；必须重新获取专辑快照。'
        )
    write_private_json(created, {
        'confirmed': generated_created.get('confirmed') or [],
        'planned': planned_boards,
        'created': [],
        'missing': [],
        'failed': [],
        'action_required': '',
    })
    proc = run_command([
        sys.executable,
        str(ROOT / 'scripts/run_reassign_batch.py'),
        str(classification),
        str(report_path),
        '--board-snapshot', str(snapshot),
        '--created-boards', str(created),
        '--safety-state', str(safety),
        '--allow-planned-board-creation',
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
        digest = approval_digest(directory, report, max_moves, verify_pages)
        write_private_json(approval_path, {
            'approval_digest': digest,
            'basis': approval_basis(
                directory,
                report,
                max_moves,
                verify_pages,
            ),
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
        'planned_board_creations': planned_boards,
        'max_moves_per_session': max_moves,
        'verify_pages': verify_pages,
        'processed': report.get('processed'),
        'approval_digest': digest,
        'report': str(report_path),
        'content_evidence': content_summary,
        'next_action': (
            f'向用户展示每条“当前专辑 → 目标专辑”和移动上限 {max_moves}；'
            '用户明确确认后才能调用 execute，且 execute 必须使用相同上限。'
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
    trusted_evidence: Any = None,
    verify_pages: int = 100,
    *,
    _launch_capability: Any = None,
) -> Dict[str, Any]:
    require_workbuddy()
    directory = run_dir_for(run_id)
    max_moves = validate_max_moves_per_session(max_moves)
    verify_pages = validate_verify_pages(verify_pages)
    if not NOTE_ID_RE.fullmatch(str(user_id or '').strip()):
        raise RuntimeError('user_id 必须是当前账号 URL 中的 24 位十六进制 id。')
    checked_url = page_origin_path(validate_xhs_url(page_url, 'custom'))
    supplied_expected = str(expected_url_substring or '').strip()
    if supplied_expected and supplied_expected not in checked_url:
        raise RuntimeError('expected_url_substring 与账号列表 URL 不一致。')
    expected = checked_url
    if profile_user_id(checked_url) != str(user_id).strip():
        raise RuntimeError('page_url 的 profile id 与 user_id 不一致。')
    validate_trusted_evidence(
        directory,
        trusted_evidence,
        expected_stage='plan',
        expected_user_id=str(user_id).strip(),
        expected_page_url=checked_url,
    )
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
    classification = directory / 'classification.json'
    snapshot = directory / 'board_snapshot.json'
    created = directory / 'created_boards.json'
    safety = directory / 'xhs_safety_state.json'
    approval_path = directory / 'approval.json'
    validate_workbuddy_capture_evidence(
        directory,
        expected_user_id=str(user_id).strip(),
        expected_page_url=checked_url,
        expected_url_substring=expected,
    )
    if not snapshot.is_file():
        raise RuntimeError('执行被拒：缺少 board_snapshot.json。')
    validate_workbuddy_snapshot_binding(
        load_json(snapshot),
        str(user_id).strip(),
        expected,
        verify_pages,
    )
    if not approval_path.is_file():
        raise RuntimeError('执行被拒：缺少 prepare 生成的 approval.json。')
    approval_record = load_json(approval_path)
    if not isinstance(approval_record, dict):
        raise RuntimeError('执行被拒：approval.json 格式无效。')
    approved_basis = approval_record.get('basis')
    if not isinstance(approved_basis, dict):
        raise RuntimeError('执行被拒：approval.json 缺少审批依据。')
    approved_max_moves = validate_max_moves_per_session(
        approved_basis.get('max_moves_per_session')
    )
    if approved_max_moves != max_moves:
        raise RuntimeError('执行被拒：移动上限与 prepare 阶段用户确认值不一致。')
    approved_verify_pages = validate_verify_pages(
        approved_basis.get('verify_pages')
    )
    if approved_verify_pages != verify_pages:
        raise RuntimeError('执行被拒：verify_pages 与 prepare 阶段确认值不一致。')
    expected_approval = approval_digest(
        directory,
        report,
        max_moves,
        verify_pages,
    )
    stored_approval = str(approval_record.get('approval_digest') or '').strip().lower()
    provided = str(approval or '').strip().lower()
    if (
        not SHA256_RE.fullmatch(stored_approval)
        or not SHA256_RE.fullmatch(provided)
        or stored_approval != expected_approval
        or provided != stored_approval
    ):
        raise RuntimeError(
            '执行被拒：approval_digest 或用户确认的移动上限不匹配。'
        )
    final_evidence = validate_trusted_evidence(
        directory,
        trusted_evidence,
        expected_stage='plan',
        expected_user_id=str(user_id).strip(),
        expected_page_url=checked_url,
    )
    report = load_trusted_json_snapshot(directory, final_evidence, 'run_report.json')
    classification_payload = load_trusted_json_snapshot(
        directory,
        final_evidence,
        'classification.json',
    )
    snapshot_payload = load_trusted_json_snapshot(
        directory,
        final_evidence,
        'board_snapshot.json',
    )
    created_payload = load_trusted_json_snapshot(
        directory,
        final_evidence,
        'created_boards.json',
    )
    approval_record = load_trusted_json_snapshot(
        directory,
        final_evidence,
        'approval.json',
    )
    if not isinstance(approval_record, dict) or not isinstance(approval_record.get('basis'), dict):
        raise RuntimeError('执行被拒：绑定的 approval.json 格式无效。')
    bound_basis = approval_record['basis']
    bound_digest = hashlib.sha256(
        canonical_json(bound_basis).encode('utf-8')
    ).hexdigest()
    stored_bound_digest = str(
        approval_record.get('approval_digest') or ''
    ).strip().lower()
    if (
        bound_digest != stored_bound_digest
        or str(approval or '').strip().lower() != stored_bound_digest
        or bound_basis.get('classification_sha256')
        != final_evidence['artifacts']['classification.json']['sha256']
        or bound_basis.get('board_snapshot_sha256')
        != final_evidence['artifacts']['board_snapshot.json']['sha256']
        or bound_basis.get('created_boards_sha256')
        != final_evidence['artifacts']['created_boards.json']['sha256']
        or bound_basis.get('max_moves_per_session') != max_moves
        or bound_basis.get('verify_pages') != verify_pages
        or bound_basis.get('mode') != 'dry_run'
        or bound_basis.get('ready_for_execute') is not True
        or bound_basis.get('blockers') != []
    ):
        raise RuntimeError('执行被拒：绑定的用户审批依据不一致。')
    expected_planned = [
        {
            'id': str(row.get('id') or ''),
            'target_board': str(row.get('target_board') or ''),
            'source_board_id': str(row.get('source_board_id') or ''),
            'membership_state': str(row.get('membership_state') or ''),
            'archive_lifecycle_state': str(row.get('archive_lifecycle_state') or ''),
            'status': str(row.get('status') or ''),
        }
        for row in report.get('processed', [])
    ]
    if bound_basis.get('planned') != expected_planned:
        raise RuntimeError('执行被拒：绑定的逐条移动方案不一致。')
    if (
        not isinstance(report, dict)
        or report.get('mode') != 'dry_run'
        or report.get('ready_for_execute') is not True
        or report.get('blockers') != []
    ):
        raise RuntimeError('执行被拒：绑定的 run_report.json 不是可执行 dry-run。')
    normalized = normalize_classification(classification_payload)
    preflight = prepare_write_preflight(
        normalized,
        snapshot_payload,
        created_payload,
        allow_low_confidence=False,
        allow_planned_board_creation=True,
    )
    resolved = preflight.pop('resolved_items')
    if preflight.get('ready_for_execute') is not True or preflight.get('blockers') != []:
        raise RuntimeError('执行被拒：内存中的最终 dry-run 复验未通过。')
    execute_args = browser_args(checked_url)
    execute_args.browser = 'playwright'
    execute_args.user_id = str(user_id).strip()
    execute_args.expected_url_substring = expected
    execute_args.allow_low_confidence = False
    execute_args.verify_pages = verify_pages
    execute_args.max_moves_per_session = max_moves
    execute_args.safety_state = str(safety)
    execute_args.inter_item_delay_sec = 5.0
    execute_args.timeout_sec = 120.0
    binding_blockers = write_binding_blockers(
        preflight.get('snapshot_source'),
        execute_args,
    )
    if binding_blockers:
        raise RuntimeError(
            '执行被拒：' + ','.join(binding_blockers)
        )
    execution_report = initial_report(resolved, 'execute')
    execution_report.update(preflight)
    execution_report.update({
        'board_snapshot': str(snapshot),
        'board_snapshot_sha256': final_evidence['artifacts']['board_snapshot.json']['sha256'],
        'created_boards': str(created),
        'created_boards_sha256': final_evidence['artifacts']['created_boards.json']['sha256'],
        'classification_sha256': final_evidence['artifacts']['classification.json']['sha256'],
    })
    if _launch_capability is not _MCP_EXECUTE_CAPABILITY:
        raise RuntimeError('mcp_execute_launch_capability_missing')
    require_profile_available()
    planned_board_creations = preflight.get('planned_board_creations') or []
    execute_kwargs: Dict[str, Any] = {
        'commit_callback': await_mcp_execute_commit,
    }
    if planned_board_creations:
        execute_kwargs['post_commit_callback'] = (
            lambda runner: execute_planned_board_creations(
                runner, planned_board_creations, execute_args, execution_report
            )
        )
    apply_batch(
        resolved,
        execution_report,
        execute_args,
        report_path,
        **execute_kwargs,
    )
    if not report_path.is_file():
        raise RuntimeError('执行未生成 run_report.json。')
    final_report = load_json(report_path)
    return {
        'ok': not final_report.get('errors'),
        'run_id': directory.name,
        'mode': final_report.get('mode'),
        'session_status': final_report.get('session_status'),
        'processed': final_report.get('processed'),
        'errors': final_report.get('errors'),
        'board_creations': final_report.get('board_creations', []),
        'verify_pages': verify_pages,
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
    capture.add_argument('--organizing-depth', choices=['quick', 'light'], required=True)
    capture.add_argument('--generate-report', action='store_true')

    prepare = sub.add_parser('prepare')
    prepare.add_argument('--run-id', required=True)
    prepare.add_argument('--user-id', required=True)
    prepare.add_argument('--page-url', required=True)
    prepare.add_argument('--expected-url-substring', required=True)
    prepare.add_argument('--verify-pages', type=int, default=100)
    prepare.add_argument('--classification-stdin', action='store_true')
    prepare.add_argument('--trusted-evidence-stdin', action='store_true')
    prepare.add_argument('--mcp-launch-fd', type=int, help=argparse.SUPPRESS)
    prepare.add_argument('--max-moves-per-session', type=int)

    execute = sub.add_parser('execute')
    execute.add_argument('--run-id', required=True)
    execute.add_argument('--user-id', required=True)
    execute.add_argument('--page-url', required=True)
    execute.add_argument('--expected-url-substring', required=True)
    execute.add_argument('--approval-digest', required=True)
    execute.add_argument('--max-moves-per-session', type=int, required=True)
    execute.add_argument('--verify-pages', type=int, required=True)
    execute.add_argument('--trusted-evidence-stdin', action='store_true')
    execute.add_argument('--mcp-launch-fd', type=int, help=argparse.SUPPRESS)
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
                args.organizing_depth,
                args.generate_report,
            )
        elif args.action == 'prepare':
            classification_rows = None
            trusted_evidence = None
            payload = None
            if args.classification_stdin or args.trusted_evidence_stdin:
                payload = json.load(sys.stdin)
                if not isinstance(payload, dict):
                    raise RuntimeError('stdin 必须提供 JSON 对象。')
            if args.classification_stdin:
                if 'classification' not in payload:
                    raise RuntimeError('stdin 必须提供包含 classification 的 JSON 对象。')
                classification_rows = payload['classification']
            if args.trusted_evidence_stdin:
                if 'trusted_evidence' not in payload:
                    raise RuntimeError('stdin 必须提供 trusted_evidence。')
                trusted_evidence = payload['trusted_evidence']
            if args.mcp_launch_fd is None:
                raise RuntimeError('mcp_launch_attestation_fd_missing')
            verify_mcp_launch_attestation(
                'prepare',
                sys.argv[2:],
                payload,
                key_fd=args.mcp_launch_fd,
            )
            result = prepare_action(
                args.run_id,
                args.user_id,
                args.page_url,
                args.expected_url_substring,
                args.verify_pages,
                classification_rows,
                args.max_moves_per_session,
                trusted_evidence,
                payload.get('proposed_board_names') if payload else None,
                payload.get('new_board_privacy') if payload else None,
            )
        else:
            trusted_evidence = None
            payload = None
            if args.trusted_evidence_stdin:
                payload = json.load(sys.stdin)
                if not isinstance(payload, dict) or 'trusted_evidence' not in payload:
                    raise RuntimeError('stdin 必须提供 trusted_evidence。')
                trusted_evidence = payload['trusted_evidence']
            if args.mcp_launch_fd is None:
                raise RuntimeError('mcp_launch_attestation_fd_missing')
            verify_mcp_launch_attestation(
                'execute',
                sys.argv[2:],
                payload,
                key_fd=args.mcp_launch_fd,
            )
            result = execute_action(
                args.run_id,
                args.user_id,
                args.page_url,
                args.expected_url_substring,
                args.approval_digest,
                args.max_moves_per_session,
                trusted_evidence,
                args.verify_pages,
                _launch_capability=_MCP_EXECUTE_CAPABILITY,
            )
    except Exception as exc:
        print(json.dumps({
            'ok': False,
            'error': redact_sensitive_text(exc),
            'action': args.action,
        }, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
