#!/usr/bin/env python3
"""Generate one album index and one static WatchBrief-style page per note."""

import argparse
import html
import json
import os
import re
from pathlib import Path

from xhs_safety import redact_sensitive_text


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def safe_board_name(board_name: str) -> str:
    safe_name = ''.join(
        '_' if character in '/\\:\x00' else character
        for character in board_name.strip()
    )
    if not safe_name:
        raise ValueError('专辑名称不能为空')
    return safe_name


NOTE_ID_RE = re.compile(r'^[0-9a-f]{24}$', re.IGNORECASE)
DEPTH_LABELS = {'quick': '快速报告', 'light': '轻度报告', 'deep': '深度报告'}
STATE_LABELS = {'planned': '计划版', 'verified': '核验版'}


def report_title(board_name: str, depth: str) -> str:
    return f'小红书专辑《{board_name}》{DEPTH_LABELS[depth]}'


def default_filename(board_name: str, depth: str) -> str:
    return f'小红书专辑《{safe_board_name(board_name)}》{DEPTH_LABELS[depth]}.html'


def detail_directory_name(board_name: str, depth: str) -> str:
    return default_filename(board_name, depth)[:-5]


def safe_detail_stem(value: str) -> str:
    cleaned = re.sub(r'[\\/:\x00]', '_', str(value or '').strip())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .')
    return (cleaned or '未命名笔记')[:90]


def normalize_rows(items, classification, board_name: str, verified_ids=None):
    if not isinstance(items, list) or not isinstance(classification, list):
        raise ValueError('items 和 classification 都必须是 JSON 数组')
    classification_by_id = {}
    for row in classification:
        if not isinstance(row, dict):
            raise ValueError('classification 每一项都必须是对象')
        note_id = str(row.get('id') or '').strip()
        if not NOTE_ID_RE.fullmatch(note_id):
            raise ValueError('classification 包含无效 note id')
        if note_id in classification_by_id:
            raise ValueError(f'classification 包含重复 note id：{note_id}')
        classification_by_id[note_id] = row

    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('items 每一项都必须是对象')
        note_id = str(item.get('id') or '').strip()
        classification_row = classification_by_id.get(note_id)
        if not classification_row:
            continue
        if verified_ids is None:
            if str(classification_row.get('target_board') or '').strip() != board_name:
                continue
        elif (
            note_id not in verified_ids
            and str(classification_row.get('target_board') or '').strip() != board_name
        ):
            continue
        merged = dict(item)
        merged.update(classification_row)
        merged['id'] = note_id
        result.append(merged)
    return result


def verified_membership_ids(snapshot, board_name: str):
    if not isinstance(snapshot, dict):
        raise ValueError('成员快照必须是 JSON 对象')
    source = snapshot.get('source')
    validation = snapshot.get('validation')
    if (
        snapshot.get('mode') != 'read_only'
        or not isinstance(source, dict)
        or source.get('writes_performed') is not False
        or not isinstance(validation, dict)
    ):
        raise ValueError('核验版要求只读且未执行写入的专辑成员快照')
    boards = snapshot.get('boards')
    matches = [
        board for board in boards or []
        if isinstance(board, dict) and str(board.get('name') or '').strip() == board_name
    ]
    if len(matches) != 1:
        raise ValueError(f'成员快照必须且只能包含一个目标专辑：{board_name}')
    target = matches[0]
    note_ids = [str(note_id).strip() for note_id in target.get('note_ids') or [] if str(note_id).strip()]
    if validation.get('full_membership_complete') is not True:
        scoped_invariants_passed = all([
            validation.get('board_names_unique') is True,
            validation.get('pagination_cursor_invariants_passed') is True,
            not validation.get('duplicate_note_ids'),
            not validation.get('multi_board_note_ids'),
            not validation.get('within_board_duplicates'),
            board_name not in (validation.get('count_mismatch_boards') or []),
            target.get('declared_total') == target.get('accessible_unique_count'),
            target.get('declared_vs_accessible_delta') == 0,
            target.get('accessible_unique_count') == len(set(note_ids)),
        ])
        if not scoped_invariants_passed:
            raise ValueError(f'目标专辑成员快照不完整：{board_name}')
    return {
        str(note_id).strip()
        for note_id in note_ids
        if str(note_id).strip()
    }


def validate_verified_membership(snapshot, board_name: str, rows) -> None:
    verified_ids = verified_membership_ids(snapshot, board_name)
    report_ids = {str(row.get('id') or '').strip() for row in rows}
    missing_from_board = sorted(report_ids - verified_ids)
    missing_from_report = sorted(verified_ids - report_ids)
    if missing_from_board:
        raise ValueError(
            '以下报告条目不在核验后的目标专辑：' + ', '.join(missing_from_board[:5])
        )
    if missing_from_report:
        raise ValueError(
            '核验后的目标专辑仍有条目未进入报告：' + ', '.join(missing_from_report[:5])
        )


def normalize_synthesis(value, rows):
    if value is None:
        return {'overview': '', 'reader_value': [], 'reading_path': [], 'subtopics': []}
    if not isinstance(value, dict):
        raise ValueError('synthesis 必须是 JSON 对象')
    report_ids = {str(row.get('id') or '') for row in rows}
    subtopics = []
    for index, subtopic in enumerate(value.get('subtopics') or []):
        if not isinstance(subtopic, dict):
            raise ValueError(f'synthesis.subtopics[{index}] 必须是对象')
        supporting_ids = [str(note_id).strip() for note_id in subtopic.get('supporting_note_ids') or []]
        unsupported = sorted(set(supporting_ids) - report_ids)
        if unsupported:
            raise ValueError('专辑总结引用了报告范围之外的 note id：' + ', '.join(unsupported[:5]))
        subtopics.append({
            'name': str(subtopic.get('name') or '').strip(),
            'summary': str(subtopic.get('summary') or '').strip(),
            'supporting_note_ids': supporting_ids,
        })
    return {
        'overview': str(value.get('overview') or '').strip(),
        'reader_value': normalize_text_list(value.get('reader_value'), 'synthesis.reader_value'),
        'reading_path': normalize_text_list(value.get('reading_path'), 'synthesis.reading_path'),
        'subtopics': subtopics,
    }


def normalize_text_list(value, field_name: str, *, required=False):
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ValueError(f'{field_name} 必须是字符串数组')
    result = [str(item).strip() for item in value if str(item).strip()]
    if required and not result:
        raise ValueError(f'{field_name} 至少需要一条内容')
    return result


def normalize_details(value, rows, depth: str):
    if not isinstance(value, dict) or not isinstance(value.get('items'), dict):
        raise ValueError('details 必须包含 items 对象')
    details_by_id = value['items']
    result = {}
    for row in rows:
        note_id = str(row.get('id') or '').strip()
        detail = details_by_id.get(note_id)
        if not isinstance(detail, dict):
            raise ValueError(f'details 缺少报告条目：{note_id}')
        status = str(detail.get('status') or 'ready').strip()
        if status == 'deep_recommended':
            if depth != 'light':
                raise ValueError(f'深度报告不能包含建议深度分析的占位页：{note_id}')
            reason = str(detail.get('reason') or '').strip()
            if reason not in {'subtitle_audio_insufficient', 'video_text_unavailable'}:
                raise ValueError(f'deep_recommended 缺少受支持的 reason：{note_id}')
            result[note_id] = {
                'status': status,
                'reason': reason,
                'one_line': '轻度模式无法从字幕或音轨取得这条视频的可用讲解；建议生成深度报告。',
                'what_it_says': (
                    '轻度模式没有从字幕或音轨取得可用讲解，无法据此整理视频内容。'
                    '不能据标题补写；'
                    '请使用深度报告读取完整时轴画面与屏幕文字。'
                ),
                'direct_statements': ['轻度模式未取得可用的视频正文。'],
                'key_points': ['当前页面不推断视频主题、动作步骤或效果。'],
                'practical_takeaways': ['生成深度报告，让 MiMo 听觉与完整时轴视觉共同整理内容。'],
                'boundaries': ['不根据标题补写字幕、音轨或画面没有证实的内容。'],
                'watch_segments': [],
                'path_table': [],
                'evidence_contract': {},
            }
            continue
        if status != 'ready':
            raise ValueError(f'details.status 无效：{note_id}')
        normalized = {
            'status': status,
            'reason': '',
            'one_line': str(detail.get('one_line') or '').strip(),
            'what_it_says': str(detail.get('what_it_says') or '').strip(),
            'direct_statements': normalize_text_list(
                detail.get('direct_statements'),
                f'details.items.{note_id}.direct_statements',
                required=True,
            ),
            'key_points': normalize_text_list(
                detail.get('key_points'),
                f'details.items.{note_id}.key_points',
                required=True,
            ),
            'practical_takeaways': normalize_text_list(
                detail.get('practical_takeaways'),
                f'details.items.{note_id}.practical_takeaways',
                required=True,
            ),
            'boundaries': normalize_text_list(
                detail.get('boundaries'),
                f'details.items.{note_id}.boundaries',
                required=True,
            ),
            'watch_segments': [],
            'path_table': [],
            'evidence_contract': (
                dict(detail.get('evidence_contract'))
                if isinstance(detail.get('evidence_contract'), dict)
                else {}
            ),
        }
        if depth == 'deep':
            content_type = str(row.get('content_type') or '').strip()
            expected_basis = {
                'video': 'mimo_audio_plus_mimo_vl_full_timeline',
                'image': 'complete_image_ocr',
            }.get(content_type)
            if expected_basis is None:
                raise ValueError(
                    f'深度报告不支持缺少明确内容类型的条目：{note_id}'
                )
            actual_basis = str(
                normalized['evidence_contract'].get('basis') or ''
            ).strip()
            if actual_basis != expected_basis:
                if content_type == 'video':
                    raise ValueError(
                        '深度视频报告必须包含 '
                        'evidence_contract.basis='
                        f'mimo_audio_plus_mimo_vl_full_timeline：{note_id}'
                    )
                raise ValueError(
                    '深度图文报告必须包含 '
                    f'evidence_contract.basis=complete_image_ocr：{note_id}'
                )
        if not normalized['one_line'] or not normalized['what_it_says']:
            raise ValueError(f'details 条目缺少 one_line 或 what_it_says：{note_id}')
        for index, segment in enumerate(detail.get('watch_segments') or []):
            if not isinstance(segment, dict):
                raise ValueError(f'details.items.{note_id}.watch_segments[{index}] 必须是对象')
            normalized['watch_segments'].append({
                'start': str(segment.get('start') or '').strip(),
                'end': str(segment.get('end') or '').strip(),
                'title': str(segment.get('title') or '').strip(),
                'reason': str(segment.get('reason') or '').strip(),
            })
        for index, path_row in enumerate(detail.get('path_table') or []):
            if not isinstance(path_row, dict):
                raise ValueError(f'details.items.{note_id}.path_table[{index}] 必须是对象')
            label = str(path_row.get('label') or '').strip()
            text = str(path_row.get('text') or '').strip()
            if label and text:
                normalized['path_table'].append({'label': label, 'text': text})
        result[note_id] = normalized
    return result


def evidence_label(row, depth: str, detail=None):
    detail = detail if isinstance(detail, dict) else {}
    if detail.get('status') == 'deep_recommended':
        return '字幕 / 音频内容不足', False
    if depth == 'quick':
        return '仅元数据', False
    evidence_contract = (
        detail.get('evidence_contract')
        if isinstance(detail.get('evidence_contract'), dict)
        else {}
    )
    if (
        depth == 'deep'
        and evidence_contract.get('basis') == 'mimo_audio_plus_mimo_vl_full_timeline'
    ):
        return 'MiMo 听觉 + 完整时轴视觉', True
    if (
        depth == 'deep'
        and evidence_contract.get('basis') == 'complete_image_ocr'
    ):
        return '完整图片 OCR', True
    content_type = str(row.get('content_type') or '').strip()
    basis = str(row.get('classification_basis') or '').strip()
    if content_type == 'image':
        if (
            basis == 'metadata_and_ocr'
            and str(row.get('ocr_status') or '').strip() == 'ok'
            and row.get('ocr_image_set_complete') is True
        ):
            return '完整图片 OCR', True
        if basis == 'image_ocr_incomplete':
            return '图片 OCR 未完成', False
        return '仅元数据', False
    if content_type == 'video':
        analysis_basis = str(row.get('video_analysis_basis') or '').strip()
        visual_status = str(row.get('visual_status') or '').strip()
        analysis_status = str(row.get('video_analysis_status') or '').strip()
        if depth == 'light':
            if analysis_status == 'success':
                return '可用字幕 / 音轨文字稿', True
            if analysis_status in {'failed', 'missing'}:
                return '视频正文不可用', False
            return '仅元数据', False
        if analysis_basis == 'full_timeline_visual_with_transcript' and visual_status == 'analyzed':
            return '完整时轴画面 + 文字稿', True
        if analysis_basis == 'full_timeline_visual' and visual_status == 'analyzed':
            return '完整时轴画面', True
        if (
            analysis_basis == 'full_timeline_visual_with_transcript'
            and analysis_status == 'success'
        ):
            return '完整视频文字稿', True
        if analysis_basis == 'transcript_only' and analysis_status == 'success':
            return '视频文字稿', True
        if analysis_status in {'failed', 'missing'}:
            return '视频内容未完成', False
        return '仅元数据', False
    return '仅元数据', False


BASE_CSS = r'''
:root{color-scheme:dark;background:#0d1117;color:#f3f5f7;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;--panel:#151b23;--line:#2a3340;--muted:#9ba8b7;--blue:#6cb8ff;--green:#63d6a2;--orange:#ffbd70;--red:#ff8c86}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#17263a 0,transparent 34%),#0d1117;line-height:1.65}body.mode-light{--blue:#ffbd70;background:radial-gradient(circle at 20% 0,#3b2a17 0,transparent 34%),#0d1117}body.mode-deep{--blue:#6cb8ff}a{color:inherit}main{max-width:1120px;margin:auto;padding:38px 22px 84px}.mode-switch{display:flex;gap:10px;align-items:center;justify-content:space-between;margin-bottom:18px}.mode-switch-links{display:flex;gap:8px;flex-wrap:wrap}.mode-switch a,.mode-current{border:1px solid #354254;border-radius:999px;padding:7px 12px;text-decoration:none;font-size:13px}.mode-current{border-color:var(--blue);color:var(--blue);background:#111821}.mode-switch a{color:#c7d0da;background:#111821}.eyebrow{font-size:12px;letter-spacing:.15em;text-transform:uppercase;color:#8fb9df;margin-bottom:10px}.hero{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:18px}.panel{background:rgba(21,27,35,.95);border:1px solid var(--line);border-radius:22px;box-shadow:0 18px 50px #0004}.hero-main{padding:32px}.hero h1{font-size:clamp(31px,5vw,56px);line-height:1.12;margin:0 0 18px}.hero-summary{margin-top:22px;border-left:4px solid var(--blue);padding:14px 18px;background:#0d141e;border-radius:0 14px 14px 0}.summary-label,.section-note{color:#78a9d5;font-size:11px;letter-spacing:.13em;text-transform:uppercase}.hero-summary p{font-size:19px;margin:6px 0}.hero-side{padding:24px;display:flex;flex-direction:column;justify-content:center;background:linear-gradient(145deg,#142b38,#172027)}.mode-label{font-size:13px;color:#b6d9f6}.mode-big{font-size:34px;font-weight:850;color:#fff;margin:4px 0}.mode-copy{color:#b6c0cb;font-size:14px}.meta-row,.badges{display:flex;flex-wrap:wrap;gap:8px}.pill,.badge{display:inline-flex;align-items:center;border:1px solid #354254;border-radius:999px;padding:6px 11px;background:#111821;color:#c7d0da;font-size:13px}.pill.blue,.badge.blue{border-color:#315b80;color:#a9d5ff;background:#112334}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:18px;margin-top:18px}.col-12{grid-column:span 12}.col-6{grid-column:span 6}.section{padding:26px}.section h2{font-size:25px;margin:4px 0 17px}.section h3{margin:0 0 10px}.lead{font-size:21px;line-height:1.75;margin:0}.overview-panel{background:linear-gradient(135deg,#1b2633,#161b22)}.overview-panel .lead strong{color:#fff}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:18px}.list-clean{margin:0;padding-left:20px}.list-clean li+li{margin-top:9px}.topic-grid,.card-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.topic-card,.note-card,.target-card{background:#121820;border:1px solid #293440;border-radius:17px;padding:18px}.topic-card h3,.note-card h2{margin:0 0 8px}.topic-card p,.note-card p{margin:0;color:#c3cbd4}.support{margin-top:12px;font-size:12px;color:#84919f;overflow-wrap:anywhere}.support a{color:#8ecbff;margin-right:8px}.note-card{position:relative;padding:22px;transition:.16s ease}.note-card:hover{transform:translateY(-2px);border-color:#4b7195}.card-link{position:absolute;inset:0;border-radius:17px;z-index:2}.note-index{font-size:32px;line-height:1;font-weight:850;color:#46617b}.note-meta{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;color:#9ba8b7;font-size:12px}.note-card h2{font-size:21px;line-height:1.35}.topic{color:#82c7ff!important;font-weight:700}.open-detail{display:inline-block;margin-top:14px;color:#8ecbff;font-weight:750}.review-badge{color:#ffb1a8;background:#421d1d;padding:2px 7px;border-radius:999px}.evidence-badge{color:#92e4bd;background:#143328;padding:2px 7px;border-radius:999px}.footer-note{margin-top:24px;color:#8995a3;font-size:13px}.back-link{display:inline-flex;margin-bottom:18px;text-decoration:none;color:#9bcfff}.core-extract{border-left:4px solid var(--blue);padding:16px 18px;background:#101923;border-radius:0 14px 14px 0;font-size:20px}.path-table{margin-top:16px;border:1px solid #2c3947;border-radius:15px;overflow:hidden}.path-row{display:grid;grid-template-columns:90px 1fr}.path-row+.path-row{border-top:1px solid #2c3947}.path-label{padding:13px;background:#101720;color:#8ecbff;font-weight:750}.path-copy{padding:13px}.target-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.target-card:nth-child(1){border-top:3px solid var(--green)}.target-card:nth-child(2){border-top:3px solid #aa90ff}.target-card:nth-child(3){border-top:3px solid var(--blue)}.target-card:nth-child(4){border-top:3px solid var(--red)}.target-card ul{margin:0;padding-left:20px}.target-card li+li{margin-top:8px}.watch-list{display:grid;gap:12px}.watch-item{display:grid;grid-template-columns:100px 1fr;gap:16px;border:1px solid #2d3946;background:#111820;border-radius:15px;padding:16px}.watch-time{font-weight:850;color:#82c7ff;text-align:center;align-self:center}.watch-item h3,.watch-item p{margin:0}.evidence-note{margin-top:14px;color:#aeb9c5;background:#10161e;border:1px solid #283543;border-radius:13px;padding:13px}.original-link{display:inline-flex;margin-top:14px;color:#9bd3ff;text-decoration:none;font-weight:750}.original-link:hover,.back-link:hover,.mode-switch a:hover{text-decoration:underline}@media(max-width:760px){.hero{grid-template-columns:1fr}.two-col,.topic-grid,.card-grid,.target-grid{grid-template-columns:1fr}.col-6{grid-column:span 12}.path-row{grid-template-columns:72px 1fr}.watch-item{grid-template-columns:1fr}.watch-time{text-align:left}.hero-main,.section{padding:21px}.mode-switch{align-items:flex-start;flex-direction:column}}
'''


def esc(value) -> str:
    return html.escape(redact_sensitive_text(str(value or '')))


def render_list(items) -> str:
    return '<ul class="list-clean">' + ''.join(f'<li>{esc(item)}</li>' for item in items) + '</ul>'


def detail_filename(index: int, row) -> str:
    return f'{index:02d}-{safe_detail_stem(row.get("title") or "未命名笔记")}.html'


def unresolved_row(row) -> bool:
    review_state = str(row.get('review_state') or '').strip()
    return (
        str(row.get('confidence') or '').strip() == 'low'
        or str(row.get('video_analysis_status') or '').strip() in {'failed', 'missing'}
        or review_state in {
            'video_content_unavailable',
            'video_content_needs_review',
            'image_ocr_incomplete',
        }
    )


def render_index(board_name: str, depth: str, state: str, rows, synthesis, details) -> str:
    title = report_title(board_name, depth)
    detail_dir = detail_directory_name(board_name, depth)
    unresolved_count = sum(
        int(
            unresolved_row(row)
            or details[str(row.get('id') or '')].get('status') == 'deep_recommended'
        )
        for row in rows
    )
    if depth == 'deep':
        coverage_badge = f'正文提炼 {len(rows) - unresolved_count} / {len(rows)}'
    elif depth == 'light':
        coverage_badge = f'轻度正文 {len(rows) - unresolved_count} / {len(rows)}'
    else:
        coverage_badge = f'标题级页面 {len(rows)} / {len(rows)}'
    unresolved_badge_html = (
        f'<span class="badge">内容待核实 {unresolved_count}</span>'
        if unresolved_count else ''
    )
    overview = synthesis.get('overview') or '本报告尚未填写专辑级结论。'
    reader_value = synthesis.get('reader_value') or ['逐条进入单篇报告，查看现有证据支持的内容结论。']
    reading_path = synthesis.get('reading_path') or ['先看专辑概览，再按主题或收藏顺序打开单条报告。']

    row_positions = {str(row.get('id') or ''): index for index, row in enumerate(rows, 1)}
    topic_cards = []
    for subtopic in synthesis.get('subtopics') or []:
        supporting_ids = subtopic.get('supporting_note_ids') or []
        supporting_links = []
        for note_id in supporting_ids:
            position = row_positions.get(note_id)
            if not position:
                continue
            row = rows[position - 1]
            href = f'{detail_dir}/{detail_filename(position, row)}'
            supporting_links.append(
                f'<a href="{esc(href)}" data-note-id="{esc(note_id)}">#{position:02d}</a>'
            )
        topic_cards.append(
            '<article class="topic-card">'
            f'<h3>{esc(subtopic.get("name") or "未命名子主题")}</h3>'
            f'<p>{esc(subtopic.get("summary"))}</p>'
            f'<div class="support">关联单条：{" ".join(supporting_links)}</div>'
            '</article>'
        )

    cards = []
    for index, row in enumerate(rows, 1):
        note_id = str(row.get('id') or '')
        detail = details[note_id]
        evidence, _ = evidence_label(row, depth, detail)
        needs_deep = detail.get('status') == 'deep_recommended'
        review_badge = '<span class="review-badge">建议深度报告</span>' if needs_deep else ('<span class="review-badge">内容待核实</span>' if unresolved_row(row) else '')
        if needs_deep:
            topic = '轻度证据不足'
            card_summary = '轻度模式无法从字幕或音轨取得可用讲解；建议生成深度报告。'
        else:
            topic = row.get('main_topic') or ('标题级主题判断' if depth == 'light' else '')
            card_summary = detail['one_line']
        href = f'{detail_dir}/{detail_filename(index, row)}'
        open_label = (
            '查看深度报告建议 →'
            if needs_deep
            else {
                'quick': '打开快速页 →',
                'light': '打开轻度页 →',
                'deep': '打开深度报告 →',
            }[depth]
        )
        cards.append(
            '<article class="note-card">'
            f'<a class="card-link" aria-label="打开单条报告：{esc(row.get("title"))}" href="{esc(href)}"></a>'
            f'<div class="note-index">{index:02d}</div>'
            f'<div class="note-meta"><span>{esc(row.get("user") or "作者未知")}</span><span>{esc(row.get("content_type") or "unknown")}</span><span class="evidence-badge">{esc(evidence)}</span>{review_badge}</div>'
            f'<h2>{esc(row.get("title") or "未命名笔记")}</h2>'
            f'<p class="topic">{esc(topic)}</p>'
            f'<p>{esc(card_summary)}</p>'
            f'<span class="open-detail">{open_label}</span>'
            '</article>'
        )

    mode_copy = {
        'quick': '只使用标题、作者和专辑归属，不读取图文正文、音轨或画面。',
        'light': '使用标题、正文、完整图文 OCR，以及可用的平台字幕或音轨；不读取视频完整时轴画面。',
        'deep': '视频同时读取 MiMo 听觉、MiMo-VL 完整时轴画面与屏幕文字；图文读取全部图片。',
    }[depth]

    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)}</title><style>{BASE_CSS}</style></head><body class="mode-{esc(depth)}"><main>'
        '<div class="eyebrow">XIAOHONGSHU ALBUM · INDEX + INDIVIDUAL REPORTS</div>'
        '<section class="hero">'
        f'<div class="panel hero-main"><h1>{esc(title)}</h1><div class="badges"><span class="badge blue">{esc(DEPTH_LABELS[depth])}</span><span class="badge">{esc(STATE_LABELS[state])}</span><span class="badge">{len(rows)} 条笔记</span><span class="badge">{esc(coverage_badge)}</span>{unresolved_badge_html}</div>'
        f'<div class="hero-summary"><div class="summary-label">album thesis · 专辑结论</div><p>{esc(overview)}</p></div></div>'
        f'<aside class="panel hero-side"><div class="mode-label">当前生成模式</div><div class="mode-big">{esc(DEPTH_LABELS[depth])}</div><div class="mode-copy">{esc(mode_copy)}</div></aside>'
        '</section>'
        '<section class="grid"><section class="panel section col-12 overview-panel"><div class="section-note">what this album gives you</div><h2>你能从这个专辑得到什么</h2>'
        f'<div class="two-col"><div>{render_list(reader_value)}</div><div><h3>建议阅读顺序</h3>{render_list(reading_path)}</div></div></section>'
        + (
            '<section class="panel section col-12"><div class="section-note">topic map</div><h2>专辑主题地图</h2><div class="topic-grid">'
            + ''.join(topic_cards) + '</div></section>' if topic_cards else ''
        )
        + '<section class="panel section col-12"><div class="section-note">click any card to drill down</div><h2>逐条报告</h2><div class="card-grid">'
        + ''.join(cards)
        + '</div><p class="footer-note">每张卡片都链接到一个独立静态 HTML；原始小红书链接放在单条报告内。</p></section></section>'
        '</main></body></html>\n'
    )


def detail_evidence_note(row, depth: str, detail) -> str:
    if detail.get('status') == 'deep_recommended':
        return '字幕或音轨没有提供可用讲解；轻度模式到此停止。建议使用深度报告读取完整时轴画面、屏幕文字与动作。'
    evidence_contract = detail.get('evidence_contract') if isinstance(detail.get('evidence_contract'), dict) else {}
    content_type = str(row.get('content_type') or '').strip()
    if depth == 'quick':
        return '本页只整理标题、作者和专辑归属，不读取图文正文、音轨或视频画面。'
    if depth == 'light':
        if content_type == 'image' and (
            str(row.get('classification_basis') or '').strip() == 'metadata_and_ocr'
            and str(row.get('ocr_status') or '').strip() == 'ok'
            and row.get('ocr_image_set_complete') is True
        ):
            return '本页依据完整图片 OCR 生成；结论只覆盖图片文字明确表达的内容。'
        if (
            content_type == 'video'
            and str(row.get('video_analysis_status') or '').strip() == 'success'
        ):
            return '本页依据可用的平台字幕或音轨文字稿生成；轻度模式不读取视频完整时轴画面，动作和画面细节未做视觉核验。'
        return '本页依据标题、作者和已核验的专辑归属生成；未取得正文的部分不据标题补写。'
    if evidence_contract.get('basis') == 'mimo_audio_plus_mimo_vl_full_timeline':
        return '本页依据 MiMo 听觉文字稿、MiMo-VL 完整时轴画面和逐帧屏幕文字生成；听觉与视觉事实分层保存。'
    if content_type == 'image':
        return '本页依据完整图片 OCR 生成；结论只覆盖图片文字明确表达的内容。'
    if str(row.get('video_analysis_status') or '') == 'success':
        if str(row.get('visual_status') or '') == 'failed':
            return '文字稿已通过质量门；视觉结构化结果未纳入结论。页面内容来自完整文字稿，不把视觉失败误写成内容失败。'
        return '本页依据通过质量门的视频文字稿生成；如有视觉证据，仅用于辅助核对。'
    return '当前正文证据不足；页面只保留能够核验的内容，并明确标出边界。'


def render_detail(board_name: str, depth: str, state: str, row, detail, index: int) -> str:
    note_id = str(row.get('id') or '')
    title = row.get('title') or '未命名笔记'
    note_url = f'https://www.xiaohongshu.com/explore/{note_id}'
    evidence, _ = evidence_label(row, depth, detail)
    path_html = ''
    if detail.get('path_table'):
        rows_html = ''.join(
            f'<div class="path-row"><div class="path-label">{esc(item["label"])}</div><div class="path-copy">{esc(item["text"])}</div></div>'
            for item in detail['path_table']
        )
        path_html = f'<div class="path-table">{rows_html}</div>'
    if depth == 'light':
        section_specs = [
            ('已确认信息', 'direct_statements'),
            ('内容线索', 'key_points'),
            ('查看重点', 'practical_takeaways'),
            ('证据边界', 'boundaries'),
        ]
    else:
        section_specs = [
            ('直接结论', 'direct_statements'),
            ('具体观点', 'key_points'),
            ('可直接采用', 'practical_takeaways'),
            ('注意边界', 'boundaries'),
        ]
    target_cards = ''.join(
        f'<article class="target-card"><h3>{label}</h3>{render_list(detail[key])}</article>'
        for label, key in section_specs
    )
    if depth == 'light':
        content_sections = (
            '<section class="grid">'
            '<section class="panel section col-12"><div class="section-note">lightweight evidence summary</div><h2>轻度证据能确认什么</h2>'
            f'<div class="core-extract">{esc(detail["what_it_says"])}</div>{path_html}</section>'
            f'<section class="panel section col-12"><div class="section-note">compact evidence cards</div><h2>轻度内容卡</h2><div class="target-grid">{target_cards}</div></section>'
            '</section>'
        )
        eyebrow = 'XIAOHONGSHU NOTE · LIGHT REPORT'
    else:
        segments = detail.get('watch_segments') or []
        if segments:
            watch_items = ''.join(
                '<article class="watch-item">'
                f'<div class="watch-time">{esc(segment.get("start"))}<br>｜<br>{esc(segment.get("end"))}</div>'
                f'<div><h3>{esc(segment.get("title") or "推荐片段")}</h3><p>{esc(segment.get("reason"))}</p></div>'
                '</article>'
                for segment in segments
            )
            watch_section = f'<section class="panel section col-12"><div class="section-note">worth watching segment</div><h2>值得补看</h2><div class="watch-list">{watch_items}</div></section>'
        else:
            watch_section = (
                '<section class="panel section col-12"><div class="section-note">what to verify in the source</div><h2>进入原笔记时重点确认</h2>'
                f'{render_list(detail["practical_takeaways"])}</section>'
            )
        content_sections = (
            '<section class="grid"><section class="panel section col-12"><div class="section-note">what this note is actually saying</div><h2>这条到底讲什么？</h2>'
            f'<div class="core-extract">{esc(detail["what_it_says"])}</div>{path_html}</section>'
            f'<section class="panel section col-12"><div class="section-note">highest compression</div><h2>核心内容卡</h2><div class="target-grid">{target_cards}</div></section>'
            f'{watch_section}</section>'
        )
        eyebrow = 'XIAOHONGSHU NOTE · WATCHBRIEF-STYLE REPORT'
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)} · {esc(report_title(board_name, depth))}</title><style>{BASE_CSS}</style></head><body class="mode-{esc(depth)}"><main>'
        f'<a class="back-link" href="../{esc(default_filename(board_name, depth))}">← 回到专辑报告（{esc(DEPTH_LABELS[depth])}）</a>'
        f'<div class="eyebrow">{esc(eyebrow)}</div>'
        '<section class="hero">'
        f'<div class="panel hero-main"><h1>{esc(title)}</h1><div class="meta-row"><span class="pill">作者：{esc(row.get("user") or "未知")}</span><span class="pill">类型：{esc(row.get("content_type") or "unknown")}</span><span class="pill blue">证据：{esc(evidence)}</span><span class="pill">{esc(DEPTH_LABELS[depth])}</span><span class="pill">{esc(STATE_LABELS[state])}</span></div>'
        f'<div class="hero-summary"><div class="summary-label">one-line brief</div><p><strong>一句话总结：</strong>{esc(detail["one_line"])}</p></div>'
        f'<div class="evidence-note">{esc(detail_evidence_note(row, depth, detail))}</div><a class="original-link" href="{note_url}" target="_blank" rel="noreferrer">打开小红书原笔记 ↗</a></div>'
        f'<aside class="panel hero-side"><div class="mode-label">专辑内序号</div><div class="mode-big">{index:02d}</div><div class="mode-copy">{esc(board_name)}</div></aside></section>'
        f'{content_sections}</main></body></html>\n'
    )


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description='生成小红书专辑总览及逐条静态 HTML 报告。')
    parser.add_argument('--items', required=True, help='visible_items.json 或等价条目 JSON')
    parser.add_argument('--classification', required=True, help='classification.json')
    parser.add_argument('--board-name', required=True, help='目标专辑名称')
    parser.add_argument('--depth', required=True, choices=('quick', 'light', 'deep'))
    parser.add_argument('--state', required=True, choices=('planned', 'verified'))
    parser.add_argument('--membership-snapshot', help='核验版使用的只读全专辑成员快照')
    parser.add_argument('--synthesis', help='可选的专辑概览和带 note id 证据的子主题 JSON')
    parser.add_argument('--details', help='逐条 WatchBrief 内容合同 JSON；正式报告必填')
    parser.add_argument('--output-dir', required=True, help='报告输出目录')
    args = parser.parse_args()

    if args.state == 'verified' and not args.membership_snapshot:
        parser.error('核验版必须提供 --membership-snapshot')

    items = load_json(Path(args.items))
    classification = load_json(Path(args.classification))
    membership_snapshot = None
    verified_ids = None
    if args.state == 'verified':
        membership_snapshot = load_json(Path(args.membership_snapshot))
        verified_ids = verified_membership_ids(membership_snapshot, args.board_name)
    rows = normalize_rows(
        items,
        classification,
        args.board_name,
        verified_ids=verified_ids,
    )
    if not rows:
        parser.error('没有可生成报告的专辑条目')
    if args.state == 'verified':
        validate_verified_membership(
            membership_snapshot,
            args.board_name,
            rows,
        )
    if not args.details:
        parser.error('生成逐条报告必须提供 --details')
    synthesis = normalize_synthesis(
        load_json(Path(args.synthesis)) if args.synthesis else None,
        rows,
    )
    details = normalize_details(load_json(Path(args.details)), rows, args.depth)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / default_filename(args.board_name, args.depth)
    detail_dir = output_dir / detail_directory_name(args.board_name, args.depth)
    detail_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(
        output,
        render_index(args.board_name, args.depth, args.state, rows, synthesis, details),
    )
    detail_outputs = []
    for index, row in enumerate(rows, 1):
        detail_output = detail_dir / detail_filename(index, row)
        atomic_write(
            detail_output,
            render_detail(
                args.board_name,
                args.depth,
                args.state,
                row,
                details[str(row.get('id') or '')],
                index,
            ),
        )
        detail_outputs.append(str(detail_output))
    print(json.dumps({
        'output': str(output),
        'detail_dir': str(detail_dir),
        'detail_count': len(detail_outputs),
        'details': detail_outputs,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
