#!/usr/bin/env python3
"""Prepare and commit the new-note baseline for a weekly organizing run.

This is deliberately a local JSON tool. It never opens a browser, schedules a
job, creates a board, or moves a Xiaohongshu note. A caller first produces a
new-note preview, generates a complete local classification plan, then commits
the baseline only after that plan covers every newly read note.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


BASELINE_SCHEMA_VERSION = 1


def load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"找不到{label}：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}不是有效 JSON：{path}") from exc


def normalized_items(value: Any, label: str = "列表") -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{label}必须是 JSON 数组")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{label}第 {index + 1} 项必须是对象")
        note_id = item.get("id")
        if not isinstance(note_id, str) or not note_id.strip():
            raise ValueError(f"{label}第 {index + 1} 项缺少非空 id")
        note_id = note_id.strip()
        if note_id in seen:
            raise ValueError(f"{label}包含重复 id：{note_id}")
        seen.add(note_id)
        copied = dict(item)
        copied["id"] = note_id
        result.append(copied)
    return result


def load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    value = load_json(path, "每周已处理基线")
    if not isinstance(value, dict):
        raise ValueError("每周已处理基线必须是 JSON 对象")
    if value.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError("每周已处理基线 schema_version 不匹配")
    ids = value.get("processed_note_ids")
    if not isinstance(ids, list) or not all(isinstance(note_id, str) and note_id.strip() for note_id in ids):
        raise ValueError("每周已处理基线的 processed_note_ids 必须是非空字符串数组")
    if len(ids) != len(set(ids)):
        raise ValueError("每周已处理基线包含重复 note id")
    return set(ids)


def build_delta(items: list[dict[str, Any]], processed_ids: set[str]) -> dict[str, Any]:
    new_items = [item for item in items if item["id"] not in processed_ids]
    new_ids = [item["id"] for item in new_items]
    candidate_ids = sorted(processed_ids | {item["id"] for item in items})
    return {
        "schema_version": 1,
        "status": "preview_only",
        "total_read_count": len(items),
        "already_processed_count": len(items) - len(new_items),
        "new_note_count": len(new_items),
        "new_note_ids": new_ids,
        "new_items": new_items,
        "baseline_candidate": {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "processed_note_ids": candidate_ids,
        },
        "automatic_move": False,
        "next_action": "先为 new_items 生成完整 classification.json；确认覆盖后才提交基线。",
    }


def classification_ids(path: Path) -> set[str]:
    items = normalized_items(load_json(path, "分类方案"), "分类方案")
    return {item["id"] for item in items}


def commit_baseline(candidate: dict[str, Any], classification_path: Path, baseline_path: Path) -> None:
    expected_ids = set(candidate["new_note_ids"])
    actual_ids = classification_ids(classification_path)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        details: list[str] = []
        if missing:
            details.append(f"分类方案缺少 {len(missing)} 条新笔记")
        if unexpected:
            details.append(f"分类方案包含 {len(unexpected)} 条不属于本次新增的笔记")
        raise ValueError("；".join(details) or "分类方案与本次新增笔记不一致")
    write_json(baseline_path, candidate["baseline_candidate"])


def write_json(path: Path, value: Any) -> Path:
    output = path.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return output.resolve()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="筛出每周整理的新笔记；默认只生成本地预览，不打开浏览器、不移动笔记。"
    )
    parser.add_argument("visible_items", type=Path, help="本次已经读取完成的 visible_items.json")
    parser.add_argument("output", type=Path, help="写入 weekly_delta.json 的路径")
    parser.add_argument("--baseline", type=Path, required=True, help="每周已处理 note id 基线路径")
    parser.add_argument(
        "--commit-after-plan",
        type=Path,
        help="完整 classification.json 路径；给出后才写入新的基线。",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    try:
        items = normalized_items(load_json(args.visible_items, "读取列表"), "读取列表")
        candidate = build_delta(items, load_baseline(args.baseline))
        output = write_json(args.output, candidate)
        baseline_updated = False
        if args.commit_after_plan is not None:
            commit_baseline(candidate, args.commit_after_plan, args.baseline)
            baseline_updated = True
    except ValueError as exc:
        raise SystemExit(f"每周整理配置无效：{exc}") from exc

    print(json.dumps({
        "status": candidate["status"],
        "output": str(output),
        "new_note_count": candidate["new_note_count"],
        "baseline_updated": baseline_updated,
        "automatic_move": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
