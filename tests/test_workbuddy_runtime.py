#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from workbuddy_runtime import (  # noqa: E402
    apply_workbuddy_browser_policy,
    is_workbuddy_host,
    workbuddy_runtime_status,
)
from extract_visible_items import resolve_backend  # noqa: E402
from run_reassign_batch import BrowserRunner, choose_backend  # noqa: E402


def browser_args(**overrides):
    values = {
        'channel': 'chromium',
        'user_data_dir': None,
        'cdp_url': None,
        'headless': False,
        'url': None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class WorkBuddyRuntimeTests(unittest.TestCase):
    def test_host_detection_is_explicit_not_process_name_heuristic(self):
        self.assertFalse(is_workbuddy_host({}))
        self.assertFalse(is_workbuddy_host({'SHELL': '/Applications/WorkBuddy.app/helper'}))
        self.assertTrue(is_workbuddy_host({'XHS_HOST': 'workbuddy'}))
        self.assertTrue(is_workbuddy_host({'XHS_HOST': ' WorkBuddy '}))

    def test_workbuddy_forces_dedicated_playwright_chromium_on_macos(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'XHS_WORKBUDDY_PLATFORM': 'darwin',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
            }
            args = browser_args()
            backend = apply_workbuddy_browser_policy('auto', args, env=env)
            self.assertEqual(backend, 'playwright')
            self.assertEqual(args.channel, 'chromium')
            self.assertEqual(
                Path(args.user_data_dir).resolve(),
                (data_dir / 'playwright-profile').resolve(),
            )
            self.assertIsNone(args.cdp_url)
            self.assertFalse(args.headless)

    def test_windows_workbuddy_forces_managed_edge_with_dedicated_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'XHS_WORKBUDDY_PLATFORM': 'win32',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
            }
            args = browser_args()
            backend = apply_workbuddy_browser_policy('auto', args, env=env)
            self.assertEqual(backend, 'playwright')
            self.assertEqual(args.channel, 'msedge')
            self.assertEqual(
                Path(args.user_data_dir).resolve(),
                (data_dir / 'playwright-profile').resolve(),
            )
            self.assertIsNone(args.cdp_url)
            self.assertFalse(args.headless)

    def test_windows_runtime_status_reports_managed_edge_not_user_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'XHS_WORKBUDDY_PLATFORM': 'win32',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
            }
            status = workbuddy_runtime_status(env)
            self.assertEqual(status['browser_channel'], 'msedge')
            self.assertEqual(status['browser_product'], 'Microsoft Edge')
            self.assertTrue(status['dedicated_profile'])
            self.assertFalse(status['uses_user_browser_profile'])

    def test_workbuddy_rejects_every_external_browser_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
            }
            for backend in ('arc', 'chrome', 'safari', 'macos-arc', 'macos-chrome', 'macos-safari'):
                with self.subTest(backend=backend):
                    with self.assertRaisesRegex(RuntimeError, '只允许使用 WorkBuddy Plugin'):
                        apply_workbuddy_browser_policy(backend, browser_args(), env=env)

    def test_workbuddy_rejects_cdp_system_channel_headless_and_other_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
            }
            invalid_args = [
                browser_args(cdp_url='http://127.0.0.1:9222'),
                browser_args(channel='chrome'),
                browser_args(headless=True),
                browser_args(user_data_dir=str(data_dir / 'other-profile')),
            ]
            for args in invalid_args:
                with self.subTest(args=args):
                    with self.assertRaises(RuntimeError):
                        apply_workbuddy_browser_policy('playwright', args, env=env)

    def test_workbuddy_profile_must_be_inside_plugin_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir.parent / 'outside-profile'),
            }
            with self.assertRaisesRegex(RuntimeError, 'CODEBUDDY_PLUGIN_DATA'):
                apply_workbuddy_browser_policy('playwright', browser_args(), env=env)

    def test_non_workbuddy_keeps_existing_explicit_backend(self):
        args = browser_args(channel='chrome', user_data_dir='/tmp/user-selected')
        backend = apply_workbuddy_browser_policy('chrome', args, env={})
        self.assertEqual(backend, 'chrome')
        self.assertEqual(args.channel, 'chrome')
        self.assertEqual(args.user_data_dir, '/tmp/user-selected')

    def test_runtime_status_has_one_machine_readable_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
            }
            with patch.dict(os.environ, env, clear=True):
                status = workbuddy_runtime_status()
            self.assertEqual(status['host'], 'workbuddy')
            self.assertEqual(status['browser_backend'], 'playwright')
            self.assertEqual(status['browser_channel'], 'chromium')
            self.assertTrue(status['dedicated_profile'])
            self.assertFalse(status['external_browser_allowed'])

    def test_existing_browser_entrypoints_apply_the_same_workbuddy_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
            }
            with patch.dict(os.environ, env, clear=True):
                extract_args = browser_args()
                execute_args = browser_args()
                self.assertEqual(resolve_backend('auto', extract_args), 'playwright')
                self.assertEqual(choose_backend('auto', execute_args), 'playwright')
                self.assertEqual(
                    Path(extract_args.user_data_dir).resolve(),
                    (data_dir / 'playwright-profile').resolve(),
                )
                self.assertEqual(
                    Path(execute_args.user_data_dir).resolve(),
                    (data_dir / 'playwright-profile').resolve(),
                )

    def test_browser_runner_opens_playwright_after_workbuddy_maps_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            env = {
                'XHS_HOST': 'workbuddy',
                'CODEBUDDY_PLUGIN_DATA': str(data_dir),
                'XHS_PLAYWRIGHT_PROFILE': str(data_dir / 'playwright-profile'),
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(BrowserRunner, '_open_playwright') as open_playwright,
            ):
                runner = BrowserRunner('auto', browser_args())
            self.assertEqual(runner.backend, 'playwright')
            open_playwright.assert_called_once_with()

    def test_workbuddy_rejects_direct_capture_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env['XHS_HOST'] = 'workbuddy'
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / 'extract_visible_items.py'),
                    str(Path(tmp) / 'visible.json'),
                    '--backend', 'playwright',
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('xhs_workbuddy_capture', proc.stderr)

    def test_playwright_startup_navigation_failure_closes_created_context(self):
        class FailingPage:
            def goto(self, *_args, **_kwargs):
                raise RuntimeError('navigation failed')

            def wait_for_load_state(self, *_args, **_kwargs):
                raise AssertionError('must not continue after goto failure')

        page = FailingPage()

        class Context:
            pages = [page]

            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        context = Context()

        class Playwright:
            def __init__(self):
                self.stopped = False
                self.chromium = types.SimpleNamespace(
                    launch_persistent_context=lambda *_args, **_kwargs: context,
                )

            def stop(self):
                self.stopped = True

        playwright = Playwright()
        manager = types.SimpleNamespace(start=lambda: playwright)
        fake_playwright = types.ModuleType('playwright')
        fake_playwright.__path__ = []
        fake_sync_api = types.ModuleType('playwright.sync_api')
        fake_sync_api.sync_playwright = lambda: manager

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            sys.modules,
            {
                'playwright': fake_playwright,
                'playwright.sync_api': fake_sync_api,
            },
        ):
            with self.assertRaisesRegex(RuntimeError, 'navigation failed'):
                BrowserRunner(
                    'playwright',
                    browser_args(
                        user_data_dir=str(Path(tmp) / 'profile'),
                        url='https://www.xiaohongshu.com/explore',
                    ),
                )

        self.assertTrue(context.closed)
        self.assertTrue(playwright.stopped)


if __name__ == '__main__':
    unittest.main()
