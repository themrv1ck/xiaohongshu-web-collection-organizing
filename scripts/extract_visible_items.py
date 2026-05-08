#!/usr/bin/env python3
import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

ITEMS_JS = r'''JSON.stringify({
  scrollY: window.scrollY,
  innerHeight: window.innerHeight,
  scrollHeight: document.documentElement.scrollHeight,
  location: location.href,
  title: document.title,
  loginRequired: /手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test((document.body && document.body.innerText) || ''),
  items: Array.from(document.querySelectorAll('section.note-item, .note-item, [data-note-id]')).map((section, index) => {
    const titleLink = section.querySelector('a.title') || section.querySelector('a[href*="/explore/"]');
    const coverLink = section.querySelector('a.cover') || section.querySelector('a[href*="/explore/"]');
    const href = (titleLink && titleLink.href) || (coverLink && coverLink.href) || section.querySelector('a')?.href || '';
    const title = ((titleLink && titleLink.innerText) || section.querySelector('[class*=title]')?.innerText || '').trim().replace(/\s+/g, ' ');
    const m = href.match(/\/([a-f0-9]{24})(?:\?|$)/i) || (section.getAttribute('data-note-id') || '').match(/([a-f0-9]{24})/i);
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


def osascript(script: str) -> str:
    res = subprocess.run(['osascript'], input=script, text=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout.strip()


def chrome_js_macos(js: str) -> str:
    script = (
        'tell application "Google Chrome"\n'
        'tell active tab of front window\n'
        f'execute javascript {json.dumps(js)}\n'
        'end tell\n'
        'end tell\n'
    )
    return osascript(script)


def extract_with_js(js_eval, out: Path, max_scrolls: int, scroll_pause: float):
    seen = {}
    stable = 0
    last_meta = {}
    for _ in range(max_scrolls):
        raw = js_eval(ITEMS_JS)
        data = json.loads(raw)
        last_meta = {k: data.get(k) for k in ('location', 'title', 'scrollY', 'innerHeight', 'scrollHeight')}
        location = str(data.get('location') or '')
        if 'xiaohongshu.com' not in location:
            raise RuntimeError(f'当前浏览器页面不是小红书页面：{location or "unknown"}')
        if data.get('loginRequired'):
            raise RuntimeError('当前小红书页面像是未登录状态。请先在浏览器里登录，再重新运行抓取。')
        before = len(seen)
        for item in data.get('items', []):
            if item.get('id') and item['id'] not in seen:
                seen[item['id']] = item
        stable = stable + 1 if len(seen) == before else 0
        if stable >= 3 and data.get('scrollY', 0) + data.get('innerHeight', 0) >= data.get('scrollHeight', 0) - 50:
            break
        js_eval('window.scrollBy(0, 1000); "ok"')
        time.sleep(scroll_pause)
    out.write_text(json.dumps(list(seen.values()), ensure_ascii=False, indent=2), encoding='utf-8')
    return {'count': len(seen), 'output': str(out), 'page': last_meta}


def extract_macos_chrome(out: Path, max_scrolls: int, scroll_pause: float):
    return extract_with_js(chrome_js_macos, out, max_scrolls, scroll_pause)


def extract_playwright(out: Path, max_scrolls: int, scroll_pause: float, url: Optional[str], channel: str, user_data_dir: Optional[str], cdp_url: Optional[str], headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError('Playwright Python 未安装。先运行：python -m pip install playwright && python -m playwright install chromium') from exc

    with sync_playwright() as p:
        browser = None
        context = None
        close_context = True
        if cdp_url:
            browser = p.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            close_context = False
        else:
            profile_dir = Path(user_data_dir or Path.home() / '.xhs-skill-browser-profile')
            profile_dir.mkdir(parents=True, exist_ok=True)
            launch_args = {'headless': headless}
            if channel and channel != 'chromium':
                launch_args['channel'] = channel
            context = p.chromium.launch_persistent_context(str(profile_dir), **launch_args)
        page = context.pages[0] if context.pages else context.new_page()
        if url:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_load_state('domcontentloaded', timeout=60000)

        def js_eval(js: str) -> str:
            return page.evaluate(js)

        result = extract_with_js(js_eval, out, max_scrolls, scroll_pause)
        if close_context:
            context.close()
        elif browser:
            browser.close()
        return result


def main():
    parser = argparse.ArgumentParser(description='抓取小红书收藏页/专辑页当前浏览器可见条目，输出 visible_items.json。')
    parser.add_argument('out', nargs='?', default='visible_items.json', help='visible_items.json 输出路径')
    parser.add_argument('--backend', choices=['auto', 'macos-chrome', 'playwright'], default='auto', help='浏览器自动化后端')
    parser.add_argument('--url', default=None, help='Playwright 模式下可选：打开指定小红书收藏/专辑页；不传则使用当前/新页面')
    parser.add_argument('--channel', default='chrome', help='Playwright 浏览器 channel：chrome、msedge、chromium')
    parser.add_argument('--user-data-dir', default=None, help='Playwright 持久化浏览器资料目录；用于保留登录态')
    parser.add_argument('--cdp-url', default=None, help='连接已启动 Chrome/Edge 的 CDP 地址，例如 http://127.0.0.1:9222')
    parser.add_argument('--headless', action='store_true', help='Playwright 新开浏览器时使用 headless；登录场景通常不要开启')
    parser.add_argument('--max-scrolls', type=int, default=30, help='最多滚动次数')
    parser.add_argument('--scroll-pause', type=float, default=1.5, help='每次滚动后的等待秒数')
    args = parser.parse_args()

    out = Path(args.out)
    backend = args.backend
    if backend == 'auto':
        backend = 'macos-chrome' if platform.system() == 'Darwin' else 'playwright'
    if backend == 'macos-chrome':
        result = extract_macos_chrome(out, args.max_scrolls, args.scroll_pause)
    else:
        result = extract_playwright(out, args.max_scrolls, args.scroll_pause, args.url, args.channel, args.user_data_dir, args.cdp_url, args.headless)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
