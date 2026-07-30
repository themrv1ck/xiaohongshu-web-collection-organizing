#!/usr/bin/env python3
import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from video_content_common import normalize_content_type
from xhs_ocr_common import image_url_from_value, load_json, resolve_image_urls
from xhs_safety import (
    default_safety_state_path,
    ensure_active_session,
    halt_if_safety_error,
    mark_security_halted,
    resolve_safety_state_path,
)


MOBILE_USER_AGENT = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 '
    'Mobile/15E148 Safari/604.1'
)
SETUP_MARKER = 'window.__SETUP_SERVER_STATE__='
SECURITY_MARKERS = ('IP 存在风险', '异常访问', '安全验证', '访问过于频繁')
SECURITY_CODE_RE = re.compile(r'["\'](?:code|errorCode)["\']\s*:\s*300012(?:\D|$)')
NOTE_ID_RE = re.compile(r'^[a-f0-9]{24}$', re.IGNORECASE)


class ScriptCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._inside_script = False
        self._chunks = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'script':
            self._inside_script = True
            self._chunks = []

    def handle_data(self, data):
        if self._inside_script:
            self._chunks.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'script' and self._inside_script:
            self.scripts.append(''.join(self._chunks))
            self._inside_script = False
            self._chunks = []


class SecurityBlockError(RuntimeError):
    pass


def canonical_note_url(item):
    note_id = str(item.get('id') or '').strip()
    if not NOTE_ID_RE.fullmatch(note_id):
        raise ValueError('图文笔记缺少合法的 24 位 note id')
    href = str(item.get('href') or '')
    if href:
        parsed = urllib.parse.urlsplit(href)
        host = (parsed.hostname or '').lower()
        if host and host != 'xiaohongshu.com' and not host.endswith('.xiaohongshu.com'):
            raise ValueError('笔记链接不是 xiaohongshu.com')
    return f'https://www.xiaohongshu.com/explore/{note_id}'


def parse_setup_state(html_text):
    collector = ScriptCollector()
    collector.feed(html_text)
    decoder = json.JSONDecoder()
    parse_error = None
    for script in collector.scripts:
        marker_index = script.find(SETUP_MARKER)
        if marker_index < 0:
            continue
        payload = script[marker_index + len(SETUP_MARKER):].lstrip()
        try:
            state, _ = decoder.raw_decode(payload)
        except json.JSONDecodeError as exc:
            parse_error = exc
            continue
        if isinstance(state, dict):
            return state
    if parse_error:
        raise ValueError(f'__SETUP_SERVER_STATE__ 不是有效 JSON：{parse_error.msg}')
    raise ValueError('页面中没有找到 __SETUP_SERVER_STATE__')


def note_data_from_state(state, note_id):
    page = state.get('LAUNCHER_SSR_STORE_PAGE_DATA')
    if not isinstance(page, dict):
        raise ValueError('缺少 LAUNCHER_SSR_STORE_PAGE_DATA')
    note_data = page.get('noteData')
    if not isinstance(note_data, dict):
        raise ValueError('缺少 noteData')
    nested = note_data.get(note_id)
    if isinstance(nested, dict):
        note_data = nested
    returned_id = str(note_data.get('noteId') or note_data.get('id') or '').strip()
    if returned_id and returned_id != note_id:
        raise ValueError('noteData 的 note id 与请求不一致')
    return note_data


def image_urls_from_note_data(note_data):
    image_list = note_data.get('imageList')
    if not isinstance(image_list, list) or not image_list:
        raise ValueError('noteData 没有非空 imageList')
    urls = []
    for index, image in enumerate(image_list):
        url = image_url_from_value(image)
        if not url:
            raise ValueError(f'imageList 第 {index + 1} 张缺少可下载 URL')
        urls.append(url)
    return urls


def fetch_note_html(url, timeout_sec=20):
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': MOBILE_USER_AGENT,
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': 'https://www.xiaohongshu.com/',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return response.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 412, 429, 461}:
            raise SecurityBlockError(f'小红书详情请求被安全限制拦截：HTTP {exc.code}') from exc
        raise


def redact_error(exc):
    message = ' '.join(str(exc).split())[:500]
    return re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?<redacted_query>', message)


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + '.tmp')
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp_path.chmod(0o600)
    temp_path.replace(path)


def enrich_item_from_html(item, html_text):
    note_id = str(item.get('id') or '')
    if not NOTE_ID_RE.fullmatch(note_id):
        raise ValueError('图文笔记缺少合法的 24 位 note id')
    try:
        state = parse_setup_state(html_text)
        note_data = note_data_from_state(state, note_id)
        detail_type = normalize_content_type(note_data.get('type'))
        if detail_type == 'unknown':
            raise ValueError('noteData 缺少可验证的图文/视频类型')
        if detail_type == 'image':
            image_urls = image_urls_from_note_data(note_data)
    except ValueError:
        for marker in SECURITY_MARKERS:
            if marker in html_text:
                raise SecurityBlockError(f'小红书页面触发安全限制：{marker}')
        if SECURITY_CODE_RE.search(html_text):
            raise SecurityBlockError('小红书页面触发安全限制：code=300012')
        raise
    enriched = dict(item)
    enriched['content_type'] = detail_type
    enriched['content_type_source'] = 'mobile_ssr_note_data.type'
    if detail_type == 'video':
        enriched['image_urls'] = []
        enriched['image_count'] = 0
        enriched['image_urls_complete'] = False
        enriched['image_list_source'] = ''
        enriched['image_enrichment_status'] = 'not_applicable'
        enriched['image_enrichment_error'] = ''
        return enriched
    enriched['image_urls'] = image_urls
    enriched['image_count'] = len(image_urls)
    enriched['image_urls_complete'] = True
    enriched['image_list_source'] = 'mobile_ssr_note_data.imageList'
    enriched['image_enrichment_status'] = 'ok'
    enriched['image_enrichment_error'] = ''
    if not enriched.get('cover_image_url'):
        enriched['cover_image_url'] = image_urls[0]
    return enriched


def main():
    parser = argparse.ArgumentParser(description='补齐图文笔记的封面和全部内页图片列表。')
    parser.add_argument('src', help='visible_items.json 路径')
    parser.add_argument('out', nargs='?', default='image_items.json', help='补全图片列表后的输出路径')
    parser.add_argument('--timeout-sec', type=int, default=20, help='单条笔记页面请求超时')
    parser.add_argument('--request-interval', type=float, default=1.5, help='相邻详情请求之间的最小间隔秒数')
    parser.add_argument('--max-items', type=int, default=None, help='本次明确授权访问多少条图文详情；最大 200')
    parser.add_argument('--allow-detail-requests', action='store_true', help='明确同意本次访问图文详情页；默认低风险模式不会发详情请求')
    parser.add_argument('--resume', action='store_true', help='复用输出中 image_enrichment_status=ok 的条目')
    parser.add_argument('--safety-state', default='', help='共享安全状态文件；默认继承输入文件旁已有状态，否则使用输出同目录的 xhs_safety_state.json')
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    if args.allow_detail_requests:
        if args.max_items is None:
            parser.error('访问图文详情必须同时明确传入 --max-items；不会默认请求全部笔记。')
        if not isinstance(args.max_items, int) or isinstance(args.max_items, bool) or not 1 <= args.max_items <= 200:
            parser.error('--max-items 必须是 1 到 200 的整数')
    safety_state = resolve_safety_state_path(args.safety_state, out, predecessors=(src,))
    ensure_active_session(
        safety_state,
        stage='image_enrichment',
        policy={
            'auto_scroll': False,
            'auto_navigation': False,
            'auto_retry': False,
            'detail_requests_enabled': bool(args.allow_detail_requests),
            'detail_request_limit': args.max_items if args.allow_detail_requests else 0,
        },
    )
    items = load_json(src)
    if not isinstance(items, list):
        raise ValueError('visible_items.json 顶层必须是数组')
    if any(not isinstance(item, dict) for item in items):
        raise ValueError('visible_items.json 每一项都必须是对象')
    item_ids = [str(item.get('id') or '').strip() for item in items]
    if any(not note_id for note_id in item_ids):
        raise ValueError('visible_items.json 每一项都必须有非空 note id')
    if len(set(item_ids)) != len(item_ids):
        raise ValueError('visible_items.json 包含重复 note id')
    previous = {}
    if args.resume and out.exists():
        old_items = load_json(out)
        if isinstance(old_items, list):
            old_ids = [str(item.get('id') or '') for item in old_items if isinstance(item, dict)]
            if len(old_ids) != len(set(old_ids)):
                raise ValueError('已有 image_items.json 包含重复 note id')
            previous = {
                str(item.get('id')): item
                for item in old_items
                if (
                    isinstance(item, dict)
                    and item.get('id')
                    and item.get('image_enrichment_status') == 'ok'
                    and item.get('image_list_source') == 'mobile_ssr_note_data.imageList'
                    and item.get('content_type_source') == 'mobile_ssr_note_data.type'
                    and normalize_content_type(item.get('content_type')) == 'image'
                    and item.get('image_urls_complete') is True
                    and isinstance(item.get('image_count'), int)
                    and item.get('image_count') == len(resolve_image_urls(item))
                )
            }

    results = []
    requested = 0
    succeeded = 0
    failed = 0
    stopped_reason = 'completed'
    for item in items:
        note_id = str(item.get('id') or '')
        item_type = normalize_content_type(item.get('content_type'))
        if item_type != 'image':
            not_applicable = dict(item)
            not_applicable['image_urls'] = []
            not_applicable['image_count'] = 0
            not_applicable['image_urls_complete'] = False
            not_applicable['image_list_source'] = ''
            not_applicable['image_enrichment_status'] = (
                'not_applicable' if item_type == 'video' else 'content_type_required'
            )
            not_applicable['image_enrichment_error'] = ''
            results.append(not_applicable)
            continue
        if note_id in previous:
            results.append(previous[note_id])
            succeeded += 1
            continue
        current_urls = resolve_image_urls(item)
        if (
            item.get('image_urls_complete') is True
            and item.get('image_enrichment_status') == 'ok'
            and item.get('image_list_source') == 'mobile_ssr_note_data.imageList'
            and item.get('content_type_source') == 'mobile_ssr_note_data.type'
            and isinstance(item.get('image_count'), int)
            and item.get('image_count') == len(current_urls)
            and current_urls
        ):
            preserved = dict(item)
            preserved.setdefault('image_enrichment_status', 'ok')
            results.append(preserved)
            succeeded += 1
            continue
        if not args.allow_detail_requests:
            not_requested = dict(item)
            not_requested['image_urls'] = current_urls
            not_requested['image_urls_complete'] = False
            not_requested['image_enrichment_status'] = 'detail_request_not_enabled'
            not_requested['image_enrichment_error'] = '默认低风险模式不会自动访问笔记详情；请先人工确认本次范围，再明确传 --allow-detail-requests 和 --max-items。'
            results.append(not_requested)
            stopped_reason = 'detail_requests_not_enabled'
            continue
        if args.max_items is not None and requested >= args.max_items:
            not_requested = dict(item)
            not_requested['image_urls'] = current_urls
            not_requested['image_urls_complete'] = False
            not_requested['image_enrichment_status'] = 'not_requested'
            not_requested['image_enrichment_error'] = '达到 --max-items 抽样上限，尚未请求该图文笔记。'
            results.append(not_requested)
            stopped_reason = 'max_items_reached'
            continue
        requested += 1
        try:
            url = canonical_note_url(item)
            html_text = fetch_note_html(url, timeout_sec=args.timeout_sec)
            results.append(enrich_item_from_html(item, html_text))
            succeeded += 1
        except SecurityBlockError as exc:
            blocked = dict(item)
            blocked['image_urls'] = resolve_image_urls(item)
            blocked['image_urls_complete'] = False
            blocked['image_enrichment_status'] = 'security_blocked'
            blocked['image_enrichment_error'] = redact_error(exc)
            results.append(blocked)
            failed += 1
            stopped_reason = 'security_blocked'
            mark_security_halted(
                safety_state,
                stage='image_enrichment',
                reason_code='security_challenge',
                message=redact_error(exc),
            )
            for remaining in items[len(results):]:
                pending = dict(remaining)
                if normalize_content_type(pending.get('content_type')) == 'image':
                    pending['image_urls'] = resolve_image_urls(pending)
                    pending['image_urls_complete'] = False
                    pending['image_enrichment_status'] = 'not_requested_after_security_block'
                    pending['image_enrichment_error'] = '前序请求触发安全限制，本条未继续请求。'
                results.append(pending)
            atomic_write_json(out, results)
            break
        except Exception as exc:
            if halt_if_safety_error(safety_state, stage='image_enrichment', error=exc):
                blocked = dict(item)
                blocked['image_urls'] = resolve_image_urls(item)
                blocked['image_urls_complete'] = False
                blocked['image_enrichment_status'] = 'security_blocked'
                blocked['image_enrichment_error'] = redact_error(exc)
                results.append(blocked)
                failed += 1
                stopped_reason = 'security_blocked'
                for remaining in items[len(results):]:
                    pending = dict(remaining)
                    if normalize_content_type(pending.get('content_type')) == 'image':
                        pending['image_urls'] = resolve_image_urls(pending)
                        pending['image_urls_complete'] = False
                        pending['image_enrichment_status'] = 'not_requested_after_security_block'
                        pending['image_enrichment_error'] = '前序请求触发安全限制，本条未继续请求。'
                    results.append(pending)
                atomic_write_json(out, results)
                break
            failed_item = dict(item)
            failed_item['image_urls'] = resolve_image_urls(item)
            failed_item['image_urls_complete'] = False
            failed_item['image_enrichment_status'] = 'error'
            failed_item['image_enrichment_error'] = redact_error(exc)
            results.append(failed_item)
            failed += 1
        atomic_write_json(out, results + [dict(remaining) for remaining in items[len(results):]])
        if args.request_interval > 0 and requested < len(items):
            time.sleep(args.request_interval)

    if len(results) < len(items):
        seen_ids = {str(item.get('id') or '') for item in results}
        results.extend(dict(item) for item in items if str(item.get('id') or '') not in seen_ids)
    atomic_write_json(out, results)
    print(json.dumps({
        'count': len(results),
        'requested': requested,
        'succeeded': succeeded,
        'failed': failed,
        'stopped_reason': stopped_reason,
        'output': str(out),
        'safety_state': str(safety_state),
    }, ensure_ascii=False, indent=2))
    if stopped_reason == 'security_blocked':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
