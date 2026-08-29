#!/usr/bin/env python3
"""Persist only live-confirmed archived notes; keep unexecuted plans explicitly pending."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archive_exclusion import USER_ID_RE, load_archived_note_map
from audit_board_transition import audit_board_transition
from collection_scope import normalized_page_binding
from xhs_safety import atomic_write_json


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_snapshot_account(snapshot: Any, expected_user_id: str) -> str:
    user_id = str(expected_user_id or "").strip().lower()
    if not USER_ID_RE.fullmatch(user_id):
        raise ValueError("--user-id 必须是 24 位小红书账号 ID")
    source = snapshot.get("source") if isinstance(snapshot, dict) else None
    if not isinstance(source, dict):
        raise ValueError("board snapshot 缺少 source 账号绑定")
    if str(source.get("user_id") or "").strip().lower() != user_id:
        raise ValueError("board snapshot.source.user_id 与 --user-id 不一致")
    if str(source.get("live_account_user_id") or "").strip().lower() != user_id:
        raise ValueError("board snapshot live account 与 --user-id 不一致")
    page = str(source.get("live_page_binding") or source.get("expected_url_substring") or "")
    binding = normalized_page_binding(page)
    if binding["user_id"] != user_id:
        raise ValueError("board snapshot 收藏页与 --user-id 不一致")
    if source.get("writes_performed") is not False:
        raise ValueError("board snapshot 必须是只读完整成员快照")
    return user_id


def main() -> int:
    parser = argparse.ArgumentParser(description="从完整只读专辑快照生成持久归档基线，不把未执行计划标成已归档。")
    parser.add_argument("existing_inventory")
    parser.add_argument("board_snapshot")
    parser.add_argument("pending_review")
    parser.add_argument("output")
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    inventory_path = Path(args.existing_inventory)
    snapshot_path = Path(args.board_snapshot)
    pending_path = Path(args.pending_review)
    output = Path(args.output)
    if output.exists():
        raise SystemExit(f"拒绝覆盖已有归档基线：{output}")
    inventory = load_json(inventory_path)
    snapshot = load_json(snapshot_path)
    pending = load_json(pending_path)
    user_id = validate_snapshot_account(snapshot, args.user_id)
    audit_board_transition(inventory, pending, snapshot)
    confirmed_map = load_archived_note_map(inventory_path)
    pending_rows = pending.get("items") if isinstance(pending, dict) else None
    if not isinstance(pending_rows, list):
        raise SystemExit("pending review.items 必须是数组")
    registry = {
        "contract": "xhs-archived-notes-registry-v1",
        "user_id": user_id,
        "generated_at": utc_now(),
        "confirmed_archived_count": len(confirmed_map),
        "confirmed_archived": [
            {
                "id": note_id,
                "board": board,
                "archive_lifecycle_state": "first_archive_confirmed",
            }
            for note_id, board in confirmed_map.items()
        ],
        "pending_count": len(pending_rows),
        "pending_not_archived": [
            {
                "id": str(row.get("id") or ""),
                "target_board": str(row.get("target_board") or ""),
                "state": "pending_not_moved",
                "archive_lifecycle_state": "first_archive_pending",
            }
            for row in pending_rows
        ],
        "source_sha256": {
            "existing_inventory": sha256(inventory_path),
            "board_snapshot": sha256(snapshot_path),
            "pending_review": sha256(pending_path),
        },
    }
    atomic_write_json(output, registry)
    print(json.dumps({
        "output": str(output),
        "confirmed_archived_count": registry["confirmed_archived_count"],
        "pending_not_archived_count": registry["pending_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
