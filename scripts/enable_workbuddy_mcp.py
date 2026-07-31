#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


SERVER_NAME = 'xiaohongshu-organizer'
MARKETPLACE_NAME = 'xiaohongshu-skill-marketplace'
MARKETPLACE_SOURCE = 'themrv1ck/xiaohongshu-web-collection-organizing'
PLUGIN_ID = f'{SERVER_NAME}@{MARKETPLACE_NAME}'
PLUGIN_REPOSITORY = f'https://github.com/{MARKETPLACE_SOURCE}'


def read_json_object(path: Path, label: str) -> Dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise RuntimeError(f'{label} 顶层必须是 JSON 对象。')
    return value


def resolve_codebuddy_cli(
    explicit: str = '',
    env: Optional[Mapping[str, str]] = None,
) -> Path:
    environment = os.environ if env is None else env
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_file():
            raise RuntimeError('指定的 WorkBuddy 官方 CLI 不存在。')
        return candidate
    resources = str(environment.get('WORKBUDDY_RESOURCES_PATH') or '').strip()
    if not resources:
        raise RuntimeError(
            'WorkBuddy 未注入 WORKBUDDY_RESOURCES_PATH；必须从 WorkBuddy 内启用插件。'
        )
    bin_dir = Path(resources).expanduser().resolve() / 'cli' / 'bin'
    candidates = [bin_dir / 'codebuddy', bin_dir / 'codebuddy.exe']
    ready = [candidate for candidate in candidates if candidate.is_file()]
    if len(ready) != 1:
        raise RuntimeError('无法唯一定位 WorkBuddy 官方 codebuddy CLI。')
    return ready[0]


def run_codebuddy_cli(
    cli: Path,
    args: Sequence[str],
    config_dir: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    environment = dict(os.environ)
    environment['WORKBUDDY_CONFIG_DIR'] = str(config_dir)
    environment['CODEBUDDY_CONFIG_DIR'] = str(config_dir)
    result = runner(
        [str(cli), *args],
        cwd=str(config_dir),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        action = ' '.join(args[:3])
        raise RuntimeError(
            f'WorkBuddy 官方 CLI 执行失败：{action}（exit={result.returncode}）。'
        )


def validate_marketplace_source(marketplace: Any) -> bool:
    if not isinstance(marketplace, dict):
        raise RuntimeError('小红书 Plugin marketplace 配置无效。')
    source = marketplace.get('source')
    if not isinstance(source, dict):
        raise RuntimeError('小红书 Plugin marketplace 缺少来源。')
    source_kind = str(source.get('source') or '').strip()
    marketplace_type = str(marketplace.get('type') or '').strip()
    local_directory = marketplace_type == 'directory' or source_kind == 'directory'
    if not local_directory:
        if (
            marketplace_type != 'github'
            or source_kind != 'github'
            or str(source.get('repo') or '').strip() != MARKETPLACE_SOURCE
        ):
            raise RuntimeError('现有小红书 Plugin marketplace 不是固定 GitHub 来源。')
        return False

    directory_text = str(
        source.get('path') or marketplace.get('installLocation') or ''
    ).strip()
    directory = Path(directory_text).expanduser()
    if not directory_text or directory.is_symlink() or not directory.is_dir():
        raise RuntimeError('现有本地小红书 Plugin marketplace 目录无效。')
    marketplace_manifest = read_json_object(
        directory / '.codebuddy-plugin' / 'marketplace.json',
        '本地 marketplace.json',
    )
    plugin_manifest = read_json_object(
        directory / '.codebuddy-plugin' / 'plugin.json',
        '本地 plugin.json',
    )
    if (
        marketplace_manifest.get('name') != MARKETPLACE_NAME
        or plugin_manifest.get('name') != SERVER_NAME
        or plugin_manifest.get('repository') != PLUGIN_REPOSITORY
    ):
        raise RuntimeError('现有本地小红书 Plugin marketplace 身份不匹配。')
    return True


def enable_server(config_dir: Path, server_name: str = SERVER_NAME) -> dict:
    config_dir = config_dir.resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_path = config_dir / 'settings.json'

    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding='utf-8'))
        if not isinstance(settings, dict):
            raise RuntimeError('WorkBuddy settings.json 顶层必须是 JSON 对象。')
        original_mode = settings_path.stat().st_mode
    else:
        settings = {}
        original_mode = None

    enabled = settings.get('enabledMcpjsonServers', [])
    if not isinstance(enabled, list) or any(
        not isinstance(item, str) for item in enabled
    ):
        raise RuntimeError('enabledMcpjsonServers 必须是字符串数组。')

    changed = server_name not in enabled
    if changed:
        settings['enabledMcpjsonServers'] = [*enabled, server_name]
        payload = json.dumps(settings, ensure_ascii=False, indent=2) + '\n'
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=config_dir,
            prefix='.settings.',
            suffix='.tmp',
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        if original_mode is not None:
            os.chmod(temporary_path, original_mode)
        os.replace(temporary_path, settings_path)

    return {
        'ok': True,
        'changed': changed,
        'server': server_name,
        'settings_path': str(settings_path),
        'restart_required': changed,
    }


def bootstrap_workbuddy_plugin(
    config_dir: Path,
    codebuddy_cli: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> dict:
    config_dir = config_dir.resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    marketplaces_path = config_dir / 'plugins' / 'known_marketplaces.json'
    settings_path = config_dir / 'settings.json'
    marketplaces = read_json_object(
        marketplaces_path,
        'plugins/known_marketplaces.json',
    )
    marketplace_added = MARKETPLACE_NAME not in marketplaces
    if marketplace_added:
        run_codebuddy_cli(
            codebuddy_cli,
            ['plugin', 'marketplace', 'add', MARKETPLACE_SOURCE],
            config_dir,
            runner=runner,
        )
        marketplaces = read_json_object(
            marketplaces_path,
            'plugins/known_marketplaces.json',
        )
        if MARKETPLACE_NAME not in marketplaces:
            raise RuntimeError('WorkBuddy 未登记小红书 Plugin marketplace。')

    marketplace = marketplaces.get(MARKETPLACE_NAME)
    local_directory_marketplace = validate_marketplace_source(marketplace)
    marketplace_updated = False
    if not marketplace_added and not local_directory_marketplace:
        run_codebuddy_cli(
            codebuddy_cli,
            ['plugin', 'marketplace', 'update', MARKETPLACE_NAME],
            config_dir,
            runner=runner,
        )
        marketplace_updated = True

    settings = read_json_object(settings_path, 'settings.json')
    enabled_plugins = settings.get('enabledPlugins', {})
    if not isinstance(enabled_plugins, dict) or any(
        not isinstance(key, str) or not isinstance(value, bool)
        for key, value in enabled_plugins.items()
    ):
        raise RuntimeError('enabledPlugins 必须是布尔值映射。')

    plugin_action = 'already_enabled'
    plugin_state = enabled_plugins.get(PLUGIN_ID)
    if plugin_state is not True:
        if plugin_state is False:
            command = ['plugin', 'enable', PLUGIN_ID, '--scope', 'user']
            plugin_action = 'enabled'
        else:
            command = ['plugin', 'install', PLUGIN_ID, '--scope', 'user']
            plugin_action = 'installed'
        run_codebuddy_cli(
            codebuddy_cli,
            command,
            config_dir,
            runner=runner,
        )
        settings = read_json_object(settings_path, 'settings.json')
        enabled_plugins = settings.get('enabledPlugins', {})
        if (
            not isinstance(enabled_plugins, dict)
            or enabled_plugins.get(PLUGIN_ID) is not True
        ):
            raise RuntimeError('WorkBuddy 未确认小红书 Plugin 已启用。')
    if not local_directory_marketplace and plugin_state is not None:
        run_codebuddy_cli(
            codebuddy_cli,
            ['plugin', 'update', PLUGIN_ID, '--scope', 'user'],
            config_dir,
            runner=runner,
        )
        plugin_action = 'updated'

    server_result = enable_server(config_dir)
    return {
        **server_result,
        'marketplace': MARKETPLACE_NAME,
        'marketplace_added': marketplace_added,
        'marketplace_updated': marketplace_updated,
        'plugin': PLUGIN_ID,
        'plugin_action': plugin_action,
        'installed': True,
        'restart_required': True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description='只启用小红书 WorkBuddy MCP，不修改其他插件设置。'
    )
    parser.add_argument(
        '--config-dir',
        help='WorkBuddy 配置目录；在 WorkBuddy 内默认读取 WORKBUDDY_CONFIG_DIR。',
    )
    parser.add_argument(
        '--codebuddy-cli',
        default='',
        help='测试或诊断时显式指定 WorkBuddy 官方 codebuddy CLI。',
    )
    parser.add_argument(
        '--install-plugin',
        action='store_true',
        required=True,
        help='确认使用 WorkBuddy 官方 CLI 安装或启用唯一的小红书 Plugin。',
    )
    args = parser.parse_args()

    configured = args.config_dir or os.environ.get('WORKBUDDY_CONFIG_DIR')
    if not configured:
        raise RuntimeError(
            '未找到 WORKBUDDY_CONFIG_DIR；请从 WorkBuddy 内运行此脚本。'
        )

    config_dir = Path(configured)
    cli = resolve_codebuddy_cli(args.codebuddy_cli)
    print(json.dumps(
        bootstrap_workbuddy_plugin(config_dir, cli),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
