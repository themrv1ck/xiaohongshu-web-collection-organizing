#!/usr/bin/env python3
"""WorkBuddy 宿主的确定性浏览器策略。

这个模块只认插件显式注入的 XHS_HOST=workbuddy，不读取进程名，也不猜宿主。
"""

import os
from pathlib import Path
from typing import Mapping, MutableMapping, Optional


WORKBUDDY_HOST = 'workbuddy'
WORKBUDDY_BACKEND = 'playwright'
WORKBUDDY_CHANNEL = 'chromium'


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
    """在 WorkBuddy 中强制使用可见的 Playwright 自带 Chromium。

    非 WorkBuddy 宿主不改动已有行为。WorkBuddy 宿主不接受任何系统浏览器、
    CDP 连接、headless 模式或外部 profile。
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
    channel = str(getattr(args, 'channel', '') or WORKBUDDY_CHANNEL).strip().lower()
    if channel != WORKBUDDY_CHANNEL:
        raise RuntimeError('WorkBuddy 专用浏览器只允许 Playwright 自带 chromium channel。')
    if bool(getattr(args, 'headless', False)):
        raise RuntimeError('WorkBuddy 专用浏览器必须保持可见，禁止 headless。')

    profile = workbuddy_profile_path(env)
    requested_profile_raw = str(getattr(args, 'user_data_dir', '') or '').strip()
    if requested_profile_raw and _normalized_path(requested_profile_raw) != profile:
        raise RuntimeError('WorkBuddy 专用浏览器禁止改用其他 user-data-dir。')

    args.channel = WORKBUDDY_CHANNEL
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
    return {
        'host': WORKBUDDY_HOST,
        'browser_backend': WORKBUDDY_BACKEND,
        'browser_channel': WORKBUDDY_CHANNEL,
        'browser_profile': str(profile),
        'dedicated_profile': True,
        'external_browser_allowed': False,
    }
