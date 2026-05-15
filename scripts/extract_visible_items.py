#!/usr/bin/env python3
import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

ITEMS_JS = r'''JSON.stringify({
  scrollY: window.scrollY,
  innerHeight: window.innerHeight,
  scrollHeight: document.documentElement.scrollHeight,
  location: location.href,
  title: document.title,
  loginRequired: /手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test((document.body && document.body.innerText) || ''),
  items: Array.from(document.querySelectorAll('section.note-item, .note-item, [data-note-id]')).map((section, index) => {
    const titleLink = section.querySelector('a.title') || section.querySelector('a[href*="/explore/"]') || section.querySelector('a[href*="/user/profile/"]');
    const coverLink = section.querySelector('a.cover') || section.querySelector('a[href*="/explore/"]') || section.querySelector('a[href*="/user/profile/"]');
    const rawHref = (titleLink && titleLink.href) || (coverLink && coverLink.href) || section.querySelector('a')?.href || '';
    const title = ((titleLink && titleLink.innerText) || section.querySelector('[class*=title]')?.innerText || '').trim().replace(/\s+/g, ' ');
    const m = rawHref.match(/\/([a-f0-9]{24})(?:\?|$)/i) || (section.getAttribute('data-note-id') || '').match(/([a-f0-9]{24})/i);
    const id = m ? m[1] : '';
    if (!rawHref || !id) return null;
    const img = section.querySelector('img');
    const userEl = section.querySelector('[class*=author] [class*=name], [class*=user] [class*=name], .author, .user');
    const descEl = section.querySelector('[class*=desc], [class*=content], [class*=text]');
    const cardText = (section.innerText || '').trim().replace(/\s+/g, ' ');
    const hashTags = Array.from(cardText.matchAll(/#([^#\s]+)/g)).map(match => match[1]);
    return {
      id,
      title,
      href: `https://www.xiaohongshu.com/explore/${id}`,
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
    with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8', delete=False) as fh:
        fh.write(js)
        js_path = fh.name
    try:
        script = (
            f'set jsSource to read POSIX file {json.dumps(js_path)} as «class utf8»\n'
            'tell application "Google Chrome"\n'
            'tell active tab of front window\n'
            'execute javascript jsSource\n'
            'end tell\n'
            'end tell\n'
        )
        return osascript(script)
    finally:
        Path(js_path).unlink(missing_ok=True)


def safari_js_macos(js: str) -> str:
    with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8', delete=False) as fh:
        fh.write(js)
        js_path = fh.name
    try:
        script = (
            f'set jsSource to read POSIX file {json.dumps(js_path)} as «class utf8»\n'
            'tell application "Safari"\n'
            'set targetTab to missing value\n'
            'set targetWindow to missing value\n'
            'repeat with w in windows\n'
            'repeat with t in tabs of w\n'
            'try\n'
            'if (URL of t as text) contains "xiaohongshu.com" then\n'
            'set targetTab to t\n'
            'set targetWindow to w\n'
            'exit repeat\n'
            'end if\n'
            'end try\n'
            'end repeat\n'
            'if targetTab is not missing value then exit repeat\n'
            'end repeat\n'
            'if targetTab is missing value then error "未找到 Safari 小红书标签页"\n'
            'set current tab of targetWindow to targetTab\n'
            'set index of targetWindow to 1\n'
            'do JavaScript jsSource in targetTab\n'
            'end tell\n'
        )
        return osascript(script)
    finally:
        Path(js_path).unlink(missing_ok=True)


def normalize_source_label(source: str) -> str:
    mapping = {
        'collection': '收藏',
        'favorite': '收藏',
        'favorites': '收藏',
        'liked': '点赞',
        'likes': '点赞',
        'like': '点赞',
        'custom': '自定义页面',
    }
    return mapping.get((source or '').strip().lower(), (source or '').strip() or '当前页面')


def merge_items(existing: List[Dict], incoming: List[Dict], source_label: str) -> List[Dict]:
    merged: Dict[str, Dict] = {}
    order: List[str] = []

    def add_source(item: Dict, label: str) -> None:
        labels = item.get('source_lists')
        if not isinstance(labels, list):
            labels = []
        if label and label not in labels:
            labels.append(label)
        item['source_lists'] = labels
        item['source_primary'] = labels[0] if labels else label

    for item in existing:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        note_id = str(item['id'])
        merged[note_id] = dict(item)
        order.append(note_id)
        if not merged[note_id].get('source_lists') and merged[note_id].get('source_primary'):
            add_source(merged[note_id], str(merged[note_id].get('source_primary')))
    for item in incoming:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        note_id = str(item['id'])
        if note_id not in merged:
            merged[note_id] = dict(item)
            order.append(note_id)
        else:
            for key, value in item.items():
                if key in {'source_lists', 'source_primary', 'first_seen'}:
                    continue
                if value and not merged[note_id].get(key):
                    merged[note_id][key] = value
        add_source(merged[note_id], source_label)
    return [merged[note_id] for note_id in order]


def extract_with_js(js_eval, out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False):
    seen = {}
    source_label = normalize_source_label(source)
    stable = 0
    last_meta = {}
    snapshots = []
    stopped_reason = 'max_scrolls_reached'
    for index in range(max_scrolls):
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
                item['source_lists'] = [source_label]
                item['source_primary'] = source_label
                seen[item['id']] = item
        stable = stable + 1 if len(seen) == before else 0
        bottom = data.get('scrollY', 0) + data.get('innerHeight', 0) >= data.get('scrollHeight', 0) - 50
        snapshots.append({
            'index': index,
            'scrollY': data.get('scrollY', 0),
            'innerHeight': data.get('innerHeight', 0),
            'scrollHeight': data.get('scrollHeight', 0),
            'item_count': len(seen),
            'new_items': len(seen) - before,
            'stable_rounds': stable,
            'at_bottom': bottom,
        })
        if stable >= 3 and bottom:
            stopped_reason = 'bottom_stable'
            break
        js_eval('window.scrollBy(0, 1000); "ok"')
        time.sleep(scroll_pause)
    items = list(seen.values())
    existing_count = 0
    if append_existing and out.exists():
        try:
            existing_items = json.loads(out.read_text(encoding='utf-8'))
            if isinstance(existing_items, list):
                existing_count = len(existing_items)
                items = merge_items(existing_items, items, source_label)
        except Exception:
            existing_count = 0
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    result = {'count': len(items), 'newly_seen_count': len(seen), 'existing_count': existing_count, 'source': source_label, 'output': str(out), 'page': last_meta}
    if manifest:
        manifest_data = {
            'output': str(out),
            'item_count': len(items),
            'newly_seen_count': len(seen),
            'existing_count': existing_count,
            'source': source_label,
            'stopped_reason': stopped_reason,
            'page': last_meta,
            'scroll_snapshots': snapshots,
        }
        manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding='utf-8')
        result['manifest'] = str(manifest)
    return result


def extract_macos_chrome(out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False):
    return extract_with_js(chrome_js_macos, out, max_scrolls, scroll_pause, manifest, source, append_existing)


def extract_macos_safari(out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False):
    return extract_with_js(safari_js_macos, out, max_scrolls, scroll_pause, manifest, source, append_existing)


def extract_playwright(out: Path, max_scrolls: int, scroll_pause: float, url: Optional[str], channel: str, user_data_dir: Optional[str], cdp_url: Optional[str], headless: bool, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False):
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

        result = extract_with_js(js_eval, out, max_scrolls, scroll_pause, manifest, source, append_existing)
        if close_context:
            context.close()
        elif browser:
            browser.close()
        return result


def main():
    parser = argparse.ArgumentParser(description='抓取小红书收藏页/点赞页/专辑页当前浏览器可见条目，输出 visible_items.json。')
    parser.add_argument('out', nargs='?', default='visible_items.json', help='visible_items.json 输出路径')
    parser.add_argument('--backend', choices=['auto', 'macos-chrome', 'macos-safari', 'playwright'], default='auto', help='浏览器自动化后端')
    parser.add_argument('--url', default=None, help='Playwright 模式下可选：打开指定小红书收藏/专辑页；不传则使用当前/新页面')
    parser.add_argument('--channel', default='chrome', help='Playwright 浏览器 channel：chrome、msedge、chromium')
    parser.add_argument('--user-data-dir', default=None, help='Playwright 持久化浏览器资料目录；用于保留登录态')
    parser.add_argument('--cdp-url', default=None, help='连接已启动 Chrome/Edge 的 CDP 地址，例如 http://127.0.0.1:9222')
    parser.add_argument('--headless', action='store_true', help='Playwright 新开浏览器时使用 headless；登录场景通常不要开启')
    parser.add_argument('--max-scrolls', type=int, default=30, help='最多滚动次数')
    parser.add_argument('--scroll-pause', type=float, default=1.5, help='每次滚动后的等待秒数')
    parser.add_argument('--manifest', default='crawl_manifest.json', help='抓取完整性 manifest 输出路径；传空字符串可禁用')
    parser.add_argument('--source', choices=['collection', 'liked', 'custom'], default='collection', help='当前页面来源标签：collection=收藏，liked=点赞，custom=自定义页面')
    parser.add_argument('--append-existing', action='store_true', help='如果输出文件已存在，按 note id 合并而不是覆盖；用于“我全都要”时先抓收藏再抓点赞')
    args = parser.parse_args()

    out = Path(args.out)
    manifest = Path(args.manifest) if args.manifest else None
    backend = args.backend
    if backend == 'auto':
        backend = 'macos-chrome' if platform.system() == 'Darwin' else 'playwright'
    if backend == 'macos-chrome':
        result = extract_macos_chrome(out, args.max_scrolls, args.scroll_pause, manifest, args.source, args.append_existing)
    elif backend == 'macos-safari':
        result = extract_macos_safari(out, args.max_scrolls, args.scroll_pause, manifest, args.source, args.append_existing)
    else:
        result = extract_playwright(out, args.max_scrolls, args.scroll_pause, args.url, args.channel, args.user_data_dir, args.cdp_url, args.headless, manifest, args.source, args.append_existing)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
