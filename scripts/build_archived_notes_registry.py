#!/usr/bin/env python3
"""Create an immutable registry for albums actually completed by this Skill."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from archive_exclusion import ArchiveExclusionError, USER_ID_RE, protected_note_map_from_snapshot
from collection_scope import normalized_page_binding
from xhs_safety import atomic_write_json


CONTRACT = "xhs-skill-archive-registry-v2"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_snapshot(snapshot: Any, user_id: str) -> List[Dict[str, Any]]:
    if not isinstance(snapshot, dict):
        raise ValueError("post board snapshot 必须是对象")
    source = snapshot.get("source")
    validation = snapshot.get("validation")
    boards = snapshot.get("boards")
    if not isinstance(source, dict) or source.get("writes_performed") is not False:
        raise ValueError("post board snapshot 必须是写入后的完整只读回读")
    if (
        str(source.get("user_id") or "").strip().lower() != user_id
        or str(source.get("live_account_user_id") or "").strip().lower() != user_id
    ):
        raise ValueError("post board snapshot 账号与 --user-id 不一致")
    binding = normalized_page_binding(
        str(source.get("live_page_binding") or source.get("expected_url_substring") or "")
    )
    if binding["user_id"] != user_id:
        raise ValueError("post board snapshot 页面与 --user-id 不一致")
    if not isinstance(validation, dict) or validation.get("full_membership_complete") is not True:
        raise ValueError("post board snapshot 没有证明完整专辑成员关系")
    if not isinstance(boards, list):
        raise ValueError("post board snapshot.boards 必须是数组")
    ids = set()
    names = set()
    note_ids = set()
    normalized = []
    for index, row in enumerate(boards):
        board_id = str(row.get("id") or "").strip().lower() if isinstance(row, dict) else ""
        name = str(row.get("name") or "").strip() if isinstance(row, dict) else ""
        members = row.get("note_ids") if isinstance(row, dict) else None
        if (
            not USER_ID_RE.fullmatch(board_id)
            or not name
            or not isinstance(members, list)
            or board_id in ids
            or name in names
        ):
            raise ValueError(f"post board snapshot.boards[{index}] 无效或重复")
        clean_members = [str(value or "").strip().lower() for value in members]
        if (
            row.get("declared_total") != len(clean_members)
            or any(not USER_ID_RE.fullmatch(value) for value in clean_members)
            or len(clean_members) != len(set(clean_members))
            or note_ids.intersection(clean_members)
        ):
            raise ValueError(f"post board snapshot 专辑 {name} 缺页、重复或数量不一致")
        ids.add(board_id)
        names.add(name)
        note_ids.update(clean_members)
        normalized.append({"id": board_id, "name": name, "note_ids": clean_members})
    return normalized


def build_registry(
    run_report: Any,
    snapshot: Any,
    *,
    user_id: str,
    previous: Any = None,
) -> Dict[str, Any]:
    clean_user_id = str(user_id or "").strip().lower()
    if not USER_ID_RE.fullmatch(clean_user_id):
        raise ValueError("--user-id 必须是 24 位小红书账号 ID")
    boards = validate_snapshot(snapshot, clean_user_id)
    if not isinstance(run_report, dict) or run_report.get("mode") != "execute":
        raise ValueError("run_report 必须是本次 Skill 的 execute 报告")
    if run_report.get("errors"):
        raise ValueError("run_report 含错误，不能登记为整理完成")
    processed = run_report.get("processed")
    if not isinstance(processed, list):
        raise ValueError("run_report.processed 必须是数组")
    completed_rows = [
        row for row in processed
        if isinstance(row, dict)
        and row.get("status") in {"success", "already_in_target"}
        and row.get("verified") is True
        and row.get("archive_lifecycle_state") == "first_archive_confirmed"
    ]
    if not completed_rows:
        raise ValueError("本次报告没有实时回读确认的归档结果")
    board_by_name = {row["name"]: row for row in boards}
    archive_names = set()
    for row in completed_rows:
        note_id = str(row.get("id") or "").strip().lower()
        target = str(row.get("target_board") or "").strip()
        board = board_by_name.get(target)
        if not USER_ID_RE.fullmatch(note_id) or board is None or note_id not in board["note_ids"]:
            raise ValueError("成功行没有在 post snapshot 的目标专辑中得到确认")
        archive_names.add(target)

    previous_ids = set()
    if previous is not None:
        protected_note_map_from_snapshot(
            snapshot,
            previous,
            expected_user_id=clean_user_id,
        )
        previous_ids = {
            str(row.get("id") or "").strip().lower()
            for row in previous.get("archived_boards") or []
        }
    archived = [
        row for row in boards
        if row["name"] in archive_names or row["id"] in previous_ids
    ]
    all_members = [note_id for board in archived for note_id in board["note_ids"]]
    if len(all_members) != len(set(all_members)):
        raise ValueError("归档专辑之间出现重复笔记，已停止")
    return {
        "contract": CONTRACT,
        "user_id": clean_user_id,
        "generated_at": utc_now(),
        "archived_board_count": len(archived),
        "archived_boards": archived,
        "confirmed_archived_count": len(all_members),
        "completed_run_item_count": len(completed_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 Skill execute 报告和完整 post snapshot 登记已完成归档专辑。"
    )
    parser.add_argument("run_report")
    parser.add_argument("post_board_snapshot")
    parser.add_argument("output")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--previous-registry", default="")
    args = parser.parse_args()
    report_path = Path(args.run_report)
    snapshot_path = Path(args.post_board_snapshot)
    output = Path(args.output)
    if output.exists():
        raise SystemExit(f"拒绝覆盖已有归档登记：{output}")
    previous_path = Path(args.previous_registry) if args.previous_registry else None
    try:
        payload = build_registry(
            load_json(report_path),
            load_json(snapshot_path),
            user_id=args.user_id,
            previous=load_json(previous_path) if previous_path else None,
        )
    except (OSError, json.JSONDecodeError, ValueError, ArchiveExclusionError) as exc:
        raise SystemExit(str(exc)) from exc
    payload["source_sha256"] = {
        "run_report": sha256(report_path),
        "post_board_snapshot": sha256(snapshot_path),
        "previous_registry": sha256(previous_path) if previous_path else "",
    }
    atomic_write_json(output, payload)
    print(json.dumps({
        "output": str(output),
        "archived_board_count": payload["archived_board_count"],
        "confirmed_archived_count": payload["confirmed_archived_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
