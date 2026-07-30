#!/usr/bin/env python3
import argparse
import json
import os
import tempfile
from pathlib import Path


SERVER_NAME = 'xiaohongshu-organizer'


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description='只启用小红书 WorkBuddy MCP，不修改其他插件设置。'
    )
    parser.add_argument(
        '--config-dir',
        help='WorkBuddy 配置目录；在 WorkBuddy 内默认读取 WORKBUDDY_CONFIG_DIR。',
    )
    args = parser.parse_args()

    configured = args.config_dir or os.environ.get('WORKBUDDY_CONFIG_DIR')
    if not configured:
        raise RuntimeError(
            '未找到 WORKBUDDY_CONFIG_DIR；请从 WorkBuddy 内运行此脚本。'
        )

    print(
        json.dumps(
            enable_server(Path(configured)),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
