#!/usr/bin/env python3
import json
import shutil
import subprocess

def check(command):
    return shutil.which(command) is not None

def run_ok(args):
    return subprocess.run(args, capture_output=True, text=True).returncode == 0

checks = {
    'osascript': check('osascript'),
    'swift': check('swift'),
    'curl': check('curl'),
    'google_chrome_bundle': run_ok(['/usr/bin/osascript', '-e', 'application id "com.google.Chrome"']),
    'swift_can_import_vision': run_ok(['/usr/bin/swift', '-e', 'import Vision; print("ok")']),
}
print(json.dumps(checks, ensure_ascii=False, indent=2))
