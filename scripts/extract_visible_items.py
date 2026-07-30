#!/usr/bin/env python3
import argparse
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from xhs_safety import (
    SafetyHaltedError,
    atomic_write_json,
    default_safety_state_path,
    ensure_active_session,
    halt_if_safety_error,
    mark_security_halted,
    resolve_safety_state_path,
    utc_now,
)


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
      '安全验证', '异常访问', '访问异常', '访问过于频繁', '操作过于频繁',
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
    return Array.from(document.querySelectorAll('section.note-item, .note-item, [data-note-id]')).map((section, index) => {
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
    const structuredType = structuredTypeForSection(section, id);
    const structuredCard = structuredCards.get(id) || {};
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
      user: ((userEl && userEl.innerText) || '').trim().replace(/\s+/g, ' '),
      desc: ((descEl && descEl.innerText) || '').trim().replace(/\s+/g, ' '),
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
            '  return (\n'
            f'{js}\n'
            '  );\n'
            '})()'
        )
    with tempfile.NamedTemporaryFile('w', suffix='.js', encoding='utf-8', delete=False) as fh:
        fh.write(js_source)
        js_path = fh.name
    try:
        if marker:
            script = (
                f'set jsSource to read POSIX file {json.dumps(js_path)} as «class utf8»\n'
                f'set expectedWindowId to {json.dumps(target_window_id)}\n'
                f'set expectedTabId to {json.dumps(target_tab_id)}\n'
                f'set expectedURLPart to {json.dumps(expected_url)}\n'
                'tell application "Arc"\n'
                'set targetTab to missing value\n'
                'set matchCount to 0\n'
                'repeat with w in windows\n'
                'repeat with t in tabs of w\n'
                'try\n'
                'set targetURL to URL of t as text\n'
                'set currentWindowId to id of w as text\n'
                'set currentTabId to id of t as text\n'
                'if (currentWindowId is equal to expectedWindowId) and (currentTabId is equal to expectedTabId) and (targetURL contains "xiaohongshu.com") and (targetURL contains expectedURLPart) then\n'
                'set matchCount to matchCount + 1\n'
                'if matchCount is 1 then set targetTab to t\n'
                'end if\n'
                'end try\n'
                'end repeat\n'
                'end repeat\n'
                'if matchCount is 0 then error "Arc 中未找到符合 window/tab/URL 定位器的小红书标签页"\n'
                'if matchCount is greater than 1 then error "Arc 中找到多个符合 window/tab/URL 定位器的标签页"\n'
                'return execute targetTab javascript jsSource\n'
                'end tell\n'
            )
        else:
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


def page_position_map(data: Dict) -> Optional[tuple]:
    pairs = []
    for item in data.get('items', []):
        position = item.get('page_index')
        if isinstance(position, int) and position >= 0 and item.get('id'):
            pairs.append((position, str(item['id'])))
    return tuple(sorted(pairs)) if pairs else None


def read_stable_items_snapshot(js_eval, settle_pause: float = 0.2, max_checks: int = 8):
    """Read until Xiaohongshu's virtualized card index/id mapping is unchanged twice."""
    data = parse_js_json_result(js_eval(ITEMS_JS))
    previous = page_position_map(data)
    if previous is None:
        return data, 1
    for check in range(2, max_checks + 1):
        time.sleep(settle_pause)
        candidate = parse_js_json_result(js_eval(ITEMS_JS))
        current = page_position_map(candidate)
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


def read_capture_snapshot(js_eval, safety_state: Optional[Path] = None) -> Dict:
    try:
        data = parse_js_json_result(js_eval(ITEMS_JS))
    except Exception as exc:
        if safety_state and halt_if_safety_error(safety_state, stage='capture', error=exc):
            raise SafetyHaltedError('抓取器返回安全异常；已写入安全停机状态。') from exc
        raise
    validate_capture_page(data, safety_state)
    return data


class SegmentedCapturePauseError(RuntimeError):
    """Raised before page access while the required inter-segment pause remains."""


def validate_segmented_capture_config(
    *,
    batch_size: Optional[int],
    pause_minutes: Optional[float],
    auto_continue: bool,
    user_authorized: bool,
    segment_index: Optional[int],
) -> Dict[str, Any]:
    """Validate the explicit contract required for one controlled segment.

    ``segmented`` deliberately does not scroll, reload, or open a page.  The
    caller is responsible for any later browser action, so all configuration
    needed to authorize that caller must be explicit and persisted.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 200:
        raise ValueError('--batch-size 必须是 1 到 200 的整数')
    if (
        isinstance(pause_minutes, bool)
        or not isinstance(pause_minutes, (int, float))
        or not math.isfinite(float(pause_minutes))
        or float(pause_minutes) <= 0
    ):
        raise ValueError('--pause-minutes 必须是大于 0 的分钟数')
    if auto_continue is not True:
        raise ValueError('segmented 模式必须显式传入 --auto-continue')
    if user_authorized is not True:
        raise ValueError('segmented 模式必须显式传入 --user-authorized')
    if not isinstance(segment_index, int) or isinstance(segment_index, bool) or segment_index < 1:
        raise ValueError('--segment-index 必须是从 1 开始的整数')
    return {
        'batch_size': batch_size,
        'pause_minutes': float(pause_minutes),
        'auto_continue': True,
        'user_authorized': True,
        'segment_index': segment_index,
    }


def _segmented_contract(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: config[key]
        for key in ('batch_size', 'pause_minutes', 'auto_continue', 'user_authorized')
    }


def _prepare_segmented_state(state: Dict[str, Any], state_path: Path, config: Dict[str, Any]) -> None:
    """Reject unsafe sequencing before making the one permitted DOM read."""
    previous = state.get('segmented_capture')
    if not previous:
        if config['segment_index'] != 1:
            raise ValueError('新的分段会话必须从 --segment-index 1 开始')
        return
    if not isinstance(previous, dict):
        mark_security_halted(
            state_path,
            stage='capture',
            reason_code='segmented_state_invalid',
            message='分段读取状态格式无效，无法确认上一段是否完整结束。',
        )
        raise SafetyHaltedError('分段读取状态无效；已安全停止，不能继续。')
    if previous.get('in_flight_segment_index') is not None:
        mark_security_halted(
            state_path,
            stage='capture',
            reason_code='segmented_capture_interrupted',
            message='上一段分段读取未记录完成，不能假设页面未被访问。',
        )
        raise SafetyHaltedError('上一段状态不完整；已安全停止，不能用 --resume 继续。')
    expected = _segmented_contract(config)
    actual = {key: previous.get(key) for key in expected}
    if actual != expected:
        raise ValueError('同一分段会话的 batch-size、pause-minutes 和授权标记必须保持不变')
    previous_index = previous.get('last_completed_segment_index')
    previous_epoch = previous.get('last_completed_epoch')
    if not isinstance(previous_index, int) or isinstance(previous_index, bool) or not isinstance(previous_epoch, (int, float)):
        mark_security_halted(
            state_path,
            stage='capture',
            reason_code='segmented_state_invalid',
            message='上一段分段读取缺少可核验的完成时间或段号。',
        )
        raise SafetyHaltedError('上一段状态不完整；已安全停止，不能继续。')
    if config['segment_index'] != previous_index + 1:
        raise ValueError(f'下一段必须使用 --segment-index {previous_index + 1}')
    elapsed_seconds = time.time() - float(previous_epoch)
    required_seconds = config['pause_minutes'] * 60
    if elapsed_seconds < required_seconds:
        remaining_seconds = max(1, math.ceil(required_seconds - elapsed_seconds))
        raise SegmentedCapturePauseError(
            f'距上一段完成尚需等待至少 {remaining_seconds} 秒；期间不会访问浏览器页面。'
        )


def _record_segmented_in_flight(state: Dict[str, Any], state_path: Path, config: Dict[str, Any]) -> None:
    record = {
        **_segmented_contract(config),
        'controller_required': True,
        'resume_allowed': False,
        'in_flight_segment_index': config['segment_index'],
        'started_at': utc_now(),
    }
    previous = state.get('segmented_capture')
    if isinstance(previous, dict):
        for key in ('last_completed_segment_index', 'last_completed_at', 'last_completed_epoch'):
            if key in previous:
                record[key] = previous[key]
    state['segmented_capture'] = record
    state['updated_at'] = utc_now()
    atomic_write_json(state_path, state)


def _record_segmented_completion(state_path: Path, config: Dict[str, Any]) -> None:
    state_path = Path(state_path)
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        mark_security_halted(
            state_path,
            stage='capture',
            reason_code='segmented_state_write_failed',
            message=f'无法在完成分段后读取安全状态：{exc}',
        )
        raise SafetyHaltedError('分段完成状态无法核验；已安全停止。') from exc
    if not isinstance(state, dict):
        mark_security_halted(
            state_path,
            stage='capture',
            reason_code='segmented_state_invalid',
            message='完成分段后安全状态格式无效。',
        )
        raise SafetyHaltedError('分段完成状态无效；已安全停止。')
    record = state.get('segmented_capture')
    if not isinstance(record, dict) or record.get('in_flight_segment_index') != config['segment_index']:
        mark_security_halted(
            state_path,
            stage='capture',
            reason_code='segmented_state_invalid',
            message='分段完成时未找到匹配的进行中段号。',
        )
        raise SafetyHaltedError('分段完成状态不匹配；已安全停止。')
    completed_at = utc_now()
    record.pop('in_flight_segment_index', None)
    record['last_completed_segment_index'] = config['segment_index']
    record['last_completed_at'] = completed_at
    record['last_completed_epoch'] = time.time()
    state['segmented_capture'] = record
    state['updated_at'] = completed_at
    checkpoints = state.get('checkpoints')
    if not isinstance(checkpoints, list):
        checkpoints = []
    checkpoints.append({
        'at': completed_at,
        'stage': 'capture',
        'event': 'segment_completed',
        'segment_index': config['segment_index'],
    })
    state['checkpoints'] = checkpoints
    atomic_write_json(state_path, state)


def _write_segmented_stop_manifest(
    manifest: Path,
    out: Path,
    state_path: Path,
    config: Dict[str, Any],
    error: BaseException,
) -> None:
    """Leave a per-segment stop record when the one DOM read cannot complete."""
    try:
        write_private_json(manifest, {
            'output': str(out),
            'capture_mode': 'segmented',
            'segment_index': config['segment_index'],
            **_segmented_contract(config),
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'controller_required': True,
            'resume_allowed': False,
            'segment_status': 'security_halted',
            'stopped_reason': 'segment_exception',
            'error_type': type(error).__name__,
            'safety_state': str(state_path),
        })
    except OSError:
        # The durable safety state remains the controlling stop signal when
        # storage itself is unavailable.
        pass


def capture_segmented_segment_with_js(
    js_eval,
    out: Path,
    *,
    batch_size: Optional[int],
    pause_minutes: Optional[float],
    auto_continue: bool,
    user_authorized: bool,
    segment_index: Optional[int],
    manifest: Optional[Path],
    source: str = 'collection',
    safety_state: Optional[Path] = None,
):
    """Persist exactly one explicitly authorized segment without browser motion.

    This is a state/manifest boundary for an upper-level, user-authorized
    controller.  It intentionally does *not* implement scrolling, reloading,
    URL navigation, retrying, or automatic execution of later segments.
    """
    config = validate_segmented_capture_config(
        batch_size=batch_size,
        pause_minutes=pause_minutes,
        auto_continue=auto_continue,
        user_authorized=user_authorized,
        segment_index=segment_index,
    )
    if manifest is None:
        raise ValueError('segmented 模式必须提供每段独立的 manifest 输出路径')
    out = Path(out)
    manifest = Path(manifest)
    if out == manifest:
        raise ValueError('segmented 模式的条目输出与 manifest 必须是不同文件')
    if out.exists() or manifest.exists():
        raise ValueError('segmented 模式不会覆盖已有段文件；请使用新的 --segment-index 或新的输出路径')
    state_path = Path(safety_state) if safety_state else default_safety_state_path(out)
    state = ensure_active_session(
        state_path,
        stage='capture',
        policy={
            'capture_mode': 'segmented',
            **_segmented_contract(config),
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'controller_required': True,
            'resume_allowed': False,
        },
    )
    try:
        _prepare_segmented_state(state, state_path, config)
        _record_segmented_in_flight(state, state_path, config)
        write_private_json(manifest, {
            'output': str(out),
            'capture_mode': 'segmented',
            'segment_index': config['segment_index'],
            **_segmented_contract(config),
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'controller_required': True,
            'resume_allowed': False,
            'segment_status': 'in_progress',
            'safety_state': str(state_path),
        })
        data = read_capture_snapshot(js_eval, state_path)
        source_label = normalize_source_label(source)
        observed_items = list(data.get('items') or [])
        captured = []
        for item in observed_items[:config['batch_size']]:
            if not isinstance(item, dict) or not item.get('id'):
                continue
            row = dict(item)
            row['source_lists'] = [source_label]
            row['source_primary'] = source_label
            captured.append(row)
        write_private_json(out, captured)
        reached_limit = len(observed_items) >= config['batch_size']
        stopped_reason = 'batch_size_reached' if reached_limit else 'current_page_captured'
        page = {k: data.get(k) for k in ('location', 'title', 'scrollY', 'innerHeight', 'scrollHeight', 'declaredItemCount')}
        result = {
            'count': len(captured),
            'newly_seen_count': len(captured),
            'existing_count': 0,
            'source': source_label,
            'output': str(out),
            'page': page,
            'capture_mode': 'segmented',
            'segment_index': config['segment_index'],
            'batch_size': config['batch_size'],
            'pause_minutes': config['pause_minutes'],
            'auto_continue': True,
            'user_authorized': True,
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'controller_required': True,
            'resume_allowed': False,
            'crawl_complete': False,
            'stopped_reason': stopped_reason,
            'next_action': (
                f"至少等待 {config['pause_minutes']:g} 分钟后，由已获用户授权的上层 Agent 决定是否读取下一段；"
                '本脚本不会自动滚动、刷新、打开 URL 或续段。'
            ),
            'safety_state': str(state_path),
        }
        write_private_json(manifest, {
            **result,
            'item_count': len(captured),
            'observed_card_count': len(observed_items),
            'segment_status': 'completed',
        })
        _record_segmented_completion(state_path, config)
        result['manifest'] = str(manifest)
        return result
    except (SegmentedCapturePauseError, ValueError):
        raise
    except SafetyHaltedError as exc:
        _write_segmented_stop_manifest(manifest, out, state_path, config, exc)
        raise
    except Exception as exc:
        mark_security_halted(
            state_path,
            stage='capture',
            reason_code='segmented_capture_exception',
            message=f'分段读取出现异常，未继续下一段：{exc}',
        )
        _write_segmented_stop_manifest(manifest, out, state_path, config, exc)
        raise SafetyHaltedError('分段读取出现异常；已安全停止，不能用 --resume 继续。') from exc


def capture_current_segment_with_js(
    js_eval,
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
    data = read_capture_snapshot(js_eval, state_path)
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


def extract_with_js(js_eval, out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, reset_top: bool = True, safety_state: Optional[Path] = None):
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
            js_eval('window.scrollTo(0, 0); "ok"')
        except Exception as exc:
            if state_path and halt_if_safety_error(state_path, stage='capture', error=exc):
                raise SafetyHaltedError('抓取器返回安全异常；已写入安全停机状态。') from exc
            raise
        if scroll_pause > 0:
            time.sleep(scroll_pause)
    for index in range(max_scrolls):
        try:
            data, stability_checks = read_stable_items_snapshot(js_eval)
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
            js_eval('window.scrollBy(0, 1000); "ok"')
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
    js_eval,
    out: Path,
    max_scrolls: int,
    scroll_pause: float,
    manifest: Optional[Path] = None,
    source: str = 'collection',
    append_existing: bool = False,
    capture_mode: str = 'passive',
    segment_limit: int = 200,
    safety_state: Optional[Path] = None,
    segmented_config: Optional[Dict[str, Any]] = None,
):
    if capture_mode == 'passive':
        return capture_current_segment_with_js(
            js_eval,
            out,
            segment_limit,
            manifest,
            source,
            append_existing,
            safety_state,
        )
    if capture_mode == 'segmented':
        if append_existing:
            raise ValueError('segmented 模式每段独立落盘，不支持 --append-existing')
        if not isinstance(segmented_config, dict):
            raise ValueError('segmented 模式缺少 batch-size、pause-minutes、授权或段号配置')
        return capture_segmented_segment_with_js(
            js_eval,
            out,
            manifest=manifest,
            source=source,
            safety_state=safety_state,
            **segmented_config,
        )
    if capture_mode == 'scroll':
        return extract_with_js(
            js_eval,
            out,
            max_scrolls,
            scroll_pause,
            manifest,
            source,
            append_existing,
            safety_state=safety_state,
        )
    raise ValueError(f'未知抓取模式：{capture_mode}')


def extract_macos_chrome(out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, capture_mode: str = 'passive', segment_limit: int = 200, safety_state: Optional[Path] = None, segmented_config: Optional[Dict[str, Any]] = None):
    return extract_with_capture_mode(chrome_js_macos, out, max_scrolls, scroll_pause, manifest, source, append_existing, capture_mode, segment_limit, safety_state, segmented_config)


def extract_macos_safari(out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, capture_mode: str = 'passive', segment_limit: int = 200, safety_state: Optional[Path] = None, segmented_config: Optional[Dict[str, Any]] = None):
    return extract_with_capture_mode(safari_js_macos, out, max_scrolls, scroll_pause, manifest, source, append_existing, capture_mode, segment_limit, safety_state, segmented_config)


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


def extract_macos_arc(out: Path, max_scrolls: int, scroll_pause: float, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, capture_mode: str = 'passive', segment_limit: int = 200, safety_state: Optional[Path] = None, arc_window_id: str = '', arc_tab_id: str = '', arc_tab_marker: str = '', arc_expected_url_substring: str = '', segmented_config: Optional[Dict[str, Any]] = None):
    selectors = {
        '--arc-window-id': str(arc_window_id or '').strip(),
        '--arc-tab-id': str(arc_tab_id or '').strip(),
        '--arc-tab-marker': str(arc_tab_marker or '').strip(),
        '--arc-expected-url-substring': str(arc_expected_url_substring or '').strip(),
    }
    missing = [name for name, value in selectors.items() if not value]
    if missing:
        raise RuntimeError(f'Arc 抓取必须提供稳定的 window id、tab id、window.name 标记和预期 URL 片段；缺少：{", ".join(missing)}')

    def js_eval(js: str) -> str:
        return arc_js_macos(
            js,
            selectors['--arc-tab-marker'],
            selectors['--arc-window-id'],
            selectors['--arc-tab-id'],
            selectors['--arc-expected-url-substring'],
        )

    result = extract_with_capture_mode(js_eval, out, max_scrolls, scroll_pause, manifest, source, append_existing, capture_mode, segment_limit, safety_state, segmented_config)
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


def resolve_backend(value: str) -> str:
    if value == 'auto':
        raise RuntimeError('抓取必须显式指定 --backend macos-arc、macos-chrome、macos-safari 或 playwright；禁止自动选择外部浏览器。')
    return value


def segment_artifact_path(template: Path, segment_index: int) -> Path:
    """Derive collision-resistant per-segment paths from existing CLI templates."""
    template = Path(template)
    suffix = template.suffix or '.json'
    return template.with_name(f'{template.stem}.segment-{segment_index:03d}{suffix}')


def extract_playwright(out: Path, max_scrolls: int, scroll_pause: float, url: Optional[str], channel: str, user_data_dir: Optional[str], cdp_url: Optional[str], headless: bool, manifest: Optional[Path] = None, source: str = 'collection', append_existing: bool = False, capture_mode: str = 'passive', segment_limit: int = 200, safety_state: Optional[Path] = None, segmented_config: Optional[Dict[str, Any]] = None):
    if capture_mode in {'passive', 'segmented'} and url:
        raise ValueError('被动或分段采集不会自动打开 URL；请先由用户在已授权浏览器中手动打开目标页面，再连接当前页面。')
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

        result = extract_with_capture_mode(js_eval, out, max_scrolls, scroll_pause, manifest, source, append_existing, capture_mode, segment_limit, safety_state, segmented_config)
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
    parser.add_argument('--capture-mode', choices=['passive', 'segmented', 'scroll'], default='passive', help='默认 passive：仅读取当前已显示卡片；segmented：记录一段受控读取配置但不自行滚动或续段；scroll 仅用于明确的兼容性调试')
    parser.add_argument('--segment-limit', type=int, default=200, help='仅 passive：单段最多写入多少条；硬上限 200')
    parser.add_argument('--batch-size', type=int, default=None, help='仅 segmented：每段读取条数，必须显式为 1 到 200')
    parser.add_argument('--pause-minutes', type=float, default=None, help='仅 segmented：两段之间的最小暂停分钟数，必须显式大于 0')
    parser.add_argument('--segment-index', type=int, default=None, help='仅 segmented：从 1 开始的当前段号')
    parser.add_argument('--auto-continue', action='store_true', help='仅 segmented：明确授权上层 Agent 在暂停后安排下一段；本脚本不会自行续段')
    parser.add_argument('--user-authorized', action='store_true', help='仅 segmented：确认本次分段读取已获得用户一次性授权')
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

    out = Path(args.out)
    manifest = Path(args.manifest) if args.manifest else None
    segmented_config = None
    if args.capture_mode == 'passive' and (not isinstance(args.segment_limit, int) or not 1 <= args.segment_limit <= 200):
        parser.error('--segment-limit 必须是 1 到 200 的整数')
    if args.capture_mode == 'segmented':
        if manifest is None:
            parser.error('segmented 模式必须提供 --manifest，且会为每段生成独立 manifest 文件')
        if args.append_existing:
            parser.error('segmented 模式每段独立落盘，不支持 --append-existing')
        try:
            segmented_config = validate_segmented_capture_config(
                batch_size=args.batch_size,
                pause_minutes=args.pause_minutes,
                auto_continue=args.auto_continue,
                user_authorized=args.user_authorized,
                segment_index=args.segment_index,
            )
        except ValueError as exc:
            parser.error(str(exc))
        out = segment_artifact_path(out, segmented_config['segment_index'])
        manifest = segment_artifact_path(manifest, segmented_config['segment_index'])
        if out == manifest:
            parser.error('segmented 模式的条目输出与 manifest 必须是不同文件')
    safety_state = resolve_safety_state_path(args.safety_state, out)
    backend = resolve_backend(args.backend)
    if backend == 'macos-chrome':
        result = extract_macos_chrome(out, args.max_scrolls, args.scroll_pause, manifest, args.source, args.append_existing, args.capture_mode, args.segment_limit, safety_state, segmented_config)
    elif backend == 'macos-safari':
        result = extract_macos_safari(out, args.max_scrolls, args.scroll_pause, manifest, args.source, args.append_existing, args.capture_mode, args.segment_limit, safety_state, segmented_config)
    elif backend == 'macos-arc':
        result = extract_macos_arc(
            out, args.max_scrolls, args.scroll_pause, manifest, args.source, args.append_existing,
            args.capture_mode, args.segment_limit, safety_state,
            args.arc_window_id, args.arc_tab_id, args.arc_tab_marker, args.arc_expected_url_substring, segmented_config,
        )
    else:
        result = extract_playwright(out, args.max_scrolls, args.scroll_pause, args.url, args.channel, args.user_data_dir, args.cdp_url, args.headless, manifest, args.source, args.append_existing, args.capture_mode, args.segment_limit, safety_state, segmented_config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
