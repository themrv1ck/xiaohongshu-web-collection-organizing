#!/usr/bin/env python3
"""Classify verified video transcripts through an explicitly selected provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from video_analysis_provider import ProviderError, build_analysis_provider
from video_content_common import (
    MIMO_VL_MODEL_SUBDIR,
    resolve_mimo_vl_root,
    safe_error,
    transcript_sha256,
)
from xhs_ocr_common import load_taxonomy


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "video_analysis.schema.json"
ANALYSIS_PROMPT_CONTRACT_VERSION = 9
CJK_IDEOGRAPH_AT_START_RE = re.compile(r"^[\u3400-\u4DBF\u4E00-\u9FFF]")
ANALYSIS_OUTPUT_CONTRACT = (
    "返回 JSON 对象必须且只能包含这五个字段，并按事实到分类的顺序生成："
    "main_topic（直接写视频实际事实主题，不得写专辑名）、"
    "content_summary（直接陈述实际内容的中文摘要字符串）、"
    "reason（1 到 4 个只陈述主要对象、动作、用途等可见或可听事实的中文字符串；不得先替专辑辩护）、"
    "target_board（允许专辑之一，无法准确匹配时为空字符串）、"
    "confidence（只能是 high、medium、low；target_board 为空时必须是 low）。"
    "不得复述格式要求，不得遗漏字段。"
)


def starts_with_cjk_ideograph(value: str) -> bool:
    """Mirror the schema's anchored CJK Unified Ideograph code-point ranges."""
    return CJK_IDEOGRAPH_AT_START_RE.search(value) is not None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_analysis(payload: Any, boards: list[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("内容分析器返回值必须是 JSON 对象")
    required = ("main_topic", "content_summary", "target_board", "confidence", "reason")
    for key in required:
        if key not in payload:
            raise ValueError(f"内容分析器返回值缺少字段：{key}")
    raw_target_board = payload.get("target_board")
    if not isinstance(raw_target_board, str):
        raise ValueError("target_board 必须是字符串")
    target_board = raw_target_board.strip()
    if target_board and target_board not in boards:
        raise ValueError(f"内容分析器返回了专辑体系之外的目标：{target_board}")
    raw_confidence = payload.get("confidence")
    if not isinstance(raw_confidence, str):
        raise ValueError("confidence 必须是字符串")
    confidence = raw_confidence.strip()
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("confidence 必须是 high、medium 或 low")
    if not target_board and confidence != "low":
        raise ValueError("target_board 为空时 confidence 必须是 low")
    raw_main_topic = payload.get("main_topic")
    raw_content_summary = payload.get("content_summary")
    if not isinstance(raw_main_topic, str) or not isinstance(raw_content_summary, str):
        raise ValueError("main_topic 和 content_summary 必须是字符串")
    main_topic = raw_main_topic.strip()
    content_summary = raw_content_summary.strip()
    if not main_topic or not starts_with_cjk_ideograph(main_topic):
        raise ValueError("main_topic 必须以中日韩统一表意文字开头并陈述事实主题")
    if not content_summary or not starts_with_cjk_ideograph(content_summary):
        raise ValueError("content_summary 必须以中日韩统一表意文字开头")
    reason = payload.get("reason")
    if (
        not isinstance(reason, list)
        or not 1 <= len(reason) <= 4
        or not all(
            isinstance(row, str)
            and bool(row.strip())
            and starts_with_cjk_ideograph(row.strip())
            for row in reason
        )
    ):
        raise ValueError("reason 必须是 1 到 4 个以中日韩统一表意文字开头的字符串")
    return {
        "main_topic": main_topic,
        "content_summary": content_summary,
        "target_board": target_board,
        "confidence": confidence,
        "reason": [str(row).strip() for row in reason[:4]],
    }


def analysis_prompt(row: dict[str, Any], boards: list[str]) -> str:
    segments = row.get("segments") if isinstance(row.get("segments"), list) else []
    return (
        "你只执行一次视频内容分类，不运行任何工具，不读取文件，也不根据标题、简介、作者或热度猜测。\n"
        "下面只有已经通过覆盖率校验的视频转写。必须先写实际事实主题、摘要和证据，再从允许专辑中选择且只能选择一个。\n"
        "专辑名代表上位主题；视频内容是某个专辑的明确子主题时就应选择该专辑，不能只因内容更具体而留空。\n"
        "如果没有任何专辑准确匹配，target_board 返回空字符串并把 confidence 设为 low。不要创造新专辑。\n"
        f"允许专辑：{json.dumps(boards, ensure_ascii=False)}\n"
        f"视频转写：{json.dumps(segments, ensure_ascii=False)}\n"
        f"{ANALYSIS_OUTPUT_CONTRACT}"
    )


def analysis_input_sha256(
    *,
    transcript_hash: str,
    boards: list[str],
    provider_identity: dict[str, Any],
) -> str:
    payload = {
        "prompt_contract_version": ANALYSIS_PROMPT_CONTRACT_VERSION,
        "transcript_sha256": transcript_hash,
        "allowed_boards": boards,
        "analysis_provider": provider_identity,
    }
    material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def provider_failure(exc: ProviderError) -> dict[str, Any]:
    metadata = getattr(exc, "metadata", {})
    return {
        "status": "failed",
        "stage": "content_analysis",
        "reason_code": str(getattr(exc, "reason_code", "analysis_provider_failed")),
        "error": safe_error(exc),
        "returncode": metadata.get("returncode") if isinstance(metadata, dict) else None,
        "stdout": str(metadata.get("stdout") or "")[:500] if isinstance(metadata, dict) else "",
        "stderr": str(metadata.get("stderr") or "")[:500] if isinstance(metadata, dict) else "",
    }


def analyze_with_provider(
    row: dict[str, Any],
    boards: list[str],
    *,
    provider: Any,
) -> dict[str, Any]:
    try:
        payload = provider.analyze(analysis_prompt(row, boards), image_paths=())
        normalized = validate_analysis(payload, boards)
    except ProviderError as exc:
        return provider_failure(exc)
    except Exception as exc:
        return {
            "status": "failed",
            "stage": "content_analysis",
            "reason_code": "analysis_provider_invalid_result",
            "error": safe_error(exc),
            "returncode": None,
            "stdout": "",
            "stderr": "",
        }
    return {"status": "success", **normalized, "error": ""}


def build_analysis_rows(
    transcripts: list[dict[str, Any]],
    analyze: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_items: int | None = None,
    initial_rows: list[dict[str, Any]] | None = None,
    allowed_boards: list[str] | None = None,
    analysis_identity: dict[str, Any] | None = None,
    on_row: Callable[[list[dict[str, Any]]], None] | None = None,
) -> list[dict[str, Any]]:
    transcript_by_id: dict[str, dict[str, Any]] = {}
    transcript_order: list[str] = []
    for row in transcripts:
        note_id = str(row.get("id") or "").strip()
        if not note_id:
            raise ValueError("文字稿输入包含缺少 ID 的条目")
        if note_id in transcript_by_id:
            raise ValueError(f"文字稿输入包含重复视频 ID：{note_id}")
        if row.get("status") == "success":
            segments = row.get("segments")
            coverage = row.get("coverage")
            current_hash = str(row.get("transcript_sha256") or "")
            if not isinstance(segments, list) or not segments:
                raise ValueError(f"成功文字稿缺少 segments：{note_id}")
            if not isinstance(coverage, dict) or coverage.get("transcript_quality_passed") is not True:
                raise ValueError(f"成功文字稿未通过质量门：{note_id}")
            if not current_hash or current_hash != transcript_sha256(segments):
                raise ValueError(f"成功文字稿哈希不匹配：{note_id}")
        transcript_by_id[note_id] = row
        transcript_order.append(note_id)

    previous_by_id: dict[str, dict[str, Any]] = {}
    for row in initial_rows or []:
        note_id = str(row.get("id") or "")
        if not note_id or note_id not in transcript_by_id:
            raise ValueError(f"断点文件包含不在当前文字稿中的视频 ID：{note_id or '<empty>'}")
        if note_id in previous_by_id:
            raise ValueError(f"断点文件包含重复视频 ID：{note_id}")
        previous_by_id[note_id] = dict(row)

    result_by_id: dict[str, dict[str, Any]] = {}
    for note_id, previous in previous_by_id.items():
        transcript = transcript_by_id[note_id]
        if transcript.get("status") != "success" or previous.get("status") != "success":
            continue
        current_input_hash = analysis_input_sha256(
            transcript_hash=str(transcript.get("transcript_sha256") or ""),
            boards=allowed_boards or [],
            provider_identity=analysis_identity or {"provider": "unspecified", "model": "", "version": ""},
        )
        if (
            not previous.get("transcript_sha256")
            or previous.get("transcript_sha256") != transcript.get("transcript_sha256")
            or previous.get("analysis_input_sha256") != current_input_hash
        ):
            continue
        if allowed_boards is not None:
            try:
                validate_analysis(previous, allowed_boards)
            except ValueError:
                continue
        result_by_id[note_id] = dict(previous)

    def ordered_rows() -> list[dict[str, Any]]:
        return [result_by_id[note_id] for note_id in transcript_order if note_id in result_by_id]

    processed = 0
    for transcript in transcripts:
        note_id = str(transcript.get("id") or "").strip()
        if note_id in result_by_id:
            continue
        if transcript.get("status") != "success":
            result_by_id[note_id] = {
                "id": note_id,
                "status": "failed",
                "stage": transcript.get("stage") or "transcript_acquisition",
                "reason_code": transcript.get("reason_code") or "video_content_unavailable",
                "error": transcript.get("error") or "视频文字稿不可用",
                "analysis_basis": "transcript_only",
                "visual_status": "not_enabled",
            }
            if on_row:
                on_row(ordered_rows())
            continue
        if max_items is not None and processed >= max_items:
            break
        result = analyze(transcript)
        result["id"] = note_id
        result["transcript_sha256"] = transcript.get("transcript_sha256") or ""
        result["analysis_input_sha256"] = analysis_input_sha256(
            transcript_hash=str(result["transcript_sha256"]),
            boards=allowed_boards or [],
            provider_identity=analysis_identity or {"provider": "unspecified", "model": "", "version": ""},
        )
        result["analysis_basis"] = "transcript_only"
        result["visual_status"] = "not_enabled"
        if analysis_identity is not None:
            result["analysis_provider"] = str(analysis_identity.get("provider") or "")
            result["analysis_model"] = str(analysis_identity.get("model") or "")
            result["analysis_provider_version"] = str(analysis_identity.get("version") or "")
        result_by_id[note_id] = result
        processed += 1
        if on_row:
            on_row(ordered_rows())
    return ordered_rows()


def main() -> int:
    parser = argparse.ArgumentParser(description="用用户明确选择的内容分析器，根据视频完整转写选择目标专辑。")
    parser.add_argument("transcripts", help="video_transcripts.json 路径")
    parser.add_argument("out", help="video_analysis.json 输出路径")
    parser.add_argument("--taxonomy")
    parser.add_argument("--analysis-provider", required=True, choices=("codex-cli", "mimo-vl-mlx", "command"))
    parser.add_argument("--analysis-command", nargs="+", help="command provider 的可执行文件和固定参数；不经过 shell")
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--codex-model")
    parser.add_argument("--mimo-vl-python")
    parser.add_argument("--mimo-vl-model")
    parser.add_argument("--mimo-vl-root", help="默认读取 XHS_MIMO_VL_ROOT 或 ~/Documents/MiMo-VL-7B-RL-2508")
    parser.add_argument("--provider-startup-timeout", type=int, default=1800)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-items", type=int)
    parser.add_argument("--resume", action="store_true", help="按视频 ID 和文字稿哈希复用已有成功结果")
    args = parser.parse_args()
    if args.resume and args.max_items is not None:
        parser.error("--resume 不能与 --max-items 同时使用")
    transcript_rows = json.loads(Path(args.transcripts).read_text(encoding="utf-8"))
    if not isinstance(transcript_rows, list):
        raise SystemExit("video_transcripts.json 必须是数组")
    boards = load_taxonomy(Path(args.taxonomy)) if args.taxonomy else load_taxonomy(None)
    out = Path(args.out)
    initial_rows: list[dict[str, Any]] = []
    if args.resume and out.exists():
        existing_payload = json.loads(out.read_text(encoding="utf-8"))
        if not isinstance(existing_payload, list):
            raise SystemExit("已有 video_analysis.json 必须是数组")
        initial_rows = existing_payload

    mimo_root = resolve_mimo_vl_root(args.mimo_vl_root)
    provider_model = (
        args.mimo_vl_model or str(mimo_root / MIMO_VL_MODEL_SUBDIR)
        if args.analysis_provider == "mimo-vl-mlx"
        else args.codex_model
        if args.analysis_provider == "codex-cli"
        else None
    )
    provider_python = args.mimo_vl_python or str(mimo_root / ".venv" / "bin" / "python")
    provider = build_analysis_provider(
        args.analysis_provider,
        model=provider_model,
        timeout=args.timeout,
        codex_bin=args.codex_bin,
        output_schema=SCHEMA_PATH,
        command=args.analysis_command,
        python_bin=provider_python,
        worker_script=ROOT / "scripts" / "mimo_vl_worker.py",
        startup_timeout=args.provider_startup_timeout,
        working_directory=ROOT,
        allowed_boards=boards,
    )
    try:
        identity = provider.identity()
        rows = build_analysis_rows(
            transcript_rows,
            lambda row: analyze_with_provider(row, boards, provider=provider),
            max_items=args.max_items,
            initial_rows=initial_rows,
            allowed_boards=boards,
            analysis_identity=identity,
            on_row=lambda current: write_json(out, current),
        )
    finally:
        provider.close()
    write_json(out, rows)
    print(json.dumps({
        "count": len(rows),
        "success_count": sum(row.get("status") == "success" for row in rows),
        "failed_count": sum(row.get("status") != "success" for row in rows),
        "input_count": len(transcript_rows),
        "complete": len(rows) == len(transcript_rows),
        "output": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
