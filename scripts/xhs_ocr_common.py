#!/usr/bin/env python3
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Optional

from video_content_common import normalize_content_type, redact_sensitive_text

EXPLICIT_TAXONOMY_KEYWORD_RULES = {
    'hermes': ['hermes', 'codex', 'token', 'skill', '自动分类'],
    '穿搭发型与品味': ['穿搭', '时尚', '男士', '香水', '老钱风', '西装', 'ootd', 'vogue', 'chanel', '高级感', '理发', '发型', '卷发', '发油', '衣服', '鞋', '莫卡辛', 't恤', '贵妇'],
    '体态纠正与康复': ['走姿', '呼吸', '康复', '梨状肌', '崴脚', '一字马', '肚腩', '骨盆', '肩胛', '前屈', '拉伸', '呼吸法', '足底', '筋膜炎'],
    '运动训练与体态': ['硬拉', '训练', '腿部力量', '跟练', '跑步动作', '跑步', '攀岩', '倒立', '半马', '深蹲', '卧推', '动物流', '弹跳', '体态'],
    '效率系统与AI': ['app', '小组件', '收藏夹批量管理', '口播神器', '科研写作', '效率', 'ai', 'agent', '智能体', 'notion', '第二大脑', '工作原理'],
    '摄影审美与创作': ['剪辑', '配乐', '徕卡', '字体', '故事感', '画线', '构图', '审美', '旅拍', '油画', '随手拍', '拍摄', '电影', '导演', '诺兰', '叙事'],
    '思考与成长': ['成长', '松弛感', '西西弗', '心智成熟', '探索新奇', '多巴胺', '大脑', '前额叶', '平庸', '拖延', '人生', '轨迹', '自控', '赚钱', '计划', '改变', '自己', '阅读', '读书', '书单', '本书'],
    '日本旅行与机位': ['日本', '东京', 'tokyo', '神社', '户隐', '富士山', '罗森', '自由行', '机位', '海外', '摩纳哥'],
    '接口探测专辑': ['接口', '探测', 'api', '抓包', 'webhook', 'runtime', 'webpack', 'userid'],
}
OCR_PIPELINE_VERSION = 'multi-image-ocr-v1'


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path: Path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + '.tmp')
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp_path.chmod(0o600)
    os.replace(temp_path, path)


def normalize_text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, list):
        value = ' '.join(str(v) for v in value)
    return re.sub(r'\s+', ' ', str(value)).strip()


def load_taxonomy(path: Optional[Path]):
    if not path:
        return []
    data = load_json(Path(path))
    boards = data.get('boards', []) if isinstance(data, dict) else data
    return boards if isinstance(boards, list) else []


def compute_rule_matches(blob: str, boards):
    matches = []
    board_set = set(boards or [])
    if not board_set:
        return []
    for index, (board, words) in enumerate(EXPLICIT_TAXONOMY_KEYWORD_RULES.items()):
        if board not in board_set:
            continue
        hits = []
        for word in words:
            if word.lower() in blob:
                hits.append(word)
        if hits:
            matches.append((board, hits, index))
    offset = len(EXPLICIT_TAXONOMY_KEYWORD_RULES)
    for index, board_value in enumerate(boards or []):
        board = normalize_text(board_value)
        if (
            board
            and board not in EXPLICIT_TAXONOMY_KEYWORD_RULES
            and board.lower() in blob
        ):
            matches.append((board, [board], offset + index))
    matches.sort(key=lambda item: (-len(item[1]), item[2]))
    return [(board, hits) for board, hits, _ in matches]


def infer_board(item: dict, ocr_entry: Optional[dict], boards):
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
        return '', 'low', ['ocr:unmatched'], 'ocr_reviewed'
    return '', 'low', ['no_rule_match'], 'pending'


def safe_slug(value: str) -> str:
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', value)
    value = value.strip('-._')
    return value or 'item'


def supported_image_bytes(data: bytes) -> bool:
    if data.startswith(b'\xff\xd8\xff'):
        return True
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return True
    if data.startswith((b'GIF87a', b'GIF89a', b'BM')):
        return True
    if data.startswith((b'II*\x00', b'MM\x00*')):
        return True
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return True
    if data.startswith(b'\xff\x0a') or data.startswith(b'\x00\x00\x00\x0cJXL '):
        return True
    if len(data) >= 12 and data[4:8] == b'ftyp':
        return data[8:12] in {
            b'avif', b'avis', b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1'
        }
    return False


def cached_image_valid(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open('rb') as handle:
            return supported_image_bytes(handle.read(32))
    except OSError:
        return False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download_image(url: str, dest: Path, timeout_sec: int = 20):
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
            'Referer': 'https://www.xiaohongshu.com/',
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        data = response.read()
    if not supported_image_bytes(data):
        raise RuntimeError('downloaded response is not a supported image')
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest.with_name(dest.name + '.part')
    try:
        temp_path.write_bytes(data)
        temp_path.chmod(0o600)
        os.replace(temp_path, dest)
    finally:
        temp_path.unlink(missing_ok=True)
    return len(data)


def normalize_image_url(value: str) -> str:
    url = normalize_text(value)
    if url.startswith('http://'):
        url = 'https://' + url[len('http://'):]
    return url if url.startswith('https://') else ''


def image_url_from_value(value) -> str:
    if isinstance(value, str):
        return normalize_image_url(value)
    if not isinstance(value, dict):
        return ''
    for key in ('urlDefault', 'url', 'urlPre', 'src'):
        url = normalize_image_url(value.get(key, ''))
        if url:
            return url
    info_list = value.get('infoList') or value.get('info_list')
    if isinstance(info_list, list):
        for scene in ('WB_DFT', 'WB_PRV', 'WB_WM'):
            for info in info_list:
                if isinstance(info, dict) and str(info.get('imageScene') or info.get('image_scene') or '') == scene:
                    url = normalize_image_url(info.get('url', ''))
                    if url:
                        return url
        for info in info_list:
            if isinstance(info, dict):
                url = normalize_image_url(info.get('url', ''))
                if url:
                    return url
    return ''


def resolve_image_urls(item: dict) -> list[str]:
    values = []
    for key in ('image_urls', 'images', 'imageList', 'image_list'):
        raw = item.get(key)
        if isinstance(raw, list) and raw:
            values = raw
            break
    urls = []
    for value in values:
        url = image_url_from_value(value)
        if url:
            urls.append(url)
    if urls:
        return urls
    for key in ('cover_image_url', 'image_url', 'cover', 'cover_url', 'currentSrc'):
        url = image_url_from_value(item.get(key))
        if url:
            return [url]
    return urls


def resolve_image_files(item: dict) -> list[str]:
    raw = item.get('image_files')
    if not isinstance(raw, list):
        return []
    files = []
    for value in raw:
        candidate = normalize_text(value)
        if candidate:
            files.append(candidate)
    return files


def run_swift_ocr(swift_script: Path, image_path: Path):
    swift_bin = shutil.which('swift') or '/usr/bin/swift'
    proc = subprocess.run([swift_bin, str(swift_script), str(image_path)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or 'swift ocr failed')
    return json.loads(proc.stdout)


@lru_cache(maxsize=1)
def swift_vision_ready():
    swift_bin = shutil.which('swift')
    if platform.system() != 'Darwin' or not swift_bin:
        return False
    try:
        proc = subprocess.run(
            [swift_bin, '-e', 'import Vision; print("ok")'],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return False
    return proc.returncode == 0


@lru_cache(maxsize=1)
def tesseract_languages():
    tesseract = shutil.which('tesseract')
    if not tesseract:
        return set()
    try:
        proc = subprocess.run(
            [tesseract, '--list-langs'],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return set()
    if proc.returncode != 0:
        return set()
    lines = (proc.stdout + '\n' + proc.stderr).splitlines()
    return {
        line.strip()
        for line in lines
        if line.strip() and not line.lower().startswith('list of available languages')
    }


def tesseract_language_ready(language: str):
    return language in tesseract_languages()


def run_tesseract_ocr(image_path: Path, languages: str = 'chi_sim'):
    tesseract = shutil.which('tesseract')
    if not tesseract:
        raise RuntimeError('tesseract not found')
    requested = [language for language in languages.split('+') if language]
    missing = [language for language in requested if not tesseract_language_ready(language)]
    if missing:
        raise RuntimeError(f'tesseract language data missing: {", ".join(missing)}')
    proc = subprocess.run([tesseract, str(image_path), 'stdout', '-l', languages], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or 'tesseract ocr failed')
    text = normalize_text(proc.stdout)
    return {'text': text, 'lines': [{'text': line, 'confidence': None} for line in proc.stdout.splitlines() if normalize_text(line)], 'average_confidence': None, 'provider': 'tesseract'}


def run_easyocr_ocr(image_path: Path):
    try:
        import easyocr
    except Exception as exc:
        raise RuntimeError('easyocr not installed') from exc
    reader = easyocr.Reader(['ch_sim', 'en'], gpu=False)
    rows = reader.readtext(str(image_path))
    lines = [{'text': normalize_text(row[1]), 'confidence': float(row[2]) if len(row) > 2 else None} for row in rows]
    confidences = [line['confidence'] for line in lines if line['confidence'] is not None]
    return {'text': normalize_text(' '.join(line['text'] for line in lines)), 'lines': lines, 'average_confidence': (sum(confidences) / len(confidences) if confidences else None), 'provider': 'easyocr'}


def detect_ocr_provider(preferred: str):
    if preferred != 'auto':
        return preferred
    if swift_vision_ready():
        return 'swift'
    if tesseract_language_ready('chi_sim'):
        return 'tesseract'
    return 'none'


def run_ocr(provider: str, image_path: Path, swift_script: Path, tesseract_lang: str):
    if provider == 'swift':
        result = run_swift_ocr(swift_script, image_path)
        result.setdefault('provider', 'swift')
        return result
    if provider == 'tesseract':
        return run_tesseract_ocr(image_path, languages=tesseract_lang)
    if provider == 'easyocr':
        return run_easyocr_ocr(image_path)
    raise RuntimeError('no OCR provider available; install tesseract or easyocr, or use macOS swift Vision')


def build_cache_path(cache_dir: Path, item_id: str, image_url: str) -> Path:
    suffix = Path(urllib.parse.urlparse(image_url).path).suffix or '.img'
    digest = hashlib.sha1(image_url.encode('utf-8')).hexdigest()[:12]
    return cache_dir / f'{safe_slug(item_id)}-{digest}{suffix}'


def image_set_sha256(image_urls: list[str]) -> str:
    blob = json.dumps(image_urls, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def ocr_run_fingerprint(provider: str, tesseract_lang: str, swift_script: Path) -> str:
    payload = {
        'pipeline_version': OCR_PIPELINE_VERSION,
        'provider': provider,
        'tesseract_lang': tesseract_lang if provider == 'tesseract' else '',
        'swift_script_sha256': '',
    }
    if provider == 'swift':
        payload['swift_script_sha256'] = hashlib.sha256(Path(swift_script).read_bytes()).hexdigest()
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def reusable_ocr_entry(
    entry: dict,
    image_references: list[str],
    image_hash: str,
    run_fingerprint: str,
    source_image_hashes: Optional[list[str]] = None,
) -> bool:
    image_count = len(image_references)
    images = entry.get('images')
    if not (
        entry.get('status') == 'ok'
        and entry.get('image_set_complete') is True
        and entry.get('image_set_sha256') == image_hash
        and entry.get('ocr_run_fingerprint') == run_fingerprint
        and entry.get('image_count_declared') == image_count
        and entry.get('image_count_available') == image_count
        and entry.get('image_count_processed') == image_count
        and isinstance(images, list)
        and len(images) == image_count
    ):
        return False
    if source_image_hashes is not None and len(source_image_hashes) != image_count:
        return False
    for image_index, (image, image_reference) in enumerate(zip(images, image_references)):
        if not isinstance(image, dict):
            return False
        if image.get('image_index') != image_index or image.get('status') != 'ok':
            return False
        if source_image_hashes is None:
            expected = hashlib.sha256(image_reference.encode('utf-8')).hexdigest()
            if image.get('source_url_sha256') != expected:
                return False
        else:
            expected = source_image_hashes[image_index]
            if (
                image.get('source_image_sha256') != expected
                or image.get('image_sha256') != expected
            ):
                return False
    return True


def perform_ocr_for_items(items, output_path: Path, cache_dir: Optional[Path] = None, swift_script: Optional[Path] = None, timeout_sec: int = 20, force: bool = False, provider: str = 'auto', tesseract_lang: str = 'chi_sim'):
    output_path = Path(output_path)
    base_dir = output_path.parent
    cache_dir = Path(cache_dir) if cache_dir else base_dir / 'ocr_cache'
    swift_script = Path(swift_script) if swift_script else Path(__file__).resolve().parent / 'ocr_image.swift.txt'
    provider = detect_ocr_provider(provider)
    run_fingerprint = ocr_run_fingerprint(provider, tesseract_lang, swift_script)
    if not isinstance(items, list):
        items = list(items)
    if any(not isinstance(item, dict) for item in items):
        raise ValueError('OCR 输入必须是对象数组')
    image_items = [
        item for item in items
        if normalize_content_type(item.get('content_type') or item.get('note_type') or item.get('type')) == 'image'
    ]
    item_ids = [str(item.get('id') or '').strip() for item in image_items]
    if any(not item_id for item_id in item_ids):
        raise ValueError('每条图文笔记都必须有非空 note id')
    if len(set(item_ids)) != len(item_ids):
        raise ValueError('图文 OCR 输入包含重复 note id')
    existing = {}
    if output_path.exists() and not force:
        try:
            existing_rows = load_json(output_path)
        except Exception:
            existing_rows = []
        if isinstance(existing_rows, list):
            existing_ids = [str(entry.get('id') or '') for entry in existing_rows if isinstance(entry, dict)]
            if len(existing_ids) != len(set(existing_ids)):
                raise ValueError('已有 ocr_results.json 包含重复 note id')
            existing = {
                str(entry.get('id')): entry
                for entry in existing_rows
                if isinstance(entry, dict) and entry.get('id')
            }
    results = []
    for item in image_items:
        item_id = str(item.get('id')).strip()
        image_urls = resolve_image_urls(item)
        image_files = resolve_image_files(item)
        local_mode = bool(image_files)
        local_paths = []
        local_hashes = []
        local_contract_valid = True
        if local_mode:
            declared_hashes = item.get('image_file_sha256')
            if (
                not isinstance(declared_hashes, list)
                or len(declared_hashes) != len(image_files)
            ):
                local_contract_valid = False
                declared_hashes = []
            for index, relative_value in enumerate(image_files):
                try:
                    relative = Path(relative_value)
                    if relative.is_absolute() or '..' in relative.parts:
                        raise ValueError('local image path must be relative')
                    unresolved = base_dir / relative
                    if unresolved.is_symlink() or not unresolved.is_file():
                        raise ValueError('local image path must be a regular file')
                    candidate = unresolved.resolve(strict=True)
                    candidate.relative_to(base_dir.resolve(strict=True))
                    expected_hash = str(declared_hashes[index] or '').strip().lower()
                    actual_hash = file_sha256(candidate)
                    if (
                        not re.fullmatch(r'[0-9a-f]{64}', expected_hash)
                        or actual_hash != expected_hash
                        or not cached_image_valid(candidate)
                    ):
                        raise ValueError('local image contract failed')
                    local_paths.append(candidate)
                    local_hashes.append(actual_hash)
                except (OSError, ValueError, IndexError):
                    local_contract_valid = False
                    break
        image_references = (
            [f'sha256:{value}' for value in local_hashes]
            if local_mode and local_contract_valid
            else image_urls
        )
        current_image_set_sha256 = image_set_sha256(image_references)
        declared_count = item.get('image_count')
        if not isinstance(declared_count, int) or declared_count < 0:
            declared_count = None
        image_set_complete = bool(
            item.get('image_urls_complete') is True
            and item.get('image_enrichment_status') == 'ok'
            and item.get('image_list_source') in {
                'mobile_ssr_note_data.imageList',
                'arc_authenticated_frontend.detail_dom.imageList',
                'workbuddy_authenticated_frontend.noteData.imageList',
                'workbuddy_authenticated_frontend.noteData.imageList.local_copy',
            }
            and image_references
            and declared_count == len(image_references)
            and (not local_mode or local_contract_valid)
        )
        if (
            image_set_complete
            and item_id in existing
            and reusable_ocr_entry(
                existing[item_id],
                image_references,
                current_image_set_sha256,
                run_fingerprint,
                local_hashes if local_mode else None,
            )
            and not force
        ):
            results.append(existing[item_id])
            continue
        entry = {
            'id': item_id,
            'title': item.get('title', ''),
            'status': 'pending',
            'ocr_text': '',
            'ocr_lines': [],
            'ocr_confidence': None,
            'ocr_provider': provider,
            'image_count_declared': declared_count,
            'image_count_available': len(image_references),
            'image_count_processed': 0,
            'image_set_complete': image_set_complete,
            'image_set_sha256': current_image_set_sha256,
            'ocr_run_fingerprint': run_fingerprint,
            'images': [],
            'error': '',
        }
        if not image_set_complete:
            entry['status'] = 'incomplete_image_set'
            entry['error'] = '图文笔记尚未取得封面和全部内页图片，未执行部分 OCR。'
            results.append(entry)
            write_json(output_path, results)
            continue
        texts = []
        all_lines = []
        confidences = []
        failed = False
        for image_index, image_reference in enumerate(image_references):
            cache_path = (
                local_paths[image_index]
                if local_mode
                else build_cache_path(cache_dir, item_id, image_reference)
            )
            image_entry = {
                'image_index': image_index,
                'status': 'pending',
                'ocr_text': '',
                'ocr_lines': [],
                'ocr_confidence': None,
                'ocr_provider': provider,
                'download_path': str(cache_path),
                'image_sha256': '',
                'error': '',
            }
            if local_mode:
                image_entry['source_image_sha256'] = local_hashes[image_index]
            else:
                image_entry['source_url_sha256'] = hashlib.sha256(
                    image_reference.encode('utf-8')
                ).hexdigest()
            try:
                if local_mode:
                    if (
                        not cached_image_valid(cache_path)
                        or file_sha256(cache_path) != local_hashes[image_index]
                    ):
                        raise RuntimeError('authenticated local image changed before OCR')
                elif force or not cached_image_valid(cache_path):
                    if cache_path.exists():
                        cache_path.unlink()
                    download_image(image_reference, cache_path, timeout_sec=timeout_sec)
                image_entry['image_sha256'] = file_sha256(cache_path)
                ocr = run_ocr(provider, cache_path, swift_script, tesseract_lang)
                image_text = normalize_text(ocr.get('text', ''))
                image_lines = []
                for line in ocr.get('lines', []):
                    if isinstance(line, dict):
                        normalized_line = dict(line)
                    elif isinstance(line, str) and normalize_text(line):
                        normalized_line = {'text': normalize_text(line), 'confidence': None}
                    else:
                        continue
                    normalized_line['image_index'] = image_index
                    image_lines.append(normalized_line)
                image_entry['status'] = 'ok'
                image_entry['ocr_text'] = image_text
                image_entry['ocr_lines'] = image_lines
                image_entry['ocr_confidence'] = ocr.get('average_confidence')
                image_entry['ocr_provider'] = ocr.get('provider', provider)
                if image_text:
                    texts.append(f'第{image_index + 1}张：{image_text}')
                all_lines.extend(image_lines)
                if image_entry['ocr_confidence'] is not None:
                    confidences.append(float(image_entry['ocr_confidence']))
            except Exception as exc:
                failed = True
                image_entry['status'] = 'error'
                image_entry['error'] = redact_sensitive_text(exc)[:500]
            entry['images'].append(image_entry)
            entry['image_count_processed'] = len(entry['images'])
            write_json(output_path, results + [entry])
        if failed:
            entry['status'] = 'error'
            entry['error'] = '至少一张图片下载或 OCR 失败，未把部分结果用于分类。'
        else:
            entry['status'] = 'ok'
            entry['ocr_text'] = normalize_text('\n'.join(texts))
            entry['ocr_lines'] = all_lines
            entry['ocr_confidence'] = sum(confidences) / len(confidences) if confidences else None
            if entry['images']:
                entry['ocr_provider'] = entry['images'][0].get('ocr_provider', provider)
        results.append(entry)
        write_json(output_path, results)
    write_json(output_path, results)
    return results
