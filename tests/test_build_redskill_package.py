#!/usr/bin/env python3
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
            self.assertEqual(result['version'], '2.0.5')
            self.assertEqual(result['validation_errors'], [])

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
            root = 'xiaohongshu-web-collection-organizing/'
            self.assertIn(root + 'SKILL.md', names)
            self.assertIn(root + '.codebuddy-plugin/plugin.json', names)
            self.assertIn(root + '.mcp.json', names)
            self.assertIn(root + 'server/xhs-workbuddy-mcp.mjs', names)
            self.assertIn(root + 'workbuddy-plugin-src/server.mjs', names)

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
                archive.writestr(root + 'manifest.yaml', 'version: 2.0.5\n')

            errors = validate_redskill_archive(archive_path)
            self.assertIn(
                '缺少 WorkBuddy 运行文件: .codebuddy-plugin/plugin.json',
                errors,
            )


if __name__ == '__main__':
    unittest.main()
