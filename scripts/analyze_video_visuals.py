#!/usr/bin/env python3
"""Classify confirmed Xiaohongshu videos from full-timeline frame evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable

from analyze_video_transcripts import ANALYSIS_OUTPUT_CONTRACT, validate_analysis
from video_analysis_provider import ProviderError, build_analysis_provider
from video_content_common import (
    MIMO_VL_MODEL_SUBDIR,
    canonical_xiaohongshu_note_url,
    load_arc_collection_note_contexts,
    load_video_transcript_module,
    normalize_content_type,
    redact_sensitive_text,
    resolve_mimo_vl_root,
    safe_error,
    transcript_sha256,
    xiaohongshu_access_url,
)
from xhs_ocr_common import load_taxonomy
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "video_analysis.schema.json"
OCR_SCRIPT = ROOT / "scripts" / "ocr_image.swift"
VISUAL_EVIDENCE_SCHEMA_VERSION = 1
VISUAL_ANALYSIS_PROMPT_CONTRACT_VERSION = 4
DEFAULT_MAX_FRAME_GAP_SECONDS = 10.0


class VisualPipelineError(RuntimeError):
    """A failure with a stable reason code and a credential-safe message."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def atomic_write_json(path: Path, data: Any) -> None:
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


def load_json_list(path: Path, label: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"{label} 必须是对象数组")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def deterministic_sample_timestamps(duration_sec: float, max_gap_sec: float) -> list[float]:
    """Return deterministic full-timeline samples containing both endpoints."""
    duration = float(duration_sec)
    max_gap = float(max_gap_sec)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("视频时长必须是正数")
    if not math.isfinite(max_gap) or max_gap <= 0:
        raise ValueError("最大抽帧间隔必须是正数")
    # Five points are the minimum needed to observe a short action instead of
    # mistaking two endpoint poses for the video's subject.
    interval_count = max(4, int(math.ceil(duration / max_gap)))
    timestamps = [round(duration * index / interval_count, 6) for index in range(interval_count + 1)]
    timestamps[0] = 0.0
    timestamps[-1] = round(duration, 6)
    return timestamps


def observed_max_gap(timestamps: Iterable[float]) -> float:
    values = list(timestamps)
    if len(values) < 2:
        return 0.0
    return round(max(right - left for left, right in zip(values, values[1:])), 6)


def stable_visual_evidence_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return exactly the evidence fields that determine model-visible visuals."""
    sampling = manifest.get("sampling") if isinstance(manifest.get("sampling"), dict) else {}
    frames = manifest.get("frames") if isinstance(manifest.get("frames"), list) else []
    stable_frames = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("evidence_manifest.frames 必须只包含对象")
        stable_frames.append({
            "index": int(frame.get("index")),
            "timestamp_sec": float(frame.get("timestamp_sec")),
            "endpoint": str(frame.get("endpoint") or ""),
            "filename": str(frame.get("filename") or ""),
            "sha256": str(frame.get("sha256") or ""),
            "ocr_status": str(frame.get("ocr_status") or ""),
            "ocr_text": str(frame.get("ocr_text") or ""),
            "ocr_confidence": frame.get("ocr_confidence"),
            "ocr_provider": str(frame.get("ocr_provider") or ""),
            "ocr_error": str(frame.get("ocr_error") or ""),
        })
    return {
        "schema_version": int(manifest.get("schema_version") or 0),
        "video_sha256": str(manifest.get("video_sha256") or ""),
        "duration_sec": float(manifest.get("duration_sec")),
        "sampling": {
            "method": str(sampling.get("method") or ""),
            "requested_max_gap_sec": float(sampling.get("requested_max_gap_sec")),
            "observed_max_gap_sec": float(sampling.get("observed_max_gap_sec")),
            "includes_start": sampling.get("includes_start") is True,
            "includes_end": sampling.get("includes_end") is True,
            "timestamps_sec": [float(value) for value in sampling.get("timestamps_sec", [])],
        },
        "frames": stable_frames,
    }


def validate_visual_evidence_manifest(manifest: dict[str, Any]) -> None:
    stable = stable_visual_evidence_payload(manifest)
    if stable["schema_version"] != VISUAL_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("evidence_manifest schema_version 不受支持")
    if re.fullmatch(r"[0-9a-f]{64}", stable["video_sha256"]) is None:
        raise ValueError("evidence_manifest video_sha256 无效")
    duration = stable["duration_sec"]
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("evidence_manifest duration_sec 无效")
    sampling = stable["sampling"]
    timestamps = sampling["timestamps_sec"]
    frames = stable["frames"]
    if len(timestamps) < 2 or len(frames) != len(timestamps):
        raise ValueError("evidence_manifest 必须覆盖首尾且每个时间戳对应一帧")
    if timestamps[0] != 0.0 or abs(timestamps[-1] - duration) > 1e-5:
        raise ValueError("evidence_manifest 没有覆盖视频首尾")
    if any(not math.isfinite(value) for value in timestamps):
        raise ValueError("evidence_manifest 包含无效时间戳")
    if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError("evidence_manifest 时间戳必须严格递增")
    requested_gap = sampling["requested_max_gap_sec"]
    recorded_gap = sampling["observed_max_gap_sec"]
    actual_gap = observed_max_gap(timestamps)
    if not math.isfinite(requested_gap) or requested_gap <= 0:
        raise ValueError("evidence_manifest 最大抽帧间隔无效")
    if abs(recorded_gap - actual_gap) > 1e-5 or actual_gap - requested_gap > 1e-5:
        raise ValueError("evidence_manifest 抽帧间隔记录不一致")
    if sampling["includes_start"] is not True or sampling["includes_end"] is not True:
        raise ValueError("evidence_manifest 首尾覆盖标记无效")
    for index, (frame, timestamp) in enumerate(zip(frames, timestamps)):
        if frame["index"] != index or abs(frame["timestamp_sec"] - timestamp) > 1e-5:
            raise ValueError("evidence_manifest 帧顺序或时间戳不一致")
        expected_endpoint = "start" if index == 0 else "end" if index == len(frames) - 1 else ""
        if frame["endpoint"] != expected_endpoint:
            raise ValueError("evidence_manifest 首尾帧标记不一致")
        if not frame["filename"] or Path(frame["filename"]).name != frame["filename"]:
            raise ValueError("evidence_manifest 帧文件名必须是安全的相对文件名")
        if re.fullmatch(r"[0-9a-f]{64}", frame["sha256"]) is None:
            raise ValueError("evidence_manifest 帧 sha256 无效")


def visual_evidence_sha256(manifest: dict[str, Any]) -> str:
    validate_visual_evidence_manifest(manifest)
    return canonical_sha256(stable_visual_evidence_payload(manifest))


def analysis_input_sha256(
    *,
    evidence_hash: str,
    transcript_hash: str,
    boards: list[str],
    provider_identity: dict[str, Any],
) -> str:
    return canonical_sha256({
        "prompt_contract_version": VISUAL_ANALYSIS_PROMPT_CONTRACT_VERSION,
        "visual_evidence_sha256": evidence_hash,
        "transcript_sha256": transcript_hash,
        "allowed_boards": boards,
        "analysis_provider": provider_identity,
    })


def qualified_transcript(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return only a transcript that already passed the deterministic quality gate."""
    if not isinstance(row, dict) or row.get("status") != "success":
        return None
    segments = row.get("segments")
    coverage = row.get("coverage")
    if not isinstance(segments, list) or not segments:
        return None
    if not isinstance(coverage, dict) or coverage.get("transcript_quality_passed") is not True:
        return None
    expected_hash = transcript_sha256(segments)
    if str(row.get("transcript_sha256") or "") != expected_hash:
        return None
    return {
        "transcript_sha256": expected_hash,
        "source_kind": str(row.get("source_kind") or ""),
        "segments": segments,
    }


def build_visual_prompt(
    evidence_manifest: dict[str, Any],
    transcript: dict[str, Any] | None,
    boards: list[str],
) -> str:
    """Build a metadata-free prompt from frames, frame OCR, and optional transcript."""
    stable = stable_visual_evidence_payload(evidence_manifest)
    frame_evidence = [{
        "index": frame["index"],
        "timestamp_sec": frame["timestamp_sec"],
        "sha256": frame["sha256"],
        "ocr_status": frame["ocr_status"],
        "ocr_text": frame["ocr_text"],
        "ocr_confidence": frame["ocr_confidence"],
    } for frame in stable["frames"]]
    transcript_evidence = transcript if transcript is not None else {"available": False}
    return (
        "你只执行一次视频真实画面理解与分类，不运行工具、不读取其他文件。\n"
        "附件图片就是该视频按完整时轴等间隔抽取的真实单帧，按 frame_evidence 的 index 和时间戳顺序对应。"
        "必须先判断画面持续展示的主要对象、动作和用途；背景音乐或歌词与画面冲突时，以画面主体为准。\n"
        "只可使用附件画面、逐帧 Vision OCR 和通过质量门的转写。严禁根据标题、简介、作者、标签、热度、封面或文件外信息猜测。\n"
        "专辑名代表上位主题；主要内容是某个专辑的明确子主题时就应选择该专辑，不能只因内容更具体而留空。\n"
        "OCR 为空或错误不代表视频失败，仍须直接观察附件画面。若画面证据不足以确定任何现有专辑，"
        "target_board 返回空字符串且 confidence 必须为 low；不得创造新专辑。\n"
        f"允许专辑：{json.dumps(boards, ensure_ascii=False)}\n"
        f"时轴证据：{json.dumps({'duration_sec': stable['duration_sec'], 'sampling': stable['sampling'], 'frame_evidence': frame_evidence}, ensure_ascii=False)}\n"
        f"合格转写：{json.dumps(transcript_evidence, ensure_ascii=False)}\n"
        f"输出极简中文 memo。{ANALYSIS_OUTPUT_CONTRACT}"
    )


def run_frame_ocr(
    image_path: Path,
    *,
    swift_script: Path = OCR_SCRIPT,
    timeout: int = 120,
) -> dict[str, Any]:
    swift_bin = shutil.which("swift") or ("/usr/bin/swift" if Path("/usr/bin/swift").exists() else "")
    if platform.system() != "Darwin" or not swift_bin or not swift_script.is_file():
        return {
            "ocr_status": "unavailable",
            "ocr_text": "",
            "ocr_lines": [],
            "ocr_confidence": None,
            "ocr_provider": "macos_vision",
            "ocr_error": "macos_vision_unavailable",
        }
    try:
        result = subprocess.run(
            [swift_bin, str(swift_script), str(image_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Vision OCR failed")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError("Vision OCR 返回值不是对象")
        lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
        return {
            "ocr_status": "ok",
            "ocr_text": str(payload.get("text") or "").strip(),
            "ocr_lines": [str(line) for line in lines],
            "ocr_confidence": payload.get("average_confidence"),
            "ocr_provider": "macos_vision",
            "ocr_error": "",
        }
    except Exception as exc:
        return {
            "ocr_status": "error",
            "ocr_text": "",
            "ocr_lines": [],
            "ocr_confidence": None,
            "ocr_provider": "macos_vision",
            "ocr_error": safe_error(exc),
        }


def probe_duration(video_path: Path, *, ffprobe_bin: str = "ffprobe", timeout: int = 120) -> float:
    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        raise VisualPipelineError("ffprobe_failed", safe_error(exc)) from exc
    if result.returncode != 0:
        message = redact_sensitive_text(result.stderr or result.stdout or "ffprobe failed")
        raise VisualPipelineError("ffprobe_failed", message[:500])
    try:
        payload = json.loads(result.stdout)
        duration = float((payload.get("format") or {}).get("duration"))
    except Exception as exc:
        raise VisualPipelineError("video_duration_invalid", safe_error(exc)) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise VisualPipelineError("video_duration_invalid", "ffprobe 没有返回有效正时长")
    return duration


def extract_frame(
    video_path: Path,
    destination: Path,
    *,
    timestamp_sec: float,
    is_last: bool,
    duration_sec: float,
    ffmpeg_bin: str = "ffmpeg",
    timeout: int = 180,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    video_filter: list[str] = []
    if is_last:
        # Decode at most the final second, reverse that bounded tail, and take
        # its first output frame. This is the final decodable frame, unlike a
        # seek exactly to duration (which legitimately produces no frame).
        tail_window = min(1.0, duration_sec)
        seek = ["-sseof", f"-{tail_window:.6f}"]
        video_filter = ["-vf", "reverse"]
    else:
        seek = ["-ss", f"{max(0.0, timestamp_sec):.6f}"]
    command = [
        ffmpeg_bin,
        "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        *seek,
        "-i", str(video_path),
        "-map", "0:v:0",
        *video_filter,
        "-frames:v", "1",
        "-pix_fmt", "yuvj420p",
        "-q:v", "2",
        str(destination),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        raise VisualPipelineError("frame_extraction_failed", safe_error(exc)) from exc
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        message = redact_sensitive_text(result.stderr or result.stdout or "ffmpeg produced no frame")
        raise VisualPipelineError("frame_extraction_failed", message[:500])


def build_evidence_manifest(
    video_path: Path,
    work_dir: Path,
    *,
    max_gap_sec: float,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    frame_ocr: Callable[[Path], dict[str, Any]] = run_frame_ocr,
) -> tuple[dict[str, Any], list[Path]]:
    duration = probe_duration(video_path, ffprobe_bin=ffprobe_bin)
    timestamps = deterministic_sample_timestamps(duration, max_gap_sec)
    frames_dir = work_dir / "frames"
    frame_rows: list[dict[str, Any]] = []
    frame_paths: list[Path] = []
    for index, timestamp in enumerate(timestamps):
        endpoint = "start" if index == 0 else "end" if index == len(timestamps) - 1 else ""
        filename = f"frame_{index:04d}_{int(round(timestamp * 1000)):012d}ms.jpg"
        path = frames_dir / filename
        extract_frame(
            video_path,
            path,
            timestamp_sec=timestamp,
            is_last=index == len(timestamps) - 1,
            duration_sec=duration,
            ffmpeg_bin=ffmpeg_bin,
        )
        ocr = frame_ocr(path)
        frame_rows.append({
            "index": index,
            "timestamp_sec": timestamp,
            "endpoint": endpoint,
            "filename": filename,
            "sha256": sha256_file(path),
            "ocr_status": str(ocr.get("ocr_status") or "error"),
            "ocr_text": str(ocr.get("ocr_text") or ""),
            "ocr_lines": list(ocr.get("ocr_lines") or []),
            "ocr_confidence": ocr.get("ocr_confidence"),
            "ocr_provider": str(ocr.get("ocr_provider") or "macos_vision"),
            "ocr_error": str(ocr.get("ocr_error") or ""),
        })
        frame_paths.append(path)
    manifest = {
        "schema_version": VISUAL_EVIDENCE_SCHEMA_VERSION,
        "video_sha256": sha256_file(video_path),
        "duration_sec": round(duration, 6),
        "sampling": {
            "method": "uniform_full_timeline_endpoints_v1",
            "requested_max_gap_sec": float(max_gap_sec),
            "observed_max_gap_sec": observed_max_gap(timestamps),
            "includes_start": timestamps[0] == 0.0,
            "includes_end": timestamps[-1] == round(duration, 6),
            "timestamps_sec": timestamps,
        },
        "frames": frame_rows,
    }
    return manifest, frame_paths


def export_arc_cookie_file(module: Any, cache_root: Path, *, profile: str) -> Path:
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_root.chmod(0o700)
    cookie_path = cache_root / "arc-cookies.txt"
    descriptor = os.open(cookie_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    os.close(descriptor)
    cookie_path.chmod(0o600)
    try:
        module.export_arc_cookies(cookie_path, profile=profile)
        cookie_path.chmod(0o600)
    except Exception:
        cookie_path.unlink(missing_ok=True)
        raise
    return cookie_path


def download_video(
    *,
    module: Any,
    access_url: str,
    cookie_file: Path,
    work_dir: Path,
    yt_dlp_bin: str = "yt-dlp",
    timeout: int = 1800,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    template = work_dir / "video.%(ext)s"
    command = [
        yt_dlp_bin,
        "--no-playlist",
        "--no-progress",
        "--quiet",
        "--no-warnings",
        "--format", "bestvideo*+bestaudio/best",
        "--merge-output-format", "mp4",
        "--output", str(template),
        access_url,
    ]
    command = module.add_cookie_args(command, cookies_from_browser=None, cookie_file=cookie_file)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        raise VisualPipelineError("video_download_failed", safe_error(exc)) from exc
    if result.returncode != 0:
        message = redact_sensitive_text(result.stderr or result.stdout or "yt-dlp failed")
        raise VisualPipelineError("video_download_failed", message[:500])
    candidates = [
        path for path in work_dir.glob("video.*")
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl", ".json", ".txt"}
    ]
    if not candidates:
        raise VisualPipelineError("video_download_missing", "yt-dlp 完成但没有找到视频文件")
    return max(candidates, key=lambda path: path.stat().st_size)


def acquire_visual_evidence(
    item: dict[str, Any],
    *,
    module: Any,
    arc_contexts: dict[str, dict[str, Any]],
    cookie_file: Path,
    work_dir: Path,
    max_gap_sec: float,
    download_timeout: int,
    ffmpeg_bin: str,
    ffprobe_bin: str,
) -> dict[str, Any]:
    note_id = str(item.get("id") or "").strip()
    canonical_url = canonical_xiaohongshu_note_url(item)
    if not note_id or not canonical_url:
        return failure_row(note_id, "canonical_note_url_missing", "缺少标准视频地址")
    context = arc_contexts.get(note_id)
    if not isinstance(context, dict) or context.get("found") is not True:
        return failure_row(note_id, "arc_collection_context_missing", "Arc 最新收藏缓存缺少这条视频的会话参数")
    if context.get("content_type") != "video":
        return failure_row(note_id, "content_type_mismatch", "Arc 收藏接口没有把这条笔记标记为视频")
    try:
        access_url = xiaohongshu_access_url(canonical_url, context)
        video_path = download_video(
            module=module,
            access_url=access_url,
            cookie_file=cookie_file,
            work_dir=work_dir,
            timeout=download_timeout,
        )
        manifest, frame_paths = build_evidence_manifest(
            video_path,
            work_dir,
            max_gap_sec=max_gap_sec,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )
        return {
            "id": note_id,
            "status": "success",
            "evidence_manifest": manifest,
            "frame_paths": frame_paths,
        }
    except VisualPipelineError as exc:
        return failure_row(note_id, exc.reason_code, redact_sensitive_text(exc)[:500])
    except Exception as exc:
        return failure_row(note_id, "visual_evidence_unavailable", safe_error(exc))


def analyze_with_provider(
    *,
    manifest: dict[str, Any],
    frame_paths: list[Path],
    transcript: dict[str, Any] | None,
    boards: list[str],
    provider: Any,
) -> dict[str, Any]:
    if not frame_paths:
        return failure_row("", "visual_frames_missing", "没有可交给视觉分析器的真实视频帧")
    try:
        payload = provider.analyze(build_visual_prompt(manifest, transcript, boards), image_paths=frame_paths)
        normalized = validate_analysis(payload, boards)
    except ProviderError as exc:
        row = failure_row(
            "",
            str(getattr(exc, "reason_code", "analysis_provider_failed")),
            safe_error(exc),
        )
        metadata = getattr(exc, "metadata", {})
        if isinstance(metadata, dict):
            row.update({
                "returncode": metadata.get("returncode"),
                "stdout": str(metadata.get("stdout") or "")[:500],
                "stderr": str(metadata.get("stderr") or "")[:500],
            })
        return row
    except Exception as exc:
        return failure_row("", "analysis_provider_invalid_result", safe_error(exc))
    return {"status": "success", **normalized, "error": ""}


def failure_row(note_id: str, reason_code: str, error: str) -> dict[str, Any]:
    return {
        "id": note_id,
        "status": "failed",
        "stage": "visual_content_analysis",
        "reason_code": str(reason_code or "visual_content_unavailable"),
        "main_topic": "",
        "content_summary": "",
        "target_board": "",
        "confidence": "low",
        "reason": [str(reason_code or "visual_content_unavailable")],
        "error": redact_sensitive_text(error)[:500],
        "analysis_basis": "full_timeline_visual",
        "visual_status": "failed",
    }


def index_unique_rows(rows: list[dict[str, Any]], label: str) -> tuple[list[str], dict[str, dict[str, Any]]]:
    order: list[str] = []
    mapping: dict[str, dict[str, Any]] = {}
    for row in rows:
        note_id = str(row.get("id") or "").strip()
        if not note_id:
            raise ValueError(f"{label} 包含缺少 ID 的条目")
        if note_id in mapping:
            raise ValueError(f"{label} 包含重复 ID：{note_id}")
        order.append(note_id)
        mapping[note_id] = row
    return order, mapping


def select_explicit_videos(items: list[dict[str, Any]], video_ids: list[str]) -> list[dict[str, Any]]:
    if not video_ids:
        raise ValueError("必须至少传一个 --video-id；禁止隐式处理全部视频")
    if len(video_ids) != len(set(video_ids)):
        raise ValueError("--video-id 不能重复")
    _, item_map = index_unique_rows(items, "visible_items.json")
    selected: list[dict[str, Any]] = []
    for note_id in video_ids:
        item = item_map.get(note_id)
        if item is None:
            raise ValueError(f"指定 ID 不在 visible_items.json：{note_id}")
        content_type = normalize_content_type(item.get("content_type") or item.get("note_type") or item.get("type"))
        if content_type != "video":
            raise ValueError(f"指定 ID 不是已确认视频：{note_id}")
        selected.append(item)
    return selected


def select_videos(
    items: list[dict[str, Any]],
    video_ids: list[str],
    *,
    all_videos: bool,
) -> list[dict[str, Any]]:
    """Select explicit IDs or every item already confirmed as a video."""
    if all_videos:
        if video_ids:
            raise ValueError("--all-videos 不能与 --video-id 同时使用")
        selected = [
            item for item in items
            if normalize_content_type(item.get("content_type") or item.get("note_type") or item.get("type")) == "video"
        ]
        if not selected:
            raise ValueError("visible_items.json 中没有已确认视频")
        index_unique_rows(selected, "visible_items.json 的视频集合")
        return selected
    return select_explicit_videos(items, video_ids)


def validate_full_analysis_contract(
    items: list[dict[str, Any]],
    base_rows: list[dict[str, Any]],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    visible_video_ids = [
        str(item.get("id") or "").strip()
        for item in items
        if normalize_content_type(item.get("content_type") or item.get("note_type") or item.get("type")) == "video"
    ]
    if any(not note_id for note_id in visible_video_ids):
        raise ValueError("visible_items.json 包含缺少 ID 的视频")
    if len(visible_video_ids) != len(set(visible_video_ids)):
        raise ValueError("visible_items.json 包含重复视频 ID")
    order, mapping = index_unique_rows(base_rows, "video_analysis.json")
    visible_set = set(visible_video_ids)
    analysis_set = set(order)
    if visible_set != analysis_set:
        missing = sorted(visible_set - analysis_set)
        extra = sorted(analysis_set - visible_set)
        raise ValueError(f"video_analysis.json 不是当前完整视频集合；missing={missing} extra={extra}")
    return order, mapping


def merge_selected_analysis(
    base_rows: list[dict[str, Any]],
    replacements: dict[str, dict[str, Any]],
    selected_ids: set[str],
) -> list[dict[str, Any]]:
    order, base_map = index_unique_rows(base_rows, "video_analysis.json")
    missing_selected = selected_ids - set(order)
    if missing_selected:
        raise ValueError("选中 ID 不在完整 video_analysis.json：" + ", ".join(sorted(missing_selected)))
    unexpected = set(replacements) - selected_ids
    if unexpected:
        raise ValueError("替换结果包含未选中的 ID：" + ", ".join(sorted(unexpected)))
    return [dict(replacements.get(note_id, base_map[note_id])) for note_id in order]


def resume_row_matches(
    row: dict[str, Any] | None,
    *,
    current_manifest: dict[str, Any],
    current_input_hash: str,
    boards: list[str],
) -> bool:
    if not isinstance(row, dict) or row.get("status") != "success":
        return False
    prior_manifest = row.get("evidence_manifest")
    if not isinstance(prior_manifest, dict):
        return False
    try:
        prior_hash = visual_evidence_sha256(prior_manifest)
        current_hash = visual_evidence_sha256(current_manifest)
        validate_analysis(row, boards)
    except (TypeError, ValueError):
        return False
    return (
        str(row.get("visual_evidence_sha256") or "") == prior_hash
        and prior_hash == current_hash
        and str(row.get("analysis_input_sha256") or "") == current_input_hash
    )


def validate_resume_rows(
    resume_rows: list[dict[str, Any]],
    base_order: list[str],
) -> dict[str, dict[str, Any]]:
    order, mapping = index_unique_rows(resume_rows, "resume video_analysis.json")
    if order != base_order:
        raise ValueError("resume video_analysis.json 的完整 ID 顺序与基准文件不一致")
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用明确选择的视觉分析器读取完整时轴画面，并严格合并回完整 video_analysis.json。"
    )
    parser.add_argument("visible_items", help="完整 visible_items.json")
    parser.add_argument("transcripts", help="完整 video_transcripts.json；失败项也应保留")
    parser.add_argument("analysis", help="待合并的完整 video_analysis.json")
    parser.add_argument("out", help="合并后的完整 video_analysis.json")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--video-id", action="append", help="明确选择一个视频 ID；可重复传入")
    selection.add_argument(
        "--all-videos",
        action="store_true",
        help="明确选择 visible_items.json 中全部已确认视频；视觉开关开启时使用",
    )
    parser.add_argument("--max-videos", type=int, help="--all-videos 时本次最多处理多少条；范围 1 到 200")
    parser.add_argument("--taxonomy")
    parser.add_argument("--browser", choices=("arc",), default="arc")
    parser.add_argument("--arc-profile", default="Default")
    parser.add_argument("--extractor-root")
    parser.add_argument("--cache-dir")
    parser.add_argument("--keep-cache", action="store_true", help="保留视频和逐帧证据；cookie 始终删除")
    parser.add_argument("--max-frame-gap-sec", type=float, default=DEFAULT_MAX_FRAME_GAP_SECONDS)
    parser.add_argument("--download-timeout", type=int, default=1800)
    parser.add_argument("--analysis-provider", required=True, choices=("codex-cli", "mimo-vl-mlx", "command"))
    parser.add_argument("--analysis-command", nargs="+", help="command provider 的可执行文件和固定参数；不经过 shell")
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--codex-model")
    parser.add_argument("--mimo-vl-python")
    parser.add_argument("--mimo-vl-model")
    parser.add_argument("--mimo-vl-root", help="默认读取 XHS_MIMO_VL_ROOT 或 ~/Documents/MiMo-VL-7B-RL-2508")
    parser.add_argument("--provider-startup-timeout", type=int, default=1800)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--ffmpeg-bin", default=shutil.which("ffmpeg") or "ffmpeg")
    parser.add_argument("--ffprobe-bin", default=shutil.which("ffprobe") or "ffprobe")
    parser.add_argument("--resume", action="store_true", help="只复用视觉证据哈希、转写哈希和专辑体系均匹配的成功项")
    parser.add_argument("--allow-video-access", action="store_true", help="明确同意本次访问所选视频；默认低风险模式不会请求视频页面或媒体")
    parser.add_argument("--safety-state", default="", help="共享安全状态文件；默认继承输入文件旁已有状态，否则使用输出同目录的 xhs_safety_state.json")
    args = parser.parse_args()

    if not args.allow_video_access:
        parser.error("默认低风险模式不会访问视频；请先人工确认本次范围，再明确传 --allow-video-access。")
    if args.all_videos and args.max_videos is None:
        parser.error("--all-videos 必须同时传 --max-videos；不会默认分析全部视频。")
    if args.max_videos is not None and (not isinstance(args.max_videos, int) or isinstance(args.max_videos, bool) or not 1 <= args.max_videos <= 200):
        parser.error("--max-videos 必须是 1 到 200 的整数")

    visible_path = Path(args.visible_items)
    transcripts_path = Path(args.transcripts)
    analysis_path = Path(args.analysis)
    out_path = Path(args.out)
    safety_state = resolve_safety_state_path(
        args.safety_state,
        out_path,
        predecessors=(visible_path, transcripts_path, analysis_path),
    )
    ensure_active_session(
        safety_state,
        stage="video_visual_analysis",
        policy={
            "auto_scroll": False,
            "auto_navigation": False,
            "auto_retry": False,
            "video_access_enabled": True,
            "video_selection": "all_videos" if args.all_videos else "video_id",
            "video_limit": args.max_videos if args.all_videos else len(set(args.video_id or [])),
        },
    )
    items = load_json_list(visible_path, "visible_items.json")
    transcript_rows = load_json_list(transcripts_path, "video_transcripts.json")
    base_rows = load_json_list(analysis_path, "video_analysis.json")
    selected_items = select_videos(items, list(args.video_id or []), all_videos=args.all_videos)
    if args.max_videos is not None:
        selected_items = selected_items[:args.max_videos]
    base_order, _base_map = validate_full_analysis_contract(items, base_rows)

    _, transcript_map = index_unique_rows(transcript_rows, "video_transcripts.json")
    visible_video_ids = {
        str(item.get("id") or "").strip()
        for item in items
        if normalize_content_type(item.get("content_type") or item.get("note_type") or item.get("type")) == "video"
    }
    extra_transcripts = set(transcript_map) - visible_video_ids
    if extra_transcripts:
        raise ValueError("video_transcripts.json 包含当前视频集合之外的 ID：" + ", ".join(sorted(extra_transcripts)))

    boards = load_taxonomy(Path(args.taxonomy)) if args.taxonomy else load_taxonomy(None)
    resume_map: dict[str, dict[str, Any]] = {}
    if args.resume and out_path.exists():
        resume_map = validate_resume_rows(load_json_list(out_path, "resume video_analysis.json"), base_order)

    cache_root = Path(args.cache_dir).expanduser() if args.cache_dir else out_path.parent / ".video-visual-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_root.chmod(0o700)
    cookie_file: Path | None = None
    replacements: dict[str, dict[str, Any]] = {}
    selected_ids = {str(item.get("id")) for item in selected_items}
    mimo_root = resolve_mimo_vl_root(args.mimo_vl_root)
    provider_model = (
        args.mimo_vl_model or str(mimo_root / MIMO_VL_MODEL_SUBDIR)
        if args.analysis_provider == "mimo-vl-mlx"
        else args.codex_model
        if args.analysis_provider == "codex-cli"
        else None
    )
    provider_python = args.mimo_vl_python or str(mimo_root / ".venv" / "bin" / "python")
    provider: Any | None = None
    try:
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
        )
        provider_identity = provider.identity()
    except Exception as exc:
        if provider is not None:
            provider.close()
        for item in selected_items:
            note_id = str(item.get("id") or "").strip()
            replacements[note_id] = failure_row(note_id, "analysis_provider_unavailable", safe_error(exc))
        merged = merge_selected_analysis(base_rows, replacements, selected_ids)
        atomic_write_json(out_path, merged)
        if not args.keep_cache:
            try:
                cache_root.rmdir()
            except OSError:
                pass
        print(json.dumps({
            "selected_count": len(replacements),
            "success_count": 0,
            "failed_count": len(replacements),
            "full_analysis_count": len(merged),
            "output": str(out_path),
        }, ensure_ascii=False, indent=2))
        return 1
    try:
        try:
            module = load_video_transcript_module(args.extractor_root)
            arc_contexts = load_arc_collection_note_contexts(profile=args.arc_profile)
        except Exception as exc:
            for item in selected_items:
                note_id = str(item.get("id") or "").strip()
                replacements[note_id] = failure_row(note_id, "visual_runtime_unavailable", safe_error(exc))
            atomic_write_json(out_path, merge_selected_analysis(base_rows, replacements, selected_ids))
        else:
            try:
                cookie_file = export_arc_cookie_file(module, cache_root, profile=args.arc_profile)
            except Exception as exc:
                for item in selected_items:
                    note_id = str(item.get("id") or "").strip()
                    replacements[note_id] = failure_row(note_id, "arc_cookie_export_failed", safe_error(exc))
                atomic_write_json(out_path, merge_selected_analysis(base_rows, replacements, selected_ids))
            else:
                for item in selected_items:
                    note_id = str(item.get("id") or "").strip()
                    work_dir = cache_root / note_id
                    if work_dir.exists() and not args.keep_cache:
                        shutil.rmtree(work_dir, ignore_errors=True)
                    work_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        acquired = acquire_visual_evidence(
                            item,
                            module=module,
                            arc_contexts=arc_contexts,
                            cookie_file=cookie_file,
                            work_dir=work_dir,
                            max_gap_sec=args.max_frame_gap_sec,
                            download_timeout=args.download_timeout,
                            ffmpeg_bin=args.ffmpeg_bin,
                            ffprobe_bin=args.ffprobe_bin,
                        )
                        if acquired.get("status") != "success":
                            replacements[note_id] = acquired
                            classified = classify_safety_error(acquired.get("error") or "")
                            if classified:
                                atomic_write_json(out_path, merge_selected_analysis(base_rows, replacements, selected_ids))
                                reason_code, message = classified
                                mark_security_halted(
                                    safety_state,
                                    stage="video_visual_analysis",
                                    reason_code=reason_code,
                                    message=message,
                                )
                                raise SafetyHaltedError("视频画面访问返回安全异常；已保存当前结果并停止本次会话。")
                        else:
                            manifest = acquired["evidence_manifest"]
                            frame_paths = acquired["frame_paths"]
                            transcript = qualified_transcript(transcript_map.get(note_id))
                            transcript_hash = str((transcript or {}).get("transcript_sha256") or "")
                            evidence_hash = visual_evidence_sha256(manifest)
                            input_hash = analysis_input_sha256(
                                evidence_hash=evidence_hash,
                                transcript_hash=transcript_hash,
                                boards=boards,
                                provider_identity=provider_identity,
                            )
                            prior = resume_map.get(note_id)
                            if args.resume and resume_row_matches(
                                prior,
                                current_manifest=manifest,
                                current_input_hash=input_hash,
                                boards=boards,
                            ):
                                replacements[note_id] = dict(prior)
                            else:
                                result = analyze_with_provider(
                                    manifest=manifest,
                                    frame_paths=frame_paths,
                                    transcript=transcript,
                                    boards=boards,
                                    provider=provider,
                                )
                                result["id"] = note_id
                                result["visual_evidence_sha256"] = evidence_hash
                                result["analysis_input_sha256"] = input_hash
                                result["transcript_sha256"] = transcript_hash
                                result["analysis_basis"] = (
                                    "full_timeline_visual_with_transcript"
                                    if transcript is not None
                                    else "full_timeline_visual"
                                )
                                result["visual_status"] = (
                                    "analyzed" if result.get("status") == "success" else "failed"
                                )
                                result["analysis_provider"] = str(provider_identity.get("provider") or "")
                                result["analysis_model"] = str(provider_identity.get("model") or "")
                                result["analysis_provider_version"] = str(provider_identity.get("version") or "")
                                result["evidence_manifest"] = manifest
                                replacements[note_id] = result
                    except SafetyHaltedError:
                        raise
                    except Exception as exc:
                        replacements[note_id] = failure_row(note_id, "visual_content_unavailable", safe_error(exc))
                    finally:
                        if not args.keep_cache:
                            shutil.rmtree(work_dir, ignore_errors=True)
                    atomic_write_json(out_path, merge_selected_analysis(base_rows, replacements, selected_ids))
    finally:
        provider.close()
        if cookie_file is not None:
            cookie_file.unlink(missing_ok=True)
        if not args.keep_cache and cache_root.is_dir():
            try:
                cache_root.rmdir()
            except OSError:
                pass

    merged = merge_selected_analysis(base_rows, replacements, selected_ids)
    atomic_write_json(out_path, merged)
    selected_rows = [row for row in merged if str(row.get("id") or "") in selected_ids]
    print(json.dumps({
        "selected_count": len(selected_rows),
        "success_count": sum(row.get("status") == "success" for row in selected_rows),
        "failed_count": sum(row.get("status") != "success" for row in selected_rows),
        "full_analysis_count": len(merged),
        "output": str(out_path),
        "safety_state": str(safety_state),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
