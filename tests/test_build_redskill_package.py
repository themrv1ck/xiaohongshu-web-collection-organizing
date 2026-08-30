#!/usr/bin/env python3
import json
import subprocess
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
    def test_builds_redskill_archive_as_transparent_bootstrap_skill(self):
        if not (ROOT / '.git').exists():
            self.skipTest('打包器只在 Git 源码仓库中构建发布包')
        with tempfile.TemporaryDirectory() as tmp:
            result = build_redskill_package(ROOT, Path(tmp))

            archive_path = Path(result['archive_path'])
            self.assertTrue(archive_path.is_file())
            self.assertEqual(result['skill_name'], 'xiaohongshu-web-collection-organizing')
            self.assertEqual(result['version'], '2.2.0')
            self.assertEqual(result['channel'], 'redskill')
            self.assertLessEqual(result['packaged_file_count'], 100)
            self.assertEqual(result['validation_errors'], [])

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                skill_text = archive.read(
                    'xiaohongshu-web-collection-organizing/SKILL.md'
                ).decode('utf-8')
            root = 'xiaohongshu-web-collection-organizing/'
            self.assertEqual(names, {
                root + 'SKILL.md',
                root + 'LICENSE.txt',
                root + 'scripts/enable_workbuddy_mcp.py',
            })
            self.assertIn('发布版本：`2.2.0`', skill_text)
            self.assertIn('不自动运营账号', skill_text)
            self.assertIn('不读取系统浏览器 Cookie', skill_text)
            self.assertIn(
                'themrv1ck/xiaohongshu-web-collection-organizing',
                skill_text,
            )

    def test_builds_skillhub_archive_as_pure_skill_with_bootstrap_installer(self):
        if not (ROOT / '.git').exists():
            self.skipTest('打包器只在 Git 源码仓库中构建发布包')
        with tempfile.TemporaryDirectory() as tmp:
            result = build_redskill_package(
                ROOT, Path(tmp), channel='skillhub'
            )
            self.assertTrue(
                result['archive_path'].endswith('-skillhub-2.2.0.zip')
            )
            self.assertEqual(result['validation_errors'], [])

            with zipfile.ZipFile(result['archive_path']) as archive:
                names = set(archive.namelist())
                skill_text = archive.read(
                    'xiaohongshu-web-collection-organizing/SKILL.md'
                ).decode('utf-8')
            self.assertIn(
                'xiaohongshu-web-collection-organizing/LICENSE.txt', names
            )
            root = 'xiaohongshu-web-collection-organizing/'
            self.assertIn(root + 'SKILL.md', names)
            self.assertIn(root + 'scripts/enable_workbuddy_mcp.py', names)
            self.assertIn(root + 'scripts/workbuddy_runtime.py', names)
            self.assertNotIn(root + '.mcp.json', names)
            self.assertNotIn(root + '.codebuddy-plugin/plugin.json', names)
            self.assertNotIn(root + 'bin/run-node.sh', names)
            self.assertNotIn(root + 'server/xhs-workbuddy-mcp.mjs', names)
            self.assertNotIn(root + 'scripts/workbuddy_bridge.py', names)
            self.assertNotIn(root + 'README.md', names)
            self.assertNotIn(root + 'manifest.yaml', names)
            self.assertIn('version: "2.2.0"', skill_text)
            self.assertIn('license: MIT', skill_text)
            self.assertIn('compatibility: "Requires network access', skill_text)
            validation = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / 'build_redskill_package.py'),
                    '--channel',
                    'skillhub',
                    '--validate-only',
                    result['archive_path'],
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(validation.returncode, 0, validation.stderr)
            self.assertTrue(json.loads(validation.stdout)['valid'])

    def test_skillhub_validation_rejects_plugin_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'skillhub-with-plugin.zip'
            root = 'xiaohongshu-web-collection-organizing/'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr(
                    root + 'SKILL.md',
                    '---\nname: xiaohongshu-web-collection-organizing\n'
                    'description: test\n---\n',
                )
                archive.writestr(root + 'LICENSE.txt', 'license text\n')
                archive.writestr(
                    root + 'scripts/enable_workbuddy_mcp.py', ''
                )
                archive.writestr(root + '.mcp.json', '{}')

            errors = validate_redskill_archive(
                archive_path, channel='skillhub'
            )
            self.assertTrue(any(
                'SkillHub 包不得包含 Plugin/MCP 或维护文件' in error
                and '.mcp.json' in error
                for error in errors
            ))

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

    def test_rejects_redskill_archive_missing_bootstrap_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'missing-runtime.zip'
            root = 'xiaohongshu-web-collection-organizing/'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr(
                    root + 'SKILL.md',
                    '---\nname: xiaohongshu-web-collection-organizing\n'
                    'description: test\n---\n',
                )
                archive.writestr(root + 'LICENSE.txt', 'license text\n')

            errors = validate_redskill_archive(archive_path)
            self.assertIn(
                '缺少 RED Skill 必需文件: scripts/enable_workbuddy_mcp.py',
                errors,
            )

    def test_rejects_redskill_archive_with_bundled_plugin_or_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / 'mixed-versions.zip'
            root = 'xiaohongshu-web-collection-organizing/'
            source = ROOT / 'templates' / 'redskill.SKILL.md'
            skill_text = source.read_text(encoding='utf-8').replace(
                '{{VERSION}}', '2.2.0'
            )
            files = {
                'SKILL.md': skill_text,
                'LICENSE.txt': 'license text\n',
                'scripts/enable_workbuddy_mcp.py': '',
                '.mcp.json': '{}',
            }
            with zipfile.ZipFile(archive_path, 'w') as archive:
                for relative, content in files.items():
                    archive.writestr(root + relative, content)

            errors = validate_redskill_archive(archive_path)
            self.assertTrue(any(
                'RED Skill 上传包含非必要运行或开发文件' in error
                and '.mcp.json' in error
                for error in errors
            ))


if __name__ == '__main__':
    unittest.main()
