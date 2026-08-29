"""Load confirmed archived note IDs before any detail, OCR, or video work."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable


class ArchiveExclusionError(ValueError):
    pass


USER_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveExclusionError(f"无法读取归档排除文件：{path}") from exc


def load_archived_note_map(
    path: str | Path | None,
    *,
    expected_user_id: str | None = None,
) -> Dict[str, str]:
    if not str(path or "").strip():
        return {}
    source = Path(path)
    payload = load_json(source)
    if not isinstance(payload, dict):
        raise ArchiveExclusionError(f"归档排除文件必须是对象：{source}")

    if payload.get("contract") == "xhs-archived-notes-registry-v1":
        registry_user_id = str(payload.get("user_id") or "").strip().lower()
        if not USER_ID_RE.fullmatch(registry_user_id):
            raise ArchiveExclusionError("archive registry.user_id 无效")
        normalized_expected = str(expected_user_id or "").strip().lower()
        if normalized_expected:
            if not USER_ID_RE.fullmatch(normalized_expected):
                raise ArchiveExclusionError("当前 collection scope user_id 无效")
            if registry_user_id != normalized_expected:
                raise ArchiveExclusionError(
                    "archive registry 账号与当前 collection scope 不一致"
                )
        rows = payload.get("confirmed_archived")
        if not isinstance(rows, list):
            raise ArchiveExclusionError("archive registry.confirmed_archived 必须是数组")
        result: Dict[str, str] = {}
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ArchiveExclusionError(f"confirmed_archived[{index}] 必须是对象")
            note_id = str(row.get("id") or "").strip()
            board = str(row.get("board") or "").strip()
            if not note_id or not board or note_id in result:
                raise ArchiveExclusionError("archive registry 包含空或重复的已归档 ID")
            result[note_id] = board
        if payload.get("confirmed_archived_count") != len(result):
            raise ArchiveExclusionError("archive registry 已归档计数不一致")
        return result

    note_to_board = payload.get("note_to_board")
    excluded_ids = payload.get("excluded_note_ids")
    if not isinstance(note_to_board, dict) or not isinstance(excluded_ids, list):
        raise ArchiveExclusionError("归档排除文件既不是 registry，也不是 existing boards inventory")
    result = {
        str(note_id).strip(): str(board).strip()
        for note_id, board in note_to_board.items()
        if str(note_id).strip() and str(board).strip()
    }
    normalized_ids = [str(note_id).strip() for note_id in excluded_ids if str(note_id).strip()]
    if len(result) != len(note_to_board) or len(normalized_ids) != len(excluded_ids):
        raise ArchiveExclusionError("existing boards inventory 包含空 ID 或空专辑名")
    if len(normalized_ids) != len(set(normalized_ids)) or set(normalized_ids) != set(result):
        raise ArchiveExclusionError("existing boards inventory 的排除 ID 与映射不一致")
    return result


def combine_archived_note_maps(
    paths: Iterable[str | Path | None],
    *,
    expected_user_id: str | None = None,
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for path in paths:
        for note_id, board in load_archived_note_map(
            path,
            expected_user_id=expected_user_id,
        ).items():
            prior_board = result.get(note_id)
            if prior_board is not None and prior_board != board:
                raise ArchiveExclusionError(
                    f"同一已归档笔记在不同基线中对应不同专辑：{note_id} "
                    f"{prior_board!r} != {board!r}"
                )
            result[note_id] = board
    return result
