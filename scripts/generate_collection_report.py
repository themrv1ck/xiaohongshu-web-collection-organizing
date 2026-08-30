#!/usr/bin/env python3
"""Generate one verified static HTML report for all Xiaohongshu albums."""

import argparse
import html
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from analyze_image_ocr import (
    image_source_sha256,
    validate_batch_payload,
    validated_image_sources,
)
from archive_rules import UNCERTAIN_BOARD_NAME
from xhs_safety import redact_sensitive_text


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def clean_text(value: Any) -> str:
    return str(redact_sensitive_text(value) or '').strip()


def validated_rows(snapshot: Any, classification: Any) -> List[Dict[str, Any]]:
    if not isinstance(snapshot, dict) or snapshot.get('mode') != 'read_only':
        raise ValueError('专辑报告只接受只读核验快照。')
    source = snapshot.get('source')
    validation = snapshot.get('validation')
    boards = snapshot.get('boards')
    if (
        not isinstance(source, dict)
        or source.get('writes_performed') is not False
        or not isinstance(validation, dict)
        or validation.get('full_membership_complete') is not True
        or validation.get('board_names_unique') is not True
        or validation.get('pagination_cursor_invariants_passed') is not True
        or validation.get('duplicate_note_ids') not in (None, [])
        or validation.get('multi_board_note_ids') not in (None, [])
        or validation.get('within_board_duplicates') not in (None, [])
        or validation.get('count_mismatch_boards') not in (None, [])
        or not isinstance(boards, list)
    ):
        raise ValueError('专辑成员快照不完整、有重复或数量不一致，已停止生成报告。')
    if not isinstance(classification, list):
        raise ValueError('classification 必须是数组。')

    membership: Dict[str, str] = {}
    board_names = set()
    for index, board in enumerate(boards):
        if not isinstance(board, dict):
            raise ValueError(f'boards[{index}] 必须是对象。')
        name = str(board.get('name') or '').strip()
        note_ids = board.get('note_ids')
        if not name or name in board_names or not isinstance(note_ids, list):
            raise ValueError(f'boards[{index}] 的名称或成员无效。')
        board_names.add(name)
        declared = board.get('declared_total')
        accessible = board.get('accessible_unique_count')
        if (
            declared != len(note_ids)
            or accessible != len(note_ids)
            or len(note_ids) != len(set(note_ids))
        ):
            raise ValueError(f'专辑“{name}”的声明数量与完整成员不一致。')
        for note_id in note_ids:
            note_id = str(note_id or '').strip()
            if not note_id or note_id in membership:
                raise ValueError(f'专辑成员为空或跨专辑重复：{note_id or "<empty>"}')
            membership[note_id] = name

    rows_by_id: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(classification):
        if not isinstance(row, dict):
            raise ValueError(f'classification[{index}] 必须是对象。')
        note_id = str(row.get('id') or '').strip()
        if not note_id or note_id in rows_by_id:
            raise ValueError(f'classification 包含空 ID 或重复 ID：{note_id or "<empty>"}')
        rows_by_id[note_id] = row
    if set(rows_by_id) != set(membership):
        missing = sorted(set(membership) - set(rows_by_id))
        extra = sorted(set(rows_by_id) - set(membership))
        raise ValueError(
            '报告分类与最终专辑成员不完全一致：'
            f'缺少 {len(missing)} 条，多出 {len(extra)} 条。'
        )

    result = []
    for note_id, board_name in membership.items():
        row = dict(rows_by_id[note_id])
        target = str(row.get('target_board') or '').strip()
        if target != board_name:
            raise ValueError(
                f'笔记 {note_id} 的分类目标“{target}”与最终专辑“{board_name}”不一致。'
            )
        row['_board_name'] = board_name
        result.append(row)
    return result


def top_values(rows: List[Dict[str, Any]], key: str, limit: int = 8) -> List[tuple[str, int]]:
    values = [
        clean_text(row.get('_report_main_topic') or row.get(key))
        if key == 'main_topic'
        else clean_text(row.get(key))
        for row in rows
    ]
    counter = Counter(value for value in values if value)
    return counter.most_common(limit)


def esc(value: Any) -> str:
    return html.escape(clean_text(value), quote=True)


def apply_image_analysis(
    rows: List[Dict[str, Any]],
    image_analysis: Any,
) -> List[Dict[str, Any]]:
    sources = validated_image_sources(rows)
    if sources and image_analysis is None:
        raise ValueError('完整图文 OCR 必须先生成图文摘要，不能把原始 OCR 直接写进报告。')
    if image_analysis is None:
        return rows
    if not isinstance(image_analysis, list):
        raise ValueError('image_analysis 必须是数组。')

    expected_ids = [row['id'] for row in sources]
    analysis_by_id: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(image_analysis):
        if not isinstance(row, dict):
            raise ValueError(f'image_analysis[{index}] 必须是对象。')
        note_id = str(row.get('id') or '').strip()
        if not note_id or note_id in analysis_by_id:
            raise ValueError(f'image_analysis 包含空 ID 或重复 ID：{note_id or "<empty>"}')
        analysis_by_id[note_id] = row
    if set(analysis_by_id) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(analysis_by_id))
        extra = sorted(set(analysis_by_id) - set(expected_ids))
        raise ValueError(
            f'图文摘要覆盖不完整：缺少 {len(missing)} 条，多出 {len(extra)} 条。'
        )

    source_by_id = {row['id']: row for row in sources}
    for note_id in expected_ids:
        source = source_by_id[note_id]
        analysis = analysis_by_id[note_id]
        if analysis.get('status') != 'success':
            raise ValueError(f'图文摘要 {note_id} 未成功。')
        if analysis.get('source_sha256') != image_source_sha256(source):
            raise ValueError(f'图文摘要 {note_id} 的来源哈希与当前 OCR 不一致。')
        normalized = validate_batch_payload({'items': [{
            'id': analysis.get('id'),
            'main_topic': analysis.get('main_topic'),
            'content_summary': analysis.get('content_summary'),
        }]}, [note_id])[0]
        source['_report_main_topic'] = normalized['main_topic']
        source['_report_content_summary'] = normalized['content_summary']

    analysis_source_by_id = {
        row['id']: row for row in sources
    }
    result = []
    for row in rows:
        source = analysis_source_by_id.get(str(row.get('id') or '').strip())
        result.append(source if source is not None else row)
    return result


def note_content(row: Dict[str, Any]) -> tuple[str, str, str]:
    """Return verified content source, label and text without re-analysis."""
    image_summary = clean_text(row.get('_report_content_summary'))
    if image_summary:
        return 'image_summary', '图文内容概括', image_summary
    summary = clean_text(row.get('content_summary'))
    if summary:
        return 'summary', '已保存内容摘要', summary
    return (
        'missing',
        '未保存可复用正文',
        '之前的整理记录未保存可复用的正文或内容摘要；本报告没有重新读取或猜测。',
    )


def content_html(source: str, label: str, content: str) -> str:
    return f'<p class="source-label">{esc(label)}</p><p class="content-copy">{esc(content)}</p>'


def render(snapshot: Dict[str, Any], rows: List[Dict[str, Any]], generated_at: str) -> str:
    rows_by_board: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        rows_by_board.setdefault(row['_board_name'], []).append(row)
    boards = snapshot['boards']
    navigation = ''.join(
        f'<a href="#board-{index}">{esc(board["name"])}<span>{len(rows_by_board.get(board["name"], []))}</span></a>'
        for index, board in enumerate(boards, 1)
    )
    sections = []
    for index, board in enumerate(boards, 1):
        name = str(board['name'])
        board_rows = rows_by_board.get(name, [])
        topics = top_values(board_rows, 'main_topic')
        types = Counter(clean_text(row.get('content_type')) or '未记录' for row in board_rows)
        content_sources = Counter(note_content(row)[0] for row in board_rows)
        topic_html = ''.join(
            f'<span class="tag">{esc(topic)}{f" · {count}" if count > 1 else ""}</span>'
            for topic, count in topics
        ) or '<span class="muted">没有保存可复用的主题标签</span>'
        type_labels = {'video': '视频', 'image': '图文', 'unknown': '未识别', '未记录': '未记录'}
        type_html = ' / '.join(
            f'{esc(type_labels.get(kind, kind))} {count}' for kind, count in types.items()
        )
        source_labels = {
            'summary': '视频内容摘要',
            'image_summary': '图文内容概括',
            'missing': '未保存正文',
        }
        source_html = ' / '.join(
            f'{source_labels[source]} {content_sources[source]}'
            for source in ('summary', 'image_summary', 'missing')
            if content_sources[source]
        )
        if name == UNCERTAIN_BOARD_NAME:
            purpose = '暂存无法可靠判断主题的笔记，等待你自行调整到合适专辑。'
        elif topics:
            purpose = '已保存的主要主题包括：' + '、'.join(topic for topic, _ in topics[:5]) + '。'
        else:
            purpose = f'以“{name}”为归档主题；现有分析没有保存更细的主题标签。'
        notes = []
        for note_index, row in enumerate(board_rows, 1):
            title = clean_text(row.get('title')) or f'未命名笔记 {note_index}'
            topic = clean_text(row.get('_report_main_topic') or row.get('main_topic'))
            source, label, content = note_content(row)
            notes.append(
                f'<article class="note" data-content-source="{source}">'
                f'<div class="num">{note_index:02d}</div><div><h3>{esc(title)}</h3>'
                f'<p class="topic">{esc(topic) if topic else "未保存细分主题"}</p>'
                f'{content_html(source, label, content)}</div></article>'
            )
        sections.append(
            f'<section class="album" id="board-{index}"><header><div><p class="eyebrow">ALBUM {index:02d}</p>'
            f'<h2>{esc(name)}</h2><p class="purpose">{esc(purpose)}</p></div>'
            f'<div class="count">{len(board_rows)}<small>条笔记</small></div></header>'
            f'<div class="meta"><div><b>内容类型</b><p>{type_html}</p><b class="meta-subtitle">可读内容</b><p>{source_html}</p></div><div><b>已保存主题</b><div class="tags">{topic_html}</div></div></div>'
            f'<details><summary>查看这个专辑的全部 {len(board_rows)} 条笔记内容</summary><div class="notes">{"".join(notes)}</div></details></section>'
        )
    source_time = clean_text(snapshot.get('generated_at')) or '未记录'
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>我的小红书专辑整理报告</title>
<script>(function(){{try{{var theme=localStorage.getItem('xhs-collection-report-theme');if(theme==='light'||theme==='dark'){{document.documentElement.dataset.theme=theme;}}}}catch(error){{}}}})();</script>
<style>
:root{{--bg:#f5f2ec;--paper:#fffdf8;--ink:#1d211f;--muted:#6f756f;--line:#dcd7cd;--accent:#c74d3b;--green:#395d4a;--soft:#f3efe7;--chip:#fff;--hero:#1d211f;--hero-ink:#fff;color-scheme:light}}
:root[data-theme="dark"]{{--bg:#111412;--paper:#1a1f1c;--ink:#edf1ed;--muted:#a8b0aa;--line:#353d37;--accent:#ff8b72;--green:#8fc7a6;--soft:#232a25;--chip:#202621;--hero:#080a09;--hero-ink:#f7faf7;color-scheme:dark}}
@media (prefers-color-scheme: dark){{:root:not([data-theme]){{--bg:#111412;--paper:#1a1f1c;--ink:#edf1ed;--muted:#a8b0aa;--line:#353d37;--accent:#ff8b72;--green:#8fc7a6;--soft:#232a25;--chip:#202621;--hero:#080a09;--hero-ink:#f7faf7;color-scheme:dark}}}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.7}}main{{width:min(1120px,calc(100% - 32px));margin:0 auto;padding:48px 0 96px}}.hero{{background:var(--hero);color:var(--hero-ink);border-radius:28px;padding:42px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px}}.hero h1{{font-size:clamp(34px,6vw,68px);line-height:1.08;margin:8px 0 18px}}.hero p{{max-width:720px;color:#d8ddd9}}.hero-side{{display:flex;flex-direction:column;align-items:flex-end;gap:18px}}.theme-toggle{{appearance:none;background:transparent;color:var(--hero-ink);border:1px solid #ffffff4a;border-radius:999px;padding:9px 14px;font:inherit;cursor:pointer}}.theme-toggle:hover{{background:#ffffff18}}.stats{{display:flex;gap:14px;align-items:flex-end}}.stat{{min-width:116px;padding:18px;border:1px solid #ffffff2e;border-radius:18px}}.stat b{{display:block;font-size:34px}}.stat small,.eyebrow{{font-size:12px;letter-spacing:.12em;color:#aeb8b1}}.scope{{margin:18px 0 26px;padding:16px 20px;border:1px solid var(--line);border-radius:16px;background:var(--paper);color:var(--muted)}}nav{{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:28px}}nav a{{text-decoration:none;color:var(--ink);background:var(--paper);border:1px solid var(--line);border-radius:999px;padding:8px 12px;overflow-wrap:anywhere}}nav span{{margin-left:8px;color:var(--accent)}}.album{{min-width:0;background:var(--paper);border:1px solid var(--line);border-radius:24px;padding:28px;margin:18px 0}}.album header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;min-width:0}}.album header>div:first-child{{min-width:0}}h2{{font-size:34px;margin:0;overflow-wrap:anywhere}}.purpose{{color:var(--muted);max-width:720px;overflow-wrap:anywhere}}.count{{font-size:42px;font-weight:800;color:var(--accent);text-align:right}}.count small{{display:block;color:var(--muted);font-size:12px;font-weight:500}}.meta{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:16px;margin:20px 0}}.meta>div{{min-width:0;background:var(--soft);border-radius:15px;padding:15px}}.meta p{{margin:6px 0 0;overflow-wrap:anywhere}}.meta-subtitle{{display:block;margin-top:14px}}.tags{{display:flex;gap:7px;flex-wrap:wrap;margin-top:8px;min-width:0}}.tag{{max-width:100%;background:var(--chip);border:1px solid var(--line);border-radius:999px;padding:4px 9px;font-size:13px;overflow-wrap:anywhere}}.album>details{{border-top:1px solid var(--line);padding-top:18px}}summary{{cursor:pointer;font-weight:700;color:var(--green);overflow-wrap:anywhere}}.notes{{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;margin-top:16px;min-width:0}}.note{{display:grid;grid-template-columns:42px minmax(0,1fr);gap:10px;min-width:0;border:1px solid var(--line);border-radius:15px;padding:14px}}.note>div{{min-width:0}}.note h3{{font-size:16px;line-height:1.45;margin:0;overflow-wrap:anywhere}}.note p{{font-size:14px;margin:6px 0;color:var(--muted);overflow-wrap:anywhere}}.note .topic{{color:var(--green);font-weight:700}}.source-label{{font-size:12px!important;color:var(--accent)!important;font-weight:700}}.content-copy{{white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere}}.num{{color:var(--accent);font-weight:800}}.muted{{color:var(--muted)}}footer{{color:var(--muted);font-size:13px;margin-top:28px}}@media(max-width:760px){{.hero{{grid-template-columns:minmax(0,1fr);padding:28px}}.hero-side{{align-items:stretch}}.stats{{align-items:stretch}}.stat{{min-width:0;flex:1}}.meta{{grid-template-columns:minmax(0,1fr)}}.album{{padding:20px}}.album header{{display:block}}.count{{text-align:left}}}}@media print{{.theme-toggle{{display:none}}}}
</style></head><body><main><section class="hero"><div><p class="eyebrow">XIAOHONGSHU ALBUM ARCHIVE</p><h1>我的小红书专辑<br>整理报告</h1><p>这份报告没有重新读取收藏，也没有移动任何笔记。图文内容概括来自对已保存完整 OCR 的离线分析，原始 OCR 不作为正文展示。</p></div><div class="hero-side"><button class="theme-toggle" id="theme-toggle" type="button" aria-pressed="false">切换深色模式</button><div class="stats"><div class="stat"><b>{len(boards)}</b><small>个专辑</small></div><div class="stat"><b>{len(rows)}</b><small>条笔记</small></div></div></div></section>
<div class="scope">最终专辑快照：{esc(source_time)}　｜　报告生成：{esc(generated_at)}　｜　成员关系：完整核验</div><nav>{navigation}</nav>{''.join(sections)}
<footer>报告依据：完整专辑成员快照 + 同批分类记录 + 与完整 OCR 来源哈希一致的图文摘要。原始 OCR 只作为离线分析证据，不直接写入报告正文。</footer></main>
<script>(function(){{var root=document.documentElement;var button=document.getElementById('theme-toggle');var media=window.matchMedia('(prefers-color-scheme: dark)');function isDark(){{return root.dataset.theme?root.dataset.theme==='dark':media.matches;}}function update(){{var dark=isDark();button.textContent=dark?'切换浅色模式':'切换深色模式';button.setAttribute('aria-pressed',String(dark));}}button.addEventListener('click',function(){{var next=isDark()?'light':'dark';root.dataset.theme=next;try{{localStorage.setItem('xhs-collection-report-theme',next);}}catch(error){{}}update();}});if(media.addEventListener){{media.addEventListener('change',function(){{if(!root.dataset.theme){{update();}}}});}}update();}})();</script></body></html>'''


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description='从完整核验结果生成小红书专辑 HTML 报告。')
    parser.add_argument('--board-snapshot', required=True)
    parser.add_argument('--classification', required=True)
    parser.add_argument('--image-analysis')
    parser.add_argument('--output', default=str(Path.home() / 'Desktop' / '我的小红书专辑整理报告.html'))
    args = parser.parse_args()
    snapshot = load_json(Path(args.board_snapshot))
    classification = load_json(Path(args.classification))
    rows = validated_rows(snapshot, classification)
    image_analysis = load_json(Path(args.image_analysis)) if args.image_analysis else None
    rows = apply_image_analysis(rows, image_analysis)
    generated_at = datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()
    output = Path(args.output).expanduser().resolve()
    atomic_write(output, render(snapshot, rows, generated_at))
    print(json.dumps({
        'ok': True,
        'output': str(output),
        'board_count': len(snapshot['boards']),
        'note_count': len(rows),
        'image_summary_count': sum(
            bool(clean_text(row.get('_report_content_summary'))) for row in rows
        ),
        'writes_to_xiaohongshu': False,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
