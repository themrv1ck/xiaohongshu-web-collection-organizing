#!/usr/bin/env python3
"""Build and validate channel-specific Xiaohongshu Skill packages."""

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
    'bin/run-node.sh',
    'server/xhs-workbuddy-mcp.mjs',
    'scripts/enable_workbuddy_mcp.py',
    'scripts/workbuddy_bridge.py',
    'scripts/workbuddy_runtime.py',
)
SKILLHUB_REQUIRED_FILES = (
    'SKILL.md',
    'LICENSE.txt',
    'scripts/enable_workbuddy_mcp.py',
)
RELEASE_EXCLUDED_PREFIXES = ('tests/', 'workbuddy-plugin-src/')
RELEASE_EXCLUDED_FILES = {'.gitignore'}
SKILLHUB_EXCLUDED_PREFIXES = (
    '.codebuddy-plugin/',
    'assets/',
    'bin/',
    'server/',
)
SKILLHUB_EXCLUDED_FILES = {
    '.mcp.json',
    'README.md',
    'manifest.yaml',
    'requirements-workbuddy.txt',
    'references/distribution-readiness-audit.md',
    'scripts/build_redskill_package.py',
    'scripts/workbuddy_bridge.py',
}
SUPPORTED_CHANNELS = {'redskill', 'skillhub'}
MAX_PACKAGE_FILES = 100
SKILLHUB_ALLOWED_EXTENSIONS = frozenset({
    '.md', '.txt', '.json', '.yaml', '.yml', '.html', '.css', '.csv', '.pdf',
    '.toml', '.xml', '.xsd', '.xsl', '.dtd', '.ini', '.cfg', '.env',
    '.js', '.cjs', '.mjs', '.ts', '.py', '.sh', '.rb', '.go', '.rs', '.java',
    '.kt', '.lua', '.sql', '.r', '.bat', '.ps1', '.zsh', '.bash',
    '.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp', '.ico',
    '.doc', '.xls', '.ppt', '.docx', '.xlsx', '.pptx',
})


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


def _skillhub_skill_text(text, version):
    """Add SkillHub-supported release metadata without changing source SKILL.md."""
    lines = text.splitlines()
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise ValueError('SKILL.md frontmatter 未闭合') from exc
    release_fields = [
        'version: "{}"'.format(version),
        'license: MIT',
        'compatibility: "Requires network access for user-authorized '
        'Xiaohongshu page and image requests; WorkBuddy users install the '
        'GitHub Plugin on first use."',
    ]
    return '\n'.join(lines[:end] + release_fields + lines[end:]) + '\n'


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


def _release_files(repo_root, channel):
    return [
        relative for relative in _tracked_files(repo_root)
        if relative.as_posix() not in RELEASE_EXCLUDED_FILES
        and not relative.as_posix().startswith(RELEASE_EXCLUDED_PREFIXES)
        and (
            channel != 'skillhub'
            or (
                relative.as_posix() not in SKILLHUB_EXCLUDED_FILES
                and not relative.as_posix().startswith(
                    SKILLHUB_EXCLUDED_PREFIXES
                )
            )
        )
    ]


def _json_version(archive, path):
    try:
        payload = json.loads(archive.read(path).decode('utf-8'))
    except KeyError:
        raise ValueError('缺少版本文件: ' + path)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('版本文件无法解析: ' + path) from exc
    version = payload.get('version')
    if not isinstance(version, str) or not version:
        raise ValueError('版本文件缺少 version: ' + path)
    return version


def validate_redskill_archive(archive_path, channel='redskill'):
    archive_path = Path(archive_path)
    if channel not in SUPPORTED_CHANNELS:
        raise ValueError('不支持的发布渠道: ' + channel)
    errors = []
    with zipfile.ZipFile(archive_path) as archive:
        file_names = [
            name for name in archive.namelist()
            if name and not name.endswith('/')
        ]
        if len(file_names) > MAX_PACKAGE_FILES:
            errors.append(
                'ZIP 文件数 {} 超过 SkillHub 默认上限 {}'.format(
                    len(file_names), MAX_PACKAGE_FILES
                )
            )
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
        allowed_metadata = {'name', 'description'}
        if channel == 'skillhub':
            allowed_metadata.update({'version', 'license', 'compatibility'})
        unexpected_keys = sorted(set(metadata) - allowed_metadata)
        if unexpected_keys:
            errors.append(
                'SKILL.md frontmatter 含不支持字段: ' + ', '.join(unexpected_keys)
            )
        required_files = (
            WORKBUDDY_REQUIRED_FILES
            if channel == 'redskill'
            else SKILLHUB_REQUIRED_FILES
        )
        for relative in required_files:
            if root + '/' + relative not in file_names:
                label = 'WorkBuddy 运行文件' if channel == 'redskill' else 'SkillHub 必需文件'
                errors.append('缺少 {}: {}'.format(label, relative))
        if channel == 'skillhub':
            for field in ('version', 'license', 'compatibility'):
                if not metadata.get(field):
                    errors.append('SkillHub SKILL.md 缺少发布字段: ' + field)
            forbidden = sorted(
                name for name in file_names
                if (
                    PurePosixPath(name).relative_to(root).as_posix()
                    in SKILLHUB_EXCLUDED_FILES
                    or PurePosixPath(name).relative_to(root).as_posix().startswith(
                        SKILLHUB_EXCLUDED_PREFIXES
                    )
                )
            )
            if forbidden:
                errors.append(
                    'SkillHub 包不得包含 Plugin/MCP 或维护文件: '
                    + ', '.join(forbidden)
                )
        version = ''
        if channel == 'redskill':
            try:
                version = _manifest_version(
                    archive.read(root + '/manifest.yaml').decode('utf-8')
                )
                plugin_version = _json_version(
                    archive, root + '/.codebuddy-plugin/plugin.json'
                )
                marketplace = json.loads(
                    archive.read(
                        root + '/.codebuddy-plugin/marketplace.json'
                    ).decode('utf-8')
                )
                marketplace_version = marketplace['plugins'][0]['version']
                runtime_text = archive.read(
                    root + '/server/xhs-workbuddy-mcp.mjs'
                ).decode('utf-8')
                skill_text = archive.read(skill_path).decode('utf-8')
            except KeyError:
                errors.append('缺少版本文件: manifest.yaml')
            except (IndexError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                errors.append('版本一致性文件无法解析: ' + str(exc))
            else:
                versions = {
                    'manifest.yaml': version,
                    '.codebuddy-plugin/plugin.json': plugin_version,
                    '.codebuddy-plugin/marketplace.json': marketplace_version,
                }
                for label, actual in versions.items():
                    if actual != version:
                        errors.append(
                            '{} 版本 {} 与 manifest {} 不一致'.format(
                                label, actual, version
                            )
                        )
                if 'PLUGIN_VERSION = "{}"'.format(version) not in runtime_text:
                    errors.append('MCP 构建产物版本与 manifest 不一致')
                if 'plugin_version={}'.format(version) not in skill_text:
                    errors.append('SKILL.md 要求的 Plugin 版本与 manifest 不一致')
        if any('/.git/' in '/' + name or name.endswith('.pyc') for name in file_names):
            errors.append('ZIP 含禁止发布的 Git 或 Python 缓存文件')
        disallowed_files = sorted(
            name for name in file_names
            if not any(
                name.lower().endswith(extension)
                for extension in SKILLHUB_ALLOWED_EXTENSIONS
            )
        )
        if disallowed_files:
            errors.append(
                'ZIP 含 SkillHub 不允许的文件类型: '
                + ', '.join(disallowed_files)
            )
        if any(info.file_size > 10 * 1024 * 1024 for info in archive.infolist()):
            errors.append('ZIP 中存在超过 10 MB 的单个文件')
        total_size = sum(info.file_size for info in archive.infolist())
        if total_size > 30 * 1024 * 1024:
            errors.append('ZIP 解压后总大小超过 30 MB')
    return errors


def build_redskill_package(repo_root, output_dir, channel='redskill'):
    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir).resolve()
    if channel not in SUPPORTED_CHANNELS:
        raise ValueError('不支持的发布渠道: ' + channel)
    skill_path = repo_root / 'SKILL.md'
    manifest_path = repo_root / 'manifest.yaml'
    metadata = _frontmatter(skill_path.read_text(encoding='utf-8'))
    skill_name = metadata.get('name', '')
    if not skill_name:
        raise ValueError('SKILL.md frontmatter 缺少 name')
    version = _manifest_version(manifest_path.read_text(encoding='utf-8'))
    tracked_files = _tracked_files(repo_root)
    release_files = _release_files(repo_root, channel)
    missing_required = [
        relative for relative in WORKBUDDY_REQUIRED_FILES
        if Path(relative) not in tracked_files
    ]
    if missing_required:
        raise ValueError('仓库缺少 WorkBuddy 运行文件: ' + ', '.join(missing_required))
    if len(release_files) > MAX_PACKAGE_FILES:
        raise ValueError(
            '发布包文件数 {} 超过上限 {}'.format(
                len(release_files), MAX_PACKAGE_FILES
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    package_dir = output_dir / skill_name
    archive_path = output_dir / '{}-{}-{}.zip'.format(
        skill_name, channel, version
    )

    if package_dir.exists():
        shutil.rmtree(str(package_dir))
    if archive_path.exists():
        archive_path.unlink()
    package_dir.mkdir()

    for relative in release_files:
        source = repo_root / relative
        destination = package_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(destination))

    if channel == 'skillhub':
        packaged_skill = package_dir / 'SKILL.md'
        packaged_skill.write_text(
            _skillhub_skill_text(
                packaged_skill.read_text(encoding='utf-8'), version
            ),
            encoding='utf-8',
        )

    with zipfile.ZipFile(
        archive_path, 'w', compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for relative in release_files:
            archive.write(
                str(package_dir / relative),
                str(PurePosixPath(skill_name) / PurePosixPath(relative.as_posix())),
            )

    validation_errors = validate_redskill_archive(archive_path, channel=channel)
    if validation_errors:
        raise ValueError('; '.join(validation_errors))
    return {
        'skill_name': skill_name,
        'version': version,
        'channel': channel,
        'source_file_count': len(tracked_files),
        'packaged_file_count': len(release_files),
        'package_dir': str(package_dir),
        'archive_path': str(archive_path),
        'validation_errors': validation_errors,
    }


def main():
    parser = argparse.ArgumentParser(
        description='生成并校验 SkillHub/RedSkill 完整运行包。'
    )
    parser.add_argument(
        '--repo-root',
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument(
        '--channel', choices=sorted(SUPPORTED_CHANNELS), default='redskill'
    )
    parser.add_argument('--validate-only', type=Path)
    args = parser.parse_args()
    if args.validate_only:
        errors = validate_redskill_archive(
            args.validate_only, channel=args.channel
        )
        print(json.dumps({'valid': not errors, 'errors': errors}, ensure_ascii=False))
        return 1 if errors else 0
    result = build_redskill_package(
        args.repo_root, args.output_dir, channel=args.channel
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
