#!/usr/bin/env python3
import json, subprocess, sys, time
from pathlib import Path

def osascript(script: str) -> str:
    res = subprocess.run(['osascript'], input=script, text=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout.strip()

def chrome_js(js: str) -> str:
    script = 'tell application "Google Chrome"
'              'tell active tab of front window
'              f'execute javascript {json.dumps(js)}
'              'end tell
'              'end tell
'
    return osascript(script)

def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else 'visible_items.json')
    seen = {}
    stable = 0
    for _ in range(30):
        raw = chrome_js(r'''JSON.stringify({scrollY: window.scrollY,innerHeight: window.innerHeight,scrollHeight: document.documentElement.scrollHeight,items: Array.from(document.querySelectorAll('section.note-item')).map(section => {const titleLink = section.querySelector('a.title'); const coverLink = section.querySelector('a.cover'); const href = (titleLink && titleLink.href) || (coverLink && coverLink.href) || ''; const title = ((titleLink && titleLink.innerText) || '').trim().replace(/\s+/g, ' '); const m = href.match(/\/([a-f0-9]{24})(?:\?|$)/i); const id = m ? m[1] : ''; if (!href || !title || !id) return null; return {id, title, href};}).filter(Boolean)})''')
        data = json.loads(raw)
        before = len(seen)
        for item in data['items']:
            if item['id'] not in seen:
                item['first_seen'] = len(seen)
                seen[item['id']] = item
        stable = stable + 1 if len(seen) == before else 0
        if stable >= 3 and data['scrollY'] + data['innerHeight'] >= data['scrollHeight'] - 50:
            break
        chrome_js('window.scrollBy(0, 1000); "ok"')
        time.sleep(1.5)
    out.write_text(json.dumps(list(seen.values()), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'count': len(seen), 'output': str(out)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
