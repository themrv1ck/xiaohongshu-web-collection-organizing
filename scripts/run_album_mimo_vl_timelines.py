#!/usr/bin/env python3
"""Run resumable, host-bound MiMo-VL timelines from an existing frame cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from analyze_video_visuals import validate_visual_evidence_manifest, visual_evidence_sha256
from video_analysis_provider import MimoVlMlxProvider, ProviderError
from video_content_common import resolve_mimo_vl_root


ROOT = Path(__file__).resolve().parents[1]
BATCH_CONTRACT = "xiaohongshu.mimo_vl_batches.v1"
TIMELINE_CONTRACT = "xiaohongshu.mimo_vl_timeline.v1"
ALLOWED_MODEL_ITEM_KEYS = {"slot", "observation", "actions", "uncertainty"}
ALLOWED_MODEL_BATCH_KEYS = {"batch_summary", "items"}
FORBIDDEN_MODEL_COORDINATE_KEYS = {"index", "timestamp", "timestamp_sec", "timestamp_seconds", "sha256", "hash"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def index_analysis(rows: Any) -> tuple[list[str], dict[str, dict]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("body.video_analysis.deep.json 必须是非空数组")
    order: list[str] = []
    indexed: dict[str, dict] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"analysis[{position}] 必须是对象")
        note_id = str(row.get("id") or "").strip()
        if not note_id:
            raise ValueError(f"analysis[{position}] 缺少 id")
        if note_id in indexed:
            raise ValueError(f"analysis 包含重复 id：{note_id}")
        manifest = row.get("evidence_manifest")
        if not isinstance(manifest, dict):
            raise ValueError(f"analysis 缺少 evidence_manifest：{note_id}")
        validate_visual_evidence_manifest(manifest)
        order.append(note_id)
        indexed[note_id] = row
    return order, indexed


def select_note_ids(
    order: list[str],
    requested: list[str],
    skipped: list[str],
) -> list[str]:
    known = set(order)
    requested_set = {str(value).strip() for value in requested if str(value).strip()}
    skipped_set = {str(value).strip() for value in skipped if str(value).strip()}
    unknown = sorted((requested_set | skipped_set) - known)
    if unknown:
        raise ValueError(f"未知 note id：{unknown}")
    overlap = sorted(requested_set & skipped_set)
    if overlap:
        raise ValueError(f"note id 同时被选择和跳过：{overlap}")
    selected = requested_set if requested_set else known
    result = [note_id for note_id in order if note_id in selected and note_id not in skipped_set]
    if not result:
        raise ValueError("本次没有可运行的 note id")
    return result


def resolve_frame_paths(note_id: str, manifest: dict, visual_cache_root: Path) -> list[Path]:
    frames_dir = visual_cache_root / note_id / "frames"
    paths: list[Path] = []
    for position, frame in enumerate(manifest["frames"]):
        filename = str(frame.get("filename") or "")
        if not filename or Path(filename).name != filename:
            raise ValueError(f"帧文件名无效：{note_id}#{position}")
        path = frames_dir / filename
        if not path.is_file():
            raise ValueError(f"缺少帧文件：{path}")
        actual_hash = file_sha256(path)
        if actual_hash != frame.get("sha256"):
            raise ValueError(f"帧文件 hash 与宿主 manifest 不一致：{note_id}#{position}")
        paths.append(path)
    return paths


def build_batch_prompt(batch_frames: list[dict], batch_index: int, batch_count: int) -> str:
    evidence_index = [
        {
            "slot": slot,
            "index": frame["index"],
            "timestamp_sec": frame["timestamp_sec"],
            "sha256": frame["sha256"],
        }
        for slot, frame in enumerate(batch_frames)
    ]
    output_items = [
        {
            "slot": slot,
            "observation": "该帧直接可见的主体、环境、操作、图表或字幕",
            "actions": ["画面中实际可见的动作"],
            "uncertainty": "",
        }
        for slot in range(len(batch_frames))
    ]
    return (
        "你是 WatchBefore 的 MiMo-VL 视频画面证据提取器。"
        f"这是完整时轴的第 {batch_index}/{batch_count} 批，附件与下列宿主证据索引按 slot 一一对应。\n"
        "宿主证据索引："
        + json.dumps(evidence_index, ensure_ascii=False, separators=(",", ":"))
        + "\n只能描述附件直接可见的事实，不得使用标题、作者、简介、音轨或常识推测。"
        "不得把画面推断写成视频说过的话。\n"
        f"items 必须正好有 {len(batch_frames)} 项，slot 必须严格为 {list(range(len(batch_frames)))}。"
        "屏幕文字由宿主逐帧 OCR 单独保存，本接口不得转录 visible_text。"
        "items 每项只能包含 slot、observation、actions、uncertainty 这四个字段，"
        "不得增加 operation、action、description 或其他字段。"
        "模型不得输出 index、timestamp、timestamp_sec、timestamp_seconds、sha256 或 hash；"
        "这些坐标只由宿主按 slot 绑定。\n"
        "uncertainty 只有在画面确有歧义时才写具体限制，否则必须是空字符串。\n"
        "actions 最多 20 项；相同动作只能保留一次，严禁重复输出同一字符串。\n"
        "严格输出一个 JSON 对象，不得输出 Markdown 或解释：\n"
        + json.dumps(
            {
                "batch_summary": "本批附件共同展示的主要视觉内容",
                "items": output_items,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def strict_string_list(
    value: Any,
    label: str,
    *,
    max_items: int | None = None,
    unique: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} 必须是字符串数组")
    if max_items is not None and len(value) > max_items:
        raise ValueError(f"{label} 最多允许 {max_items} 项")
    result: list[str] = []
    for position, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label}[{position}] 必须是非空字符串")
        result.append(item.strip())
    if unique and len(set(result)) != len(result):
        raise ValueError(f"{label} 不得包含重复字符串")
    return result


def bind_model_batch(payload: Any, batch_frames: list[dict]) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("MiMo-VL 返回值必须是 JSON 对象")
    if set(payload) != ALLOWED_MODEL_BATCH_KEYS:
        raise ValueError(f"MiMo-VL 批次字段与严格合同不一致：{sorted(payload)}")
    summary = payload.get("batch_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("batch_summary 必须是非空字符串")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != len(batch_frames):
        raise ValueError("MiMo-VL 返回帧数与输入帧数不一致")
    bound_frames: list[dict] = []
    for slot, (item, trusted) in enumerate(zip(items, batch_frames)):
        if not isinstance(item, dict):
            raise ValueError(f"items[{slot}] 必须是对象")
        forbidden = sorted(set(item) & FORBIDDEN_MODEL_COORDINATE_KEYS)
        if forbidden:
            raise ValueError(f"items[{slot}] 不得由模型提供坐标或 hash：{forbidden}")
        if set(item) != ALLOWED_MODEL_ITEM_KEYS:
            raise ValueError(f"items[{slot}] 字段与严格合同不一致：{sorted(item)}")
        if item.get("slot") != slot or isinstance(item.get("slot"), bool):
            raise ValueError(f"items[{slot}].slot 与附件顺序不一致")
        observation = item.get("observation")
        uncertainty = item.get("uncertainty")
        if not isinstance(observation, str) or not observation.strip():
            raise ValueError(f"items[{slot}].observation 必须是非空字符串")
        if not isinstance(uncertainty, str):
            raise ValueError(f"items[{slot}].uncertainty 必须是字符串")
        bound_frames.append({
            "index": trusted["index"],
            "timestamp_sec": trusted["timestamp_sec"],
            "sha256": trusted["sha256"],
            "observation": observation.strip(),
            "visible_text": [],
            "actions": strict_string_list(
                item.get("actions"),
                f"items[{slot}].actions",
                max_items=20,
                unique=True,
            ),
            "uncertainty": uncertainty.strip(),
        })
    visual_caveats = list(dict.fromkeys(
        frame["uncertainty"] for frame in bound_frames if frame["uncertainty"]
    ))
    return {
        "overall_visual_summary": summary.strip(),
        "frames": bound_frames,
        "visual_caveats": visual_caveats,
    }


def validate_bound_batch(
    batch: Any,
    expected_frames: list[dict],
    start: int,
    *,
    max_frame_count: int | None = 6,
) -> int:
    if not isinstance(batch, dict):
        raise ValueError("恢复批次必须是对象")
    summary = batch.get("overall_visual_summary")
    frames = batch.get("frames")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("恢复批次缺少 overall_visual_summary")
    if not isinstance(frames, list) or not frames:
        raise ValueError("恢复批次帧数无效")
    if max_frame_count is not None and len(frames) > max_frame_count:
        raise ValueError("恢复批次帧数无效")
    if start + len(frames) > len(expected_frames):
        raise ValueError("恢复批次超出宿主 manifest")
    for offset, (frame, expected) in enumerate(zip(frames, expected_frames[start:start + len(frames)])):
        if not isinstance(frame, dict):
            raise ValueError("恢复批次帧必须是对象")
        actual = (frame.get("index"), frame.get("timestamp_sec"), frame.get("sha256"))
        trusted = (expected.get("index"), expected.get("timestamp_sec"), expected.get("sha256"))
        if actual != trusted:
            raise ValueError(f"恢复批次 index/timestamp/hash 不一致：#{start + offset}")
        if not isinstance(frame.get("observation"), str) or not frame["observation"].strip():
            raise ValueError(f"恢复批次 observation 无效：#{start + offset}")
        strict_string_list(frame.get("visible_text"), f"恢复批次 visible_text：#{start + offset}")
        strict_string_list(frame.get("actions"), f"恢复批次 actions：#{start + offset}")
        if not isinstance(frame.get("uncertainty"), str):
            raise ValueError(f"恢复批次 uncertainty 无效：#{start + offset}")
    strict_string_list(batch.get("visual_caveats"), "恢复批次 visual_caveats")
    return start + len(frames)


def load_resume_state(
    path: Path,
    manifest: dict,
    provider_identity: dict,
) -> tuple[dict, int]:
    empty = {
        "contract": BATCH_CONTRACT,
        "provider": deepcopy(provider_identity),
        "video_sha256": manifest["video_sha256"],
        "frame_manifest_sha256": visual_evidence_sha256(manifest),
        "completed_frames": 0,
        "batches": [],
    }
    if not path.exists():
        return empty, 0
    state = load_json(path)
    if not isinstance(state, dict):
        raise ValueError(f"恢复文件必须是对象：{path}")
    if state.get("provider") != provider_identity:
        raise ValueError(f"恢复文件 provider 不一致：{path}")
    if state.get("video_sha256") != manifest["video_sha256"]:
        raise ValueError(f"恢复文件视频 hash 不一致：{path}")
    stored_manifest_hash = state.get("frame_manifest_sha256")
    if stored_manifest_hash is not None and stored_manifest_hash != visual_evidence_sha256(manifest):
        raise ValueError(f"恢复文件 manifest hash 不一致：{path}")
    batches = state.get("batches")
    if not isinstance(batches, list):
        raise ValueError(f"恢复文件 batches 无效：{path}")
    cursor = 0
    for batch in batches:
        cursor = validate_bound_batch(batch, manifest["frames"], cursor)
    if state.get("completed_frames") != cursor:
        raise ValueError(f"恢复文件 completed_frames 不一致：{path}")
    normalized = deepcopy(empty)
    normalized["batches"] = deepcopy(batches)
    normalized["completed_frames"] = cursor
    return normalized, cursor


def build_timeline(manifest: dict, provider_identity: dict, batches: list[dict]) -> dict:
    cursor = 0
    summaries: list[dict] = []
    frames: list[dict] = []
    caveats: list[str] = []
    for batch_index, batch in enumerate(batches, start=1):
        cursor = validate_bound_batch(batch, manifest["frames"], cursor)
        batch_frames = batch["frames"]
        summaries.append({
            "batch_index": batch_index,
            "start_timestamp_sec": batch_frames[0]["timestamp_sec"],
            "end_timestamp_sec": batch_frames[-1]["timestamp_sec"],
            "summary": batch["overall_visual_summary"],
        })
        frames.extend(deepcopy(batch_frames))
        caveats.extend(f"第 {batch_index} 批：{value}" for value in batch["visual_caveats"])
    if cursor != len(manifest["frames"]):
        raise ValueError("未完成全部帧，不得写入最终时间线")
    return {
        "contract": TIMELINE_CONTRACT,
        "provider": deepcopy(provider_identity),
        "video_sha256": manifest["video_sha256"],
        "sampling": deepcopy(manifest["sampling"]),
        "batch_summaries": summaries,
        "overall_visual_summary": f"完整时轴已分 {len(batches)} 批分析；逐帧事实按时间顺序保存。",
        "frames": frames,
        "visual_caveats": caveats,
    }


def validate_complete_timeline(timeline: Any, manifest: dict, provider_identity: dict) -> None:
    if not isinstance(timeline, dict) or timeline.get("contract") != TIMELINE_CONTRACT:
        raise ValueError("已有最终时间线合同无效")
    if timeline.get("provider") != provider_identity:
        raise ValueError("已有最终时间线 provider 不一致")
    if timeline.get("video_sha256") != manifest["video_sha256"]:
        raise ValueError("已有最终时间线视频 hash 不一致")
    if timeline.get("sampling") != manifest["sampling"]:
        raise ValueError("已有最终时间线 sampling 不一致")
    frames = timeline.get("frames")
    if not isinstance(frames, list) or len(frames) != len(manifest["frames"]):
        raise ValueError("已有最终时间线帧数不一致")
    synthetic_batch = {
        "overall_visual_summary": timeline.get("overall_visual_summary"),
        "frames": frames,
        "visual_caveats": timeline.get("visual_caveats"),
    }
    validate_bound_batch(
        synthetic_batch,
        manifest["frames"],
        0,
        max_frame_count=None,
    )


def run_note(
    note_id: str,
    manifest: dict,
    frame_paths: list[Path],
    output_dir: Path,
    provider: Any,
    max_frames_per_request: int,
) -> dict:
    if not 1 <= max_frames_per_request <= 6:
        raise ValueError("--max-frames-per-request 必须为 1 到 6")
    if len(frame_paths) != len(manifest["frames"]):
        raise ValueError(f"帧路径数与 manifest 不一致：{note_id}")
    provider_identity = provider.identity()
    note_output = output_dir / note_id
    batches_path = note_output / "batches.json"
    timeline_path = note_output / "mimo_vl_timeline.json"
    if timeline_path.exists():
        validate_complete_timeline(load_json(timeline_path), manifest, provider_identity)
        return {"note_id": note_id, "status": "already_complete", "frame_count": len(frame_paths)}

    state, cursor = load_resume_state(batches_path, manifest, provider_identity)
    remaining_count = len(manifest["frames"]) - cursor
    new_batch_count = (remaining_count + max_frames_per_request - 1) // max_frames_per_request
    final_batch_count = len(state["batches"]) + new_batch_count
    while cursor < len(manifest["frames"]):
        end = min(cursor + max_frames_per_request, len(manifest["frames"]))
        trusted_frames = manifest["frames"][cursor:end]
        prompt = build_batch_prompt(trusted_frames, len(state["batches"]) + 1, final_batch_count)
        payload = provider.analyze(prompt, image_paths=frame_paths[cursor:end])
        batch = bind_model_batch(payload, trusted_frames)
        state["batches"].append(batch)
        cursor = end
        state["completed_frames"] = cursor
        atomic_json(batches_path, state)

    timeline = build_timeline(manifest, provider_identity, state["batches"])
    atomic_json(timeline_path, timeline)
    return {"note_id": note_id, "status": "completed", "frame_count": len(frame_paths)}


def main() -> int:
    parser = argparse.ArgumentParser(description="从既有宿主帧缓存生成可恢复的 MiMo-VL 完整时轴。")
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--visual-cache-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mimo-model", required=True)
    parser.add_argument("--mimo-python", type=Path)
    parser.add_argument("--note-id", action="append", default=[])
    parser.add_argument("--skip-note-id", action="append", default=[])
    parser.add_argument("--max-frames-per-request", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--startup-timeout", type=int, default=1800)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args()
    if not 1 <= args.max_frames_per_request <= 6:
        parser.error("--max-frames-per-request 必须为 1 到 6")

    order, analysis_by_id = index_analysis(load_json(args.analysis))
    selected = select_note_ids(order, args.note_id, args.skip_note_id)
    cache_root = args.visual_cache_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    frame_paths_by_id = {
        note_id: resolve_frame_paths(note_id, analysis_by_id[note_id]["evidence_manifest"], cache_root)
        for note_id in selected
    }
    mimo_python = args.mimo_python or (resolve_mimo_vl_root() / ".venv" / "bin" / "python")
    provider = MimoVlMlxProvider(
        model=args.mimo_model,
        python_bin=mimo_python,
        worker_script=ROOT / "scripts" / "mimo_vl_worker.py",
        timeout=args.timeout,
        startup_timeout=args.startup_timeout,
        max_tokens=args.max_tokens,
        working_directory=ROOT,
    )
    results: list[dict] = []
    try:
        for note_id in selected:
            try:
                results.append(run_note(
                    note_id,
                    analysis_by_id[note_id]["evidence_manifest"],
                    frame_paths_by_id[note_id],
                    output_dir,
                    provider,
                    args.max_frames_per_request,
                ))
            except ProviderError as exc:
                print(
                    json.dumps(
                        {"note_id": note_id, "provider_error": exc.to_dict()},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                raise
    finally:
        provider.close()
    print(json.dumps({"results": results, "output_dir": str(output_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
