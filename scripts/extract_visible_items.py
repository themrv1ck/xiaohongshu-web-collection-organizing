#!/usr/bin/env python3
import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from browser_page_runtime import run_page_javascript
from xhs_safety import (
    SafetyHaltedError,
    default_safety_state_path,
    ensure_active_session,
    halt_if_safety_error,
    mark_security_halted,
    resolve_safety_state_path,
)
from workbuddy_runtime import apply_workbuddy_browser_policy, is_workbuddy_host


def write_private_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + '.tmp')
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp_path.chmod(0o600)
    os.replace(temp_path, path)

ITEMS_JS = r'''JSON.stringify({
  scrollY: window.scrollY,
  innerHeight: window.innerHeight,
  scrollHeight: document.documentElement.scrollHeight,
  location: location.href,
  title: document.title,
  declaredItemCount: (() => {
    const match = ((document.body && document.body.innerText) || '').match(/笔记\s*[・·]\s*(\d+)/);
    return match ? Number(match[1]) : null;
  })(),
  loginRequired: /手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test((document.body && document.body.innerText) || ''),
  securityMarker: (() => {
    const text = `${location.href}\n${(document.body && document.body.innerText) || ''}`.toLowerCase();
    const markers = [
      '安全验证', '异常访问', '访问异常', '当前请求异常', '300031', 'website-login/error',
      '访问过于频繁', '操作过于频繁',
      '请求过于频繁', '网络环境存在风险', '当前环境存在风险', '请完成验证',
      '拖动滑块', 'captcha', 'security verification', 'abnormal access', 'too many requests'
    ];
    return markers.find(marker => text.includes(marker.toLowerCase())) || '';
  })(),
  items: (() => {
    const unwrap = value => {
      let current = value;
      for (let i = 0; i < 5; i += 1) {
        if (current && typeof current === 'object' && current.__v_isRef === true) {
          current = current._value !== undefined ? current._value : current._rawValue;
        } else break;
      }
      return current;
    };
    const structuredTypes = new Map();
    const structuredCards = new Map();
    const noteGroups = unwrap(window.__INITIAL_STATE__?.user?.notes) || [];
    for (const rawGroup of noteGroups) {
      const group = unwrap(rawGroup);
      if (!Array.isArray(group)) continue;
      for (const entry of group) {
        const card = unwrap(entry?.noteCard) || {};
        const noteId = String(card.noteId || entry?.id || '');
        if (noteId) {
          structuredCards.set(noteId, card);
          if (card.type) structuredTypes.set(noteId, {value: card.type, source: 'xhs_initial_state_note_type'});
        }
      }
    }
    const structuredTypeForSection = (section, noteId) => {
      if (structuredTypes.has(noteId)) return structuredTypes.get(noteId);
      const attr = section.getAttribute('data-note-type') || section.getAttribute('data-type') || '';
      if (attr) return {value: attr, source: 'xhs_dom_data_type'};
      const hasVideoMarker = Array.from(section.querySelectorAll('use')).some(node =>
        (node.getAttribute('href') || node.getAttribute('xlink:href') || '') === '#play-s'
      );
      if (hasVideoMarker) return {value: 'video', source: 'xhs_dom_play_marker'};
      let instance = section.__vueParentComponent;
      for (let depth = 0; instance && depth < 8; depth += 1, instance = instance.parent) {
        const props = instance.props || {};
        const candidates = [props.note, props.feed, props.item, instance.setupState?.note, instance.setupState?.feed];
        for (const candidate of candidates) {
          const card = unwrap(candidate?.noteCard) || unwrap(candidate) || {};
          const candidateId = String(card.noteId || candidate?.id || '');
          if ((!candidateId || candidateId === noteId) && card.type) {
            return {value: card.type, source: 'xhs_vue_note_type'};
          }
        }
      }
      return {value: '', source: 'xhs_dom_play_marker_absent'};
    };
    const normalizeType = value => {
      const type = String(value || '').toLowerCase();
      if (type === 'video') return 'video';
      if (['normal', 'image', 'images', 'note'].includes(type)) return 'image';
      return 'unknown';
    };
    const imageUrlFromValue = value => {
      if (typeof value === 'string') return value;
      if (!value || typeof value !== 'object') return '';
      for (const key of ['urlDefault', 'url', 'urlPre', 'src']) {
        if (typeof value[key] === 'string' && value[key]) return value[key];
      }
      const infoList = value.infoList || value.info_list;
      if (Array.isArray(infoList)) {
        for (const scene of ['WB_DFT', 'WB_PRV', 'WB_WM']) {
          const found = infoList.find(info => info && (info.imageScene || info.image_scene) === scene && info.url);
          if (found) return found.url;
        }
      }
      return '';
    };
    const allSections = Array.from(document.querySelectorAll('section.note-item, .note-item, [data-note-id]'));
    const tabContainer = typeof document.querySelector === 'function'
      ? document.querySelector('.feeds-tab-container')
      : null;
    const tabPanels = Array.from(new Set(
      allSections.map(section => (
        typeof section.closest === 'function' ? section.closest('.tab-content-item') : null
      )).filter(Boolean)
    ));
    const activeTabPanel = tabContainer && tabPanels.length > 1
      ? tabPanels.reduce((best, panel) => {
          const hostRect = tabContainer.getBoundingClientRect();
          const panelRect = panel.getBoundingClientRect();
          const overlap = Math.max(
            0,
            Math.min(hostRect.right, panelRect.right) - Math.max(hostRect.left, panelRect.left)
          );
          return overlap > best.overlap ? {panel, overlap} : best;
        }, {panel: null, overlap: -1}).panel
      : null;
    const activeSections = activeTabPanel
      ? allSections.filter(section => activeTabPanel.contains(section))
      : allSections;
    return activeSections.map((section, index) => {
    const titleLink = section.querySelector('a.title') || section.querySelector('a[href*="/explore/"]') || section.querySelector('a[href*="/user/profile/"]');
    const coverLink = section.querySelector('a.cover') || section.querySelector('a[href*="/explore/"]') || section.querySelector('a[href*="/user/profile/"]');
    const rawHref = (titleLink && titleLink.href) || (coverLink && coverLink.href) || section.querySelector('a')?.href || '';
    const m = rawHref.match(/\/([a-f0-9]{24})(?:\?|$)/i) || (section.getAttribute('data-note-id') || '').match(/([a-f0-9]{24})/i);
    const id = m ? m[1] : '';
    if (!rawHref || !id) return null;
    const structuredCard = structuredCards.get(id) || {};
    const domTitle = (titleLink && titleLink.innerText) || section.querySelector('[class*=title]')?.innerText || '';
    const title = String(domTitle || structuredCard.displayTitle || structuredCard.title || '').trim().replace(/\s+/g, ' ');
    const img = section.querySelector('img');
    const userEl = section.querySelector('[class*=author] [class*=name], [class*=user] [class*=name], .author, .user');
    const descEl = section.querySelector('[class*=desc], [class*=content], [class*=text]');
    const user = String(
      (userEl && userEl.innerText)
      || structuredCard.user?.nickname
      || structuredCard.user?.nickName
      || ''
    ).trim().replace(/\s+/g, ' ');
    const desc = String(
      (descEl && descEl.innerText)
      || structuredCard.desc
      || structuredCard.description
      || ''
    ).trim().replace(/\s+/g, ' ');
    const cardText = String(section.innerText || [title, user, desc].filter(Boolean).join(' '))
      .trim().replace(/\s+/g, ' ');
    const hashTags = Array.from(cardText.matchAll(/#([^#\s]+)/g)).map(match => match[1]);
    const structuredType = structuredTypeForSection(section, id);
    const rawImageList = Array.isArray(structuredCard.imageList)
      ? structuredCard.imageList
      : Array.isArray(structuredCard.images) ? structuredCard.images : [];
    const structuredImageUrls = rawImageList.map(imageUrlFromValue).filter(Boolean);
    const coverImageUrl = (img && (img.currentSrc || img.src)) || '';
    const imageUrls = rawImageList.length ? structuredImageUrls : (coverImageUrl ? [coverImageUrl] : []);
    return {
      id,
      title,
      href: `https://www.xiaohongshu.com/explore/${id}`,
      cover_image_url: coverImageUrl,
      image_urls: imageUrls,
      image_count: null,
      image_urls_complete: false,
      image_list_source: rawImageList.length ? 'collection_card_observed_images' : 'collection_card_cover_only',
      user,
      desc,
      tags: hashTags,
      card_text: cardText,
      content_type: normalizeType(structuredType.value),
      content_type_source: structuredType.source,
      page_index: /^\d+$/.test(section.getAttribute('data-index') || '') ? Number(section.getAttribute('data-index')) : null,
      first_seen: index
    };
  }).filter(Boolean);
  })()
})'''


def osascript(script: str, timeout: int = 10) -> str:
    try:
        res = subprocess.run(['osascript'], input=script, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'Arc/Safari 脚本在 {timeout} 秒内没有返回') from exc
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout.strip()


def jxa_osascript(script: str, timeout: int = 10) -> str:
    """Run a JXA script without asking macOS to launch Arc implicitly."""
    try:
        res = subprocess.run(
            ['osascript', '-l', 'JavaScript', '-e', script],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'Arc JXA 脚本在 {timeout} 秒内没有返回') from exc
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout.strip()


def require_macos_app_running(process_name: str) -> None:
    result = subprocess.run(['pgrep', '-x', process_name], text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f'{process_name} 当前未运行；为避免隐式启动外部应用，请先取得用户本轮授权并让用户手动打开。')


def chrome_js_macos(js: str) -> str:
    require_macos_app_running('Google Chrome')
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
    require_macos_app_running('Safari')
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


def arc_js_macos(
    js: str,
    tab_marker: Optional[str] = None,
    window_id: Optional[str] = None,
    tab_id: Optional[str] = None,
    expected_url_substring: Optional[str] = None,
) -> str:
    marker = str(tab_marker or '').strip()
    target_window_id = str(window_id or '').strip()
    target_tab_id = str(tab_id or '').strip()
    expected_url = str(expected_url_substring or '').strip()
    selector_values = (marker, target_window_id, target_tab_id, expected_url)
    if any(selector_values) and not all(selector_values):
        raise ValueError('Arc 定位器必须同时提供 window id、tab id、window.name 标记和预期 URL 片段')
    require_macos_app_running('Arc')
    js_source = js
    if marker:
        js_source = (
            '(function() {\n'
            f'  if (window.name !== {json.dumps(marker, ensure_ascii=False)}) '
            'throw new Error("Arc tab runtime marker mismatch");\n'
            f'  return eval({json.dumps(js, ensure_ascii=False)});\n'
            '})()'
        )
        # Arc's live AppleScript `windows` collection can become an invalid
        # specifier while a virtualized page is materializing.  JXA indexes
        # the already-open window and tab by their immutable ids instead.
        script = (
            'const app = Application("Arc");\n'
            f'const expectedWindowId = {json.dumps(target_window_id, ensure_ascii=False)};\n'
            f'const expectedTabId = {json.dumps(target_tab_id, ensure_ascii=False)};\n'
            f'const expectedURLPart = {json.dumps(expected_url, ensure_ascii=False)};\n'
            'let targetWindow = null;\n'
            'for (let index = 0; index < app.windows.length; index += 1) {\n'
            '  const candidate = app.windows[index];\n'
            '  if (candidate.id() === expectedWindowId) { targetWindow = candidate; break; }\n'
            '}\n'
            'if (!targetWindow) throw new Error("Arc 中未找到符合 window id 定位器的窗口");\n'
            'let targetTab = null;\n'
            'for (let index = 0; index < targetWindow.tabs.length; index += 1) {\n'
            '  const candidate = targetWindow.tabs[index];\n'
            '  if (candidate.id() === expectedTabId) { targetTab = candidate; break; }\n'
            '}\n'
            'if (!targetTab) throw new Error("Arc 中未找到符合 tab id 定位器的标签页");\n'
            'const targetURL = targetTab.url();\n'
            'if (!targetURL.includes("xiaohongshu.com") || !targetURL.includes(expectedURLPart)) {\n'
            '  throw new Error("Arc 工作标签页 URL 不再匹配");\n'
            '}\n'
            f'const jsSource = {json.dumps(js_source, ensure_ascii=False)};\n'
            'app.execute(targetTab, {javascript: jsSource});\n'
        )
        return jxa_osascript(script)
    with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8', delete=False) as fh:
        fh.write(js_source)
        js_path = fh.name
    try:
        # Read-only collection extraction keeps its established behavior.
        # Account-changing callers must supply a unique marker explicitly.
        script = (
            f'set jsSource to read POSIX file {json.dumps(js_path)} as «class utf8»\n'
            'tell application "Arc"\n'
            'set targetTab to missing value\n'
            'repeat with w in windows\n'
            'repeat with t in tabs of w\n'
            'try\n'
            'if (URL of t as text) contains "xiaohongshu.com" then\n'
            'set targetTab to t\n'
            'exit repeat\n'
            'end if\n'
            'end try\n'
            'end repeat\n'
            'if targetTab is not missing value then exit repeat\n'
            'end repeat\n'
            'if targetTab is missing value then error "未找到 Arc 小红书标签页"\n'
            'return execute targetTab javascript jsSource\n'
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


def parse_js_json_result(raw):
    """Normalize browser script transports to the JSON object returned by ITEMS_JS.

    Chrome/Safari return the JSON text directly, while Arc's `execute` command
    serializes that text once more before handing it to osascript.
    """
    value = raw
    for _ in range(2):
        if not isinstance(value, str):
            break
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError(f'浏览器脚本返回格式错误：期望 JSON 对象，实际为 {type(value).__name__}')
    return value


def page_position_map(data: Dict) -> tuple:
    pairs = []
    for item in data.get('items', []):
        position = item.get('page_index')
        if isinstance(position, int) and position >= 0 and item.get('id'):
            pairs.append((position, str(item['id'])))
    return tuple(sorted(pairs))


def stable_snapshot_signature(data: Dict) -> tuple:
    """Bind a stable virtual-list read to both positions and declared total."""
    positions = page_position_map(data)
    declared = data.get('declaredItemCount')
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 0:
        declared = None
    return positions, declared


def declared_item_count(data: Dict) -> Optional[int]:
    value = data.get('declaredItemCount')
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def read_stable_items_snapshot(run_script, settle_pause: float = 0.2, max_checks: int = 8):
    """Read until both virtualized positions and the declared total are stable."""
    data = parse_js_json_result(run_script(ITEMS_JS))
    previous = stable_snapshot_signature(data)
    initial_declared = declared_item_count(data)
    for check in range(2, max_checks + 1):
        time.sleep(settle_pause)
        candidate = parse_js_json_result(run_script(ITEMS_JS))
        if declared_item_count(candidate) != initial_declared:
            raise RuntimeError(
                '小红书前端声明总数在同一次稳定读取中发生变化，已停止。'
            )
        current = stable_snapshot_signature(candidate)
        if current == previous:
            return candidate, check
        data, previous = candidate, current
    raise RuntimeError('小红书虚拟列表在限定时间内未稳定，未写入可能错位的卡片数据。')


def validate_capture_page(data: Dict, safety_state: Optional[Path] = None) -> None:
    """Fail closed before storing or scrolling when the current page is not usable."""
    location = str(data.get('location') or '')
    if 'xiaohongshu.com' not in location:
        raise RuntimeError(f'当前浏览器页面不是小红书页面：{location or "unknown"}')
    security_marker = str(data.get('securityMarker') or '').strip()
    if security_marker:
        message = f'小红书页面出现安全提示：{security_marker}'
        if safety_state:
            mark_security_halted(
                safety_state,
                stage='capture',
                reason_code='security_challenge',
                message=message,
            )
        raise SafetyHaltedError(message)
    if data.get('loginRequired'):
        message = '当前小红书页面像是未登录状态；已停止，不能自动继续或切换详情请求。'
        if safety_state:
            mark_security_halted(
                safety_state,
                stage='capture',
                reason_code='login_required',
                message=message,
            )
        raise SafetyHaltedError(message)


def read_capture_snapshot(run_script, safety_state: Optional[Path] = None) -> Dict:
    try:
        data = parse_js_json_result(run_script(ITEMS_JS))
    except Exception as exc:
        if safety_state and halt_if_safety_error(safety_state, stage='capture', error=exc):
            raise SafetyHaltedError('抓取器返回安全异常；已写入安全停机状态。') from exc
        raise
    validate_capture_page(data, safety_state)
    return data


def capture_current_segment_with_js(
    run_script,
    out: Path,
    segment_limit: int = 200,
    manifest: Optional[Path] = None,
    source: str = 'collection',
    append_existing: bool = False,
    safety_state: Optional[Path] = None,
):
    """Passively record only the cards already rendered in the current page view.

    This intentionally performs one DOM read only: no top reset, scrolling,
    clicking, navigation, or automatic continuation into the next segment.
    """
    if not isinstance(segment_limit, int) or isinstance(segment_limit, bool) or not 1 <= segment_limit <= 200:
        raise ValueError('--segment-limit 必须是 1 到 200 的整数')
    out = Path(out)
    manifest = Path(manifest) if manifest else None
    state_path = Path(safety_state) if safety_state else default_safety_state_path(out)
    ensure_active_session(
        state_path,
        stage='capture',
        policy={
            'capture_mode': 'passive',
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'segment_limit': segment_limit,
        },
    )
    data = read_capture_snapshot(run_script, state_path)
    source_label = normalize_source_label(source)
    observed_items = list(data.get('items') or [])
    captured = []
    for item in observed_items[:segment_limit]:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        row = dict(item)
        row['source_lists'] = [source_label]
        row['source_primary'] = source_label
        captured.append(row)
    existing_count = 0
    items = captured
    if append_existing and out.exists():
        existing_items = json.loads(out.read_text(encoding='utf-8'))
        if not isinstance(existing_items, list):
            raise ValueError('已有 visible_items.json 顶层必须是数组')
        existing_count = len(existing_items)
        items = merge_items(existing_items, captured, source_label)
    write_private_json(out, items)
    reached_limit = len(observed_items) >= segment_limit
    stopped_reason = 'segment_limit_reached' if reached_limit else 'current_page_captured'
    page = {k: data.get(k) for k in ('location', 'title', 'scrollY', 'innerHeight', 'scrollHeight', 'declaredItemCount')}
    result = {
        'count': len(items),
        'newly_seen_count': len(captured),
        'existing_count': existing_count,
        'source': source_label,
        'output': str(out),
        'page': page,
        'capture_mode': 'passive',
        'segment_limit': segment_limit,
        'crawl_complete': False,
        'stopped_reason': stopped_reason,
        'next_action': '用户手动滚动到下一段后，再明确启动下一次被动采集。',
        'safety_state': str(state_path),
    }
    if manifest:
        write_private_json(manifest, {
            **result,
            'item_count': len(items),
            'observed_card_count': len(observed_items),
            'auto_continue': False,
            'auto_scroll': False,
        })
        result['manifest'] = str(manifest)
    return result


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


def extract_with_js(run_script, out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, reset_top: bool = True, safety_state: Optional[Path] = None):
    seen = {}
    seen_by_page_index = {}
    source_label = normalize_source_label(source)
    stable = 0
    last_meta = {}
    snapshots = []
    stopped_reason = 'max_scrolls_reached'
    state_path = Path(safety_state) if safety_state else None
    if state_path:
        ensure_active_session(
            state_path,
            stage='capture',
            policy={
                'capture_mode': 'scroll',
                'auto_scroll': True,
                'auto_navigation': False,
                'auto_retry': False,
            },
        )
    if reset_top:
        try:
            run_script('window.scrollTo(0, 0); "ok"')
        except Exception as exc:
            if state_path and halt_if_safety_error(state_path, stage='capture', error=exc):
                raise SafetyHaltedError('抓取器返回安全异常；已写入安全停机状态。') from exc
            raise
        if scroll_pause > 0:
            time.sleep(scroll_pause)
    for index in range(max_scrolls):
        try:
            data, stability_checks = read_stable_items_snapshot(run_script)
        except Exception as exc:
            if state_path and halt_if_safety_error(state_path, stage='capture', error=exc):
                raise SafetyHaltedError('抓取器返回安全异常；已写入安全停机状态。') from exc
            raise
        last_meta = {k: data.get(k) for k in ('location', 'title', 'scrollY', 'innerHeight', 'scrollHeight', 'declaredItemCount')}
        validate_capture_page(data, state_path)
        changes = 0
        for item in data.get('items', []):
            note_id = str(item.get('id') or '')
            if not note_id:
                continue
            page_index = item.get('page_index')
            if isinstance(page_index, int) and page_index >= 0:
                previous_id = seen_by_page_index.get(page_index)
                if previous_id and previous_id != note_id:
                    previous_item = seen.get(previous_id)
                    if previous_item and previous_item.get('page_index') == page_index:
                        del seen[previous_id]
                    changes += 1
                seen_by_page_index[page_index] = note_id
            if note_id not in seen:
                changes += 1
            item['source_lists'] = [source_label]
            item['source_primary'] = source_label
            seen[note_id] = item
        stable = stable + 1 if changes == 0 else 0
        bottom = data.get('scrollY', 0) + data.get('innerHeight', 0) >= data.get('scrollHeight', 0) - 50
        declared_count = data.get('declaredItemCount')
        declared_count_reached = not isinstance(declared_count, int) or len(seen_by_page_index) >= declared_count
        snapshots.append({
            'index': index,
            'scrollY': data.get('scrollY', 0),
            'innerHeight': data.get('innerHeight', 0),
            'scrollHeight': data.get('scrollHeight', 0),
            'item_count': len(seen),
            'changes': changes,
            'stable_rounds': stable,
            'at_bottom': bottom,
            'dom_stability_checks': stability_checks,
        })
        if stable >= 3 and bottom and declared_count_reached:
            stopped_reason = 'bottom_stable'
            break
        try:
            run_script('window.scrollBy(0, 1000); "ok"')
        except Exception as exc:
            if state_path and halt_if_safety_error(state_path, stage='capture', error=exc):
                raise SafetyHaltedError('抓取器返回安全异常；已写入安全停机状态。') from exc
            raise
        time.sleep(scroll_pause)
    items = list(seen.values())
    page_positions = sorted(seen_by_page_index)
    missing_page_positions = []
    if page_positions:
        missing_page_positions = sorted(set(range(page_positions[0], page_positions[-1] + 1)) - set(page_positions))
    declared_count = last_meta.get('declaredItemCount')
    position_count = len(page_positions)
    crawl_complete = bool(
        stopped_reason == 'bottom_stable'
        and (not page_positions or (page_positions[0] == 0 and not missing_page_positions))
        and (not isinstance(declared_count, int) or declared_count == position_count)
    )
    existing_count = 0
    if append_existing and out.exists():
        try:
            existing_items = json.loads(out.read_text(encoding='utf-8'))
            if isinstance(existing_items, list):
                existing_count = len(existing_items)
                items = merge_items(existing_items, items, source_label)
        except Exception:
            existing_count = 0
    write_private_json(out, items)
    result = {'count': len(items), 'newly_seen_count': len(seen), 'existing_count': existing_count, 'source': source_label, 'output': str(out), 'page': last_meta, 'crawl_complete': crawl_complete}
    if state_path:
        result['safety_state'] = str(state_path)
    if manifest:
        manifest_data = {
            'output': str(out),
            'item_count': len(items),
            'newly_seen_count': len(seen),
            'existing_count': existing_count,
            'source': source_label,
            'stopped_reason': stopped_reason,
            'page': last_meta,
            'crawl_complete': crawl_complete,
            'page_position_count': position_count,
            'page_position_min': page_positions[0] if page_positions else None,
            'page_position_max': page_positions[-1] if page_positions else None,
            'missing_page_positions': missing_page_positions,
            'scroll_snapshots': snapshots,
        }
        manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding='utf-8')
        result['manifest'] = str(manifest)
    return result


def extract_with_capture_mode(
    run_script,
    out: Path,
    max_scrolls: int,
    scroll_pause: float,
    manifest: Optional[Path] = None,
    source: str = 'collection',
    append_existing: bool = False,
    capture_mode: str = 'passive',
    segment_limit: int = 200,
    safety_state: Optional[Path] = None,
):
    if capture_mode == 'passive':
        return capture_current_segment_with_js(
            run_script,
            out,
            segment_limit,
            manifest,
            source,
            append_existing,
            safety_state,
        )
    if capture_mode == 'scroll':
        return extract_with_js(
            run_script,
            out,
            max_scrolls,
            scroll_pause,
            manifest,
            source,
            append_existing,
            safety_state=safety_state,
        )
    raise ValueError(f'未知抓取模式：{capture_mode}')


def extract_macos_chrome(out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, capture_mode: str = 'passive', segment_limit: int = 200, safety_state: Optional[Path] = None):
    return extract_with_capture_mode(chrome_js_macos, out, max_scrolls, scroll_pause, manifest, source, append_existing, capture_mode, segment_limit, safety_state)


def extract_macos_safari(out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, capture_mode: str = 'passive', segment_limit: int = 200, safety_state: Optional[Path] = None):
    return extract_with_capture_mode(safari_js_macos, out, max_scrolls, scroll_pause, manifest, source, append_existing, capture_mode, segment_limit, safety_state)


def apply_arc_collection_content_types(items: List[Dict], contexts: Dict[str, Dict]) -> int:
    verified = 0
    for item in items:
        context = contexts.get(str(item.get('id') or '')) or {}
        content_type = context.get('content_type')
        if content_type in {'video', 'image'}:
            item['content_type'] = content_type
            item['content_type_source'] = 'xhs_arc_collection_api_type'
            verified += 1
    return verified


def extract_macos_arc(out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, capture_mode: str = 'passive', segment_limit: int = 200, safety_state: Optional[Path] = None, arc_window_id: str = '', arc_tab_id: str = '', arc_tab_marker: str = '', arc_expected_url_substring: str = ''):
    selectors = {
        '--arc-window-id': str(arc_window_id or '').strip(),
        '--arc-tab-id': str(arc_tab_id or '').strip(),
        '--arc-tab-marker': str(arc_tab_marker or '').strip(),
        '--arc-expected-url-substring': str(arc_expected_url_substring or '').strip(),
    }
    missing = [name for name, value in selectors.items() if not value]
    if missing:
        raise RuntimeError(f'Arc 抓取必须提供稳定的 window id、tab id、window.name 标记和预期 URL 片段；缺少：{", ".join(missing)}')

    def run_script(js: str) -> str:
        return arc_js_macos(
            js,
            selectors['--arc-tab-marker'],
            selectors['--arc-window-id'],
            selectors['--arc-tab-id'],
            selectors['--arc-expected-url-substring'],
        )

    result = extract_with_capture_mode(run_script, out, max_scrolls, scroll_pause, manifest, source, append_existing, capture_mode, segment_limit, safety_state)
    if capture_mode != 'scroll':
        return result
    from video_content_common import load_arc_collection_note_contexts

    contexts = load_arc_collection_note_contexts(profile='Default')
    items = json.loads(out.read_text(encoding='utf-8'))
    verified = apply_arc_collection_content_types(items, contexts)
    write_private_json(out, items)
    result['api_content_type_verified_count'] = verified
    if manifest and manifest.exists():
        manifest_data = json.loads(manifest.read_text(encoding='utf-8'))
        manifest_data['api_content_type_verified_count'] = verified
        manifest_data['api_content_type_unverified_count'] = len(items) - verified
        write_private_json(manifest, manifest_data)
    return result


def resolve_backend(value: str, args=None) -> str:
    if args is not None:
        value = apply_workbuddy_browser_policy(value, args)
    if value == 'auto':
        raise RuntimeError('抓取必须显式指定 --backend macos-arc、macos-chrome、macos-safari 或 playwright；禁止自动选择外部浏览器。')
    return value


def extract_playwright(out: Path, max_scrolls: int, scroll_pause: float, url: Optional[str], channel: str, user_data_dir: Optional[str], cdp_url: Optional[str], headless: bool, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, capture_mode: str = 'passive', segment_limit: int = 200, safety_state: Optional[Path] = None):
    if capture_mode == 'passive' and url:
        raise ValueError('被动采集不会自动打开 URL；请先由用户在已授权浏览器中手动打开目标页面，再连接当前页面。')
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

        def run_script(js: str) -> str:
            return run_page_javascript(page, js)

        result = extract_with_capture_mode(run_script, out, max_scrolls, scroll_pause, manifest, source, append_existing, capture_mode, segment_limit, safety_state)
        if close_context:
            context.close()
        elif browser:
            browser.close()
        return result


def main():
    parser = argparse.ArgumentParser(description='抓取小红书收藏页/点赞页/专辑页当前浏览器可见条目，输出 visible_items.json。')
    parser.add_argument('out', nargs='?', default='visible_items.json', help='visible_items.json 输出路径')
    parser.add_argument('--backend', choices=['auto', 'macos-arc', 'macos-chrome', 'macos-safari', 'playwright'], default='auto', help='浏览器自动化后端')
    parser.add_argument('--url', default=None, help='Playwright 模式下可选：打开指定小红书收藏/专辑页；不传则使用当前/新页面')
    parser.add_argument('--channel', default='chromium', help='Playwright 浏览器 channel：chrome、msedge、chromium；默认使用 Playwright 自带 chromium')
    parser.add_argument('--user-data-dir', default=None, help='Playwright 持久化浏览器资料目录；用于保留登录态')
    parser.add_argument('--cdp-url', default=None, help='连接已启动 Chrome/Edge 的 CDP 地址，例如 http://127.0.0.1:9222')
    parser.add_argument('--headless', action='store_true', help='Playwright 新开浏览器时使用 headless；登录场景通常不要开启')
    parser.add_argument('--capture-mode', choices=['passive', 'scroll'], default='passive', help='默认 passive：仅读取当前已显示卡片，不自动滚动；scroll 仅用于明确的兼容性调试')
    parser.add_argument('--segment-limit', type=int, default=200, help='被动采集单段最多写入多少条；硬上限 200')
    parser.add_argument('--max-scrolls', type=int, default=30, help='最多滚动次数')
    parser.add_argument('--scroll-pause', type=float, default=1.5, help='每次滚动后的等待秒数')
    parser.add_argument('--manifest', default='crawl_manifest.json', help='抓取完整性 manifest 输出路径；传空字符串可禁用')
    parser.add_argument('--source', choices=['collection', 'liked', 'custom'], default='collection', help='当前页面来源标签：collection=收藏，liked=点赞，custom=自定义页面')
    parser.add_argument('--append-existing', action='store_true', help='如果输出文件已存在，按 note id 合并而不是覆盖；用于“我全都要”时先抓收藏再抓点赞')
    parser.add_argument('--safety-state', default='', help='共享安全状态文件；默认与输出文件同目录的 xhs_safety_state.json')
    parser.add_argument('--arc-window-id', default='', help='Arc 抓取必填：工作窗口的 Arc AppleScript 唯一 id')
    parser.add_argument('--arc-tab-id', default='', help='Arc 抓取必填：工作标签页的 Arc AppleScript 唯一 id')
    parser.add_argument('--arc-tab-marker', default='', help='Arc 抓取必填：用户预先写入工作标签页 window.name 的唯一标记；只核验不写入')
    parser.add_argument('--arc-expected-url-substring', default='', help='Arc 抓取必填：当前收藏/点赞页面 URL 的稳定片段')
    args = parser.parse_args()

    if is_workbuddy_host():
        raise SystemExit(
            'WorkBuddy 中禁止直接运行抓取脚本；必须通过 xhs_workbuddy_capture 的完整覆盖证据通路。'
        )

    out = Path(args.out)
    manifest = Path(args.manifest) if args.manifest else None
    if args.capture_mode == 'passive' and (not isinstance(args.segment_limit, int) or not 1 <= args.segment_limit <= 200):
        parser.error('--segment-limit 必须是 1 到 200 的整数')
    safety_state = resolve_safety_state_path(args.safety_state, out)
    backend = resolve_backend(args.backend, args)
    if backend == 'macos-chrome':
        result = extract_macos_chrome(out, args.max_scrolls, args.scroll_pause, manifest, args.source, args.append_existing, args.capture_mode, args.segment_limit, safety_state)
    elif backend == 'macos-safari':
        result = extract_macos_safari(out, args.max_scrolls, args.scroll_pause, manifest, args.source, args.append_existing, args.capture_mode, args.segment_limit, safety_state)
    elif backend == 'macos-arc':
        result = extract_macos_arc(
            out, args.max_scrolls, args.scroll_pause, manifest, args.source, args.append_existing,
            args.capture_mode, args.segment_limit, safety_state,
            args.arc_window_id, args.arc_tab_id, args.arc_tab_marker, args.arc_expected_url_substring,
        )
    else:
        result = extract_playwright(out, args.max_scrolls, args.scroll_pause, args.url, args.channel, args.user_data_dir, args.cdp_url, args.headless, manifest, args.source, args.append_existing, args.capture_mode, args.segment_limit, safety_state)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
