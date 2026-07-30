#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from video_content_common import ANALYSIS_PROVIDERS, local_capability_preflight, video_content_environment
from xhs_ocr_common import swift_vision_ready, tesseract_language_ready


def check(command):
    return shutil.which(command) is not None


def run_ok(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).returncode == 0
    except Exception:
        return False


def chrome_candidates(system):
    candidates = []
    if system == 'Darwin':
        candidates.extend([
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        ])
    elif system == 'Windows':
        roots = [os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'), os.environ.get('LOCALAPPDATA')]
        for root in [r for r in roots if r]:
            candidates.extend([
                str(Path(root) / 'Google/Chrome/Application/chrome.exe'),
                str(Path(root) / 'Microsoft/Edge/Application/msedge.exe'),
            ])
    else:
        candidates.extend(['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/microsoft-edge'])
    return candidates


def executable_exists(path):
    return bool(path) and Path(path).exists()


def python_import_ok(module):
    return run_ok([shutil.which('python3') or shutil.which('python') or 'python', '-c', f'import {module}; print("ok")'])


def main():
    parser = argparse.ArgumentParser(description='检查小红书整理基础环境；首次只读预检与功能启用后的完整检查严格分离。')
    parser.add_argument(
        '--capability-preflight', action='store_true',
        help='首次使用的本地只读预检：不访问浏览器、不联网、不安装、不加载大模型',
    )
    parser.add_argument('--ocr', action='store_true', help='用户开启图文 OCR 后，检测本机中文 OCR 能力')
    parser.add_argument('--video-content', action='store_true', help='检测“根据视频实际内容分类”可选功能')
    parser.add_argument('--browser', choices=['arc', 'chrome', 'safari', 'edge', 'brave', 'firefox'])
    parser.add_argument('--extractor-root')
    parser.add_argument('--mimo-asr-root', help='只读预检使用的 MiMo ASR 安装根目录；也可用 XHS_MIMO_ASR_ROOT')
    parser.add_argument('--analysis-provider', choices=ANALYSIS_PROVIDERS)
    parser.add_argument('--analysis-command', help='analysis-provider=command 时必填；指定 Agent/模型分析命令')
    parser.add_argument('--mimo-vl-root', help='独立 MiMo-VL 安装根目录；也可用 XHS_MIMO_VL_ROOT')
    parser.add_argument('--visual-analysis', action='store_true', help='同时检测完整时轴画面分析能力；不传表示仅文字稿')
    parser.add_argument('--check-login-state', action='store_true', help='只确认所选浏览器能否提供小红书登录态，不输出 cookie 内容')
    parser.add_argument(
        '--host-visual-capability', choices=['ready', 'unavailable', 'unknown'], default='unknown',
        help='由当前宿主 Agent 声明自身看图能力；无法证明时必须使用 unknown',
    )
    parser.add_argument('--host-visual-name', default='', help='宿主声明的视觉 Agent/模型名称')
    args = parser.parse_args()
    if args.video_content and not args.browser:
        parser.error('--video-content 必须同时显式指定 --browser；禁止自动选择外部浏览器')
    if args.video_content and not args.analysis_provider:
        parser.error('--video-content 必须同时显式指定 --analysis-provider')
    if args.analysis_provider == 'command' and not args.analysis_command:
        parser.error('--analysis-provider command 必须同时提供 --analysis-command')
    if args.visual_analysis and not args.video_content:
        parser.error('--visual-analysis 必须与 --video-content 同时使用')
    if args.capability_preflight and any((
        args.ocr,
        args.video_content,
        args.browser,
        args.analysis_provider,
        args.analysis_command,
        args.visual_analysis,
        args.check_login_state,
    )):
        parser.error('--capability-preflight 是独立只读模式，不能与功能启用、浏览器或 provider 参数同时使用')
    if (args.host_visual_capability != 'unknown' or args.host_visual_name) and not args.capability_preflight:
        parser.error('--host-visual-capability/--host-visual-name 只能与 --capability-preflight 同时使用')
    if args.mimo_asr_root and not args.capability_preflight:
        parser.error('--mimo-asr-root 只能与 --capability-preflight 同时使用')
    system = platform.system()
    is_macos = system == 'Darwin'
    is_windows = system == 'Windows'
    if args.capability_preflight:
        tesseract_found = check('tesseract')
        tesseract_chi_sim = tesseract_language_ready('chi_sim') if tesseract_found else False
        macos_swift = check('swift') if is_macos else False
        macos_swift_vision = swift_vision_ready() if is_macos and macos_swift else False
        if macos_swift and macos_swift_vision:
            ocr_provider = 'swift-vision'
        elif tesseract_found and tesseract_chi_sim:
            ocr_provider = 'tesseract-chi_sim'
        else:
            ocr_provider = 'none'
        ocr_ready = ocr_provider != 'none'
        video = local_capability_preflight(
            extractor_root=args.extractor_root,
            mimo_asr_root=args.mimo_asr_root,
            mimo_vl_root=args.mimo_vl_root,
            host_visual_capability=args.host_visual_capability,
            host_visual_name=args.host_visual_name,
        )
        asr = video['video_audio']['local_asr']
        local_visual = video['video_visual']['local_mimo_vl']
        asr_status = 'ready' if asr['ready'] else 'unsupported' if 'mimo-asr-apple-silicon' in asr['missing'] else 'missing'
        local_visual_status = (
            'ready'
            if local_visual['ready']
            else 'unsupported'
            if 'mimo-vl-apple-silicon' in local_visual['missing']
            else 'missing'
        )
        report = {
            'schema_version': 1,
            'mode': 'capability_preflight',
            'platform': system,
            'safety': {
                'browser_access': 'not_performed',
                'network_access': 'not_performed',
                'installation': 'not_performed',
                'model_loading': 'not_performed',
                'disk_scan': 'configured_and_documented_paths_only',
            },
            'capabilities': {
                'ocr': {
                    'status': 'ready' if ocr_ready else 'missing',
                    'ready': ocr_ready,
                    'provider': ocr_provider,
                    'source': 'local_probe',
                    'missing': [] if ocr_ready else ['chinese-ocr'],
                    'easyocr_package_detected_unverified': importlib.util.find_spec('easyocr') is not None,
                },
                'video_audio': {
                    'status': video['video_audio']['status'],
                    'ready': video['video_audio']['ready'],
                    'subtitle_components_ready': video['video_audio']['subtitle_components_ready'],
                    'media_tools_ready': video['video_audio']['media_tools_ready'],
                    'local_asr': {**asr, 'status': asr_status},
                    'note': video['video_audio']['note'],
                },
                'local_visual': {**local_visual, 'status': local_visual_status},
                'host_visual': video['video_visual']['host_visual_ai'],
            },
            'installation_authorized': False,
            'next_step': '先展示检查结果，再询问是否开启 OCR，以及选择“声音和画面都分析”“只分析声音”或“不开启”。',
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    python_cmd = shutil.which('python3') or shutil.which('python')
    python_version = '.'.join(str(x) for x in sys.version_info[:3])
    python_supported = sys.version_info >= (3, 9)
    chrome_paths = [p for p in chrome_candidates(system) if executable_exists(p)]
    ocr_enabled = bool(args.ocr)
    ocr_checked = bool(args.ocr)
    tesseract_found = check('tesseract') if ocr_checked else False
    tesseract_chi_sim = tesseract_language_ready('chi_sim') if tesseract_found else False
    easyocr_ready = python_import_ok('easyocr') if ocr_checked else False
    macos_swift = check('swift') if is_macos and ocr_checked else False
    macos_swift_vision = swift_vision_ready() if is_macos and ocr_checked else False
    checks = {
        'platform': system,
        'python': bool(python_cmd),
        'python_executable': python_cmd,
        'python_version': python_version,
        'python_version_supported': python_supported,
        'curl': check('curl'),
        'node': check('node'),
        'playwright_python': python_import_ok('playwright'),
        'chrome_or_edge_executable': bool(chrome_paths) or check('google-chrome') or check('chromium') or check('chrome') or check('msedge'),
        'chrome_or_edge_paths': chrome_paths,
        'ocr_checked': ocr_checked,
        'ocr_feature_enabled': ocr_enabled,
        'tesseract': tesseract_found,
        'tesseract_chi_sim': tesseract_chi_sim,
        'easyocr_python': easyocr_ready,
        'easyocr_available_as_explicit_alternative': easyocr_ready,
        'paddleocr_python': False,
        'paddleocr_supported': False,
        'macos_osascript': check('osascript') if is_macos else False,
        'macos_swift': macos_swift,
        # Pure path checks only. Environment inspection must never launch or connect to a browser.
        'macos_google_chrome_bundle': Path('/Applications/Google Chrome.app').exists() if is_macos else False,
        'macos_safari_bundle': Path('/Applications/Safari.app').exists() if is_macos else False,
        'macos_arc_bundle': Path('/Applications/Arc.app').exists() if is_macos else False,
        'macos_swift_can_import_vision': macos_swift_vision,
    }
    checks['browser_automation_ready'] = bool(
        (is_macos and checks['macos_osascript'] and (
            checks['macos_google_chrome_bundle'] or checks['macos_safari_bundle'] or checks['macos_arc_bundle']
        ))
        or (checks['playwright_python'] and checks['chrome_or_edge_executable'])
    )
    checks['ocr_ready'] = bool(ocr_checked and (
        (is_macos and checks['macos_swift'] and checks['macos_swift_can_import_vision'])
        or (checks['tesseract'] and checks['tesseract_chi_sim'])
    ))
    checks['image_text_recognition_ready'] = checks['ocr_ready']
    if checks['macos_swift_can_import_vision']:
        checks['ocr_provider'] = 'swift-vision'
    elif checks['tesseract'] and checks['tesseract_chi_sim']:
        checks['ocr_provider'] = 'tesseract-chi_sim'
    else:
        checks['ocr_provider'] = 'none'
    checks['ocr_status'] = 'not_enabled' if not ocr_checked else ('ready' if checks['ocr_ready'] else 'missing')
    checks['script_runtime_ready'] = bool(checks['python'] and checks['python_version_supported'])
    checks['ocr_install_purpose'] = (
        'OCR 用于识别小红书图文笔记封面和全部内页图片里的中文文字，提高分类和专辑归档准确性。'
    )
    if is_macos:
        checks['ocr_install_size'] = {
            'preferred_provider': 'swift-vision',
            'estimated_download': 'Swift + Vision 已可用时为 0 MB；否则 Command Line Tools 为 GB 级',
            'estimated_disk': '系统内置 Vision OCR 模型为 0 MB 额外占用；Command Line Tools 随 macOS 版本变化',
            'exact_size_policy': '安装前显示 macOS 安装窗口报告的实际大小。',
        }
    elif is_windows:
        checks['ocr_install_size'] = {
            'preferred_provider': 'tesseract-chi_sim',
            'estimated_download': '通常为几十到数百 MB，取决于安装器和附带语言',
            'estimated_disk': '安装前显示包管理器或安装器的预计磁盘占用。',
            'exact_size_policy': '采用当前安装器报告的大小，不硬编码跨平台固定数字。',
        }
    else:
        checks['ocr_install_size'] = {
            'preferred_provider': 'tesseract-chi_sim',
            'estimated_download': '取决于当前系统软件包',
            'estimated_disk': '取决于当前系统软件包',
            'exact_size_policy': '安装前显示包管理器报告的大小。',
        }
    if checks['ocr_ready']:
        checks['ocr_install_size'] = {
            'preferred_provider': checks['ocr_provider'],
            'estimated_download': '0 MB；复用已经安装的 OCR provider',
            'estimated_disk': '0 MB 额外占用',
            'exact_size_policy': '无需安装。',
        }
    if not ocr_checked:
        checks['ocr_install_suggestions'] = []
        checks['ocr_install_required'] = False
        checks['ocr_install_authorized_by_enable_switch'] = False
        checks['should_ask_user_to_install_ocr'] = False
        checks['ocr_message'] = '图文 OCR 开关未开启，因此没有检测或安装 OCR。'
    elif not checks['ocr_ready']:
        if is_macos:
            checks['ocr_install_suggestions'] = [
                '安装 Apple Command Line Tools，让 Swift 使用系统内置 Vision OCR；实际大小由 macOS 安装窗口显示。',
                '除非用户明确选择 GB 级 EasyOCR 方案，否则不得自动切换。',
            ]
        elif is_windows:
            checks['ocr_install_suggestions'] = [
                '安装 Tesseract OCR 和 chi_sim 简体中文语言包，并把 tesseract.exe 加入 PATH。',
                '确认 `tesseract --list-langs` 包含 chi_sim 后，才能判定中文 OCR 就绪。',
            ]
        else:
            checks['ocr_install_suggestions'] = [
                '安装 Tesseract OCR 和 chi_sim 简体中文语言包。',
            ]
        checks['ocr_install_required'] = True
        checks['ocr_install_authorized_by_enable_switch'] = True
        checks['should_ask_user_to_install_ocr'] = False
        checks['ocr_message'] = '用户已开启图文 OCR；当前缺少可用中文 OCR，应按当前平台安装并复验。'
    else:
        checks['ocr_install_suggestions'] = []
        checks['ocr_install_required'] = False
        checks['ocr_install_authorized_by_enable_switch'] = True
        checks['should_ask_user_to_install_ocr'] = False
        checks['ocr_message'] = '已找到可复用的中文 OCR，无需安装。'
    if not checks['python_version_supported']:
        checks['python_install_suggestion'] = 'Install Python 3.9 or newer, then rerun this script with that Python.'
    else:
        checks['python_install_suggestion'] = ''
    checks['windows_supported_path_ready'] = bool(is_windows and checks['playwright_python'] and checks['chrome_or_edge_executable'] and checks['ocr_ready'])
    if args.video_content:
        checks['video_content_classification'] = video_content_environment(
            extractor_root=args.extractor_root,
            browser=str(args.browser),
            check_login_state=args.check_login_state,
            analysis_provider=args.analysis_provider,
            analysis_command=args.analysis_command,
            mimo_vl_root=args.mimo_vl_root,
            visual_analysis=args.visual_analysis,
        )
    else:
        checks['video_content_classification'] = {
            'enabled': False,
            'checked': False,
            'message': '开关未开启，因此没有检测或安装视频内容分类依赖。',
        }
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
