#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from build_redskill_package import (  # noqa: E402
    build_redskill_package,
    validate_redskill_archive,
)


class BuildRedSkillPackageTests(unittest.TestCase):
    def test_builds_complete_archive_with_matching_skill_root(self):
        if not (ROOT / '.git').exists():
            self.skipTest('打包器只在 Git 源码仓库中构建发布包')
        with tempfile.TemporaryDirectory() as tmp:
            result = build_redskill_package(ROOT, Path(tmp))

            archive_path = Path(result['archive_path'])
            self.assertTrue(archive_path.is_file())
            self.assertEqual(result['skill_name'], 'xiaohongshu-web-collection-organizing')
            self.assertEqual(result['version'], '2.0.7')
            self.assertEqual(result['channel'], 'redskill')
            self.assertLessEqual(result['packaged_file_count'], 100)
            self.assertEqual(result['validation_errors'], [])

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
            root = 'xiaohongshu-web-collection-organizing/'
            self.assertIn(root + 'SKILL.md', names)
            self.assertIn(root + '.codebuddy-plugin/plugin.json', names)
            self.assertIn(root + '.mcp.json', names)
            self.assertIn(root + 'server/xhs-workbuddy-mcp.mjs', names)
            self.assertFalse(any(name.startswith(root + 'tests/') for name in names))
            self.assertFalse(
                any(name.startswith(root + 'workbuddy-plugin-src/') for name in names)
            )
            self.assertIn(root + 'LICENSE.txt', names)
            self.assertIn(root + 'bin/run-node.sh', names)
            self.assertIn(root + 'scripts/ocr_image.swift.txt', names)

    def test_builds_skillhub_archive_from_same_runtime_files(self):
        if not (ROOT / '.git').exists():
            self.skipTest('打包器只在 Git 源码仓库中构建发布包')
        with tempfile.TemporaryDirectory() as tmp:
            result = build_redskill_package(
                ROOT, Path(tmp), channel='skillhub'
            )
            self.assertTrue(
                result['archive_path'].endswith('-skillhub-2.0.7.zip')
            )
            self.assertEqual(result['validation_errors'], [])

            with zipfile.ZipFile(result['archive_path']) as archive:
                names = set(archive.namelist())
            self.assertIn(
                'xiaohongshu-web-collection-organizing/LICENSE.txt', names
            )

    def test_rejects_archive_with_platform_forbidden_license(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'forbidden-license.zip'
            root = 'xiaohongshu-web-collection-organizing/'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr(
                    root + 'SKILL.md',
                    '---\nname: xiaohongshu-web-collection-organizing\n'
                    'description: test\n---\n',
                )
                archive.writestr(root + 'LICENSE', 'license text\n')

            errors = validate_redskill_archive(archive_path)
            self.assertIn(
                'ZIP 含 SkillHub 不允许的文件类型: '
                'xiaohongshu-web-collection-organizing/LICENSE',
                errors,
            )

    def test_rejects_archive_when_root_does_not_match_skill_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'bad-root.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr(
                    'wrong-name/SKILL.md',
                    '---\nname: expected-name\ndescription: test\n---\n',
                )

            errors = validate_redskill_archive(archive_path)
            self.assertIn(
                'ZIP 顶层目录 wrong-name 与 SKILL.md name expected-name 不一致',
                errors,
            )

    def test_rejects_archive_missing_workbuddy_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'missing-runtime.zip'
            root = 'xiaohongshu-web-collection-organizing/'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr(
                    root + 'SKILL.md',
                    '---\nname: xiaohongshu-web-collection-organizing\n'
                    'description: test\n---\n',
                )
                archive.writestr(root + 'manifest.yaml', 'version: 2.0.7\n')

            errors = validate_redskill_archive(archive_path)
            self.assertIn(
                '缺少 WorkBuddy 运行文件: .codebuddy-plugin/plugin.json',
                errors,
            )

    def test_rejects_mixed_release_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'mixed-versions.zip'
            root = 'xiaohongshu-web-collection-organizing/'
            files = {
                'SKILL.md': (
                    '---\nname: xiaohongshu-web-collection-organizing\n'
                    'description: test\n---\nplugin_version=2.0.7\n'
                ),
                'manifest.yaml': 'version: 2.0.7\n',
                '.codebuddy-plugin/plugin.json': json.dumps({
                    'version': '2.0.5',
                }),
                '.codebuddy-plugin/marketplace.json': json.dumps({
                    'plugins': [{'version': '2.0.7'}],
                }),
                '.mcp.json': '{}',
                'bin/run-node.sh': '#!/bin/sh\n',
                'server/xhs-workbuddy-mcp.mjs': (
                    'var PLUGIN_VERSION = "2.0.7";\n'
                ),
                'scripts/enable_workbuddy_mcp.py': '',
                'scripts/workbuddy_bridge.py': '',
                'scripts/workbuddy_runtime.py': '',
            }
            with zipfile.ZipFile(archive_path, 'w') as archive:
                for relative, content in files.items():
                    archive.writestr(root + relative, content)

            errors = validate_redskill_archive(archive_path)
            self.assertIn(
                '.codebuddy-plugin/plugin.json 版本 2.0.5 与 manifest 2.0.7 不一致',
                errors,
            )


if __name__ == '__main__':
    unittest.main()
