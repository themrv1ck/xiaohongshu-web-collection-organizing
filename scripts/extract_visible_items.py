#!/usr/bin/env python3
import json
import subprocess
import sys
import time
from pathlib import Path


def osascript(script: str) -> str:
    res = subprocess.run(['osascript'], input=script, text=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout.strip()


def chrome_js(js: str) -> str:
    script = (
        'tell application "Google Chrome"\n'
        'tell active tab of front window\n'
        f'execute javascript {json.dumps(js)}\n'
        'end tell\n'
        'end tell\n'
    )
    return osascript(script)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else 'visible_items.json')
    seen = {}
    stable = 0
    script = r'''JSON.stringify({
      scrollY: window.scrollY,
      innerHeight: window.innerHeight,
      scrollHeight: document.documentElement.scrollHeight,
      items: Array.from(document.querySelectorAll('section.note-item')).map((section, index) => {
        const titleLink = section.querySelector('a.title');
        const coverLink = section.querySelector('a.cover');
        const href = (titleLink && titleLink.href) || (coverLink && coverLink.href) || '';
        const title = ((titleLink && titleLink.innerText) || '').trim().replace(/\s+/g, ' ');
        const m = href.match(/\/([a-f0-9]{24})(?:\?|$)/i);
        const id = m ? m[1] : '';
        if (!href || !id) return null;
        const img = section.querySelector('img');
        const userEl = section.querySelector('[class*=author] [class*=name], [class*=user] [class*=name], .author, .user');
        const descEl = section.querySelector('[class*=desc], [class*=content], [class*=text]');
        const cardText = (section.innerText || '').trim().replace(/\s+/g, ' ');
        const hashTags = Array.from(cardText.matchAll(/#([^#\s]+)/g)).map(match => match[1]);
        return {
          id,
          title,
          href,
          cover_image_url: (img && (img.currentSrc || img.src)) || '',
          user: ((userEl && userEl.innerText) || '').trim().replace(/\s+/g, ' '),
          desc: ((descEl && descEl.innerText) || '').trim().replace(/\s+/g, ' '),
          tags: hashTags,
          card_text: cardText,
          first_seen: index
        };
      }).filter(Boolean)
    })'''
    for _ in range(30):
        raw = chrome_js(script)
        data = json.loads(raw)
        before = len(seen)
        for item in data['items']:
            if item['id'] not in seen:
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
