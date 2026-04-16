#!/usr/bin/env python3
import hashlib
import json
import re
import subprocess
import urllib.request
from pathlib import Path

DEFAULT_RULES = {
    '家居装修与收纳': ['家居', '装修', '餐边柜', '镜柜', '台盆柜', '厨房', '豪宅', '收纳', '客厅', '卧室'],
    '穿搭发型与品味': ['穿搭', '时尚', '男士', '香水', '老钱风', '西装', 'ootd', 'vogue', 'chanel'],
    '滑雪': ['滑雪', '单板', '雪场', '固定器', '换刃', 'casi'],
    '体态纠正与康复': ['走姿', '呼吸', '康复', '梨状肌', '崴脚', '一字马', '肚腩'],
    '运动训练与体态': ['硬拉', '训练', '腿部力量', '跟练', '跑步动作'],
    '效率系统与AI': ['app', '小组件', '收藏夹批量管理', '口播神器', '科研写作', '效率', 'ai'],
    '摄影审美与创作': ['剪辑', '配乐', '徕卡', '字体', '故事感', '画线'],
    '思考与成长': ['成长', '松弛感', '西西弗', '心智成熟', '探索新奇'],
}

def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write_json(path: Path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def normalize_text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, list):
        value = ' '.join(str(v) for v in value)
    return re.sub(r'\s+', ' ', str(value)).strip()

def load_taxonomy(path: Path | None):
    if not path:
        return list(DEFAULT_RULES.keys()) + ['杂项灵感']
    data = load_json(Path(path))
    boards = data.get('boards', []) if isinstance(data, dict) else data
    return boards or (list(DEFAULT_RULES.keys()) + ['杂项灵感'])

def choose_fallback_board(boards):
    return '杂项灵感' if '杂项灵感' in boards else (boards[-1] if boards else '杂项灵感')

def compute_rule_matches(blob: str, boards):
    matches = []
    board_set = set(boards or [])
    for board, words in DEFAULT_RULES.items():
        if board_set and board not in board_set:
            continue
        hits = []
        for word in words:
            if word.lower() in blob:
                hits.append(word)
        if hits:
            matches.append((board, hits))
    return matches

def infer_board(item: dict, ocr_entry: dict | None, boards):
    fallback = choose_fallback_board(boards)
    text_fields = [
        item.get('title', ''),
        item.get('desc', ''),
        ' '.join(item.get('tags', []) or []),
        item.get('user', ''),
        item.get('card_text', ''),
        (ocr_entry or {}).get('ocr_text', ''),
    ]
    blob = normalize_text('\\n'.join(part for part in text_fields if part)).lower()
    matches = compute_rule_matches(blob, boards)
    if matches:
        board, hits = matches[0]
        reason = []
        ocr_text = normalize_text((ocr_entry or {}).get('ocr_text', ''))
        for hit in hits:
            if ocr_text and hit.lower() in ocr_text.lower():
                reason.append(f'ocr:{hit}')
            else:
                reason.append(hit)
        confidence = 'high' if len(reason) >= 2 else 'medium'
        review_state = 'ocr_reviewed' if ocr_entry and ocr_entry.get('status') == 'ok' else 'classified'
        return board, confidence, reason, review_state
    if ocr_entry and normalize_text(ocr_entry.get('ocr_text', '')):
        return fallback, 'low', ['ocr:unmatched'], 'ocr_reviewed'
    return fallback, 'low', ['no_rule_match'], 'pending'

def safe_slug(value: str) -> str:
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', value)
    value = value.strip('-._')
    return value or 'item'

def download_image(url: str, dest: Path, timeout_sec: int = 20):
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
            'Referer': 'https://www.xiaohongshu.com/',
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        data = response.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return len(data)

def resolve_image_url(item: dict) -> str:
    for key in ('cover_image_url', 'image_url', 'cover', 'cover_url', 'currentSrc'):
        value = normalize_text(item.get(key, ''))
        if value.startswith('http://') or value.startswith('https://'):
            return value
    return ''

def run_swift_ocr(swift_script: Path, image_path: Path):
    proc = subprocess.run(
        ['/usr/bin/swift', str(swift_script), str(image_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or 'swift ocr failed')
    return json.loads(proc.stdout)

def build_cache_path(cache_dir: Path, item_id: str, image_url: str) -> Path:
    suffix = Path(urllib.request.urlparse(image_url).path).suffix or '.img'
    digest = hashlib.sha1(image_url.encode('utf-8')).hexdigest()[:12]
    return cache_dir / f'{safe_slug(item_id)}-{digest}{suffix}'

def perform_ocr_for_items(items, output_path: Path, cache_dir: Path | None = None, swift_script: Path | None = None, timeout_sec: int = 20, force: bool = False):
    output_path = Path(output_path)
    base_dir = output_path.parent
    cache_dir = Path(cache_dir) if cache_dir else base_dir / 'ocr_cache'
    swift_script = Path(swift_script) if swift_script else Path(__file__).resolve().parent / 'ocr_image.swift'
    existing = {}
    if output_path.exists() and not force:
        try:
            existing = {entry.get('id'): entry for entry in load_json(output_path) if isinstance(entry, dict) and entry.get('id')}
        except Exception:
            existing = {}
    results = []
    for item in items:
        item_id = item.get('id') or safe_slug(item.get('title', 'item'))
        if item_id in existing and existing[item_id].get('status') == 'ok' and not force:
            results.append(existing[item_id])
            continue
        image_url = resolve_image_url(item)
        entry = {
            'id': item_id,
            'title': item.get('title', ''),
            'image_url': image_url,
            'status': 'pending',
            'ocr_text': '',
            'ocr_lines': [],
            'ocr_confidence': None,
            'error': '',
        }
        if not image_url:
            entry['status'] = 'missing_image_url'
            results.append(entry)
            continue
        cache_path = build_cache_path(cache_dir, item_id, image_url)
        try:
            if force or not cache_path.exists():
                download_image(image_url, cache_path, timeout_sec=timeout_sec)
            ocr = run_swift_ocr(swift_script, cache_path)
            entry['status'] = 'ok'
            entry['ocr_text'] = normalize_text(ocr.get('text', ''))
            entry['ocr_lines'] = ocr.get('lines', [])
            entry['ocr_confidence'] = ocr.get('average_confidence')
            entry['download_path'] = str(cache_path)
        except Exception as exc:
            entry['status'] = 'error'
            entry['error'] = str(exc)
            entry['download_path'] = str(cache_path)
        results.append(entry)
        write_json(output_path, results)
    write_json(output_path, results)
    return results
