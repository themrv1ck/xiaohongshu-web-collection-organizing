#!/usr/bin/env python3
"""Visible Xiaohongshu album UI primitives.

This module deliberately uses only user-facing DOM controls and card links.
It never scans the webpack/Rspack runtime and never calls a private endpoint.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlparse

from extract_visible_items import arc_js_macos, jxa_osascript, require_macos_app_running


NOTE_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
SECURITY_MARKERS = (
    "安全验证",
    "异常访问",
    "访问异常",
    "当前请求异常",
    "300031",
    "website-login/error",
    "访问过于频繁",
    "操作过于频繁",
    "请求过于频繁",
    "网络环境存在风险",
    "当前环境存在风险",
    "拖动滑块",
    "captcha",
)


class VisibleUiContractError(RuntimeError):
    """Raised when the visible page cannot prove the requested state."""


def parse_arc_json(raw: Any) -> Any:
    value = raw
    for _ in range(2):
        if not isinstance(value, str):
            break
        value = json.loads(value)
    return value


def _require_note_id(value: Any, label: str) -> str:
    note_id = str(value or "").strip()
    if not NOTE_ID_RE.fullmatch(note_id):
        raise VisibleUiContractError(f"{label} 不是 24 位十六进制 id")
    return note_id.lower()


def validate_album_scroll_snapshots(
    declared_count: int,
    snapshots: Sequence[Sequence[Mapping[str, Any]]],
) -> List[Dict[str, Any]]:
    """Validate an accumulating visible-album sequence without guessing gaps."""
    if not isinstance(declared_count, int) or isinstance(declared_count, bool) or declared_count < 0:
        raise VisibleUiContractError("页面没有提供有效的专辑总数")
    if not snapshots:
        raise VisibleUiContractError("没有读取到任何专辑页面快照")

    previous: Dict[str, Dict[str, Any]] = {}
    for snapshot_index, rows in enumerate(snapshots, 1):
        current: Dict[str, Dict[str, Any]] = {}
        names: set[str] = set()
        for row_index, row in enumerate(rows):
            board_id = _require_note_id(row.get("id"), f"快照 {snapshot_index} 专辑 {row_index} id")
            name = str(row.get("name") or "").strip()
            if not name:
                raise VisibleUiContractError(f"快照 {snapshot_index} 出现空专辑名")
            if board_id in current or name in names:
                raise VisibleUiContractError(f"快照 {snapshot_index} 出现重复专辑 id 或名称")
            normalized = dict(row)
            normalized["id"] = board_id
            normalized["name"] = name
            current[board_id] = normalized
            names.add(name)
        if len(current) > declared_count:
            raise VisibleUiContractError(
                f"可见专辑数超过页面声明总数：声明 {declared_count}，读取 {len(current)}"
            )
        for board_id, old in previous.items():
            now = current.get(board_id)
            if now is None:
                raise VisibleUiContractError("向下滚动后已有专辑消失，无法证明列表完整")
            if now["name"] != old["name"]:
                raise VisibleUiContractError("向下滚动时专辑 id 与名称绑定发生变化")
        previous = current

    final_rows = list(previous.values())
    if len(final_rows) != declared_count:
        raise VisibleUiContractError(
            f"专辑列表缺页：页面声明 {declared_count}，只读取到 {len(final_rows)}"
        )
    return final_rows


def validate_board_note_snapshots(
    declared_count: int,
    snapshots: Sequence[Sequence[Mapping[str, Any]]],
) -> List[Dict[str, Any]]:
    """Validate visible note cards for one album against its declared count."""
    if not isinstance(declared_count, int) or isinstance(declared_count, bool) or declared_count < 0:
        raise VisibleUiContractError("专辑页面没有提供有效的笔记总数")
    if not snapshots:
        raise VisibleUiContractError("没有读取到任何专辑成员页面快照")
    previous: Dict[str, Dict[str, Any]] = {}
    for snapshot_index, rows in enumerate(snapshots, 1):
        current: Dict[str, Dict[str, Any]] = {}
        for row_index, row in enumerate(rows):
            note_id = _require_note_id(row.get("id"), f"成员快照 {snapshot_index} 笔记 {row_index} id")
            if note_id in current:
                raise VisibleUiContractError(f"成员快照 {snapshot_index} 出现重复笔记 id")
            normalized = dict(row)
            normalized["id"] = note_id
            current[note_id] = normalized
        if len(current) > declared_count:
            raise VisibleUiContractError(
                f"可见成员数超过页面声明总数：声明 {declared_count}，读取 {len(current)}"
            )
        for note_id in previous:
            if note_id not in current:
                raise VisibleUiContractError("向下滚动后已有笔记消失，无法证明成员列表完整")
        previous = current
    final_rows = list(previous.values())
    if len(final_rows) != declared_count:
        raise VisibleUiContractError(
            f"专辑成员缺页：页面声明 {declared_count}，只读取到 {len(final_rows)}"
        )
    return final_rows


def validate_new_collection_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    note_id: str,
) -> None:
    """Require one exact append and prove that no previous member disappeared."""
    expected_note_id = _require_note_id(note_id, "note id")
    for label, row in (("before", before), ("after", after)):
        if not isinstance(row, Mapping):
            raise VisibleUiContractError(f"{label} board snapshot 格式错误")
        if row.get("declared_total") != len(row.get("note_ids") or []):
            raise VisibleUiContractError(f"{label} board snapshot 不完整")
    if before.get("id") != after.get("id") or before.get("name") != after.get("name"):
        raise VisibleUiContractError("写入前后专辑身份发生变化")
    before_ids = [_require_note_id(value, "before note id") for value in before.get("note_ids") or []]
    after_ids = [_require_note_id(value, "after note id") for value in after.get("note_ids") or []]
    if len(before_ids) != len(set(before_ids)) or len(after_ids) != len(set(after_ids)):
        raise VisibleUiContractError("写入前后专辑成员出现重复")
    if expected_note_id in before_ids:
        raise VisibleUiContractError("待归档笔记在写入前已属于目标专辑")
    if after.get("declared_total") != before.get("declared_total") + 1:
        raise VisibleUiContractError("目标专辑成员数没有精确增加 1")
    if set(after_ids) != set(before_ids) | {expected_note_id}:
        raise VisibleUiContractError("写入后的专辑成员不是原集合加新笔记")


VISIBLE_UI_CORE_JS = r"""
function xhsUiText(value) {
  return value === undefined || value === null ? '' : String(value).trim();
}
function xhsUiSecurityMarker(value) {
  const haystack = xhsUiText(value).toLowerCase();
  const markers = SECURITY_MARKERS_JSON;
  return markers.find(marker => haystack.includes(marker.toLowerCase())) || '';
}
function xhsUiAssertContext(payload) {
  const href = String(window.location.href || '');
  const bodyText = (document.body && document.body.innerText) || '';
  const marker = xhsUiSecurityMarker(href) || xhsUiSecurityMarker(bodyText);
  if (marker) throw new Error('SAFETY_BREAKER: Xiaohongshu security challenge detected: ' + marker);
  if (window.location.hostname !== 'www.xiaohongshu.com') throw new Error('current page is not Xiaohongshu');
  if (window.name !== payload.tab_marker) throw new Error('Arc tab runtime marker mismatch');
  if (/手机号登录|登录后推荐|马上登录即可|扫码登录|验证码登录/.test(bodyText)) {
    throw new Error('current Xiaohongshu page looks logged out');
  }
  const own = Array.from(document.querySelectorAll('a[href*="/user/profile/"]'))
    .find(link => xhsUiText(link.textContent) === '我');
  if (!own) throw new Error('current account cannot be verified from visible UI');
  const ownUrl = new URL(own.getAttribute('href') || '', window.location.origin);
  const ownMatch = ownUrl.pathname.match(/^\/user\/profile\/([0-9a-fA-F]{24})\/?$/);
  if (!ownMatch || ownMatch[1].toLowerCase() !== payload.user_id) {
    throw new Error('current Xiaohongshu account does not match');
  }
  return ownMatch[1].toLowerCase();
}
function xhsUiVisible(element) {
  if (!element) return false;
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
}
function xhsUiClick(element) {
  if (!xhsUiVisible(element)) throw new Error('visible UI control is missing');
  const rect = element.getBoundingClientRect();
  const base = {
    bubbles: true, cancelable: true, composed: true,
    clientX: rect.left + rect.width / 2,
    clientY: rect.top + rect.height / 2,
    button: 0, buttons: 1, pointerId: 1, pointerType: 'mouse', isPrimary: true
  };
  for (const event of [
    new PointerEvent('pointerdown', base),
    new MouseEvent('mousedown', base),
    new PointerEvent('pointerup', {...base, buttons: 0}),
    new MouseEvent('mouseup', {...base, buttons: 0}),
    new MouseEvent('click', {...base, buttons: 0})
  ]) element.dispatchEvent(event);
}
function xhsUiIdFromPath(value, kind) {
  const url = new URL(value || '', window.location.origin);
  const pattern = kind === 'board'
    ? /^\/board\/([0-9a-fA-F]{24})\/?$/
    : /\/(?:explore|item)\/([0-9a-fA-F]{24})(?:\/|$)/;
  const match = url.pathname.match(pattern);
  return match ? match[1].toLowerCase() : '';
}
""".replace("SECURITY_MARKERS_JSON", json.dumps(SECURITY_MARKERS, ensure_ascii=False))


def _payload(user_id: str, tab_marker: str) -> Dict[str, str]:
    return {
        "user_id": _require_note_id(user_id, "user id"),
        "tab_marker": str(tab_marker or "").strip(),
    }


def build_open_album_tab_js(user_id: str, tab_marker: str) -> str:
    payload = _payload(user_id, tab_marker)
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  xhsUiAssertContext(payload);
  const current = new URL(window.location.href);
  if (current.pathname !== '/user/profile/' + payload.user_id || current.searchParams.get('tab') !== 'fav') {
    throw new Error('open the current account favorites page before reading albums');
  }
  const tabs = Array.from(document.querySelectorAll('.reds-tab-item'))
    .filter(element => /^专辑・\d+$/.test(xhsUiText(element.innerText)));
  if (tabs.length !== 1) throw new Error('visible album tab match count must be 1');
  xhsUiClick(tabs[0]);
  return JSON.stringify({clicked: true});
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


def build_album_list_snapshot_js(user_id: str, tab_marker: str) -> str:
    payload = _payload(user_id, tab_marker)
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  const liveUserId = xhsUiAssertContext(payload);
  const current = new URL(window.location.href);
  if (current.pathname !== '/user/profile/' + payload.user_id || current.searchParams.get('tab') !== 'fav') {
    throw new Error('album list page binding mismatch');
  }
  const tab = Array.from(document.querySelectorAll('.reds-tab-item'))
    .find(element => /^专辑・\d+$/.test(xhsUiText(element.innerText)));
  if (!tab || !tab.classList.contains('active')) throw new Error('visible album tab is not active');
  const countMatch = xhsUiText(tab.innerText).match(/^专辑・(\d+)$/);
  if (!countMatch) throw new Error('visible album count is missing');
  const boards = [];
  const seen = new Set();
  for (const anchor of document.querySelectorAll('a[href*="/board/"]')) {
    const id = xhsUiIdFromPath(anchor.href, 'board');
    if (!id || seen.has(id)) continue;
    const title = anchor.querySelector('.title');
    const name = xhsUiText(title && title.innerText);
    const text = xhsUiText(anchor.innerText);
    const totalMatch = text.match(/笔记・(\d+)/);
    if (!name || !totalMatch) continue;
    seen.add(id);
    boards.push({
      id,
      name,
      privacy: /私密/.test(text) || !!anchor.querySelector('use[href$="#lock"],use[xlink\\:href$="#lock"]') ? 1 : 0,
      declared_total: Number(totalMatch[1]),
      path: '/board/' + id
    });
  }
  const root = document.scrollingElement;
  return JSON.stringify({
    live_account_user_id: liveUserId,
    live_page_binding: current.origin + current.pathname + '?tab=fav',
    declared_board_count: Number(countMatch[1]),
    boards,
    scroll_y: window.scrollY,
    scroll_height: root ? root.scrollHeight : 0,
    viewport_height: window.innerHeight,
    at_bottom: !!root && window.scrollY + window.innerHeight >= root.scrollHeight - 2
  });
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


def build_scroll_to_bottom_js(user_id: str, tab_marker: str) -> str:
    payload = _payload(user_id, tab_marker)
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  xhsUiAssertContext(payload);
  const root = document.scrollingElement;
  if (!root) throw new Error('page scrolling element is missing');
  window.scrollTo(0, root.scrollHeight);
  return JSON.stringify({scroll_y: window.scrollY, scroll_height: root.scrollHeight});
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


def build_board_snapshot_js(user_id: str, tab_marker: str, board_id: str) -> str:
    payload = _payload(user_id, tab_marker)
    payload["board_id"] = _require_note_id(board_id, "board id")
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  xhsUiAssertContext(payload);
  const current = new URL(window.location.href);
  if (current.pathname !== '/board/' + payload.board_id) throw new Error('board page binding mismatch');
  const boardName = xhsUiText(document.querySelector('.board-info .name')?.innerText);
  const countText = xhsUiText(document.querySelector('.board-info .note-count')?.innerText);
  const countMatch = countText.match(/笔记・(\d+)/);
  if (!boardName || !countMatch) throw new Error('visible board identity or note count is missing');
  const notes = [];
  const seen = new Set();
  for (const section of document.querySelectorAll('section.note-item')) {
    let id = xhsUiText(section.dataset.noteId).toLowerCase();
    if (!/^[0-9a-f]{24}$/.test(id)) {
      const link = section.querySelector('a[href*="/explore/"],a[href*="/item/"]');
      id = link ? xhsUiIdFromPath(link.href, 'note') : '';
    }
    if (!id || seen.has(id)) continue;
    seen.add(id);
    notes.push({
      id,
      title: xhsUiText(section.querySelector('a.title')?.innerText)
    });
  }
  const root = document.scrollingElement;
  return JSON.stringify({
    board_id: payload.board_id,
    board_name: boardName,
    declared_total: Number(countMatch[1]),
    notes,
    scroll_y: window.scrollY,
    scroll_height: root ? root.scrollHeight : 0,
    viewport_height: window.innerHeight,
    at_bottom: !!root && window.scrollY + window.innerHeight >= root.scrollHeight - 2
  });
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


def build_create_modal_probe_js(user_id: str, tab_marker: str) -> str:
    payload = _payload(user_id, tab_marker)
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  xhsUiAssertContext(payload);
  const modal = document.querySelector('.reds-modal-open .modal');
  if (!modal) return JSON.stringify({open: false});
  const title = modal.querySelector('input.input-content');
  const description = modal.querySelector('textarea.textarea-content');
  const privacy = modal.querySelector('.dot-container.switch');
  const done = modal.querySelector('.footer .btn.done');
  const cancel = modal.querySelector('.footer .btn:not(.done)');
  return JSON.stringify({
    open: true,
    fields_complete: !!title && !!description && !!privacy && !!done && !!cancel,
    public_enabled: !!privacy && privacy.classList.contains('turn-on')
  });
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


def build_open_create_modal_js(user_id: str, tab_marker: str) -> str:
    payload = _payload(user_id, tab_marker)
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  xhsUiAssertContext(payload);
  const triggers = Array.from(document.querySelectorAll('.create-board'))
    .filter(element => xhsUiText(element.innerText) === '创建专辑');
  if (triggers.length !== 1) throw new Error('visible create-board control match count must be 1');
  triggers[0].click();
  return JSON.stringify({clicked: true});
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


def build_submit_create_board_js(
    user_id: str,
    tab_marker: str,
    *,
    name: str,
    description: str,
    privacy: int,
) -> str:
    payload = _payload(user_id, tab_marker)
    payload.update({
        "name": str(name),
        "description": str(description),
        "privacy": int(privacy),
    })
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  xhsUiAssertContext(payload);
  const modal = document.querySelector('.reds-modal-open .modal');
  if (!modal) throw new Error('create-board modal is not open');
  const title = modal.querySelector('input.input-content');
  const description = modal.querySelector('textarea.textarea-content');
  const privacy = modal.querySelector('.dot-container.switch');
  const done = modal.querySelector('.footer .btn.done');
  if (!title || !description || !privacy || !done) throw new Error('create-board fields are incomplete');
  const setValue = (element, value) => {
    const descriptor = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value');
    if (!descriptor || typeof descriptor.set !== 'function') throw new Error('form value setter is unavailable');
    descriptor.set.call(element, value);
    element.dispatchEvent(new InputEvent('input', {bubbles: true, composed: true, inputType: 'insertText', data: value}));
    element.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
  };
  setValue(title, payload.name);
  setValue(description, payload.description);
  const isPublic = privacy.classList.contains('turn-on');
  const wantPublic = payload.privacy === 0;
  if (isPublic !== wantPublic) xhsUiClick(privacy);
  if (xhsUiText(title.value) !== payload.name || xhsUiText(description.value) !== payload.description) {
    throw new Error('create-board form did not retain approved values');
  }
  xhsUiClick(done);
  return JSON.stringify({submitted: true});
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


def build_collect_into_board_js(
    user_id: str,
    tab_marker: str,
    *,
    note_id: str,
    target_board: str,
    timeout_ms: int = 10000,
) -> str:
    payload = _payload(user_id, tab_marker)
    payload.update({
        "note_id": _require_note_id(note_id, "note id"),
        "target_board": str(target_board or "").strip(),
        "timeout_ms": int(timeout_ms),
    })
    if not payload["target_board"]:
        raise VisibleUiContractError("target board 不能为空")
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  xhsUiAssertContext(payload);
  if (window.location.pathname !== '/explore/' + payload.note_id) throw new Error('note page binding mismatch');
  const collect = document.querySelector('#note-page-collect-board-guide');
  const icon = collect && collect.querySelector('use');
  const href = icon && (icon.getAttribute('href') || icon.getAttribute('xlink:href')) || '';
  if (!href.endsWith('#collect')) {
    throw new Error('historical collected notes cannot be reassigned through visible UI without uncollecting');
  }
  const runId = 'xhs_ui_' + Date.now() + '_' + Math.floor(Math.random() * 1000000);
  const state = document.createElement('div');
  state.id = 'xhs-visible-ui-state-' + runId;
  state.hidden = true;
  state.textContent = JSON.stringify({done: false, events: []});
  document.documentElement.appendChild(state);
  const events = [];
  const publish = value => { state.textContent = JSON.stringify(value); };
  let joinClicked = false;
  let boardClicked = false;
  let panelSignature = '';
  let panelStableTicks = 0;
  let timer = null;
  const stop = () => {
    observer.disconnect();
    if (timer !== null) clearInterval(timer);
  };
  const fail = error => {
    stop();
    const message = error && error.message ? error.message : String(error);
    publish({
      done: true,
      ok: false,
      error: joinClicked
        ? 'HIGH_RISK_STATE_UNCERTAIN: collect may be applied but album assignment was not verified; ' + message
        : message,
      events
    });
  };
  const drive = () => {
    try {
      xhsUiAssertContext(payload);
      if (!joinClicked) {
        const join = Array.from(document.querySelectorAll('.tooltip-container .right-area,.right-area'))
          .find(element => xhsUiText(element.innerText) === '加入专辑');
        if (join) {
          joinClicked = true;
          events.push('ui:join_album_visible', 'ui:join_album_clicked');
          xhsUiClick(join);
        }
      }
      if (joinClicked && !boardClicked) {
        const boards = Array.from(document.querySelectorAll('.board-list .board-item'))
          .filter(element => xhsUiText(element.innerText) === payload.target_board);
        if (boards.length > 1) throw new Error('target board visible match count exceeds 1');
        if (boards.length === 1) {
          boardClicked = true;
          events.push('board:FOUND:' + payload.target_board, 'ui:board_clicked');
          xhsUiClick(boards[0]);
        } else {
          const container = document.querySelector('.board-list-container');
          if (container) {
            const signature = [
              container.scrollTop,
              container.scrollHeight,
              container.clientHeight,
              document.querySelectorAll('.board-list .board-item').length
            ].join(':');
            panelStableTicks = signature === panelSignature ? panelStableTicks + 1 : 0;
            panelSignature = signature;
            if (container.scrollHeight > container.clientHeight) {
              container.scrollTop = container.scrollHeight;
              events.push('ui:album_panel_scrolled');
            } else if (panelStableTicks >= 5) {
              throw new Error('target board is absent from the complete visible album selector');
            }
            if (
              container.scrollTop + container.clientHeight >= container.scrollHeight - 1
              && panelStableTicks >= 5
            ) {
              throw new Error('album selector reached the end without the prevalidated target board');
            }
          }
        }
      }
      if (boardClicked) {
        const success = Array.from(document.querySelectorAll('.message-container,.msg-container,.left-area'))
          .find(element => xhsUiText(element.innerText) === '已加入' + payload.target_board);
        if (success) {
          stop();
          events.push('ui:join_confirmed');
          publish({done: true, ok: true, result: {
            id: payload.note_id,
            target_board: payload.target_board,
            events,
            visible_confirmation: xhsUiText(success.innerText)
          }});
        }
      }
    } catch (error) {
      fail(error);
    }
  };
  const observer = new MutationObserver(drive);
  observer.observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true});
  timer = setInterval(drive, 100);
  setTimeout(() => {
    const current = JSON.parse(state.textContent || '{}');
    if (!current.done) {
      fail(new Error('visible collect-to-album flow timed out'));
    }
  }, payload.timeout_ms);
  events.push('ui:collect_clicked');
  xhsUiClick(collect);
  return runId;
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


def build_note_collect_probe_js(user_id: str, tab_marker: str, note_id: str) -> str:
    payload = _payload(user_id, tab_marker)
    payload["note_id"] = _require_note_id(note_id, "note id")
    return (
        r"""(() => {
PAYLOAD_AND_CORE
  xhsUiAssertContext(payload);
  if (window.location.pathname !== '/explore/' + payload.note_id) throw new Error('note page binding mismatch');
  const collect = document.querySelector('#note-page-collect-board-guide');
  const icon = collect && collect.querySelector('use');
  const href = icon && (icon.getAttribute('href') || icon.getAttribute('xlink:href')) || '';
  if (!collect || !xhsUiVisible(collect)) throw new Error('visible collect control is missing');
  if (!href.endsWith('#collect') && !href.endsWith('#collected')) {
    throw new Error('visible collect state is unknown');
  }
  return JSON.stringify({
    note_id: payload.note_id,
    collected: href.endsWith('#collected'),
    control_visible: true
  });
})()"""
        .replace("PAYLOAD_AND_CORE", f"const payload = {json.dumps(payload, ensure_ascii=False)};\n{VISIBLE_UI_CORE_JS}")
    )


@dataclass(frozen=True)
class ArcVisibleUiSession:
    window_id: str
    tab_id: str
    tab_marker: str
    user_id: str

    def __post_init__(self) -> None:
        for label, value in (
            ("window id", self.window_id),
            ("tab id", self.tab_id),
            ("tab marker", self.tab_marker),
        ):
            if not str(value or "").strip():
                raise VisibleUiContractError(f"Arc {label} 不能为空")
        _require_note_id(self.user_id, "user id")

    def run_json(self, script: str) -> Any:
        raw = arc_js_macos(
            script,
            tab_marker=self.tab_marker,
            window_id=self.window_id,
            tab_id=self.tab_id,
            expected_url_substring="xiaohongshu.com",
        )
        return parse_arc_json(raw)

    def navigate(self, path: str, query: Mapping[str, str] | None = None) -> None:
        clean_path = str(path or "").strip()
        if not clean_path.startswith("/") or ".." in clean_path:
            raise VisibleUiContractError("Arc 目标路径无效")
        query_string = urlencode(dict(query or {}))
        target = "https://www.xiaohongshu.com" + clean_path
        if query_string:
            target += "?" + query_string
        parsed = urlparse(target)
        forbidden = {"xsec_token", "xsec_source", "sign", "signature"}
        if parsed.scheme != "https" or parsed.hostname != "www.xiaohongshu.com":
            raise VisibleUiContractError("Arc 只允许导航到小红书正式站")
        if forbidden.intersection(parse_qs(parsed.query)):
            raise VisibleUiContractError("Arc 导航目标不得包含会话或签名参数")
        require_macos_app_running("Arc")
        navigate_script = (
            "const app=Application('Arc');\n"
            f"const w=app.windows.byId({json.dumps(self.window_id)});\n"
            f"const t=w.tabs.byId({json.dumps(self.tab_id)});\n"
            "const current=String(t.url()||'');\n"
            "if(!current.includes('xiaohongshu.com')) throw new Error('Arc bound tab is no longer Xiaohongshu');\n"
            f"t.url={json.dumps(target, ensure_ascii=False)};\n"
            "true;"
        )
        jxa_osascript(navigate_script, timeout=15)

        status_script = (
            "const app=Application('Arc');\n"
            f"const w=app.windows.byId({json.dumps(self.window_id)});\n"
            f"const t=w.tabs.byId({json.dumps(self.tab_id)});\n"
            "JSON.stringify({url:String(t.url()||''),title:String(t.title()||''),loading:Boolean(t.loading())});"
        )
        deadline = time.monotonic() + 20.0
        last_status: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_status = parse_arc_json(jxa_osascript(status_script, timeout=5))
            current_url = str(last_status.get("url") or "")
            marker = next(
                (value for value in SECURITY_MARKERS if value.lower() in (current_url + " " + str(last_status.get("title") or "")).lower()),
                "",
            )
            if marker:
                raise VisibleUiContractError(
                    f"SAFETY_BREAKER: Xiaohongshu security challenge detected: {marker}"
                )
            if current_url == target and last_status.get("loading") is False:
                return
            time.sleep(0.1)
        raise VisibleUiContractError(
            f"Arc 导航未在截止时间内完成：{last_status.get('url') or '无 URL'}"
        )

    def wait_for(self, script: str, *, timeout_sec: float = 20.0) -> Any:
        deadline = time.monotonic() + timeout_sec
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                value = self.run_json(script)
                if value:
                    return value
            except Exception as exc:  # page may still be materializing
                last_error = exc
            time.sleep(0.2)
        if last_error:
            raise VisibleUiContractError(f"Arc 正式页面未在截止时间内就绪：{last_error}") from last_error
        raise VisibleUiContractError("Arc 正式页面未在截止时间内就绪")


def _validate_prefix(
    snapshots: Sequence[Sequence[Mapping[str, Any]]],
    *,
    kind: str,
) -> None:
    latest_count = len(snapshots[-1])
    if kind == "albums":
        validate_album_scroll_snapshots(latest_count, snapshots)
    else:
        validate_board_note_snapshots(latest_count, snapshots)


def read_visible_album_list(
    session: ArcVisibleUiSession,
    *,
    progress_timeout_sec: float = 5.0,
    overall_timeout_sec: float = 45.0,
) -> Dict[str, Any]:
    """Read every album card visible through the official profile UI."""
    session.navigate(f"/user/profile/{session.user_id}", {"tab": "fav"})
    session.wait_for(build_open_album_tab_js(session.user_id, session.tab_marker))

    snapshots: List[List[Dict[str, Any]]] = []
    declared_count: int | None = None
    live_binding = ""
    live_user_id = ""
    last_count = -1
    progress_deadline = time.monotonic() + progress_timeout_sec
    overall_deadline = time.monotonic() + overall_timeout_sec

    while time.monotonic() < overall_deadline:
        data = session.wait_for(
            build_album_list_snapshot_js(session.user_id, session.tab_marker),
            timeout_sec=min(20.0, max(1.0, overall_deadline - time.monotonic())),
        )
        current_declared = data.get("declared_board_count")
        if not isinstance(current_declared, int) or isinstance(current_declared, bool) or current_declared < 0:
            raise VisibleUiContractError("页面没有提供有效的专辑总数")
        if declared_count is None:
            declared_count = current_declared
            live_binding = str(data.get("live_page_binding") or "")
            live_user_id = str(data.get("live_account_user_id") or "")
        elif current_declared != declared_count:
            raise VisibleUiContractError(
                f"读取过程中专辑总数变化：原为 {declared_count}，现为 {current_declared}"
            )
        rows = data.get("boards")
        if not isinstance(rows, list):
            raise VisibleUiContractError("专辑列表页面返回格式错误")
        snapshots.append([dict(row) for row in rows])
        _validate_prefix(snapshots, kind="albums")
        current_count = len(rows)
        if current_count == declared_count:
            return {
                "declared_board_count": declared_count,
                "boards": validate_album_scroll_snapshots(declared_count, snapshots),
                "page_count": len(snapshots),
                "live_page_binding": live_binding,
                "live_account_user_id": live_user_id,
            }
        if current_count > last_count:
            last_count = current_count
            progress_deadline = time.monotonic() + progress_timeout_sec
        elif time.monotonic() >= progress_deadline:
            break
        session.run_json(build_scroll_to_bottom_js(session.user_id, session.tab_marker))
        time.sleep(0.2)

    final_count = len(snapshots[-1]) if snapshots else 0
    raise VisibleUiContractError(
        f"专辑列表缺页：页面声明 {declared_count}，只读取到 {final_count}"
    )


def read_visible_board(
    session: ArcVisibleUiSession,
    board: Mapping[str, Any],
    *,
    progress_timeout_sec: float = 5.0,
    overall_timeout_sec: float = 45.0,
) -> Dict[str, Any]:
    """Read one album's complete visible member cards and enforce its count."""
    board_id = _require_note_id(board.get("id"), "board id")
    expected_name = str(board.get("name") or "").strip()
    if not expected_name:
        raise VisibleUiContractError("board name 不能为空")
    session.navigate(f"/board/{board_id}")

    snapshots: List[List[Dict[str, Any]]] = []
    declared_count: int | None = None
    last_count = -1
    progress_deadline = time.monotonic() + progress_timeout_sec
    overall_deadline = time.monotonic() + overall_timeout_sec
    while time.monotonic() < overall_deadline:
        data = session.wait_for(
            build_board_snapshot_js(session.user_id, session.tab_marker, board_id),
            timeout_sec=min(20.0, max(1.0, overall_deadline - time.monotonic())),
        )
        if str(data.get("board_id") or "").lower() != board_id:
            raise VisibleUiContractError("专辑页面 id 绑定不一致")
        if str(data.get("board_name") or "").strip() != expected_name:
            raise VisibleUiContractError("专辑页面 id 与名称绑定不一致")
        current_declared = data.get("declared_total")
        if not isinstance(current_declared, int) or isinstance(current_declared, bool) or current_declared < 0:
            raise VisibleUiContractError("专辑页面没有提供有效的笔记总数")
        expected_declared = board.get("declared_total")
        if isinstance(expected_declared, int) and current_declared != expected_declared:
            raise VisibleUiContractError(
                f"专辑 {expected_name} 数量变化：列表为 {expected_declared}，详情为 {current_declared}"
            )
        if declared_count is None:
            declared_count = current_declared
        elif current_declared != declared_count:
            raise VisibleUiContractError(
                f"读取专辑 {expected_name} 时成员总数变化：原为 {declared_count}，现为 {current_declared}"
            )
        rows = data.get("notes")
        if not isinstance(rows, list):
            raise VisibleUiContractError("专辑成员页面返回格式错误")
        snapshots.append([dict(row) for row in rows])
        _validate_prefix(snapshots, kind="notes")
        current_count = len(rows)
        if current_count == declared_count:
            notes = validate_board_note_snapshots(declared_count, snapshots)
            return {
                "id": board_id,
                "name": expected_name,
                "privacy": board.get("privacy"),
                "declared_total": declared_count,
                "accessible_unique_count": len(notes),
                "declared_vs_accessible_delta": 0,
                "page_count": len(snapshots),
                "note_ids": [row["id"] for row in notes],
                "notes": notes,
            }
        if current_count > last_count:
            last_count = current_count
            progress_deadline = time.monotonic() + progress_timeout_sec
        elif time.monotonic() >= progress_deadline:
            break
        session.run_json(build_scroll_to_bottom_js(session.user_id, session.tab_marker))
        time.sleep(0.2)
    final_count = len(snapshots[-1]) if snapshots else 0
    raise VisibleUiContractError(
        f"专辑成员缺页：{expected_name} 声明 {declared_count}，只读取到 {final_count}"
    )


def capture_visible_album_snapshot(session: ArcVisibleUiSession) -> Dict[str, Any]:
    """Capture all album memberships through visible Arc pages."""
    album_list = read_visible_album_list(session)
    boards = []
    membership: Dict[str, List[Dict[str, str]]] = {}
    for board in album_list["boards"]:
        row = read_visible_board(session, board)
        boards.append({key: value for key, value in row.items() if key != "notes"})
        for note_id in row["note_ids"]:
            membership.setdefault(note_id, []).append({
                "board_id": row["id"],
                "board_name": row["name"],
            })
    duplicate_note_ids = sorted(note_id for note_id, refs in membership.items() if len(refs) > 1)
    if duplicate_note_ids:
        raise VisibleUiContractError(
            f"同一笔记出现在多个专辑，已停止：{duplicate_note_ids[:5]}"
        )
    return {
        "mode": "read_only",
        "source": {
            "browser": "Arc",
            "window_id": session.window_id,
            "tab_id": session.tab_id,
            "tab_marker": session.tab_marker,
            "expected_url_substring": album_list["live_page_binding"],
            "live_page_binding": album_list["live_page_binding"],
            "live_account_user_id": album_list["live_account_user_id"],
            "calls": ["visible profile album cards", "visible album note cards"],
            "writes_performed": False,
        },
        "boards": boards,
        "membership": dict(sorted(membership.items())),
        "validation": {
            "board_count": len(boards),
            "board_list_page_count": album_list["page_count"],
            "board_names_unique": len({board["name"] for board in boards}) == len(boards),
            "pagination_cursor_invariants_passed": True,
            "accessible_note_occurrences": sum(len(board["note_ids"]) for board in boards),
            "accessible_unique_note_ids_across_boards": len(membership),
            "duplicate_note_ids": [],
            "multi_board_note_ids": [],
            "within_board_duplicates": [],
            "count_mismatch_boards": [],
            "display_count_consistent": True,
            "full_membership_complete": True,
        },
    }


def wait_create_modal(session: ArcVisibleUiSession, *, timeout_sec: float = 10.0) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        probe = session.run_json(build_create_modal_probe_js(session.user_id, session.tab_marker))
        if probe.get("open") is True:
            if probe.get("fields_complete") is not True:
                raise VisibleUiContractError("创建专辑弹窗字段不完整")
            return probe
        time.sleep(0.2)
    raise VisibleUiContractError("创建专辑弹窗未打开")


def create_visible_board(
    session: ArcVisibleUiSession,
    *,
    name: str,
    description: str,
    privacy: int,
    execute: bool,
) -> Dict[str, Any]:
    """Create one album through the visible profile form and verify it empty."""
    clean_name = str(name or "").strip()
    if not clean_name or clean_name != name:
        raise VisibleUiContractError("专辑名称不能为空或带首尾空格")
    if privacy not in (0, 1):
        raise VisibleUiContractError("privacy 只能是 0 或 1")
    before = read_visible_album_list(session)
    matches = [board for board in before["boards"] if board["name"] == clean_name]
    if len(matches) > 1:
        raise VisibleUiContractError("已有多个同名专辑")
    if matches:
        board = read_visible_board(session, matches[0])
        if board["declared_total"] != 0 or board["note_ids"]:
            raise VisibleUiContractError("同名专辑已存在但不是空专辑")
        if matches[0].get("privacy") != privacy:
            raise VisibleUiContractError("同名专辑隐私设置与批准值不一致")
        return {
            "status": "already_exists",
            "writePerformed": False,
            "board": {key: matches[0][key] for key in ("id", "name", "privacy")},
            "boardCountBefore": before["declared_board_count"],
            "boardCountAfter": before["declared_board_count"],
            "emptyBoardVerified": True,
            "events": ["ui:board_already_exists", "verify:existing_board_empty"],
        }
    if not execute:
        return {
            "status": "planned",
            "writePerformed": False,
            "board": None,
            "boardCountBefore": before["declared_board_count"],
            "boardCountAfter": before["declared_board_count"],
            "events": ["dry_run:no_account_changes"],
        }

    session.run_json(build_open_create_modal_js(session.user_id, session.tab_marker))
    modal = wait_create_modal(session)
    if modal["public_enabled"] != (privacy == 0):
        # The submit script toggles exactly once after re-reading the current switch.
        pass
    session.run_json(build_submit_create_board_js(
        session.user_id,
        session.tab_marker,
        name=clean_name,
        description=description,
        privacy=privacy,
    ))

    deadline = time.monotonic() + 15.0
    after = None
    while time.monotonic() < deadline:
        try:
            after = read_visible_album_list(session, progress_timeout_sec=2.0, overall_timeout_sec=10.0)
            if after["declared_board_count"] == before["declared_board_count"] + 1:
                break
        except VisibleUiContractError:
            pass
        time.sleep(0.2)
    if after is None or after["declared_board_count"] != before["declared_board_count"] + 1:
        raise VisibleUiContractError("创建专辑后总数没有精确增加 1")
    matches = [board for board in after["boards"] if board["name"] == clean_name]
    if len(matches) != 1:
        raise VisibleUiContractError("创建后无法唯一找到新专辑")
    board = read_visible_board(session, matches[0])
    if board["declared_total"] != 0 or board["note_ids"]:
        raise VisibleUiContractError("新建专辑没有被验证为空")
    return {
        "status": "created",
        "writePerformed": True,
        "board": {key: matches[0][key] for key in ("id", "name", "privacy")},
        "boardCountBefore": before["declared_board_count"],
        "boardCountAfter": after["declared_board_count"],
        "emptyBoardVerified": True,
        "events": ["ui:create_submitted", "verify:board_count_plus_one", "verify:new_board_empty"],
    }


def collect_new_note_into_board(
    session: ArcVisibleUiSession,
    *,
    note_id: str,
    target_board: str,
    execute: bool,
) -> Dict[str, Any]:
    """Collect one currently-uncollected note and immediately choose an album."""
    clean_note_id = _require_note_id(note_id, "note id")
    clean_target = str(target_board or "").strip()
    if not clean_target or clean_target != target_board:
        raise VisibleUiContractError("target board 不能为空或带首尾空格")

    before_albums = read_visible_album_list(session)
    matches = [row for row in before_albums["boards"] if row["name"] == clean_target]
    if len(matches) != 1:
        raise VisibleUiContractError("目标专辑必须在可见列表中唯一存在")
    before_board = read_visible_board(session, matches[0])
    session.navigate(f"/explore/{clean_note_id}")
    probe = session.wait_for(
        build_note_collect_probe_js(session.user_id, session.tab_marker, clean_note_id)
    )
    if probe.get("collected") is not False:
        raise VisibleUiContractError(
            "该笔记在本次操作前已收藏；为保护历史收藏，不会取消后重新收藏"
        )
    if not execute:
        return {
            "status": "planned",
            "writePerformed": False,
            "note_id": clean_note_id,
            "target_board": clean_target,
            "board_id": before_board["id"],
            "events": ["preflight:note_uncollected", "dry_run:no_account_changes"],
        }

    run_id = session.run_json(build_collect_into_board_js(
        session.user_id,
        session.tab_marker,
        note_id=clean_note_id,
        target_board=clean_target,
    ))
    result = poll_collect_into_board(session, run_id)

    after_albums = read_visible_album_list(session)
    if after_albums["declared_board_count"] != before_albums["declared_board_count"]:
        raise VisibleUiContractError("归档后专辑总数发生变化")
    before_identity = {(row["id"], row["name"]) for row in before_albums["boards"]}
    after_identity = {(row["id"], row["name"]) for row in after_albums["boards"]}
    if before_identity != after_identity:
        raise VisibleUiContractError("归档后专辑 id/名称集合发生变化")
    after_match = [row for row in after_albums["boards"] if row["id"] == before_board["id"]]
    if len(after_match) != 1:
        raise VisibleUiContractError("归档后无法唯一找回目标专辑")
    after_board = read_visible_board(session, after_match[0])
    validate_new_collection_transition(before_board, after_board, clean_note_id)
    return {
        "status": "success",
        "writePerformed": True,
        "note_id": clean_note_id,
        "target_board": clean_target,
        "board_id": before_board["id"],
        "visible_confirmation": result.get("visible_confirmation"),
        "events": list(result.get("events") or []) + ["verify:exact_member_append"],
    }


def poll_collect_into_board(
    session: ArcVisibleUiSession,
    run_id: str,
    *,
    timeout_sec: float = 15.0,
) -> Dict[str, Any]:
    if not re.fullmatch(r"xhs_ui_\d+_\d+", str(run_id or "")):
        raise VisibleUiContractError("visible UI job id 无效")
    state_id = "xhs-visible-ui-state-" + run_id
    poll_js = (
        "(() => {"
        f"const node=document.getElementById({json.dumps(state_id)});"
        "if(!node)return JSON.stringify(null);"
        "const state=JSON.parse(node.textContent||'{}');"
        "if(state.done)node.remove();"
        "return JSON.stringify(state);"
        "})()"
    )
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        state = session.run_json(poll_js)
        if state is None:
            raise VisibleUiContractError("visible UI job state disappeared")
        if state.get("done"):
            if state.get("ok") is True:
                return dict(state.get("result") or {})
            raise VisibleUiContractError(str(state.get("error") or "visible UI job failed"))
        time.sleep(0.2)
    raise VisibleUiContractError("visible UI job timed out")
