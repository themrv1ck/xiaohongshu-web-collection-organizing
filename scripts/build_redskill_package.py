#!/usr/bin/env python3
"""Build and validate the Xiaohongshu RedSkill distribution package."""

import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath


WORKBUDDY_REQUIRED_FILES = (
    '.codebuddy-plugin/marketplace.json',
    '.codebuddy-plugin/plugin.json',
    '.mcp.json',
    'bin/run-node',
    'server/xhs-workbuddy-mcp.mjs',
    'scripts/enable_workbuddy_mcp.py',
    'scripts/workbuddy_bridge.py',
    'scripts/workbuddy_runtime.py',
    'workbuddy-plugin-src/package.json',
    'workbuddy-plugin-src/server.mjs',
)


def _frontmatter(text):
    lines = text.splitlines()
    if len(lines) < 4 or lines[0].strip() != '---':
        raise ValueError('SKILL.md 缺少 YAML frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise ValueError('SKILL.md frontmatter 未闭合') from exc
    values = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if ':' not in line:
            raise ValueError('SKILL.md frontmatter 行格式错误: ' + line)
        key, value = line.split(':', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _manifest_version(text):
    for line in text.splitlines():
        if line.startswith('version:'):
            return line.split(':', 1)[1].strip().strip('"').strip("'")
    raise ValueError('manifest.yaml 缺少 version')


def _tracked_files(repo_root):
    completed = subprocess.run(
        ['git', 'ls-files', '-z'],
        cwd=str(repo_root),
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        Path(raw.decode('utf-8'))
        for raw in completed.stdout.split(b'\0')
        if raw
    ]


def validate_redskill_archive(archive_path):
    archive_path = Path(archive_path)
    errors = []
    with zipfile.ZipFile(archive_path) as archive:
        file_names = [
            name for name in archive.namelist()
            if name and not name.endswith('/')
        ]
        roots = {PurePosixPath(name).parts[0] for name in file_names}
        if len(roots) != 1:
            return ['ZIP 必须且只能包含一个顶层 Skill 目录']
        root = next(iter(roots))
        skill_path = root + '/SKILL.md'
        if skill_path not in file_names:
            return ['SKILL.md 必须位于 ZIP 唯一顶层目录的根部']
        try:
            metadata = _frontmatter(archive.read(skill_path).decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            return [str(exc)]
        skill_name = metadata.get('name', '')
        if root != skill_name:
            errors.append(
                'ZIP 顶层目录 {} 与 SKILL.md name {} 不一致'.format(
                    root, skill_name or '<缺失>'
                )
            )
        unexpected_keys = sorted(set(metadata) - {'name', 'description'})
        if unexpected_keys:
            errors.append(
                'SKILL.md frontmatter 含不支持字段: ' + ', '.join(unexpected_keys)
            )
        for relative in WORKBUDDY_REQUIRED_FILES:
            if root + '/' + relative not in file_names:
                errors.append('缺少 WorkBuddy 运行文件: ' + relative)
        if any('/.git/' in '/' + name or name.endswith('.pyc') for name in file_names):
            errors.append('ZIP 含禁止发布的 Git 或 Python 缓存文件')
        if any(info.file_size > 10 * 1024 * 1024 for info in archive.infolist()):
            errors.append('ZIP 中存在超过 10 MB 的单个文件')
        total_size = sum(info.file_size for info in archive.infolist())
        if total_size > 30 * 1024 * 1024:
            errors.append('ZIP 解压后总大小超过 30 MB')
    return errors


def build_redskill_package(repo_root, output_dir):
    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir).resolve()
    skill_path = repo_root / 'SKILL.md'
    manifest_path = repo_root / 'manifest.yaml'
    metadata = _frontmatter(skill_path.read_text(encoding='utf-8'))
    skill_name = metadata.get('name', '')
    if not skill_name:
        raise ValueError('SKILL.md frontmatter 缺少 name')
    version = _manifest_version(manifest_path.read_text(encoding='utf-8'))
    tracked_files = _tracked_files(repo_root)
    missing_required = [
        relative for relative in WORKBUDDY_REQUIRED_FILES
        if Path(relative) not in tracked_files
    ]
    if missing_required:
        raise ValueError('仓库缺少 WorkBuddy 运行文件: ' + ', '.join(missing_required))

    output_dir.mkdir(parents=True, exist_ok=True)
    package_dir = output_dir / skill_name
    archive_path = output_dir / '{}-redskill-{}.zip'.format(skill_name, version)

    if package_dir.exists():
        shutil.rmtree(str(package_dir))
    if archive_path.exists():
        archive_path.unlink()
    package_dir.mkdir()

    for relative in tracked_files:
        source = repo_root / relative
        destination = package_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(destination))

    with zipfile.ZipFile(
        archive_path, 'w', compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for relative in tracked_files:
            archive.write(
                str(repo_root / relative),
                str(PurePosixPath(skill_name) / PurePosixPath(relative.as_posix())),
            )

    validation_errors = validate_redskill_archive(archive_path)
    if validation_errors:
        raise ValueError('; '.join(validation_errors))
    return {
        'skill_name': skill_name,
        'version': version,
        'tracked_file_count': len(tracked_files),
        'package_dir': str(package_dir),
        'archive_path': str(archive_path),
        'validation_errors': validation_errors,
    }


def main():
    parser = argparse.ArgumentParser(
        description='生成并校验 RedSkill 文件夹、ZIP 和单文件入口。'
    )
    parser.add_argument(
        '--repo-root',
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--validate-only', type=Path)
    args = parser.parse_args()
    if args.validate_only:
        errors = validate_redskill_archive(args.validate_only)
        print(json.dumps({'valid': not errors, 'errors': errors}, ensure_ascii=False))
        return 1 if errors else 0
    result = build_redskill_package(args.repo_root, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
