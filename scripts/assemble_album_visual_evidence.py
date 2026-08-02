#!/usr/bin/env python3
"""Assemble strictly bound MiMo audio and MiMo-VL album evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from analyze_video_visuals import validate_visual_evidence_manifest, visual_evidence_sha256


BUNDLE_CONTRACT = "xiaohongshu.album.visual_evidence.v1"
EVIDENCE_VERSION = "watchbrief_v5.visual_evidence.v1"
PROMPT_VERSION = "watchbrief_v5.mimo_visual_prompt.v1"
TIMELINE_CONTRACT = "xiaohongshu.mimo_vl_timeline.v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_sha256(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def require_sha256(value: Any, label: str) -> str:
    text = str(value or "")
    if SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"{label} 不是有效 sha256")
    return text


def require_finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 不是数值")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} 不是有限数值")
    return result


def index_rows(rows: Any, label: str) -> tuple[list[str], dict[str, dict]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label} 必须是非空数组")
    order: list[str] = []
    indexed: dict[str, dict] = {}
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{label}[{position}] 必须是对象")
        note_id = str(row.get("id") or "").strip()
        if not note_id:
            raise ValueError(f"{label}[{position}] 缺少 id")
        if note_id in indexed:
            raise ValueError(f"{label} 包含重复 id：{note_id}")
        order.append(note_id)
        indexed[note_id] = row
    return order, indexed


def validate_transcript(note_id: str, transcript: dict) -> tuple[list[dict], str]:
    if transcript.get("status") != "success" or transcript.get("source_kind") != "mimo_audio":
        raise ValueError(f"MiMo 听觉文字稿未成功：{note_id}")
    coverage = transcript.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("transcript_quality_passed") is not True:
        raise ValueError(f"MiMo 听觉文字稿未通过质量门：{note_id}")
    segments = transcript.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError(f"MiMo 听觉文字稿缺少分段：{note_id}")
    for position, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"MiMo 听觉分段格式无效：{note_id}#{position}")
        start = require_finite_number(segment.get("start"), f"MiMo 听觉分段 start：{note_id}#{position}")
        end = require_finite_number(segment.get("end"), f"MiMo 听觉分段 end：{note_id}#{position}")
        if start < 0 or end <= start or not str(segment.get("text") or "").strip():
            raise ValueError(f"MiMo 听觉分段内容无效：{note_id}#{position}")
    transcript_hash = stable_sha256(segments)
    if transcript_hash != require_sha256(
        transcript.get("transcript_sha256"),
        f"MiMo 听觉文字稿 hash：{note_id}",
    ):
        raise ValueError(f"MiMo 听觉文字稿 hash 不一致：{note_id}")
    return segments, transcript_hash


def validate_timeline(note_id: str, manifest: dict, timeline: dict) -> None:
    validate_visual_evidence_manifest(manifest)
    if timeline.get("contract") != TIMELINE_CONTRACT:
        raise ValueError(f"MiMo-VL 时间线合同无效：{note_id}")
    provider = timeline.get("provider")
    if not isinstance(provider, dict) or provider.get("provider") != "mimo-vl-mlx":
        raise ValueError(f"MiMo-VL 时间线 provider 无效：{note_id}")
    if timeline.get("video_sha256") != manifest.get("video_sha256"):
        raise ValueError(f"MiMo-VL 时间线视频 hash 不一致：{note_id}")

    manifest_sampling = manifest["sampling"]
    timeline_sampling = timeline.get("sampling")
    if not isinstance(timeline_sampling, dict):
        raise ValueError(f"MiMo-VL 时间线缺少 sampling：{note_id}")
    sampling_pairs = (
        ("method", "method"),
        ("requested_max_gap_sec", "requested_max_gap_sec"),
        ("observed_max_gap_sec", "observed_max_gap_sec"),
        ("includes_start", "includes_start"),
        ("includes_end", "includes_end"),
        ("timestamps_sec", "timestamps_sec"),
    )
    for manifest_key, timeline_key in sampling_pairs:
        if timeline_sampling.get(timeline_key) != manifest_sampling.get(manifest_key):
            raise ValueError(f"MiMo-VL 时间线 sampling 不一致：{note_id}#{timeline_key}")

    manifest_frames = manifest["frames"]
    analyzed_frames = timeline.get("frames")
    if not isinstance(analyzed_frames, list) or len(analyzed_frames) != len(manifest_frames):
        raise ValueError(f"MiMo-VL 时间线帧数不一致：{note_id}")
    for position, (host_frame, analyzed) in enumerate(zip(manifest_frames, analyzed_frames)):
        if not isinstance(analyzed, dict):
            raise ValueError(f"MiMo-VL 时间线帧格式无效：{note_id}#{position}")
        expected = (host_frame.get("index"), host_frame.get("timestamp_sec"), host_frame.get("sha256"))
        actual = (analyzed.get("index"), analyzed.get("timestamp_sec"), analyzed.get("sha256"))
        if expected != actual or expected[0] != position:
            raise ValueError(f"MiMo-VL 帧 index/timestamp/hash 不一致：{note_id}#{position}")
        if not isinstance(analyzed.get("visible_text"), list) or not isinstance(analyzed.get("actions"), list):
            raise ValueError(f"MiMo-VL 帧分析数组字段无效：{note_id}#{position}")
        for field in ("observation", "uncertainty"):
            if not isinstance(analyzed.get(field), str):
                raise ValueError(f"MiMo-VL 帧分析字段无效：{note_id}#{position}#{field}")
    if not str(timeline.get("overall_visual_summary") or "").strip():
        raise ValueError(f"MiMo-VL 时间线缺少总结：{note_id}")
    if not isinstance(timeline.get("batch_summaries"), list) or not timeline["batch_summaries"]:
        raise ValueError(f"MiMo-VL 时间线缺少批次总结：{note_id}")
    if not isinstance(timeline.get("visual_caveats"), list):
        raise ValueError(f"MiMo-VL 时间线 visual_caveats 无效：{note_id}")


def host_frames(manifest: dict) -> list[dict]:
    result = []
    for frame in manifest["frames"]:
        result.append({
            "index": frame["index"],
            "timestamp_seconds": frame["timestamp_sec"],
            "endpoint": frame.get("endpoint") or "",
            "sha256": frame["sha256"],
            "ocr_status": frame.get("ocr_status") or "",
            "ocr_text": frame.get("ocr_text") or "",
            "ocr_lines": frame.get("ocr_lines") if isinstance(frame.get("ocr_lines"), list) else [],
            "ocr_confidence": frame.get("ocr_confidence"),
            "ocr_provider": frame.get("ocr_provider") or "",
            "ocr_error": frame.get("ocr_error") or "",
        })
    return result


def analyzed_frames(timeline: dict) -> list[dict]:
    return [
        {
            "index": frame["index"],
            "timestamp_seconds": frame["timestamp_sec"],
            "sha256": frame["sha256"],
            "observation": frame["observation"],
            "visible_text": frame["visible_text"],
            "actions": frame["actions"],
            "uncertainty": frame["uncertainty"],
        }
        for frame in timeline["frames"]
    ]


def build_screen_text_timeline(note_id: str, manifest: dict) -> dict:
    frames = manifest["frames"]
    duration = float(manifest["duration_sec"])
    segments = []
    providers = set()
    for position, frame in enumerate(frames):
        if frame.get("ocr_status") != "ok":
            raise ValueError(f"宿主逐帧 OCR 未成功：{note_id}#{position}")
        provider = str(frame.get("ocr_provider") or "").strip()
        if not provider:
            raise ValueError(f"宿主逐帧 OCR 缺少 provider：{note_id}#{position}")
        providers.add(provider)
        text = str(frame.get("ocr_text") or "").strip()
        if not text:
            continue
        start = float(frame["timestamp_sec"])
        end = float(frames[position + 1]["timestamp_sec"]) if position + 1 < len(frames) else duration
        segments.append({
            "start": start,
            "end": end,
            "text": text,
            "sample_frame_sha256": frame["sha256"],
        })
    if len(providers) != 1:
        raise ValueError(f"宿主 manifest OCR provider 不唯一：{note_id}")
    sampling = manifest["sampling"]
    return {
        "provider": providers.pop(),
        "requested_max_gap_seconds": sampling["requested_max_gap_sec"],
        "observed_max_gap_seconds": sampling["observed_max_gap_sec"],
        "includes_start": sampling["includes_start"],
        "includes_end": sampling["includes_end"],
        "verbatim_visible_text": True,
        "text_detected": bool(segments),
        "segments": segments,
    }


def with_visual_hash(evidence: dict) -> dict:
    result = deepcopy(evidence)
    result.pop("visual_evidence_hash", None)
    result["visual_evidence_hash"] = stable_sha256(result)
    return result


def assemble_timeline_evidence(
    note_id: str,
    manifest: dict,
    transcript: dict,
    timeline: dict,
    report_text_track: str,
) -> dict:
    segments, transcript_hash = validate_transcript(note_id, transcript)
    validate_timeline(note_id, manifest, timeline)
    sampling = manifest["sampling"]
    screen_text_timeline = build_screen_text_timeline(note_id, manifest)
    if report_text_track == "screen_text" and not screen_text_timeline["segments"]:
        raise ValueError(f"不能把空屏幕文字轨设为主内容：{note_id}")
    evidence = {
        "evidence_version": EVIDENCE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "provider": deepcopy(timeline["provider"]),
        "inference": {"batch_count": len(timeline["batch_summaries"])},
        "video_sha256": manifest["video_sha256"],
        "duration_seconds": manifest["duration_sec"],
        "sampling": {
            "method": sampling["method"],
            "requested_max_gap_seconds": sampling["requested_max_gap_sec"],
            "observed_max_gap_seconds": sampling["observed_max_gap_sec"],
            "includes_start": sampling["includes_start"],
            "includes_end": sampling["includes_end"],
            "timestamps_seconds": list(sampling["timestamps_sec"]),
        },
        "frames": host_frames(manifest),
        "analysis": {
            "overall_visual_summary": timeline["overall_visual_summary"],
            "batch_summaries": deepcopy(timeline["batch_summaries"]),
            "frames": analyzed_frames(timeline),
            "visual_caveats": deepcopy(timeline["visual_caveats"]),
        },
        "screen_text_timeline": screen_text_timeline,
        "transcript_sha256": transcript_hash,
        "frame_manifest_sha256": visual_evidence_sha256(manifest),
        "audio_evidence": {
            "provider": "mimo_audio",
            "transcript_sha256": transcript_hash,
            "segments": deepcopy(segments),
        },
        "report_text_track": report_text_track,
    }
    return with_visual_hash(evidence)


def validate_prebuilt_evidence(
    note_id: str,
    manifest: dict,
    transcript: dict,
    evidence: dict,
    report_text_track: str,
) -> dict:
    validate_visual_evidence_manifest(manifest)
    segments, transcript_hash = validate_transcript(note_id, transcript)
    if not isinstance(evidence, dict):
        raise ValueError(f"既有视觉证据格式无效：{note_id}")
    existing_hash = require_sha256(evidence.get("visual_evidence_hash"), f"既有视觉证据 hash：{note_id}")
    existing_material = deepcopy(evidence)
    existing_material.pop("visual_evidence_hash", None)
    if stable_sha256(existing_material) != existing_hash:
        raise ValueError(f"既有视觉证据 hash 不一致：{note_id}")
    provider = evidence.get("provider")
    if evidence.get("evidence_version") != EVIDENCE_VERSION:
        raise ValueError(f"既有视觉证据版本无效：{note_id}")
    if not isinstance(provider, dict) or provider.get("provider") != "mimo-vl-mlx":
        raise ValueError(f"既有视觉证据 provider 无效：{note_id}")
    if evidence.get("video_sha256") != manifest.get("video_sha256"):
        raise ValueError(f"既有视觉证据视频 hash 不一致：{note_id}")
    duration = require_finite_number(evidence.get("duration_seconds"), f"既有证据时长：{note_id}")
    if duration != float(manifest["duration_sec"]):
        raise ValueError(f"既有视觉证据时长不一致：{note_id}")
    sampling = evidence.get("sampling")
    frames = evidence.get("frames")
    analysis = evidence.get("analysis")
    analyzed = analysis.get("frames") if isinstance(analysis, dict) else None
    if not isinstance(sampling, dict) or sampling.get("includes_start") is not True or sampling.get("includes_end") is not True:
        raise ValueError(f"既有视觉证据未覆盖首尾：{note_id}")
    timestamps = sampling.get("timestamps_seconds")
    if not isinstance(frames, list) or not frames or not isinstance(analyzed, list):
        raise ValueError(f"既有视觉证据缺少帧：{note_id}")
    if not isinstance(timestamps, list) or len(frames) != len(timestamps) or len(frames) != len(analyzed):
        raise ValueError(f"既有视觉证据帧数不一致：{note_id}")
    if timestamps[0] != 0.0 or timestamps[-1] != duration:
        raise ValueError(f"既有视觉证据未覆盖首尾：{note_id}")
    for position, (timestamp, frame, analyzed_frame) in enumerate(zip(timestamps, frames, analyzed)):
        if not isinstance(frame, dict) or not isinstance(analyzed_frame, dict):
            raise ValueError(f"既有视觉证据帧格式无效：{note_id}#{position}")
        expected = (position, timestamp, require_sha256(frame.get("sha256"), f"既有帧 hash：{note_id}#{position}"))
        actual = (frame.get("index"), frame.get("timestamp_seconds"), frame.get("sha256"))
        analyzed_actual = (
            analyzed_frame.get("index"),
            analyzed_frame.get("timestamp_seconds"),
            analyzed_frame.get("sha256"),
        )
        if expected != actual or expected != analyzed_actual:
            raise ValueError(f"既有视觉证据 index/timestamp/hash 未严格绑定：{note_id}#{position}")
        expected_endpoint = "start" if position == 0 else "end" if position == len(frames) - 1 else ""
        if str(frame.get("endpoint") or "") != expected_endpoint:
            raise ValueError(f"既有视觉证据首尾帧标记无效：{note_id}#{position}")
        if "ocr_text" not in frame or not str(frame.get("ocr_provider") or "").strip():
            raise ValueError(f"既有视觉证据未保留宿主 OCR：{note_id}#{position}")

    if evidence.get("transcript_sha256") != transcript_hash:
        raise ValueError(f"既有视觉证据未绑定听觉文字稿：{note_id}")
    audio = evidence.get("audio_evidence")
    if not isinstance(audio, dict) or audio.get("provider") != "mimo_audio":
        raise ValueError(f"既有视觉证据缺少 MiMo 听觉证据：{note_id}")
    if audio.get("transcript_sha256") != transcript_hash or audio.get("segments") != segments:
        raise ValueError(f"既有视觉证据的听觉文字稿未严格绑定：{note_id}")
    screen_track = evidence.get("screen_text_timeline")
    screen_segments = screen_track.get("segments") if isinstance(screen_track, dict) else None
    if not isinstance(screen_track, dict) or screen_track.get("verbatim_visible_text") is not True:
        raise ValueError(f"既有视觉证据缺少逐字屏幕文字：{note_id}")
    if not isinstance(screen_segments, list):
        raise ValueError(f"既有视觉证据缺少屏幕文字分段数组：{note_id}")
    if (
        not isinstance(screen_track.get("text_detected"), bool)
        or screen_track["text_detected"] != bool(screen_segments)
    ):
        raise ValueError(f"既有视觉证据屏幕文字状态不一致：{note_id}")
    if report_text_track == "screen_text" and not screen_segments:
        raise ValueError(f"既有视觉证据把空屏幕文字轨设为主内容：{note_id}")
    for position, segment in enumerate(screen_segments):
        if not isinstance(segment, dict) or not str(segment.get("text") or "").strip():
            raise ValueError(f"既有屏幕文字分段无效：{note_id}#{position}")
        require_sha256(segment.get("sample_frame_sha256"), f"屏幕文字帧 hash：{note_id}#{position}")

    result = deepcopy(evidence)
    result["report_text_track"] = report_text_track
    return with_visual_hash(result)


def assemble_bundle(
    analysis_rows: Any,
    transcript_rows: Any,
    timelines_dir: Path,
    prebuilt_bundle: Any | None,
    screen_text_note_ids: set[str],
) -> dict:
    order, analysis_by_id = index_rows(analysis_rows, "analysis")
    transcript_order, transcripts_by_id = index_rows(transcript_rows, "transcripts")
    if set(order) != set(transcript_order):
        missing = sorted(set(order) - set(transcript_order))
        extra = sorted(set(transcript_order) - set(order))
        raise ValueError(f"分析与文字稿 id 不一致：missing={missing}, extra={extra}")
    unknown_screen_ids = sorted(screen_text_note_ids - set(order))
    if unknown_screen_ids:
        raise ValueError(f"--screen-text-note-id 不属于本批视频：{unknown_screen_ids}")

    if prebuilt_bundle is None:
        prebuilt_items = {}
    else:
        if (
            not isinstance(prebuilt_bundle, dict)
            or prebuilt_bundle.get("contract") != BUNDLE_CONTRACT
        ):
            raise ValueError("既有视觉证据包合同无效")
        prebuilt_items = prebuilt_bundle.get("items")
        if not isinstance(prebuilt_items, dict):
            raise ValueError("既有视觉证据包没有 items 对象")
    unknown_prebuilt_ids = sorted(set(prebuilt_items) - set(order))
    if unknown_prebuilt_ids:
        raise ValueError(f"既有证据包包含未知 id：{unknown_prebuilt_ids}")

    items: dict[str, dict] = {}
    for note_id in order:
        row = analysis_by_id[note_id]
        manifest = row.get("evidence_manifest")
        if not isinstance(manifest, dict):
            raise ValueError(f"分析缺少 evidence_manifest：{note_id}")
        report_text_track = "screen_text" if note_id in screen_text_note_ids else "mimo_audio"
        if note_id in prebuilt_items:
            items[note_id] = validate_prebuilt_evidence(
                note_id,
                manifest,
                transcripts_by_id[note_id],
                prebuilt_items[note_id],
                report_text_track,
            )
            continue
        timeline_path = timelines_dir / note_id / "mimo_vl_timeline.json"
        if not timeline_path.is_file():
            raise ValueError(f"缺少 MiMo-VL 时间线：{timeline_path}")
        items[note_id] = assemble_timeline_evidence(
            note_id,
            manifest,
            transcripts_by_id[note_id],
            load_json(timeline_path),
            report_text_track,
        )
    return {"contract": BUNDLE_CONTRACT, "items": items}


def main() -> int:
    parser = argparse.ArgumentParser(description="装配严格绑定的小红书专辑 MiMo 多模态证据包。")
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--transcripts", required=True, type=Path)
    parser.add_argument("--visual-timelines-dir", required=True, type=Path)
    parser.add_argument(
        "--prebuilt-evidence",
        type=Path,
        help="可选；需要保留独立密集采样时传入已校验的视觉证据 bundle",
    )
    parser.add_argument("--screen-text-note-id", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    bundle = assemble_bundle(
        load_json(args.analysis),
        load_json(args.transcripts),
        args.visual_timelines_dir,
        load_json(args.prebuilt_evidence) if args.prebuilt_evidence else None,
        set(args.screen_text_note_id),
    )
    atomic_json(args.output, bundle)
    print(json.dumps({"output": str(args.output), "item_count": len(bundle["items"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
