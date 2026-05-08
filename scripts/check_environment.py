#!/usr/bin/env python3
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


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
    system = platform.system()
    is_macos = system == 'Darwin'
    is_windows = system == 'Windows'
    python_cmd = shutil.which('python3') or shutil.which('python')
    chrome_paths = [p for p in chrome_candidates(system) if executable_exists(p)]
    checks = {
        'platform': system,
        'python': bool(python_cmd),
        'curl': check('curl'),
        'node': check('node'),
        'playwright_python': python_import_ok('playwright'),
        'chrome_or_edge_executable': bool(chrome_paths) or check('google-chrome') or check('chromium') or check('chrome') or check('msedge'),
        'chrome_or_edge_paths': chrome_paths,
        'tesseract': check('tesseract'),
        'easyocr_python': python_import_ok('easyocr'),
        'paddleocr_python': python_import_ok('paddleocr'),
        'macos_osascript': check('osascript') if is_macos else False,
        'macos_swift': check('swift') if is_macos else False,
        'macos_google_chrome_bundle': run_ok(['/usr/bin/osascript', '-e', 'application id "com.google.Chrome"']) if is_macos else False,
        'macos_swift_can_import_vision': run_ok(['/usr/bin/swift', '-e', 'import Vision; print("ok")']) if is_macos else False,
    }
    checks['browser_automation_ready'] = bool(
        (is_macos and checks['macos_osascript'] and checks['macos_google_chrome_bundle'])
        or (checks['playwright_python'] and checks['chrome_or_edge_executable'])
    )
    checks['ocr_ready'] = bool(
        (is_macos and checks['macos_swift'] and checks['macos_swift_can_import_vision'])
        or checks['tesseract']
        or checks['easyocr_python']
        or checks['paddleocr_python']
    )
    checks['image_text_recognition_ready'] = checks['ocr_ready']
    checks['ocr_install_purpose'] = (
        'OCR is used to recognize Chinese text in Xiaohongshu cover/images so '
        'classification and board assignment are more accurate.'
    )
    if not checks['ocr_ready']:
        if is_macos:
            checks['ocr_install_suggestions'] = [
                'Confirm Xcode Command Line Tools / swift is available so macOS Vision OCR can run.',
                'If Vision is unavailable, install Tesseract with Chinese language data or use EasyOCR.',
            ]
        elif is_windows:
            checks['ocr_install_suggestions'] = [
                'Install Tesseract OCR with chi_sim Chinese language data and add tesseract.exe to PATH.',
                'Or run: python -m pip install easyocr',
            ]
        else:
            checks['ocr_install_suggestions'] = [
                'Install Tesseract OCR with Chinese language data, or run: python3 -m pip install easyocr',
            ]
        checks['should_ask_user_to_install_ocr'] = True
    else:
        checks['ocr_install_suggestions'] = []
        checks['should_ask_user_to_install_ocr'] = False
    checks['windows_supported_path_ready'] = bool(is_windows and checks['playwright_python'] and checks['chrome_or_edge_executable'] and checks['ocr_ready'])
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
