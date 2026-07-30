#!/usr/bin/env python3
"""Create the local, user-approved configuration for a Xiaohongshu organizing run.

This script only writes JSON.  It never opens a browser, reads Xiaohongshu,
installs software, or schedules an unattended browser action.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


SKILL_VERSION = "2.0.0"
SCHEMA_VERSION = 1
DEFAULT_STARTUP_MODE = "quick"
DEFAULT_SOURCE = "collection"
DEFAULT_OCR_ENABLED = False
DEFAULT_BATCH_SIZE = 200
MAX_BATCH_SIZE = 200
DEFAULT_PAUSE_MINUTES = 3
STARTUP_MODES = ("quick", "complete")
SOURCES = ("collection", "liked", "all")
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _normalized_choice(value: Any, choices: tuple[str, ...], field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是：{', '.join(choices)}")
    normalized = value.strip().lower()
    if normalized not in choices:
        raise ValueError(f"{field} 必须是：{', '.join(choices)}")
    return normalized


def normalize_startup_mode(value: Any) -> str:
    """Normalize the two v2.0 startup modes without silently guessing aliases."""
    return _normalized_choice(value, STARTUP_MODES, "启动方式")


def normalize_source(value: Any) -> str:
    return _normalized_choice(value, SOURCES, "整理范围")


def normalize_ocr_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"enabled", "true", "on", "1"}:
            return True
        if normalized in {"disabled", "false", "off", "0"}:
            return False
    raise ValueError("OCR 选项必须是 enabled 或 disabled")


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field}必须是正整数")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}必须是正整数") from exc
    if normalized <= 0 or str(normalized) != str(value).strip():
        raise ValueError(f"{field}必须是正整数")
    return normalized


def validate_batch_settings(batch_size: Any = DEFAULT_BATCH_SIZE, pause_minutes: Any = DEFAULT_PAUSE_MINUTES) -> tuple[int, int]:
    """Validate user-controlled reading batches; no group can exceed 200 items."""
    normalized_batch_size = _positive_int(batch_size, "每组条数")
    if normalized_batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"每组条数不能超过 {MAX_BATCH_SIZE}")
    normalized_pause_minutes = _positive_int(pause_minutes, "暂停分钟数")
    return normalized_batch_size, normalized_pause_minutes


def build_weekly_task(
    enabled: bool = False,
    weekday: Any = None,
    time: Any = None,
) -> dict[str, Any]:
    """Build a weekly task declaration with no unattended write permission."""
    if not isinstance(enabled, bool):
        raise ValueError("每周任务开关必须是 true 或 false")
    if not enabled:
        return {
            "enabled": False,
            "weekday": None,
            "time": None,
            "processed_note_ids_file": "weekly_processed_note_ids.json",
            "creates_classification_plan_only": True,
            "requires_current_browser_confirmation": True,
            "automatic_move": False,
        }

    normalized_weekday = _normalized_choice(weekday, WEEKDAYS, "每周任务星期")
    if not isinstance(time, str) or not TIME_PATTERN.fullmatch(time.strip()):
        raise ValueError("每周任务时间必须是 HH:MM（24 小时制）")
    return {
        "enabled": True,
        "weekday": normalized_weekday,
        "time": time.strip(),
        "processed_note_ids_file": "weekly_processed_note_ids.json",
        "creates_classification_plan_only": True,
        "requires_current_browser_confirmation": True,
        "automatic_move": False,
    }


def build_onboarding_config(
    *,
    startup_mode: Any = DEFAULT_STARTUP_MODE,
    source: Any = DEFAULT_SOURCE,
    ocr_enabled: Any = DEFAULT_OCR_ENABLED,
    batch_size: Any = DEFAULT_BATCH_SIZE,
    pause_minutes: Any = DEFAULT_PAUSE_MINUTES,
    expand_album_items: bool = False,
    weekly_enabled: bool = False,
    weekly_weekday: Any = None,
    weekly_time: Any = None,
) -> dict[str, Any]:
    """Build the complete v2.0 configuration before any browser work begins."""
    if not isinstance(expand_album_items, bool):
        raise ValueError("审阅展开选项必须是 true 或 false")
    normalized_batch_size, normalized_pause_minutes = validate_batch_settings(batch_size, pause_minutes)
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_version": SKILL_VERSION,
        "startup_mode": normalize_startup_mode(startup_mode),
        "source": normalize_source(source),
        "ocr_enabled": normalize_ocr_enabled(ocr_enabled),
        "collection": {
            "batch_size": normalized_batch_size,
            "hard_max_batch_size": MAX_BATCH_SIZE,
            "pause_minutes": normalized_pause_minutes,
            "auto_continue_after_pause": True,
        },
        "review": {
            "expand_album_items": expand_album_items,
        },
        "weekly_task": build_weekly_task(
            enabled=weekly_enabled,
            weekday=weekly_weekday,
            time=weekly_time,
        ),
    }


def write_config(path: Path, config: dict[str, Any]) -> Path:
    """Atomically persist a validated configuration as UTF-8 JSON."""
    output = path.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return output.resolve()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="写入小红书整理 Skill v2.0 的本地启动配置；不会访问浏览器或小红书。"
    )
    parser.add_argument("output", type=Path, help="要写入的 JSON 配置文件路径")
    parser.add_argument("--startup-mode", default=DEFAULT_STARTUP_MODE, choices=STARTUP_MODES)
    parser.add_argument("--source", default=DEFAULT_SOURCE, choices=SOURCES)
    parser.add_argument("--ocr", default="disabled", choices=("enabled", "disabled"))
    parser.add_argument("--batch-size", default=DEFAULT_BATCH_SIZE, type=int)
    parser.add_argument("--pause-minutes", default=DEFAULT_PAUSE_MINUTES, type=int)
    parser.add_argument(
        "--expand-album-items",
        action="store_true",
        help="分类方案阶段展开每个专辑里的条目；默认只显示专辑和数量。",
    )
    parser.add_argument("--weekly-enabled", action="store_true", help="启用每周整理任务配置")
    parser.add_argument("--weekly-weekday", choices=WEEKDAYS)
    parser.add_argument("--weekly-time", help="每周任务时间，格式 HH:MM（24 小时制）")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    try:
        config = build_onboarding_config(
            startup_mode=args.startup_mode,
            source=args.source,
            ocr_enabled=args.ocr,
            batch_size=args.batch_size,
            pause_minutes=args.pause_minutes,
            expand_album_items=args.expand_album_items,
            weekly_enabled=args.weekly_enabled,
            weekly_weekday=args.weekly_weekday,
            weekly_time=args.weekly_time,
        )
    except ValueError as exc:
        raise SystemExit(f"配置无效：{exc}") from exc

    output = write_config(args.output, config)
    print(json.dumps({"status": "written", "path": str(output), "config": config}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
