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


def protected_note_map_from_snapshot(
    snapshot: Any,
    registry: Any,
    *,
    expected_user_id: str,
) -> Dict[str, str]:
    """Protect current members of albums previously archived by this Skill only."""
    if registry is None:
        return {}
    if not isinstance(registry, dict) or registry.get("contract") != "xhs-skill-archive-registry-v2":
        raise ArchiveExclusionError("缺少有效的 Skill 归档登记；不会把任意已有专辑当成受保护专辑")
    user_id = str(expected_user_id or "").strip().lower()
    if not USER_ID_RE.fullmatch(user_id) or str(registry.get("user_id") or "").strip().lower() != user_id:
        raise ArchiveExclusionError("Skill 归档登记账号与当前账号不一致")
    archived_rows = registry.get("archived_boards")
    snapshot_rows = snapshot.get("boards") if isinstance(snapshot, dict) else None
    if not isinstance(archived_rows, list) or not isinstance(snapshot_rows, list):
        raise ArchiveExclusionError("Skill 归档登记或本轮专辑快照格式无效")
    if registry.get("archived_board_count") != len(archived_rows):
        raise ArchiveExclusionError("Skill 归档登记的专辑数量不一致")
    registry_note_ids = set()
    for index, archived in enumerate(archived_rows):
        note_ids = archived.get("note_ids") if isinstance(archived, dict) else None
        if not isinstance(note_ids, list):
            raise ArchiveExclusionError(f"archived_boards[{index}].note_ids 无效")
        for note_id_value in note_ids:
            note_id = str(note_id_value or "").strip().lower()
            if not USER_ID_RE.fullmatch(note_id) or note_id in registry_note_ids:
                raise ArchiveExclusionError("Skill 归档登记包含无效或重复笔记 ID")
            registry_note_ids.add(note_id)
    if registry.get("confirmed_archived_count") != len(registry_note_ids):
        raise ArchiveExclusionError("Skill 归档登记的笔记数量不一致")
    live_by_id: Dict[str, Dict[str, Any]] = {}
    live_names = set()
    live_note_ids = set()
    for row in snapshot_rows:
        board_id = str(row.get("id") or "").strip().lower() if isinstance(row, dict) else ""
        name = str(row.get("name") or "").strip() if isinstance(row, dict) else ""
        note_ids = row.get("note_ids") if isinstance(row, dict) else None
        if (
            not USER_ID_RE.fullmatch(board_id)
            or not name
            or not isinstance(note_ids, list)
            or board_id in live_by_id
            or name in live_names
        ):
            raise ArchiveExclusionError("本轮专辑快照包含无效或重复专辑")
        if row.get("declared_total") != len(note_ids) or len(note_ids) != len(set(note_ids)):
            raise ArchiveExclusionError("本轮专辑快照缺页、重复或数量不一致")
        for note_id_value in note_ids:
            note_id = str(note_id_value or "").strip().lower()
            if not USER_ID_RE.fullmatch(note_id) or note_id in live_note_ids:
                raise ArchiveExclusionError("本轮专辑快照包含无效或跨专辑重复笔记 ID")
            live_note_ids.add(note_id)
        live_by_id[board_id] = row
        live_names.add(name)
    protected: Dict[str, str] = {}
    seen_ids = set()
    seen_names = set()
    for index, archived in enumerate(archived_rows):
        board_id = str(archived.get("id") or "").strip().lower() if isinstance(archived, dict) else ""
        name = str(archived.get("name") or "").strip() if isinstance(archived, dict) else ""
        if (
            not USER_ID_RE.fullmatch(board_id)
            or not name
            or board_id in seen_ids
            or name in seen_names
        ):
            raise ArchiveExclusionError(f"archived_boards[{index}] 无效或重复")
        seen_ids.add(board_id)
        seen_names.add(name)
        live = live_by_id.get(board_id)
        if live is None or str(live.get("name") or "").strip() != name:
            raise ArchiveExclusionError(f"已归档专辑身份变化或缺失：{name}")
        for note_id_value in live.get("note_ids") or []:
            note_id = str(note_id_value or "").strip().lower()
            if not USER_ID_RE.fullmatch(note_id) or note_id in protected:
                raise ArchiveExclusionError("已归档专辑的本轮成员包含无效或重复 ID")
            protected[note_id] = name
    return protected


def combine_live_protected_note_maps(
    registry_paths: Iterable[str | Path | None],
    *,
    board_snapshot_path: str | Path | None,
    expected_user_id: str | None = None,
) -> Dict[str, str]:
    """Resolve protection from registered album identities and one live snapshot."""
    paths = [Path(path) for path in registry_paths if str(path or "").strip()]
    if not paths:
        return {}
    if not str(board_snapshot_path or "").strip():
        raise ArchiveExclusionError(
            "使用 Skill 归档登记时必须同时提供本轮完整 board_snapshot.json；"
            "不能只按旧 note id 或全部收藏猜保护范围"
        )
    snapshot = load_json(Path(board_snapshot_path))
    source = snapshot.get("source") if isinstance(snapshot, dict) else None
    snapshot_user_id = ""
    if isinstance(source, dict):
        snapshot_user_id = str(
            source.get("live_account_user_id") or source.get("user_id") or ""
        ).strip().lower()
    user_id = str(expected_user_id or snapshot_user_id).strip().lower()
    if not USER_ID_RE.fullmatch(user_id):
        raise ArchiveExclusionError("board_snapshot.json 缺少有效账号 id")
    if snapshot_user_id and snapshot_user_id != user_id:
        raise ArchiveExclusionError("board_snapshot.json 账号与当前范围不一致")

    result: Dict[str, str] = {}
    for path in paths:
        registry = load_json(path)
        current = protected_note_map_from_snapshot(
            snapshot,
            registry,
            expected_user_id=user_id,
        )
        for note_id, board in current.items():
            previous = result.get(note_id)
            if previous is not None and previous != board:
                raise ArchiveExclusionError(
                    f"同一实时成员被多个登记专辑保护：{note_id}"
                )
            result[note_id] = board
    return result
