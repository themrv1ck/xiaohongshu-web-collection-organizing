#!/usr/bin/env python3
"""Build and verify an explicit, audited Xiaohongshu collection scope.

The normal collection contract is a full collection: declared count, note IDs,
and page indexes must agree exactly. A user may instead explicitly limit a run
to the currently accessible note-ID set. A later passive capture may be joined
only as an explicit incremental union with that confirmed base; it never
claims the current declared page count as complete coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


SCOPE_SCHEMA_VERSION = 1
FULL_COLLECTION = "full_collection"
USER_CONFIRMED_ACCESSIBLE = "user_confirmed_accessible_collection"
INCREMENTAL = "incremental"
NOTE_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
SEGMENT_RE = re.compile(r"^segment-(\d+)-collection\.json$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class CollectionScopeError(ValueError):
    """Raised when a capture or a downstream input no longer matches scope."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectionScopeError(f"{label} 无法读取：{path.name}") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def normalized_page_binding(location: str) -> dict[str, str]:
    parsed = urlsplit(str(location or ""))
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"www.xiaohongshu.com", "xiaohongshu.com"}:
        raise CollectionScopeError("采集页面不是 https://www.xiaohongshu.com")
    match = re.fullmatch(r"/user/profile/([0-9a-f]{24})", parsed.path, re.IGNORECASE)
    if not match:
        raise CollectionScopeError("采集页面不是已绑定的个人收藏页")
    tab = (parse_qs(parsed.query).get("tab") or [""])[0]
    if tab != "fav":
        raise CollectionScopeError("采集页面不是收藏 tab=fav")
    return {
        "origin": "https://www.xiaohongshu.com",
        "path": parsed.path,
        "user_id": match.group(1).lower(),
        "tab": "fav",
    }


def validated_page_binding(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise CollectionScopeError(f"{label} 必须是页面绑定对象")
    expected_keys = {"origin", "path", "user_id", "tab"}
    if set(value) != expected_keys:
        raise CollectionScopeError(f"{label} 字段不完整")
    origin = str(value.get("origin") or "")
    path = str(value.get("path") or "")
    user_id = str(value.get("user_id") or "").lower()
    tab = str(value.get("tab") or "")
    if origin != "https://www.xiaohongshu.com" or tab != "fav" or not NOTE_ID_RE.fullmatch(user_id):
        raise CollectionScopeError(f"{label} 无效")
    expected_path = f"/user/profile/{user_id}"
    if path.lower() != expected_path:
        raise CollectionScopeError(f"{label} 无效")
    return {"origin": origin, "path": expected_path, "user_id": user_id, "tab": tab}


def _segment_entries(run_dir: Path) -> list[tuple[int, Path, Path]]:
    entries: list[tuple[int, Path, Path]] = []
    for path in run_dir.iterdir():
        match = SEGMENT_RE.fullmatch(path.name)
        if not match:
            continue
        token = match.group(1)
        entries.append((int(token), path, run_dir / f"segment-{token}-manifest.json"))
    entries.sort(key=lambda row: row[0])
    if not entries:
        raise CollectionScopeError("没有找到 passive collection 分段")
    expected = list(range(1, len(entries) + 1))
    actual = [index for index, _collection, _manifest in entries]
    if actual != expected:
        raise CollectionScopeError("分段编号必须从 001 起连续，不能跳号")
    return entries


def _require_bool(value: Any, label: str, expected: bool) -> None:
    if value is not expected:
        raise CollectionScopeError(f"{label} 必须为 {str(expected).lower()}")


def _require_nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CollectionScopeError(f"{label} 必须是非负整数")
    return value


def _require_sha256(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(digest):
        raise CollectionScopeError(f"{label} 必须是 sha256")
    return digest


def _validate_manifest(
    manifest: dict[str, Any],
    *,
    collection_path: Path,
    safety_path: Path,
    source: str,
) -> tuple[int, str, dict[str, str]]:
    if manifest.get("capture_mode") != "passive":
        raise CollectionScopeError("范围只能由 passive 分段建立")
    if manifest.get("segment_limit") != 200:
        raise CollectionScopeError("范围分段必须使用固定上限 200")
    _require_bool(manifest.get("auto_scroll"), "manifest.auto_scroll", False)
    _require_bool(manifest.get("auto_continue"), "manifest.auto_continue", False)
    if manifest.get("source") != source:
        raise CollectionScopeError("各分段 source 不一致")
    if Path(str(manifest.get("output") or "")).resolve() != collection_path.resolve():
        raise CollectionScopeError("manifest 输出文件绑定不一致")
    if Path(str(manifest.get("safety_state") or "")).resolve() != safety_path.resolve():
        raise CollectionScopeError("manifest 安全状态绑定不一致")
    page = manifest.get("page")
    if not isinstance(page, dict):
        raise CollectionScopeError("manifest 缺少页面元数据")
    declared = page.get("declaredItemCount")
    location = page.get("location")
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 0:
        raise CollectionScopeError("页面声明数量无效")
    if not isinstance(location, str) or not location:
        raise CollectionScopeError("页面地址无效")
    binding = normalized_page_binding(location)
    return declared, location, binding


def _canonicalize_segments(run_dir: Path, source: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    safety_path = (run_dir / "xhs_safety_state.json").resolve()
    state = read_json(safety_path, "共享安全状态")
    if not isinstance(state, dict) or state.get("state") != "active" or state.get("security_halted") is True:
        raise CollectionScopeError("共享安全状态不是 active，拒绝建立范围")
    safety_sha256 = sha256_file(safety_path)

    by_position: dict[int, dict[str, Any]] = {}
    id_to_position: dict[str, int] = {}
    declared_values: set[int] = set()
    locations: set[str] = set()
    bindings: set[tuple[tuple[str, str], ...]] = set()
    evidence: list[dict[str, Any]] = []

    for _index, collection_path, manifest_path in _segment_entries(run_dir):
        if not manifest_path.is_file():
            raise CollectionScopeError(f"缺少分段 manifest：{manifest_path.name}")
        rows = read_json(collection_path, "采集分段")
        manifest = read_json(manifest_path, "采集 manifest")
        if not isinstance(rows, list) or not isinstance(manifest, dict):
            raise CollectionScopeError("分段或 manifest 顶层格式无效")
        declared, location, binding = _validate_manifest(
            manifest,
            collection_path=collection_path,
            safety_path=safety_path,
            source=source,
        )
        for key in ("count", "item_count", "newly_seen_count"):
            if manifest.get(key) != len(rows):
                raise CollectionScopeError(f"manifest.{key} 与分段行数不一致")
        observed_card_count = manifest.get("observed_card_count")
        if not isinstance(observed_card_count, int) or observed_card_count < len(rows):
            raise CollectionScopeError("manifest.observed_card_count 与分段行数不一致")
        if manifest.get("existing_count") != 0:
            raise CollectionScopeError("独立 passive 分段不能引用已有合并行")
        declared_values.add(declared)
        locations.add(location)
        bindings.add(tuple(sorted(binding.items())))
        evidence.append({
            "collection_file": collection_path.name,
            "collection_sha256": sha256_file(collection_path),
            "manifest_file": manifest_path.name,
            "manifest_sha256": sha256_file(manifest_path),
        })

        for row in rows:
            if not isinstance(row, dict):
                raise CollectionScopeError("分段含有非对象笔记行")
            note_id = str(row.get("id") or "").strip().lower()
            position = row.get("page_index")
            if not NOTE_ID_RE.fullmatch(note_id):
                raise CollectionScopeError("分段含有无效 note id")
            if not isinstance(position, int) or isinstance(position, bool) or not 0 <= position < declared:
                raise CollectionScopeError("分段含有无效 page_index")
            if row.get("source_primary") != source or row.get("source_lists") != [source]:
                raise CollectionScopeError("分段笔记来源标签不一致")
            old_position = id_to_position.get(note_id)
            if old_position is not None and old_position != position:
                raise CollectionScopeError("同一 note id 出现在多个 page_index")
            old_row = by_position.get(position)
            if old_row is not None and str(old_row.get("id") or "").lower() != note_id:
                raise CollectionScopeError("同一 page_index 出现多个 note id")
            id_to_position[note_id] = position
            if old_row is None:
                copied = dict(row)
                copied["id"] = note_id
                by_position[position] = copied
            else:
                for key, value in row.items():
                    if old_row.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                        old_row[key] = value

    if len(declared_values) != 1 or len(locations) != 1 or len(bindings) != 1:
        raise CollectionScopeError("各 passive 分段的页面绑定或声明数量发生变化")
    declared_count = next(iter(declared_values))
    items = [by_position[position] for position in sorted(by_position)]
    return items, {
        "declared_count": declared_count,
        "location": next(iter(locations)),
        "page_binding": dict(next(iter(bindings))),
        "safety_path": str(safety_path),
        "safety_sha256": safety_sha256,
        "safety_session_id": str(state.get("session_id") or ""),
        "evidence": evidence,
    }


def build_collection_scope(
    *,
    run_dir: Path | str,
    visible_out: Path | str,
    scope_out: Path | str,
    scope_kind: str,
    expected_accessible_count: int | None = None,
    expected_unidentified_count: int | None = None,
    base_scope: Path | str | None = None,
    expected_delta_count: int | None = None,
    source: str = "收藏",
) -> dict[str, Any]:
    if scope_kind == INCREMENTAL:
        return build_incremental_collection_scope(
            run_dir=run_dir,
            visible_out=visible_out,
            scope_out=scope_out,
            base_scope=base_scope,
            expected_delta_count=expected_delta_count,
            source=source,
        )
    run_path = Path(run_dir).resolve()
    visible_path = Path(visible_out).resolve()
    scope_path = Path(scope_out).resolve()
    if visible_path.exists() or scope_path.exists():
        raise CollectionScopeError("输出已存在；拒绝覆盖既有范围或输入")
    if scope_kind not in {FULL_COLLECTION, USER_CONFIRMED_ACCESSIBLE}:
        raise CollectionScopeError("scope_kind 无效")
    if base_scope is not None or expected_delta_count is not None:
        raise CollectionScopeError("只有 incremental 范围可以传 base_scope 或 expected_delta_count")
    if not isinstance(expected_accessible_count, int) or isinstance(expected_accessible_count, bool) or expected_accessible_count < 1:
        raise CollectionScopeError("expected_accessible_count 必须是正整数")
    if scope_kind == USER_CONFIRMED_ACCESSIBLE:
        if not isinstance(expected_unidentified_count, int) or isinstance(expected_unidentified_count, bool) or expected_unidentified_count < 0:
            raise CollectionScopeError("用户确认的可访问范围必须显式给出 unresolved UI count")
    elif expected_unidentified_count not in {None, 0}:
        raise CollectionScopeError("全量范围不能声明 unresolved UI count")

    items, meta = _canonicalize_segments(run_path, source)
    accessible_count = len(items)
    if accessible_count != expected_accessible_count:
        raise CollectionScopeError("实际可访问 note id 数与用户确认范围不一致")
    positions = [item.get("page_index") for item in items]
    if positions != list(range(accessible_count)):
        raise CollectionScopeError("可访问范围的 page_index 必须精确覆盖 0..N-1")
    declared_count = int(meta["declared_count"])
    unresolved_count = declared_count - accessible_count
    if unresolved_count < 0:
        raise CollectionScopeError("可访问 note id 数不能大于页面声明数")
    if scope_kind == FULL_COLLECTION and unresolved_count != 0:
        raise CollectionScopeError("全量收藏范围声明数与可访问 note id 数不一致")
    if scope_kind == USER_CONFIRMED_ACCESSIBLE and unresolved_count != expected_unidentified_count:
        raise CollectionScopeError("未识别 UI 计数与用户确认值不一致")

    note_ids = [str(item["id"]) for item in items]
    atomic_write_json(visible_path, items)
    scope = {
        "schema_version": SCOPE_SCHEMA_VERSION,
        "scope_kind": scope_kind,
        "source": source,
        "user_confirmation_required": scope_kind == USER_CONFIRMED_ACCESSIBLE,
        "declared_count": declared_count,
        "accessible_count": accessible_count,
        "unidentified_count": unresolved_count,
        "note_ids": note_ids,
        "note_ids_sha256": canonical_hash(note_ids),
        "visible_items_file": visible_path.name,
        "visible_items_sha256": sha256_file(visible_path),
        "page_index_coverage": {
            "start": 0,
            "end": accessible_count - 1,
            "count": accessible_count,
            "complete": True,
        },
        "page_binding": meta["page_binding"],
        "safety_state": meta["safety_path"],
        "safety_session_id": meta["safety_session_id"],
        "segment_evidence": meta["evidence"],
        "created_at": utc_now(),
    }
    atomic_write_json(scope_path, scope)
    return scope


def _normalized_note_ids(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CollectionScopeError(f"{label} 必须是非空 note id 数组")
    normalized = [str(note_id or "").strip().lower() for note_id in value]
    if any(not NOTE_ID_RE.fullmatch(note_id) for note_id in normalized) or len(normalized) != len(set(normalized)):
        raise CollectionScopeError(f"{label} 无效或重复")
    return normalized


def _validate_incremental_scope(scope: dict[str, Any], note_ids: list[str]) -> None:
    """Validate the self-contained evidence for an incremental union scope."""
    if scope.get("user_confirmation_required") is not True:
        raise CollectionScopeError("incremental collection_scope 必须绑定用户确认的 base scope")
    if scope.get("coverage_claim") != "incremental_union_not_full_collection":
        raise CollectionScopeError("incremental collection_scope 不能声明当前收藏为全量")
    if scope.get("base_scope_kind") != USER_CONFIRMED_ACCESSIBLE:
        raise CollectionScopeError("incremental collection_scope base scope 类型无效")
    if not isinstance(scope.get("base_scope_file"), str) or not scope.get("base_scope_file"):
        raise CollectionScopeError("incremental collection_scope 缺少 base scope 文件")
    _require_sha256(scope.get("base_scope_sha256"), "incremental base_scope_sha256")
    if not isinstance(scope.get("base_visible_items_file"), str) or not scope.get("base_visible_items_file"):
        raise CollectionScopeError("incremental collection_scope 缺少 base visible_items 文件")
    _require_sha256(scope.get("base_visible_items_sha256"), "incremental base_visible_items_sha256")

    base_ids = _normalized_note_ids(scope.get("base_note_ids"), "incremental base_note_ids")
    delta_ids = _normalized_note_ids(scope.get("delta_note_ids"), "incremental delta_note_ids")
    base_count = _require_nonnegative_int(scope.get("base_count"), "incremental base_count")
    delta_count = _require_nonnegative_int(scope.get("delta_count"), "incremental delta_count")
    union_count = _require_nonnegative_int(scope.get("union_count"), "incremental union_count")
    if base_count != len(base_ids) or delta_count != len(delta_ids) or union_count != len(note_ids):
        raise CollectionScopeError("incremental collection_scope base/delta/union 计数不一致")
    if note_ids != delta_ids + base_ids:
        raise CollectionScopeError("incremental collection_scope union 顺序必须是 delta 后接 base")
    if set(base_ids).intersection(delta_ids):
        raise CollectionScopeError("incremental collection_scope delta note id 不能与 base 重叠")
    if _require_sha256(scope.get("base_note_ids_sha256"), "incremental base_note_ids_sha256") != canonical_hash(base_ids):
        raise CollectionScopeError("incremental collection_scope base note id 哈希不一致")
    if _require_sha256(scope.get("delta_note_ids_sha256"), "incremental delta_note_ids_sha256") != canonical_hash(delta_ids):
        raise CollectionScopeError("incremental collection_scope delta note id 哈希不一致")

    current_declared = _require_nonnegative_int(
        scope.get("current_declared_count"), "incremental current_declared_count"
    )
    current_unidentified = _require_nonnegative_int(
        scope.get("current_unidentified_count"), "incremental current_unidentified_count"
    )
    if current_declared != union_count + current_unidentified:
        raise CollectionScopeError("incremental collection_scope 当前页面诊断计数不一致")

    current_binding = validated_page_binding(scope.get("current_page_binding"), "incremental current_page_binding")
    page_binding = validated_page_binding(scope.get("page_binding"), "incremental page_binding")
    if current_binding != page_binding:
        raise CollectionScopeError("incremental collection_scope 当前页面绑定不一致")
    current_safety = scope.get("current_safety_state")
    if not isinstance(current_safety, dict) or current_safety.get("state") != "active":
        raise CollectionScopeError("incremental collection_scope 当前安全状态不是 active")
    if not isinstance(current_safety.get("path"), str) or not current_safety.get("path"):
        raise CollectionScopeError("incremental collection_scope 缺少当前安全状态路径")
    _require_sha256(current_safety.get("sha256"), "incremental 当前安全状态 sha256")
    if not isinstance(current_safety.get("session_id"), str) or not current_safety.get("session_id"):
        raise CollectionScopeError("incremental collection_scope 缺少当前安全会话")

    evidence = scope.get("delta_segment_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise CollectionScopeError("incremental collection_scope 缺少 delta passive 分段证据")
    for index, entry in enumerate(evidence, start=1):
        if not isinstance(entry, dict):
            raise CollectionScopeError("incremental collection_scope delta 分段证据无效")
        for file_key, hash_key in (
            ("collection_file", "collection_sha256"),
            ("manifest_file", "manifest_sha256"),
        ):
            if not isinstance(entry.get(file_key), str) or not entry.get(file_key):
                raise CollectionScopeError(f"incremental delta 分段 {index} 缺少 {file_key}")
            _require_sha256(entry.get(hash_key), f"incremental delta 分段 {index} {hash_key}")


def load_scope(path: Path | str) -> dict[str, Any]:
    scope_path = Path(path)
    scope = read_json(scope_path, "collection_scope")
    if not isinstance(scope, dict):
        raise CollectionScopeError("collection_scope 顶层必须是对象")
    if scope.get("schema_version") != SCOPE_SCHEMA_VERSION:
        raise CollectionScopeError("collection_scope schema_version 不支持")
    scope_kind = scope.get("scope_kind")
    if scope_kind not in {FULL_COLLECTION, USER_CONFIRMED_ACCESSIBLE, INCREMENTAL}:
        raise CollectionScopeError("collection_scope scope_kind 无效")
    normalized = _normalized_note_ids(scope.get("note_ids"), "collection_scope note_ids")
    if _require_sha256(scope.get("note_ids_sha256"), "collection_scope note_ids_sha256") != canonical_hash(normalized):
        raise CollectionScopeError("collection_scope note id 哈希不一致")
    if scope_kind == INCREMENTAL:
        _validate_incremental_scope(scope, normalized)
    else:
        accessible_count = _require_nonnegative_int(scope.get("accessible_count"), "collection_scope accessible_count")
        declared_count = _require_nonnegative_int(scope.get("declared_count"), "collection_scope declared_count")
        unidentified_count = _require_nonnegative_int(scope.get("unidentified_count"), "collection_scope unidentified_count")
        if accessible_count != len(normalized):
            raise CollectionScopeError("collection_scope accessible_count 与 note id 数不一致")
        if declared_count != len(normalized) + unidentified_count:
            raise CollectionScopeError("collection_scope 声明/可访问/未识别计数不一致")
        if scope_kind == FULL_COLLECTION and unidentified_count != 0:
            raise CollectionScopeError("全量 collection_scope 不能包含未识别计数")
    scope["note_ids"] = normalized
    return scope


def _item_ids(items: Any, label: str) -> list[str]:
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise CollectionScopeError(f"{label} 必须是对象数组")
    result = [str(item.get("id") or "").strip().lower() for item in items]
    if any(not NOTE_ID_RE.fullmatch(note_id) for note_id in result) or len(result) != len(set(result)):
        raise CollectionScopeError(f"{label} 含无效或重复 note id")
    return result


def validate_scope_input(
    scope_path: Path | str,
    items: Any,
    *,
    stage: str,
    require_original_visible_hash: bool = False,
    items_path: Path | str | None = None,
) -> dict[str, Any]:
    """Reject an input whose exact ordered ID collection differs from scope."""
    scope = load_scope(scope_path)
    actual_ids = _item_ids(items, stage)
    if actual_ids != scope["note_ids"]:
        raise CollectionScopeError(f"{stage} 的 note id 集合或顺序与 collection_scope 不一致")
    if require_original_visible_hash:
        if items_path is None:
            raise CollectionScopeError(f"{stage} 缺少原始 visible_items 路径")
        if sha256_file(Path(items_path)) != scope.get("visible_items_sha256"):
            raise CollectionScopeError(f"{stage} 的 visible_items 哈希与 collection_scope 不一致")
    return scope


def _scope_visible_items_path(scope_path: Path, scope: dict[str, Any]) -> Path:
    file_name = scope.get("visible_items_file")
    if not isinstance(file_name, str) or not file_name:
        raise CollectionScopeError("base collection_scope 缺少 visible_items_file")
    file_path = Path(file_name)
    if file_path.is_absolute() or file_path.name != file_name:
        raise CollectionScopeError("base collection_scope visible_items_file 必须是同目录文件名")
    return (scope_path.parent / file_path).resolve()


def build_incremental_collection_scope(
    *,
    run_dir: Path | str,
    visible_out: Path | str,
    scope_out: Path | str,
    base_scope: Path | str | None,
    expected_delta_count: int | None,
    source: str = "收藏",
) -> dict[str, Any]:
    """Build a non-full scope from an audited base plus a passive new-note delta.

    The current page's declared count is deliberately diagnostic only: this
    function proves exactly the ordered union it wrote, never that the union is
    the full current favorites page.
    """
    if base_scope is None:
        raise CollectionScopeError("incremental 范围必须提供 base_scope")
    if not isinstance(expected_delta_count, int) or isinstance(expected_delta_count, bool) or expected_delta_count < 1:
        raise CollectionScopeError("incremental expected_delta_count 必须是正整数")

    run_path = Path(run_dir).resolve()
    visible_path = Path(visible_out).resolve()
    scope_path = Path(scope_out).resolve()
    if visible_path.exists() or scope_path.exists():
        raise CollectionScopeError("输出已存在；拒绝覆盖既有范围或输入")

    base_scope_path = Path(base_scope).resolve()
    base = load_scope(base_scope_path)
    if base.get("scope_kind") != USER_CONFIRMED_ACCESSIBLE or base.get("user_confirmation_required") is not True:
        raise CollectionScopeError("incremental base_scope 必须是已有用户确认的可访问范围")
    if base.get("source") != source:
        raise CollectionScopeError("incremental base_scope 与 delta source 不一致")
    base_binding = validated_page_binding(base.get("page_binding"), "incremental base_scope page_binding")
    base_visible_path = _scope_visible_items_path(base_scope_path, base)
    base_items = read_json(base_visible_path, "base visible_items")
    validate_scope_input(
        base_scope_path,
        base_items,
        stage="incremental base visible_items",
        require_original_visible_hash=True,
        items_path=base_visible_path,
    )
    base_ids = _item_ids(base_items, "incremental base visible_items")
    if base_ids != base["note_ids"]:
        raise CollectionScopeError("incremental base visible_items 顺序与 base_scope 不一致")

    delta_items, delta_meta = _canonicalize_segments(run_path, source)
    delta_ids = _item_ids(delta_items, "incremental delta passive items")
    if len(delta_ids) != expected_delta_count:
        raise CollectionScopeError("incremental delta note id 数与期待计数不一致")
    delta_positions = [item.get("page_index") for item in delta_items]
    if delta_positions != list(range(expected_delta_count)):
        raise CollectionScopeError("incremental delta page_index 必须精确覆盖 0..N-1")
    overlap = set(base_ids).intersection(delta_ids)
    if overlap:
        raise CollectionScopeError("incremental delta note id 不能与 base_scope 重叠")
    current_binding = validated_page_binding(delta_meta["page_binding"], "incremental current page_binding")
    if current_binding != base_binding:
        raise CollectionScopeError("incremental delta 当前页面与 base_scope 页面绑定不一致")
    safety_session_id = str(delta_meta.get("safety_session_id") or "")
    if not safety_session_id:
        raise CollectionScopeError("incremental delta 缺少 active 安全会话")

    union_items = [*delta_items, *base_items]
    union_ids = [*delta_ids, *base_ids]
    if _item_ids(union_items, "incremental union visible_items") != union_ids:
        raise CollectionScopeError("incremental union note id 顺序无效")
    base_count = len(base_ids)
    delta_count = len(delta_ids)
    union_count = len(union_ids)
    if union_count != base_count + delta_count:
        raise CollectionScopeError("incremental union 计数无效")
    current_declared_count = int(delta_meta["declared_count"])
    current_unidentified_count = current_declared_count - union_count
    if current_unidentified_count < 0:
        raise CollectionScopeError("incremental union note id 数不能大于当前页面声明数")

    atomic_write_json(visible_path, union_items)
    scope = {
        "schema_version": SCOPE_SCHEMA_VERSION,
        "scope_kind": INCREMENTAL,
        "source": source,
        "user_confirmation_required": True,
        "coverage_claim": "incremental_union_not_full_collection",
        "base_scope_file": base_scope_path.name,
        "base_scope_sha256": sha256_file(base_scope_path),
        "base_scope_kind": base["scope_kind"],
        "base_visible_items_file": base_visible_path.name,
        "base_visible_items_sha256": sha256_file(base_visible_path),
        "base_count": base_count,
        "base_note_ids": base_ids,
        "base_note_ids_sha256": canonical_hash(base_ids),
        "delta_count": delta_count,
        "delta_note_ids": delta_ids,
        "delta_note_ids_sha256": canonical_hash(delta_ids),
        "union_count": union_count,
        "current_declared_count": current_declared_count,
        "current_unidentified_count": current_unidentified_count,
        "note_ids": union_ids,
        "note_ids_sha256": canonical_hash(union_ids),
        "visible_items_file": visible_path.name,
        "visible_items_sha256": sha256_file(visible_path),
        "delta_page_index_coverage": {
            "start": 0,
            "end": delta_count - 1,
            "count": delta_count,
            "complete": True,
        },
        "page_binding": current_binding,
        "current_page_binding": current_binding,
        "current_safety_state": {
            "path": delta_meta["safety_path"],
            "sha256": delta_meta["safety_sha256"],
            "state": "active",
            "session_id": safety_session_id,
        },
        "delta_segment_evidence": delta_meta["evidence"],
        "created_at": utc_now(),
    }
    atomic_write_json(scope_path, scope)
    return scope


def validate_scope_snapshot(scope_path: Path | str, snapshot: Any) -> dict[str, Any]:
    """Ensure a board snapshot belongs to the same account and favorites page."""
    scope = load_scope(scope_path)
    if not isinstance(snapshot, dict):
        raise CollectionScopeError("board_snapshot 顶层必须是对象")
    source = snapshot.get("source")
    if not isinstance(source, dict):
        raise CollectionScopeError("board_snapshot 缺少 source 绑定")
    binding = scope.get("page_binding") if isinstance(scope.get("page_binding"), dict) else {}
    user_id = str(binding.get("user_id") or "").lower()
    if str(source.get("live_account_user_id") or source.get("user_id") or "").lower() != user_id:
        raise CollectionScopeError("board_snapshot 账号与 collection_scope 不一致")
    page = str(source.get("live_page_binding") or source.get("expected_url_substring") or "")
    try:
        snapshot_binding = normalized_page_binding(page)
    except CollectionScopeError as exc:
        raise CollectionScopeError("board_snapshot 页面绑定无效") from exc
    if snapshot_binding != binding:
        raise CollectionScopeError("board_snapshot 收藏页与 collection_scope 不一致")
    return scope


def main() -> None:
    parser = argparse.ArgumentParser(description="从 passive 收藏分段建立全量、用户确认或增量笔记范围。")
    parser.add_argument("run_dir", help="包含 segment-###-collection.json 的运行目录")
    parser.add_argument("visible_items", help="新建 visible_items.json 输出路径；已存在会拒绝覆盖")
    parser.add_argument("collection_scope", help="新建 collection_scope.json 输出路径；已存在会拒绝覆盖")
    parser.add_argument("--scope-kind", choices=(FULL_COLLECTION, USER_CONFIRMED_ACCESSIBLE, INCREMENTAL), default=FULL_COLLECTION)
    parser.add_argument("--expected-accessible-count", type=int, help="全量或用户明确确认的可访问 note id 数")
    parser.add_argument("--expected-unidentified-count", type=int, help="仅用户确认可访问范围：页面计数中没有真实 note id 的数量")
    parser.add_argument("--base-scope", help="仅 incremental：已存在的用户确认 base collection_scope.json")
    parser.add_argument("--expected-delta-count", type=int, help="仅 incremental：本次 passive 新增 note id 的精确数量")
    parser.add_argument("--source", default="收藏", choices=("收藏", "点赞"))
    args = parser.parse_args()
    if args.scope_kind == INCREMENTAL:
        if not args.base_scope:
            parser.error("--scope-kind incremental 必须提供 --base-scope")
        if args.expected_delta_count is None:
            parser.error("--scope-kind incremental 必须提供 --expected-delta-count")
        if args.expected_accessible_count is not None or args.expected_unidentified_count is not None:
            parser.error("incremental 不接受 --expected-accessible-count 或 --expected-unidentified-count")
    else:
        if args.expected_accessible_count is None:
            parser.error("全量或用户确认范围必须提供 --expected-accessible-count")
        if args.base_scope or args.expected_delta_count is not None:
            parser.error("只有 incremental 可以传 --base-scope 或 --expected-delta-count")
    scope = build_collection_scope(
        run_dir=args.run_dir,
        visible_out=args.visible_items,
        scope_out=args.collection_scope,
        scope_kind=args.scope_kind,
        expected_accessible_count=args.expected_accessible_count,
        expected_unidentified_count=args.expected_unidentified_count,
        base_scope=args.base_scope,
        expected_delta_count=args.expected_delta_count,
        source=args.source,
    )
    summary = {"scope_kind": scope["scope_kind"]}
    if scope["scope_kind"] == INCREMENTAL:
        summary.update({
            "base_count": scope["base_count"],
            "delta_count": scope["delta_count"],
            "union_count": scope["union_count"],
            "current_declared_count": scope["current_declared_count"],
            "current_unidentified_count": scope["current_unidentified_count"],
        })
    else:
        summary.update({
            "declared_count": scope["declared_count"],
            "accessible_count": scope["accessible_count"],
            "unidentified_count": scope["unidentified_count"],
        })
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
