#!/usr/bin/env python3
"""WorkBuddy 宿主的确定性浏览器策略。

这个模块只认插件显式注入的 XHS_HOST=workbuddy，不读取进程名，也不猜宿主。
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Mapping, MutableMapping, Optional


WORKBUDDY_HOST = 'workbuddy'
WORKBUDDY_BACKEND = 'playwright'
WORKBUDDY_DEFAULT_CHANNEL = 'chromium'
WORKBUDDY_WINDOWS_CHANNEL = 'msedge'


def _environment(env: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _normalized_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _path_is_inside(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(parent)]) == str(parent)
    except ValueError:
        return False


def is_workbuddy_host(env: Optional[Mapping[str, str]] = None) -> bool:
    current = _environment(env)
    return str(current.get('XHS_HOST') or '').strip().lower() == WORKBUDDY_HOST


def workbuddy_platform(env: Optional[Mapping[str, str]] = None) -> str:
    current = _environment(env)
    supplied = str(current.get('XHS_WORKBUDDY_PLATFORM') or '').strip().lower()
    if supplied in {'win32', 'darwin', 'linux'}:
        return supplied
    if os.name == 'nt':
        return 'win32'
    if sys.platform == 'darwin':
        return 'darwin'
    return 'linux'


def workbuddy_browser_channel(env: Optional[Mapping[str, str]] = None) -> str:
    return (
        WORKBUDDY_WINDOWS_CHANNEL
        if workbuddy_platform(env) == 'win32'
        else WORKBUDDY_DEFAULT_CHANNEL
    )


def find_windows_edge_executable(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    current = _environment(env)
    candidates = []
    discovered = shutil.which('msedge')
    if discovered:
        candidates.append(Path(discovered))
    for key in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        root = str(current.get(key) or '').strip()
        if root:
            candidates.append(
                Path(root) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
            )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def workbuddy_profile_path(env: Optional[Mapping[str, str]] = None) -> Path:
    current = _environment(env)
    plugin_data_raw = str(current.get('CODEBUDDY_PLUGIN_DATA') or '').strip()
    profile_raw = str(current.get('XHS_PLAYWRIGHT_PROFILE') or '').strip()
    if not plugin_data_raw or not profile_raw:
        raise RuntimeError(
            'WorkBuddy Plugin 配置不完整：必须同时提供 '
            'CODEBUDDY_PLUGIN_DATA 和 XHS_PLAYWRIGHT_PROFILE。'
        )
    plugin_data = _normalized_path(plugin_data_raw)
    profile = _normalized_path(profile_raw)
    if profile == plugin_data or not _path_is_inside(profile, plugin_data):
        raise RuntimeError(
            'WorkBuddy Playwright 登录目录必须位于 CODEBUDDY_PLUGIN_DATA 内。'
        )
    return profile


def apply_workbuddy_browser_policy(
    requested_backend: str,
    args,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """在 WorkBuddy 中强制使用可见的独立 Playwright 浏览器。

    非 WorkBuddy 宿主不改动已有行为。Windows 只复用系统 Edge 程序文件，
    仍强制使用插件独立 profile；所有平台都拒绝 CDP、headless 和外部 profile。
    """

    if not is_workbuddy_host(env):
        return requested_backend

    if requested_backend not in {'auto', WORKBUDDY_BACKEND}:
        raise RuntimeError(
            '检测到 WorkBuddy：只允许使用 WorkBuddy Plugin + Playwright 专用浏览器；'
            f'拒绝后端 {requested_backend!r}。'
        )
    if str(getattr(args, 'cdp_url', '') or '').strip():
        raise RuntimeError('WorkBuddy 专用浏览器禁止 CDP 连接，不能接管系统 Chrome/Edge。')
    required_channel = workbuddy_browser_channel(env)
    requested_channel = str(
        getattr(args, 'channel', '') or WORKBUDDY_DEFAULT_CHANNEL
    ).strip().lower()
    allowed_requested_channels = (
        {WORKBUDDY_DEFAULT_CHANNEL, WORKBUDDY_WINDOWS_CHANNEL}
        if required_channel == WORKBUDDY_WINDOWS_CHANNEL
        else {WORKBUDDY_DEFAULT_CHANNEL}
    )
    if requested_channel not in allowed_requested_channels:
        raise RuntimeError(
            f'WorkBuddy 专用浏览器只允许受管的 {required_channel} channel。'
        )
    if bool(getattr(args, 'headless', False)):
        raise RuntimeError('WorkBuddy 专用浏览器必须保持可见，禁止 headless。')

    profile = workbuddy_profile_path(env)
    requested_profile_raw = str(getattr(args, 'user_data_dir', '') or '').strip()
    if requested_profile_raw and _normalized_path(requested_profile_raw) != profile:
        raise RuntimeError('WorkBuddy 专用浏览器禁止改用其他 user-data-dir。')

    args.channel = required_channel
    args.user_data_dir = str(profile)
    args.cdp_url = None
    args.headless = False
    return WORKBUDDY_BACKEND


def workbuddy_runtime_status(
    env: Optional[MutableMapping[str, str]] = None,
) -> dict:
    current = os.environ if env is None else env
    if not is_workbuddy_host(current):
        return {
            'host': 'other',
            'browser_backend': None,
            'browser_channel': None,
            'browser_profile': None,
            'dedicated_profile': False,
            'external_browser_allowed': True,
        }
    profile = workbuddy_profile_path(current)
    channel = workbuddy_browser_channel(current)
    return {
        'host': WORKBUDDY_HOST,
        'browser_backend': WORKBUDDY_BACKEND,
        'browser_channel': channel,
        'browser_product': (
            'Microsoft Edge' if channel == WORKBUDDY_WINDOWS_CHANNEL else 'Chromium'
        ),
        'browser_profile': str(profile),
        'dedicated_profile': True,
        'uses_user_browser_profile': False,
        'external_browser_allowed': False,
    }
