#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from enable_workbuddy_mcp import (  # noqa: E402
    MARKETPLACE_NAME,
    MARKETPLACE_SOURCE,
    PLUGIN_ID,
    bootstrap_workbuddy_plugin,
    enable_server,
    resolve_codebuddy_cli,
)


class EnableWorkBuddyMcpTests(unittest.TestCase):
    def test_adds_only_xiaohongshu_server_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            settings_path = config_dir / 'settings.json'
            settings_path.write_text(
                json.dumps({
                    'enabledPlugins': {'other@example': True},
                    'enabledMcpjsonServers': ['existing-server'],
                }),
                encoding='utf-8',
            )

            result = enable_server(config_dir)
            settings = json.loads(settings_path.read_text(encoding='utf-8'))

            self.assertTrue(result['changed'])
            self.assertEqual(
                settings['enabledMcpjsonServers'],
                ['existing-server', 'xiaohongshu-organizer'],
            )
            self.assertEqual(
                settings['enabledPlugins'],
                {'other@example': True},
            )

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            first = enable_server(config_dir)
            second = enable_server(config_dir)

            self.assertTrue(first['changed'])
            self.assertFalse(second['changed'])
            settings = json.loads(
                (config_dir / 'settings.json').read_text(encoding='utf-8')
            )
            self.assertEqual(
                settings['enabledMcpjsonServers'],
                ['xiaohongshu-organizer'],
            )

    def test_rejects_invalid_existing_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / 'settings.json').write_text(
                json.dumps({'enabledMcpjsonServers': 'all'}),
                encoding='utf-8',
            )
            with self.assertRaisesRegex(RuntimeError, '字符串数组'):
                enable_server(config_dir)

    def test_resolves_only_cli_in_workbuddy_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            resources = Path(tmp)
            cli = resources / 'cli' / 'bin' / 'codebuddy'
            cli.parent.mkdir(parents=True)
            cli.write_text('#!/bin/sh\n', encoding='utf-8')
            self.assertEqual(
                resolve_codebuddy_cli(
                    env={'WORKBUDDY_RESOURCES_PATH': str(resources)}
                ),
                cli.resolve(),
            )

    def test_bootstrap_installs_missing_marketplace_and_plugin_before_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            cli = config_dir / 'codebuddy'
            cli.write_text('', encoding='utf-8')
            calls = []

            def runner(command, **kwargs):
                calls.append(command[1:])
                if command[1:4] == ['plugin', 'marketplace', 'add']:
                    path = config_dir / 'plugins' / 'known_marketplaces.json'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({
                        MARKETPLACE_NAME: {
                            'type': 'github',
                            'source': {'source': 'github', 'repo': MARKETPLACE_SOURCE},
                        },
                    }), encoding='utf-8')
                elif command[1:3] == ['plugin', 'install']:
                    (config_dir / 'settings.json').write_text(json.dumps({
                        'enabledPlugins': {PLUGIN_ID: True},
                    }), encoding='utf-8')
                return SimpleNamespace(returncode=0)

            result = bootstrap_workbuddy_plugin(
                config_dir,
                cli,
                runner=runner,
            )
            settings = json.loads(
                (config_dir / 'settings.json').read_text(encoding='utf-8')
            )
            self.assertEqual(calls, [
                ['plugin', 'marketplace', 'add', MARKETPLACE_SOURCE],
                ['plugin', 'install', PLUGIN_ID, '--scope', 'user'],
            ])
            self.assertTrue(result['marketplace_added'])
            self.assertEqual(result['plugin_action'], 'installed')
            self.assertTrue(result['installed'])
            self.assertTrue(result['restart_required'])
            self.assertEqual(
                settings['enabledMcpjsonServers'],
                ['xiaohongshu-organizer'],
            )

    def test_bootstrap_preserves_enabled_local_directory_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            local_marketplace = config_dir / 'local-marketplace'
            manifests = local_marketplace / '.codebuddy-plugin'
            manifests.mkdir(parents=True)
            (manifests / 'marketplace.json').write_text(json.dumps({
                'name': MARKETPLACE_NAME,
            }), encoding='utf-8')
            (manifests / 'plugin.json').write_text(json.dumps({
                'name': 'xiaohongshu-organizer',
                'repository': 'https://github.com/' + MARKETPLACE_SOURCE,
            }), encoding='utf-8')
            marketplace_path = config_dir / 'plugins' / 'known_marketplaces.json'
            marketplace_path.parent.mkdir(parents=True)
            marketplace_path.write_text(json.dumps({
                MARKETPLACE_NAME: {
                    'type': 'directory',
                    'source': {
                        'source': 'directory',
                        'path': str(local_marketplace),
                    },
                },
            }), encoding='utf-8')
            (config_dir / 'settings.json').write_text(json.dumps({
                'enabledPlugins': {PLUGIN_ID: True, 'other@example': True},
                'enabledMcpjsonServers': ['other-server'],
            }), encoding='utf-8')
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                return SimpleNamespace(returncode=0)

            result = bootstrap_workbuddy_plugin(
                config_dir,
                config_dir / 'unused-cli',
                runner=runner,
            )
            settings = json.loads(
                (config_dir / 'settings.json').read_text(encoding='utf-8')
            )
            self.assertEqual(calls, [])
            self.assertEqual(result['plugin_action'], 'already_enabled')
            self.assertEqual(
                settings['enabledPlugins'],
                {PLUGIN_ID: True, 'other@example': True},
            )
            self.assertEqual(
                settings['enabledMcpjsonServers'],
                ['other-server', 'xiaohongshu-organizer'],
            )

    def test_bootstrap_updates_existing_remote_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            marketplace_path = config_dir / 'plugins' / 'known_marketplaces.json'
            marketplace_path.parent.mkdir(parents=True)
            marketplace_path.write_text(json.dumps({
                MARKETPLACE_NAME: {
                    'type': 'github',
                    'source': {'source': 'github', 'repo': MARKETPLACE_SOURCE},
                },
            }), encoding='utf-8')
            (config_dir / 'settings.json').write_text(json.dumps({
                'enabledPlugins': {PLUGIN_ID: True},
            }), encoding='utf-8')
            calls = []

            def runner(command, **kwargs):
                calls.append(command[1:])
                return SimpleNamespace(returncode=0)

            result = bootstrap_workbuddy_plugin(
                config_dir,
                config_dir / 'codebuddy',
                runner=runner,
            )
            self.assertEqual(calls, [
                ['plugin', 'marketplace', 'update', MARKETPLACE_NAME],
                ['plugin', 'update', PLUGIN_ID, '--scope', 'user'],
            ])
            self.assertTrue(result['marketplace_updated'])
            self.assertEqual(result['plugin_action'], 'updated')

    def test_bootstrap_rejects_existing_marketplace_with_wrong_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            marketplace_path = config_dir / 'plugins' / 'known_marketplaces.json'
            marketplace_path.parent.mkdir(parents=True)
            marketplace_path.write_text(json.dumps({
                MARKETPLACE_NAME: {
                    'type': 'github',
                    'source': {'source': 'github', 'repo': 'attacker/wrong-repo'},
                },
            }), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '固定 GitHub 来源'):
                bootstrap_workbuddy_plugin(
                    config_dir,
                    config_dir / 'codebuddy',
                    runner=lambda *args, **kwargs: SimpleNamespace(returncode=0),
                )
            self.assertFalse((config_dir / 'settings.json').exists())

    def test_bootstrap_cli_failure_does_not_enable_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)

            def runner(command, **kwargs):
                return SimpleNamespace(returncode=7)

            with self.assertRaisesRegex(RuntimeError, 'exit=7'):
                bootstrap_workbuddy_plugin(
                    config_dir,
                    config_dir / 'codebuddy',
                    runner=runner,
                )
            self.assertFalse((config_dir / 'settings.json').exists())


if __name__ == '__main__':
    unittest.main()
