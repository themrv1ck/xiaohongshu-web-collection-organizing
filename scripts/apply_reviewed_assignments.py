#!/usr/bin/env python3
"""Apply an exact, evidence-reviewed assignment set to full classification rows."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from analyze_video_transcripts import validate_analysis
from analyze_video_visuals import validate_visual_evidence_manifest, visual_evidence_sha256
from video_content_common import normalize_content_type
from xhs_safety import atomic_write_json


REVIEW_CONTRACT = "xhs-deep-classification-independent-review-v1"
VIDEO_BASES = {"full_timeline_visual", "full_timeline_visual_with_transcript"}


class ReviewContractError(ValueError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewContractError(f"无法读取 JSON：{path}") from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_unique_ids(rows: Any, label: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not isinstance(rows, list):
        raise ReviewContractError(f"{label} 必须是数组")
    result: list[dict[str, Any]] = []
    indexed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReviewContractError(f"{label}[{index}] 必须是对象")
        note_id = str(row.get("id") or "").strip()
        if not note_id:
            raise ReviewContractError(f"{label}[{index}] 缺少 ID")
        if note_id in indexed:
            raise ReviewContractError(f"{label} 包含重复 ID：{note_id}")
        result.append(row)
        indexed[note_id] = row
    return result, indexed


def require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ReviewContractError(f"{label} 必须是非空字符串数组")
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise ReviewContractError(f"{label} 包含重复值")
    return normalized


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key) or (key in before) != (key in after)
    }


def apply_reviewed_assignments(
    raw_payload: Any,
    review_payload: Any,
    inventory_payload: Any,
    scope_payload: Any,
    video_payload: Any,
    ocr_payload: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_rows, raw_by_id = require_unique_ids(raw_payload, "classification.raw.json")
    if not isinstance(scope_payload, dict):
        raise ReviewContractError("collection_scope.json 必须是对象")
    scope_ids = require_string_list(scope_payload.get("note_ids"), "collection_scope.note_ids")
    raw_ids = [str(row["id"]).strip() for row in raw_rows]
    if raw_ids != scope_ids:
        raise ReviewContractError("classification.raw.json 的 ID 或顺序与 collection_scope 不完全一致")

    if not isinstance(inventory_payload, dict):
        raise ReviewContractError("existing_boards_inventory.json 必须是对象")
    boards = require_string_list(inventory_payload.get("boards"), "existing_boards_inventory.boards")
    note_to_board_raw = inventory_payload.get("note_to_board")
    if not isinstance(note_to_board_raw, dict):
        raise ReviewContractError("existing_boards_inventory.note_to_board 必须是对象")
    note_to_board = {
        str(note_id).strip(): str(board).strip()
        for note_id, board in note_to_board_raw.items()
        if str(note_id).strip() and str(board).strip()
    }
    if len(note_to_board) != len(note_to_board_raw):
        raise ReviewContractError("existing_boards_inventory.note_to_board 包含空 ID 或空专辑名")
    excluded_ids = require_string_list(
        inventory_payload.get("excluded_note_ids"),
        "existing_boards_inventory.excluded_note_ids",
    )
    if set(excluded_ids) != set(note_to_board):
        raise ReviewContractError("excluded_note_ids 与 note_to_board 的 ID 集合不一致")
    if not set(note_to_board).issubset(scope_ids):
        raise ReviewContractError("existing board inventory 包含 collection_scope 之外的 ID")
    unknown_source_boards = sorted(set(note_to_board.values()) - set(boards))
    if unknown_source_boards:
        raise ReviewContractError("existing board inventory 包含未知专辑：" + ", ".join(unknown_source_boards))

    if not isinstance(review_payload, dict) or review_payload.get("contract") != REVIEW_CONTRACT:
        raise ReviewContractError("独立复核清单合同版本不正确")
    policy = review_payload.get("policy")
    if not isinstance(policy, dict):
        raise ReviewContractError("独立复核清单缺少 policy")
    if policy.get("existing_board_members_are_excluded") is not True:
        raise ReviewContractError("独立复核清单没有声明排除现有专辑成员")
    if policy.get("new_boards_allowed") is not False:
        raise ReviewContractError("独立复核清单必须禁止新建专辑")
    review_rows, review_by_id = require_unique_ids(review_payload.get("items"), "review.items")
    if review_payload.get("scope_count") != len(review_rows):
        raise ReviewContractError("review.scope_count 与实际复核条目数不一致")
    expected_review_ids = set(scope_ids) - set(note_to_board)
    if set(review_by_id) != expected_review_ids:
        missing = sorted(expected_review_ids - set(review_by_id))
        extra = sorted(set(review_by_id) - expected_review_ids)
        raise ReviewContractError(f"复核 ID 必须精确等于 scope 减现有专辑成员；missing={missing} extra={extra}")

    video_rows, video_by_id = require_unique_ids(video_payload, "video_analysis.json")
    ocr_rows, ocr_by_id = require_unique_ids(ocr_payload, "ocr_results.json")
    del video_rows, ocr_rows

    result = copy.deepcopy(raw_rows)
    result_by_id = {str(row["id"]).strip(): row for row in result}
    changes: list[dict[str, Any]] = []

    for note_id, source_board in note_to_board.items():
        row = raw_by_id[note_id]
        if row.get("excluded") is not True:
            raise ReviewContractError(f"现有专辑成员没有 excluded=true：{note_id}")
        if row.get("exclude_reason") != "existing_board_member_protected":
            raise ReviewContractError(f"现有专辑成员 exclude_reason 不正确：{note_id}")
        if row.get("source_board") != source_board:
            raise ReviewContractError(f"现有专辑成员 source_board 不正确：{note_id}")
        if row.get("target_board") != "":
            raise ReviewContractError(f"现有专辑成员 target_board 必须为空：{note_id}")

    for review in review_rows:
        note_id = str(review["id"]).strip()
        raw_row = raw_by_id[note_id]
        output_row = result_by_id[note_id]
        content_type = normalize_content_type(
            raw_row.get("content_type") or raw_row.get("note_type") or raw_row.get("type")
        )
        declared_type = str(review.get("content_type") or "").strip()
        if declared_type != content_type or content_type not in {"video", "image"}:
            raise ReviewContractError(f"复核内容类型与采集事实不一致：{note_id}")
        target_board = str(review.get("target_board") or "").strip()
        confidence = str(review.get("confidence") or "").strip()
        if target_board not in boards:
            raise ReviewContractError(f"复核目标不属于现有专辑：{note_id} {target_board}")
        if confidence not in {"high", "medium"}:
            raise ReviewContractError(f"复核置信度必须是 high 或 medium：{note_id}")

        allowed_changes = {"target_board", "confidence"}
        if content_type == "video":
            analysis = video_by_id.get(note_id)
            if not analysis:
                raise ReviewContractError(f"复核视频缺少分析结果：{note_id}")
            if analysis.get("status") != "success" or analysis.get("visual_status") != "analyzed":
                raise ReviewContractError(f"复核视频没有成功完成视觉分析：{note_id}")
            if analysis.get("analysis_basis") not in VIDEO_BASES:
                raise ReviewContractError(f"复核视频不是完整时轴分析：{note_id}")
            try:
                validate_analysis(analysis, boards)
                manifest = analysis.get("evidence_manifest")
                if not isinstance(manifest, dict):
                    raise ValueError("缺少 evidence_manifest")
                validate_visual_evidence_manifest(manifest)
            except (TypeError, ValueError) as exc:
                raise ReviewContractError(f"复核视频证据合同无效：{note_id} {exc}") from exc
            if analysis.get("visual_evidence_sha256") != visual_evidence_sha256(manifest):
                raise ReviewContractError(f"复核视频视觉证据哈希不一致：{note_id}")
            frames = manifest.get("frames")
            if not isinstance(frames, list) or not frames or any(
                not isinstance(frame, dict) or frame.get("ocr_status") != "ok" for frame in frames
            ):
                raise ReviewContractError(f"复核视频存在未完成 OCR 的取样帧：{note_id}")
            if raw_row.get("classification_basis") != "video_content":
                raise ReviewContractError(f"复核视频分类基础不正确：{note_id}")
            if raw_row.get("video_analysis_status") != "success":
                raise ReviewContractError(f"复核视频分类没有绑定成功分析：{note_id}")
            if raw_row.get("visual_status") != "analyzed":
                raise ReviewContractError(f"复核视频分类没有绑定视觉结果：{note_id}")
            output_row["review_state"] = "video_content_classified"
            allowed_changes.add("review_state")
        else:
            ocr = ocr_by_id.get(note_id)
            if not ocr:
                raise ReviewContractError(f"复核图文缺少 OCR 结果：{note_id}")
            if ocr.get("status") != "ok" or ocr.get("image_set_complete") is not True:
                raise ReviewContractError(f"复核图文没有完成全图片 OCR：{note_id}")
            if not str(ocr.get("ocr_run_fingerprint") or "").strip():
                raise ReviewContractError(f"复核图文缺少 OCR 指纹：{note_id}")
            if raw_row.get("classification_basis") != "metadata_and_ocr":
                raise ReviewContractError(f"复核图文分类没有使用 OCR：{note_id}")
            if raw_row.get("ocr_status") != "ok" or raw_row.get("ocr_image_set_complete") is not True:
                raise ReviewContractError(f"复核图文分类没有绑定完整 OCR：{note_id}")

        output_row["target_board"] = target_board
        output_row["confidence"] = confidence
        changed = changed_fields(raw_row, output_row)
        if not changed.issubset(allowed_changes):
            raise ReviewContractError(f"复核覆盖修改了未授权字段：{note_id} {sorted(changed)}")
        changes.append({
            "id": note_id,
            "content_type": content_type,
            "target_board": target_board,
            "confidence": confidence,
            "changed_fields": sorted(changed),
        })

    for note_id in note_to_board:
        if result_by_id[note_id] != raw_by_id[note_id]:
            raise ReviewContractError(f"现有专辑成员被修改：{note_id}")
    if [str(row["id"]).strip() for row in result] != scope_ids:
        raise ReviewContractError("复核输出改变了 ID 或顺序")

    target_counts = Counter(row["target_board"] for row in changes)
    type_counts = Counter(row["content_type"] for row in changes)
    confidence_counts = Counter(row["confidence"] for row in changes)
    audit = {
        "contract": "xhs-reviewed-assignment-application-v1",
        "assertions": {
            "scope_rows": len(scope_ids),
            "existing_rows_unchanged": len(note_to_board),
            "reviewed_rows": len(changes),
            "id_order_preserved": True,
            "review_ids_equal_scope_minus_existing": True,
            "all_targets_are_existing_boards": True,
            "no_low_confidence": True,
        },
        "counts": {
            "content_type": dict(sorted(type_counts.items())),
            "confidence": dict(sorted(confidence_counts.items())),
            "target_board": dict(sorted(target_counts.items())),
        },
        "changes": changes,
    }
    return result, audit


def main() -> int:
    parser = argparse.ArgumentParser(description="按 ID 应用精确的独立证据复核分类，并保持现有专辑成员整行不变。")
    parser.add_argument("raw_classification")
    parser.add_argument("review")
    parser.add_argument("existing_inventory")
    parser.add_argument("collection_scope")
    parser.add_argument("video_analysis")
    parser.add_argument("ocr_results")
    parser.add_argument("output")
    parser.add_argument("--audit-output", default="")
    args = parser.parse_args()

    paths = {
        "raw_classification": Path(args.raw_classification),
        "review": Path(args.review),
        "existing_inventory": Path(args.existing_inventory),
        "collection_scope": Path(args.collection_scope),
        "video_analysis": Path(args.video_analysis),
        "ocr_results": Path(args.ocr_results),
    }
    output = Path(args.output)
    audit_output = Path(args.audit_output) if args.audit_output else output.with_name(
        output.stem + ".review-application.json"
    )
    if output.exists():
        raise SystemExit(f"拒绝覆盖已有输出：{output}")
    if audit_output.exists():
        raise SystemExit(f"拒绝覆盖已有审计输出：{audit_output}")

    result, audit = apply_reviewed_assignments(
        load_json(paths["raw_classification"]),
        load_json(paths["review"]),
        load_json(paths["existing_inventory"]),
        load_json(paths["collection_scope"]),
        load_json(paths["video_analysis"]),
        load_json(paths["ocr_results"]),
    )
    audit["input_sha256"] = {name: file_sha256(path) for name, path in paths.items()}
    atomic_write_json(output, result)
    audit["output_sha256"] = file_sha256(output)
    atomic_write_json(audit_output, audit)
    print(json.dumps({
        "output": str(output),
        "audit_output": str(audit_output),
        "scope_rows": audit["assertions"]["scope_rows"],
        "existing_rows_unchanged": audit["assertions"]["existing_rows_unchanged"],
        "reviewed_rows": audit["assertions"]["reviewed_rows"],
        "output_sha256": audit["output_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
