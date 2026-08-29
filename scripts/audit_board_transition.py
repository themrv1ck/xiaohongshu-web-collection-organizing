#!/usr/bin/env python3
"""Audit that a board move preserves every existing member and adds reviewed items exactly once."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from xhs_safety import atomic_write_json


class BoardTransitionError(ValueError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoardTransitionError(f"无法读取 JSON：{path}") from exc


def require_unique_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise BoardTransitionError(f"{label} 必须是非空字符串数组")
    result = [item.strip() for item in value]
    if len(result) != len(set(result)):
        raise BoardTransitionError(f"{label} 包含重复值")
    return result


def normalize_snapshot(snapshot: Any, label: str) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise BoardTransitionError(f"{label} 必须是对象")
    boards = snapshot.get("boards")
    if not isinstance(boards, list) or not boards:
        raise BoardTransitionError(f"{label}.boards 必须是非空数组")
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    membership: dict[str, list[dict[str, str]]] = defaultdict(list)
    occurrence_count = 0
    for index, board in enumerate(boards):
        if not isinstance(board, dict):
            raise BoardTransitionError(f"{label}.boards[{index}] 必须是对象")
        board_id = str(board.get("id") or "").strip()
        name = str(board.get("name") or "").strip()
        if not board_id or not name or board_id in by_id or name in by_name:
            raise BoardTransitionError(f"{label} 包含空或重复的专辑身份")
        note_ids = require_unique_strings(board.get("note_ids"), f"{label}.{name}.note_ids")
        declared = board.get("declared_total")
        accessible = board.get("accessible_unique_count")
        if declared != len(note_ids) or accessible != len(note_ids):
            raise BoardTransitionError(f"{label} 专辑计数与可访问成员不一致：{name}")
        if board.get("declared_vs_accessible_delta") != 0:
            raise BoardTransitionError(f"{label} 专辑 declared delta 非零：{name}")
        normalized = {
            "id": board_id,
            "name": name,
            "privacy": board.get("privacy"),
            "note_ids": set(note_ids),
        }
        by_id[board_id] = normalized
        by_name[name] = normalized
        occurrence_count += len(note_ids)
        for note_id in note_ids:
            membership[note_id].append({"board_id": board_id, "board_name": name})
    duplicates = sorted(note_id for note_id, refs in membership.items() if len(refs) != 1)
    if duplicates:
        raise BoardTransitionError(f"{label} 存在全局重复专辑成员：{duplicates}")
    validation = snapshot.get("validation")
    if not isinstance(validation, dict) or validation.get("full_membership_complete") is not True:
        raise BoardTransitionError(f"{label} 没有完整成员快照证明")
    if validation.get("board_names_unique") is not True:
        raise BoardTransitionError(f"{label} 专辑名不唯一")
    if validation.get("pagination_cursor_invariants_passed") is not True:
        raise BoardTransitionError(f"{label} 分页游标校验失败")
    if validation.get("duplicate_note_ids") not in ([], None):
        raise BoardTransitionError(f"{label} validation 报告重复成员")
    if validation.get("multi_board_note_ids") not in ([], None):
        raise BoardTransitionError(f"{label} validation 报告跨专辑重复成员")
    if validation.get("within_board_duplicates") not in ([], None):
        raise BoardTransitionError(f"{label} validation 报告板内重复成员")
    if validation.get("count_mismatch_boards") not in ([], None):
        raise BoardTransitionError(f"{label} validation 报告计数不一致")
    if validation.get("board_count") != len(boards):
        raise BoardTransitionError(f"{label} validation.board_count 不一致")
    if validation.get("accessible_note_occurrences") != occurrence_count:
        raise BoardTransitionError(f"{label} validation 成员 occurrence 计数不一致")
    if validation.get("accessible_unique_note_ids_across_boards") != len(membership):
        raise BoardTransitionError(f"{label} validation 唯一成员计数不一致")
    return {
        "source": snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {},
        "by_id": by_id,
        "by_name": by_name,
        "membership": dict(membership),
        "board_count": len(boards),
        "occurrence_count": occurrence_count,
        "unique_count": len(membership),
    }


def validate_binding(
    source: dict[str, Any],
    *,
    browser: str,
    user_id: str,
    url: str,
    verify_pages: int,
    safety_state: str,
    window_id: str,
    tab_id: str,
    tab_marker: str,
) -> None:
    expected = {
        "browser": browser,
        "user_id": user_id,
        "live_account_user_id": user_id,
        "expected_url_substring": url,
        "live_page_binding": url,
        "verify_pages": verify_pages,
        "safety_state": safety_state,
        "window_id": window_id,
        "tab_id": tab_id,
        "tab_marker": tab_marker,
        "writes_performed": False,
    }
    mismatches = {
        key: {"expected": value, "actual": source.get(key)}
        for key, value in expected.items()
        if source.get(key) != value
    }
    if mismatches:
        raise BoardTransitionError(f"快照 Arc 绑定不精确：{mismatches}")


def audit_board_transition(
    inventory: Any,
    review: Any,
    pre_snapshot: Any,
    post_snapshot: Optional[Any] = None,
    *,
    binding: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not isinstance(inventory, dict) or not isinstance(review, dict):
        raise BoardTransitionError("inventory 和 review 必须是对象")
    inventory_boards = require_unique_strings(inventory.get("boards"), "inventory.boards")
    note_to_board = inventory.get("note_to_board")
    if not isinstance(note_to_board, dict):
        raise BoardTransitionError("inventory.note_to_board 必须是对象")
    excluded = require_unique_strings(inventory.get("excluded_note_ids"), "inventory.excluded_note_ids")
    if set(excluded) != set(note_to_board):
        raise BoardTransitionError("inventory excluded IDs 与 note_to_board 不一致")
    review_items = review.get("items")
    if not isinstance(review_items, list):
        raise BoardTransitionError("review.items 必须是数组")
    reviewed: dict[str, str] = {}
    for index, row in enumerate(review_items):
        if not isinstance(row, dict):
            raise BoardTransitionError(f"review.items[{index}] 必须是对象")
        note_id = str(row.get("id") or "").strip()
        target = str(row.get("target_board") or "").strip()
        if not note_id or note_id in reviewed or target not in inventory_boards:
            raise BoardTransitionError("review 包含空、重复 ID 或未知目标专辑")
        reviewed[note_id] = target
    if set(reviewed) & set(note_to_board):
        raise BoardTransitionError("review 与现有专辑成员有交集")

    pre = normalize_snapshot(pre_snapshot, "pre")
    if binding:
        validate_binding(pre["source"], **binding)
    if set(pre["by_name"]) != set(inventory_boards):
        raise BoardTransitionError("pre 的专辑名集合与 inventory 不一致")
    pre_note_to_board = {
        note_id: refs[0]["board_name"] for note_id, refs in pre["membership"].items()
    }
    if pre_note_to_board != {str(key): str(value) for key, value in note_to_board.items()}:
        raise BoardTransitionError("pre 成员与 inventory.note_to_board 不完全一致")
    present_reviewed = sorted(set(reviewed) & set(pre["membership"]))
    if present_reviewed:
        raise BoardTransitionError(f"待移动条目在 pre 已属于专辑：{present_reviewed}")

    report = {
        "contract": "xhs-board-transition-audit-v1",
        "passed": True,
        "phase": "preflight" if post_snapshot is None else "post_move",
        "pre": {
            "board_count": pre["board_count"],
            "existing_unique_members": pre["unique_count"],
            "existing_occurrences": pre["occurrence_count"],
            "reviewed_absent": len(reviewed),
        },
        "reviewed_count": len(reviewed),
        "target_counts": dict(sorted(Counter(reviewed.values()).items())),
    }
    if post_snapshot is None:
        return report

    post = normalize_snapshot(post_snapshot, "post")
    if binding:
        validate_binding(post["source"], **binding)
    pre_identity = {board_id: (row["name"], row["privacy"]) for board_id, row in pre["by_id"].items()}
    post_identity = {board_id: (row["name"], row["privacy"]) for board_id, row in post["by_id"].items()}
    if post_identity != pre_identity:
        raise BoardTransitionError("post 的专辑 ID、名称或隐私属性发生变化")
    reviewed_by_board: dict[str, set[str]] = defaultdict(set)
    for note_id, target in reviewed.items():
        reviewed_by_board[target].add(note_id)
    for name, pre_board in pre["by_name"].items():
        expected = pre_board["note_ids"] | reviewed_by_board.get(name, set())
        if post["by_name"][name]["note_ids"] != expected:
            raise BoardTransitionError(f"post 专辑成员集合不等于 pre 加本次目标：{name}")
    expected_total = len(note_to_board) + len(reviewed)
    if post["unique_count"] != expected_total or post["occurrence_count"] != expected_total:
        raise BoardTransitionError("post 总成员不等于现有成员加本次复核条目")
    for note_id, target in reviewed.items():
        refs = post["membership"].get(note_id, [])
        if len(refs) != 1 or refs[0]["board_name"] != target:
            raise BoardTransitionError(f"复核条目没有全局恰好一次进入目标专辑：{note_id}")
    report["post"] = {
        "board_count": post["board_count"],
        "unique_members": post["unique_count"],
        "occurrences": post["occurrence_count"],
        "existing_members_preserved": len(note_to_board),
        "reviewed_exactly_once_in_target": len(reviewed),
        "board_identity_unchanged": True,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="严格核验移动前后专辑集合等式与 Arc 精确绑定。")
    parser.add_argument("inventory")
    parser.add_argument("review")
    parser.add_argument("pre_snapshot")
    parser.add_argument("--post-snapshot", default="")
    parser.add_argument("--report", required=True)
    parser.add_argument("--browser", default="arc")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--expected-url", required=True)
    parser.add_argument("--verify-pages", type=int, required=True)
    parser.add_argument("--safety-state", required=True)
    parser.add_argument("--arc-window-id", required=True)
    parser.add_argument("--arc-tab-id", required=True)
    parser.add_argument("--arc-tab-marker", required=True)
    args = parser.parse_args()
    report_path = Path(args.report)
    if report_path.exists():
        raise SystemExit(f"拒绝覆盖已有审计报告：{report_path}")
    report = audit_board_transition(
        load_json(Path(args.inventory)),
        load_json(Path(args.review)),
        load_json(Path(args.pre_snapshot)),
        load_json(Path(args.post_snapshot)) if args.post_snapshot else None,
        binding={
            "browser": args.browser,
            "user_id": args.user_id,
            "url": args.expected_url,
            "verify_pages": args.verify_pages,
            "safety_state": args.safety_state,
            "window_id": args.arc_window_id,
            "tab_id": args.arc_tab_id,
            "tab_marker": args.arc_tab_marker,
        },
    )
    atomic_write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
