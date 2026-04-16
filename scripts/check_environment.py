#!/usr/bin/env python3
import json, shutil, subprocess
checks = {
    'osascript': shutil.which('osascript') is not None,
    'google_chrome_bundle': subprocess.run(['/usr/bin/osascript', '-e', 'application id "com.google.Chrome"'], capture_output=True, text=True).returncode == 0,
}
print(json.dumps(checks, ensure_ascii=False, indent=2))
