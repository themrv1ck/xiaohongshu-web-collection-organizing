#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from enable_workbuddy_mcp import enable_server  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
