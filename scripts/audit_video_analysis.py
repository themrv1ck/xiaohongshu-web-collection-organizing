#!/usr/bin/env python3
"""Strictly audit complete-timeline video analysis before classification."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from analyze_video_transcripts import validate_analysis
from analyze_video_visuals import canonical_sha256, validate_visual_evidence_manifest, visual_evidence_sha256
from video_content_common import normalize_content_type
from xhs_safety import atomic_write_json


SCHEMA_CONTRACTS = {
    "output-schema-v4": {
        "prompt_contract_version": 8,
        "schema_sha256": "16c0c20ac94119bc98d2e2078f18d4f5051040b6a1fa232caf546f2ae32233c3",
    },
    "output-schema-v5": {
        "prompt_contract_version": 9,
        "schema_sha256": "1c3e55fea4c2baf16501ae4ee510c2e17b5e8ae123ad1a5a179f39ac8d4508e6",
    },
}
FULL_TIMELINE_BASES = {"full_timeline_visual", "full_timeline_visual_with_transcript"}


class VideoAuditError(ValueError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoAuditError(f"无法读取 JSON：{path}") from exc


def require_unique_rows(payload: Any, label: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(payload, list):
        raise VideoAuditError(f"{label} 必须是数组")
    rows: list[dict[str, Any]] = []
    ids: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise VideoAuditError(f"{label}[{index}] 必须是对象")
        note_id = str(row.get("id") or "").strip()
        if not note_id:
            raise VideoAuditError(f"{label}[{index}] 缺少 ID")
        if note_id in seen:
            raise VideoAuditError(f"{label} 包含重复 ID：{note_id}")
        seen.add(note_id)
        rows.append(row)
        ids.append(note_id)
    return rows, ids


def schema_contract(row: dict[str, Any], note_id: str) -> dict[str, Any]:
    version = str(row.get("analysis_provider_version") or "")
    matches = [contract for marker, contract in SCHEMA_CONTRACTS.items() if marker in version]
    if len(matches) != 1:
        raise VideoAuditError(f"成功视频 provider schema 版本不受支持：{note_id} {version}")
    return matches[0]


def audit_success(row: dict[str, Any], boards: list[str]) -> dict[str, Any]:
    note_id = str(row["id"])
    if row.get("visual_status") != "analyzed":
        raise VideoAuditError(f"成功视频 visual_status 不是 analyzed：{note_id}")
    if row.get("analysis_basis") not in FULL_TIMELINE_BASES:
        raise VideoAuditError(f"成功视频不是完整时轴分析：{note_id}")
    try:
        validate_analysis(row, boards)
        manifest = row.get("evidence_manifest")
        if not isinstance(manifest, dict):
            raise ValueError("缺少 evidence_manifest")
        validate_visual_evidence_manifest(manifest)
    except (TypeError, ValueError) as exc:
        raise VideoAuditError(f"成功视频分析合同无效：{note_id} {exc}") from exc
    frames = manifest.get("frames")
    if not isinstance(frames, list) or len(frames) < 5:
        raise VideoAuditError(f"成功视频取样帧少于 5：{note_id}")
    for frame in frames:
        if not isinstance(frame, dict) or frame.get("ocr_status") != "ok":
            raise VideoAuditError(f"成功视频存在未完成 OCR 的取样帧：{note_id}")
        if frame.get("ocr_provider") != "macos_vision":
            raise VideoAuditError(f"成功视频取样帧 OCR provider 不正确：{note_id}")
    evidence_hash = visual_evidence_sha256(manifest)
    if row.get("visual_evidence_sha256") != evidence_hash:
        raise VideoAuditError(f"成功视频 visual_evidence_sha256 不一致：{note_id}")

    contract = schema_contract(row, note_id)
    schema_hash = str(row.get("analysis_schema_sha256") or "")
    if schema_hash != contract["schema_sha256"]:
        raise VideoAuditError(f"成功视频 schema 哈希不正确：{note_id}")
    provider_identity = {
        "provider": str(row.get("analysis_provider") or ""),
        "model": str(row.get("analysis_model") or ""),
        "version": str(row.get("analysis_provider_version") or ""),
        "schema_sha256": schema_hash,
    }
    if not all(provider_identity.values()):
        raise VideoAuditError(f"成功视频 provider identity 不完整：{note_id}")
    transcript_hash = str(row.get("transcript_sha256") or "")
    expected_input_hash = canonical_sha256({
        "prompt_contract_version": contract["prompt_contract_version"],
        "visual_evidence_sha256": evidence_hash,
        "transcript_sha256": transcript_hash,
        "allowed_boards": boards,
        "analysis_provider": provider_identity,
    })
    if row.get("analysis_input_sha256") != expected_input_hash:
        raise VideoAuditError(f"成功视频 analysis_input_sha256 不一致：{note_id}")
    sampling = manifest["sampling"]
    return {
        "id": note_id,
        "frame_count": len(frames),
        "duration_sec": manifest["duration_sec"],
        "observed_max_gap_sec": sampling["observed_max_gap_sec"],
        "schema_prompt_contract": contract["prompt_contract_version"],
    }


def audit_failure(row: dict[str, Any]) -> None:
    note_id = str(row["id"])
    if row.get("visual_status") != "failed":
        raise VideoAuditError(f"失败视频 visual_status 不是 failed：{note_id}")
    if row.get("analysis_basis") not in FULL_TIMELINE_BASES:
        raise VideoAuditError(f"失败视频没有声明完整时轴尝试：{note_id}")
    if any(str(row.get(key) or "").strip() for key in ("main_topic", "content_summary", "target_board")):
        raise VideoAuditError(f"失败视频包含伪分类内容：{note_id}")
    if row.get("confidence") != "low":
        raise VideoAuditError(f"失败视频 confidence 不是 low：{note_id}")
    reason = row.get("reason")
    if not isinstance(reason, list) or not reason or not all(str(value).strip() for value in reason):
        raise VideoAuditError(f"失败视频缺少 reason：{note_id}")
    if not str(row.get("reason_code") or "").strip() or not str(row.get("error") or "").strip():
        raise VideoAuditError(f"失败视频缺少明确失败原因：{note_id}")


def audit_video_analysis(
    items_payload: Any,
    analysis_payload: Any,
    taxonomy_payload: Any,
    completed_prefix: int,
) -> dict[str, Any]:
    items, _ = require_unique_rows(items_payload, "image_items.json")
    expected_video_ids = [
        str(row["id"]).strip()
        for row in items
        if normalize_content_type(row.get("content_type") or row.get("note_type") or row.get("type")) == "video"
    ]
    analysis, analysis_ids = require_unique_rows(analysis_payload, "video_analysis.json")
    if analysis_ids != expected_video_ids:
        raise VideoAuditError("video_analysis 的 ID 或顺序与采集视频集合不完全一致")
    if not isinstance(taxonomy_payload, dict):
        raise VideoAuditError("taxonomy 必须是对象")
    boards = taxonomy_payload.get("boards")
    if not isinstance(boards, list) or not boards or not all(isinstance(value, str) and value for value in boards):
        raise VideoAuditError("taxonomy.boards 必须是非空字符串数组")
    if len(boards) != len(set(boards)):
        raise VideoAuditError("taxonomy.boards 包含重复专辑名")
    if not 0 <= completed_prefix <= len(analysis):
        raise VideoAuditError("completed_prefix 超出视频范围")

    success_details = []
    failure_ids = []
    for index, row in enumerate(analysis):
        status = row.get("status")
        visual_status = row.get("visual_status")
        if index >= completed_prefix:
            if visual_status != "not_enabled":
                raise VideoAuditError(f"未进入本批的视频不是 not_enabled：index={index} id={row['id']}")
            continue
        if status == "success":
            success_details.append(audit_success(row, boards))
        elif status == "failed":
            audit_failure(row)
            failure_ids.append(str(row["id"]))
        else:
            raise VideoAuditError(f"已完成范围包含非终态视频：index={index} id={row['id']}")

    frame_count = sum(detail["frame_count"] for detail in success_details)
    prompt_counts = Counter(str(detail["schema_prompt_contract"]) for detail in success_details)
    return {
        "contract": "xhs-full-timeline-video-analysis-audit-v1",
        "passed": True,
        "video_count": len(analysis),
        "completed_prefix": completed_prefix,
        "success_count": len(success_details),
        "failed_count": len(failure_ids),
        "pending_count": len(analysis) - completed_prefix,
        "sampled_frame_count": frame_count,
        "prompt_contract_counts": dict(sorted(prompt_counts.items())),
        "failure_ids": failure_ids,
        "success_details": success_details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="严格审计视频全时轴视觉、逐帧 OCR、哈希链与显式失败合同。")
    parser.add_argument("items")
    parser.add_argument("analysis")
    parser.add_argument("taxonomy")
    parser.add_argument("--completed-prefix", type=int, required=True)
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    report = audit_video_analysis(
        load_json(Path(args.items)),
        load_json(Path(args.analysis)),
        load_json(Path(args.taxonomy)),
        args.completed_prefix,
    )
    if args.report:
        report_path = Path(args.report)
        if report_path.exists():
            raise SystemExit(f"拒绝覆盖已有审计报告：{report_path}")
        atomic_write_json(report_path, report)
    print(json.dumps({key: report[key] for key in (
        "passed", "video_count", "completed_prefix", "success_count", "failed_count",
        "pending_count", "sampled_frame_count", "prompt_contract_counts",
    )}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
