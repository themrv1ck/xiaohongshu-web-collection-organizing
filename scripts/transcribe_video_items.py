#!/usr/bin/env python3
"""Transcribe only Xiaohongshu items explicitly identified as videos."""

from __future__ import annotations

import argparse
import json
import math
import os
import select
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Callable

from transcript_quality import validate_transcript_coverage
from video_content_common import (
    canonical_xiaohongshu_note_url,
    find_arc_collection_note_context,
    load_arc_collection_note_contexts,
    load_video_transcript_module,
    normalize_content_type,
    redact_sensitive_text,
    safe_error,
    transcript_sha256,
    video_content_environment,
    xiaohongshu_access_url,
)
from xhs_safety import (
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    mark_security_halted,
    resolve_safety_state_path,
)


class BatchInfrastructureError(RuntimeError):
    """Abort the batch while preserving checkpoints when shared infrastructure dies."""


SUSPICIOUS_YT_DLP_PAGE_ERRORS = (
    "unable to extract initial state",
    "no video formats found",
)

VIDEO_EXTRACTOR_PREPARED_SAMPLE_RATE = 16_000
VIDEO_EXTRACTOR_PREPARED_CHANNELS = 1
VIDEO_EXTRACTOR_PREPARED_SAMPLE_WIDTH = 2


MIMO_WORKER_SCRIPT = r'''
import contextlib
import json
import os
import sys
import traceback

protocol = sys.stdout
model_path = sys.argv[1]
tokenizer_path = sys.argv[2]
os.environ["HF_HUB_DISABLE_XET"] = "1"

try:
    with contextlib.redirect_stdout(sys.stderr):
        from mlx_audio.stt.generate import generate_transcription
        from mlx_audio.stt.utils import load_model
        model = load_model(model_path)
    protocol.write(json.dumps({"ready": True}) + "\n")
    protocol.flush()
except Exception as exc:
    protocol.write(json.dumps({"ready": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
    protocol.flush()
    raise

for raw_line in sys.stdin:
    try:
        payload = json.loads(raw_line)
        if payload.get("action") == "close":
            protocol.write(json.dumps({"closed": True}) + "\n")
            protocol.flush()
            break
        with contextlib.redirect_stdout(sys.stderr):
            generate_transcription(
                model=model,
                audio=payload["audio"],
                output_path=payload["output_path"],
                format="json",
                audio_tokenizer_dir=tokenizer_path,
            )
        protocol.write(json.dumps({"ok": True}) + "\n")
        protocol.flush()
    except Exception as exc:
        protocol.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
        protocol.flush()
'''.strip()


class PersistentMiMoWorker:
    def __init__(self, runtime: Any, *, startup_timeout: int = 180):
        self.runtime = runtime
        self.process: subprocess.Popen[str] | None = None
        env = os.environ.copy()
        env["HF_HUB_DISABLE_XET"] = "1"
        self.process = subprocess.Popen(
            [str(runtime.python), "-u", "-c", MIMO_WORKER_SCRIPT, str(runtime.model), str(runtime.tokenizer)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )
        response = self._read_response(startup_timeout)
        if response.get("ready") is not True:
            self.close()
            raise BatchInfrastructureError(f"MiMo 批量进程启动失败：{response.get('error') or 'unknown error'}")

    def _read_response(self, timeout: int) -> dict[str, Any]:
        process = self.process
        if process is None or process.stdout is None:
            raise BatchInfrastructureError("MiMo 批量进程不可用")
        readable, _, _ = select.select([process.stdout], [], [], timeout)
        if not readable:
            self._terminate()
            raise BatchInfrastructureError(f"MiMo 批量进程等待超过 {timeout} 秒")
        line = process.stdout.readline()
        if not line:
            returncode = process.poll()
            self._terminate()
            raise BatchInfrastructureError(f"MiMo 批量进程意外退出：returncode={returncode}")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self._terminate()
            raise BatchInfrastructureError("MiMo 批量进程返回了无法解析的结果") from exc
        if not isinstance(payload, dict):
            self._terminate()
            raise BatchInfrastructureError("MiMo 批量进程返回值不是对象")
        return payload

    def transcribe(self, audio_path: Path, output_prefix: Path, *, timeout: int) -> dict[str, Any]:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise BatchInfrastructureError("MiMo 批量进程已经退出")
        payload = {"audio": str(audio_path), "output_path": str(output_prefix)}
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._terminate()
            raise BatchInfrastructureError("无法向 MiMo 批量进程发送音频") from exc
        return self._read_response(timeout)

    def _terminate(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write(json.dumps({"action": "close"}) + "\n")
                process.stdin.flush()
                self._read_response(30)
            except (BatchInfrastructureError, BrokenPipeError, OSError):
                pass
        self._terminate()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        self.process = None


def _mimo_audio_limits(module: Any, runtime: Any) -> tuple[int, int]:
    config_path = Path(runtime.tokenizer) / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise module.UserVisibleError(f"MiMo Audio Tokenizer 配置无法读取：{config_path}") from exc
    n_fft = config.get("nfft") if isinstance(config, dict) else None
    sample_rate = config.get("sampling_rate") if isinstance(config, dict) else None
    if not isinstance(n_fft, int) or isinstance(n_fft, bool) or n_fft <= 0:
        raise module.UserVisibleError(f"MiMo Audio Tokenizer 的 nfft 无效：{n_fft!r}")
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise module.UserVisibleError(f"MiMo Audio Tokenizer 的 sampling_rate 无效：{sample_rate!r}")
    return n_fft, sample_rate


def _read_prepared_pcm_wav(module: Any, path: Path) -> tuple[Any, bytes]:
    try:
        with wave.open(str(path), "rb") as handle:
            params = handle.getparams()
            frames = handle.readframes(params.nframes)
    except (OSError, EOFError, wave.Error) as exc:
        raise module.UserVisibleError(f"无法读取待转写 WAV：{path}") from exc
    if params.comptype != "NONE":
        raise module.UserVisibleError(f"待转写 WAV 不是无压缩 PCM：{path}")
    if (
        params.framerate != VIDEO_EXTRACTOR_PREPARED_SAMPLE_RATE
        or params.nchannels != VIDEO_EXTRACTOR_PREPARED_CHANNELS
        or params.sampwidth != VIDEO_EXTRACTOR_PREPARED_SAMPLE_WIDTH
    ):
        raise module.UserVisibleError(
            "Video Transcript Extractor 的切片输出不符合 16000 Hz 单声道 16-bit PCM WAV 约定："
            f"{path} (sample_rate={params.framerate}, channels={params.nchannels}, "
            f"sample_width={params.sampwidth})"
        )
    expected_bytes = params.nframes * params.nchannels * params.sampwidth
    if len(frames) != expected_bytes:
        raise module.UserVisibleError(
            f"待转写 WAV 数据不完整：{path} (expected={expected_bytes}, actual={len(frames)})"
        )
    return params, frames


def _write_pcm_wav(path: Path, params: Any, frames: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{os.getpid()}.tmp.wav")
    try:
        with wave.open(str(temporary), "wb") as handle:
            handle.setnchannels(params.nchannels)
            handle.setsampwidth(params.sampwidth)
            handle.setframerate(params.framerate)
            handle.setcomptype(params.comptype, params.compname)
            handle.writeframes(frames)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_mimo_audio_input(
    module: Any,
    runtime: Any,
    audio_paths: list[Path],
    output_dir: Path,
) -> list[Path]:
    """Make the extractor's PCM chunks safe for MiMo without dropping samples."""
    if not audio_paths:
        raise module.UserVisibleError("Video Transcript Extractor 没有生成待转写音频块。")

    n_fft, mimo_sample_rate = _mimo_audio_limits(module, runtime)
    inspected: list[tuple[Path, Any, bytes, int]] = []
    for path in audio_paths:
        params, frames = _read_prepared_pcm_wav(module, path)
        minimum_frames = math.ceil(n_fft * params.framerate / mimo_sample_rate)
        inspected.append((path, params, frames, minimum_frames))

    short_indices = [
        index
        for index, (_path, params, _frames, minimum_frames) in enumerate(inspected)
        if params.nframes < minimum_frames
    ]
    if not short_indices:
        return list(audio_paths)
    if short_indices != [len(inspected) - 1]:
        details = ", ".join(
            f"{inspected[index][0].name}:{inspected[index][1].nframes}"
            for index in short_indices
        )
        raise module.UserVisibleError(
            "发现非尾部 MiMo 过短音频块，拒绝静默跳过或改写时轴：" + details
        )

    tail_path, tail_params, tail_frames, minimum_frames = inspected[-1]
    if len(inspected) == 1:
        padding_frames = minimum_frames - tail_params.nframes
        silence = b"\0" * (padding_frames * tail_params.nchannels * tail_params.sampwidth)
        padded_path = output_dir / f"{tail_path.stem}_mimo_padded.wav"
        _write_pcm_wav(padded_path, tail_params, tail_frames + silence)
        return [padded_path]

    previous_path, previous_params, previous_frames, _minimum_frames = inspected[-2]
    previous_format = (
        previous_params.nchannels,
        previous_params.sampwidth,
        previous_params.framerate,
        previous_params.comptype,
    )
    tail_format = (
        tail_params.nchannels,
        tail_params.sampwidth,
        tail_params.framerate,
        tail_params.comptype,
    )
    if previous_format != tail_format:
        raise module.UserVisibleError(
            f"MiMo 尾部块与前一块音频格式不一致，无法无损合并：{previous_path} / {tail_path}"
        )
    merged_path = output_dir / f"{previous_path.stem}_with_{tail_path.stem}_mimo.wav"
    _write_pcm_wav(merged_path, previous_params, previous_frames + tail_frames)
    return [*audio_paths[:-2], merged_path]


def prepare_mimo_audio_for_transcription(
    module: Any,
    runtime: Any,
    audio_paths: list[Path],
    output_dir: Path,
    *,
    chunk_seconds: int,
) -> list[Path]:
    chunks = module.prepare_audio_for_transcription(
        audio_paths,
        output_dir,
        chunk_seconds=chunk_seconds,
    )
    return ensure_mimo_audio_input(module, runtime, chunks, output_dir)


def transcribe_audio_files_with_worker(
    module: Any,
    runtime: Any,
    worker: PersistentMiMoWorker,
    audio_paths: list[Path],
    output_dir: Path,
    *,
    timeout: int,
) -> tuple[dict[str, Any], list[Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    offset = 0.0
    merged: list[dict[str, Any]] = []
    transcript_paths: list[Path] = []
    empty_chunk_count = 0
    for audio_path in audio_paths:
        output_prefix = output_dir / audio_path.stem
        response = worker.transcribe(audio_path, output_prefix, timeout=timeout)
        if response.get("ok") is not True:
            raise module.UserVisibleError(f"MiMo 转写失败：{response.get('error') or 'unknown error'}")
        transcript_path = output_dir / f"{audio_path.stem}.json"
        if not transcript_path.exists() or transcript_path.stat().st_size == 0:
            raise module.UserVisibleError(f"MiMo 没有生成转写 JSON：{transcript_path}")
        duration = float(module.audio_duration(audio_path))
        try:
            local_segments = module.parse_transcript_json(transcript_path, fallback_duration=duration)
        except module.UserVisibleError:
            try:
                raw_payload = json.loads(transcript_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise
            if isinstance(raw_payload, dict):
                raw_segments = raw_payload.get("segments")
                if isinstance(raw_segments, list):
                    explicitly_empty = all(
                        isinstance(segment, dict)
                        and not module.clean_segment_text(
                            module.first_present(segment, ("text", "sentence", "content"))
                        )
                        for segment in raw_segments
                    ) and not module.clean_segment_text(raw_payload.get("text"))
                else:
                    explicitly_empty = "text" in raw_payload and not module.clean_segment_text(raw_payload.get("text"))
            elif isinstance(raw_payload, list):
                explicitly_empty = all(
                    isinstance(segment, dict)
                    and not module.clean_segment_text(
                        module.first_present(segment, ("text", "sentence", "content"))
                    )
                    for segment in raw_payload
                )
            else:
                explicitly_empty = False
            if not explicitly_empty:
                raise
            local_segments = []
            empty_chunk_count += 1
        transcript_paths.append(transcript_path)
        for segment in local_segments:
            merged.append({
                "start": float(segment["start"]) + offset,
                "end": float(segment["end"]) + offset,
                "text": module.clean_segment_text(segment["text"]),
            })
        offset += duration
    if not merged:
        raise module.UserVisibleError("MiMo 没有生成任何可用字幕段。")
    return {
        "source": {"kind": "mimo_audio", "transcriber": "MiMo MLX"},
        "language": "unknown",
        "transcript_quality": "asr",
        "segments": merged,
        "segment_count": len(merged),
        "char_count": sum(len(segment["text"]) for segment in merged),
        "empty_chunk_count": empty_chunk_count,
    }, transcript_paths


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


def safe_command_result(result: Any) -> dict[str, Any]:
    return {
        "returncode": getattr(result, "returncode", None),
        "stdout": redact_sensitive_text(getattr(result, "stdout", ""))[:500],
        "stderr": redact_sensitive_text(getattr(result, "stderr", ""))[:500],
    }


def probe_video_metadata(
    module: Any,
    url: str,
    *,
    cookies_from_browser: str | None,
    cookie_file: Path | None,
    timeout: int,
) -> dict[str, Any]:
    command = ["yt-dlp", "--dump-single-json", "--skip-download", "--no-warnings", url]
    command = module.add_cookie_args(command, cookies_from_browser=cookies_from_browser, cookie_file=cookie_file)
    result = module.run(command, timeout=timeout)
    if result.returncode != 0:
        return {"ok": False, "duration": None, "result": safe_command_result(result)}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "duration": None, "result": safe_command_result(result)}
    duration = payload.get("duration") if isinstance(payload, dict) else None
    return {"ok": True, "duration": duration, "result": {"returncode": 0, "stdout": "", "stderr": ""}}


def probe_known_success_video(
    item: dict[str, Any],
    *,
    module: Any,
    browser: str,
    arc_profile: str,
    arc_contexts: dict[str, dict[str, Any]] | None,
    shared_cookie_file: Path | None,
    timeout: int,
) -> bool:
    note_id = str(item.get("id") or "").strip()
    canonical_url = canonical_xiaohongshu_note_url(item)
    if not note_id or not canonical_url:
        return False

    access_url = canonical_url
    cookies_from_browser: str | None = browser
    if browser == "arc":
        arc_context = (
            arc_contexts.get(note_id, {"found": False, "content_type": "unknown"})
            if arc_contexts is not None
            else find_arc_collection_note_context(note_id, profile=arc_profile)
        )
        if arc_context.get("found") is not True or arc_context.get("content_type") != "video":
            return False
        access_url = xiaohongshu_access_url(canonical_url, arc_context)
        cookies_from_browser = None

    try:
        metadata = probe_video_metadata(
            module,
            access_url,
            cookies_from_browser=cookies_from_browser,
            cookie_file=shared_cookie_file,
            timeout=timeout,
        )
    except Exception:
        return False
    return metadata.get("ok") is True


def is_suspicious_yt_dlp_page_failure(row: dict[str, Any]) -> bool:
    if row.get("status") == "success":
        return False
    error = str(row.get("error") or "").lower()
    return any(message in error for message in SUSPICIOUS_YT_DLP_PAGE_ERRORS)


def is_valid_success_transcript(row: dict[str, Any]) -> bool:
    if row.get("status") != "success":
        return False
    segments = row.get("segments")
    coverage = row.get("coverage")
    if not isinstance(segments, list) or not segments:
        return False
    if not isinstance(coverage, dict) or coverage.get("transcript_quality_passed") is not True:
        return False
    return bool(row.get("transcript_sha256")) and row.get("transcript_sha256") == transcript_sha256(segments)


def transcript_source_name(material: dict[str, Any], *, used_audio: bool) -> str:
    if used_audio:
        return "mimo_audio"
    source = material.get("source") if isinstance(material.get("source"), dict) else {}
    kind = str(source.get("kind") or "").lower()
    if kind in {"vtt", "srt"}:
        return f"subtitle_{kind}"
    return "subtitle_platform"


def acquire_video_transcript(
    item: dict[str, Any],
    *,
    module: Any,
    browser: str,
    arc_profile: str,
    work_dir: Path,
    subtitle_timeout: int,
    audio_timeout: int,
    transcribe_timeout: int,
    chunk_seconds: int,
    keep_cache: bool,
    arc_contexts: dict[str, dict[str, Any]] | None = None,
    runtime: Any | None = None,
    shared_cookie_file: Path | None = None,
    mimo_worker: PersistentMiMoWorker | None = None,
) -> dict[str, Any]:
    note_id = str(item.get("id") or "").strip()
    canonical_url = canonical_xiaohongshu_note_url(item)
    if not note_id or not canonical_url:
        return {
            "id": note_id,
            "status": "failed",
            "stage": "input",
            "reason_code": "canonical_note_url_missing",
            "error": "缺少可安全使用的小红书标准视频地址",
        }

    access_url = canonical_url
    arc_context: dict[str, Any] | None = None
    if browser == "arc":
        arc_context = (
            arc_contexts.get(note_id, {"found": False, "content_type": "unknown"})
            if arc_contexts is not None
            else find_arc_collection_note_context(note_id, profile=arc_profile)
        )
        if arc_context.get("found") is not True:
            return {
                "id": note_id,
                "status": "failed",
                "stage": "arc_collection_context",
                "reason_code": "arc_collection_context_missing",
                "error": "Arc 最新收藏缓存里没有这条笔记的会话参数；请先重新抓取收藏页",
            }
        if arc_context.get("content_type") != "video":
            return {
                "id": note_id,
                "status": "failed",
                "stage": "content_type_verification",
                "reason_code": "content_type_mismatch",
                "error": "Arc 收藏接口没有把这条笔记标记为视频",
            }
        access_url = xiaohongshu_access_url(canonical_url, arc_context)

    if work_dir.exists() and not keep_cache:
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    cookie_path: Path | None = None
    owns_cookie_file = False
    cookies_from_browser: str | None = browser
    try:
        runtime = runtime or module.ensure_environment()
        if browser == "arc":
            cookie_path = shared_cookie_file or work_dir / "arc-cookies.txt"
            if shared_cookie_file is None:
                module.export_arc_cookies(cookie_path, profile=arc_profile)
                owns_cookie_file = True
            cookies_from_browser = None

        metadata = probe_video_metadata(
            module,
            access_url,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookie_path,
            timeout=subtitle_timeout,
        )
        material: dict[str, Any]
        used_audio = False
        subtitle_error = ""
        try:
            material, _subtitle_path = module.fetch_platform_subtitles(
                access_url,
                work_dir / "subtitles",
                cookies_from_browser=cookies_from_browser,
                cookie_file=cookie_path,
                timeout=subtitle_timeout,
            )
        except Exception as exc:
            subtitle_error = safe_error(exc)
            used_audio = True
            audio_paths = module.download_audio(
                access_url,
                work_dir / "audio",
                cookies_from_browser=cookies_from_browser,
                cookie_file=cookie_path,
                timeout=audio_timeout,
            )
            if not metadata.get("duration"):
                metadata["duration"] = sum(float(module.audio_duration(path)) for path in audio_paths)
            chunks = prepare_mimo_audio_for_transcription(
                module,
                runtime,
                audio_paths,
                work_dir / "audio_chunks",
                chunk_seconds=chunk_seconds,
            )
            if mimo_worker is not None:
                material, _transcript_paths = transcribe_audio_files_with_worker(
                    module,
                    runtime,
                    mimo_worker,
                    chunks,
                    work_dir / "transcripts",
                    timeout=transcribe_timeout,
                )
            else:
                material, _transcript_paths = module.transcribe_audio_files(
                    runtime,
                    chunks,
                    work_dir / "transcripts",
                    timeout=transcribe_timeout,
                )

        segments = module.iter_segments(material)
        source = transcript_source_name(material, used_audio=used_audio)
        coverage = validate_transcript_coverage(
            video_duration=metadata.get("duration"),
            segments=segments,
            transcript_source=source,
        )
        if coverage["transcript_quality_passed"] is not True:
            return {
                "id": note_id,
                "status": "failed",
                "stage": "transcript_quality",
                "reason_code": "transcript_coverage_too_low",
                "error": "转写覆盖率不足，不能用于内容分类",
                "coverage": coverage,
                "arc_collection_context": {
                    "verified": bool(arc_context and arc_context.get("found")),
                    "content_type": (arc_context or {}).get("content_type") or "",
                    "cache_age_seconds": round(float((arc_context or {}).get("cache_age_seconds") or 0), 1) if arc_context else None,
                },
            }
        return {
            "id": note_id,
            "status": "success",
            "content_type": "video",
            "source_url": canonical_url,
            "source_kind": source,
            "transcript_sha256": transcript_sha256(segments),
            "segment_count": len(segments),
            "char_count": sum(len("".join(str(segment.get("text") or "").split())) for segment in segments),
            "segments": segments,
            "coverage": coverage,
            "arc_collection_context": {
                "verified": bool(arc_context and arc_context.get("found")),
                "content_type": (arc_context or {}).get("content_type") or "",
                "cache_age_seconds": round(float((arc_context or {}).get("cache_age_seconds") or 0), 1) if arc_context else None,
            },
            "subtitle_error_before_audio_fallback": subtitle_error,
            "error": "",
        }
    except BatchInfrastructureError:
        raise
    except Exception as exc:
        return {
            "id": note_id,
            "status": "failed",
            "stage": "transcript_acquisition",
            "reason_code": "video_content_unavailable",
            "error": safe_error(exc),
        }
    finally:
        if owns_cookie_file and cookie_path is not None:
            cookie_path.unlink(missing_ok=True)
        if not keep_cache:
            shutil.rmtree(work_dir, ignore_errors=True)


def build_transcript_rows(
    items: list[dict[str, Any]],
    acquire: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_videos: int | None = None,
    video_ids: set[str] | None = None,
    initial_rows: list[dict[str, Any]] | None = None,
    on_row: Callable[[list[dict[str, Any]]], None] | None = None,
    control_probe: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    explicit_video_order: list[str] = []
    explicit_video_ids: set[str] = set()
    video_items_by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        if normalize_content_type(item.get("content_type") or item.get("note_type") or item.get("type")) != "video":
            continue
        note_id = str(item.get("id") or "").strip()
        if not note_id:
            raise ValueError("当前输入包含缺少 ID 的视频")
        if note_id in explicit_video_ids:
            raise ValueError(f"当前输入包含重复视频 ID：{note_id}")
        explicit_video_ids.add(note_id)
        explicit_video_order.append(note_id)
        video_items_by_id[note_id] = item
    if video_ids is not None:
        missing = sorted(video_ids - explicit_video_ids)
        if missing:
            raise ValueError(f"指定 ID 不存在或不是已确认的视频：{', '.join(missing)}")

    result_by_id: dict[str, dict[str, Any]] = {}
    checkpoint_ids: set[str] = set()
    for row in initial_rows or []:
        note_id = str(row.get("id") or "").strip()
        if not note_id or note_id not in explicit_video_ids:
            raise ValueError(f"断点文件包含不在当前输入中的视频 ID：{note_id or '<empty>'}")
        if note_id in checkpoint_ids:
            raise ValueError(f"断点文件包含重复视频 ID：{note_id}")
        checkpoint_ids.add(note_id)
        if row.get("status") != "success":
            continue
        segments = row.get("segments")
        coverage = row.get("coverage")
        if not isinstance(segments, list) or not segments:
            raise ValueError(f"断点文件中的成功文字稿缺少 segments：{note_id}")
        if not isinstance(coverage, dict) or coverage.get("transcript_quality_passed") is not True:
            raise ValueError(f"断点文件中的成功文字稿未通过质量门：{note_id}")
        expected_hash = transcript_sha256(segments)
        if not row.get("transcript_sha256") or row.get("transcript_sha256") != expected_hash:
            raise ValueError(f"断点文件中的文字稿哈希不匹配：{note_id}")
        result_by_id[note_id] = dict(row)

    control_note_id = next((note_id for note_id in explicit_video_order if note_id in result_by_id), None)

    def ordered_rows() -> list[dict[str, Any]]:
        return [result_by_id[note_id] for note_id in explicit_video_order if note_id in result_by_id]

    processed = 0
    for item in items:
        if normalize_content_type(item.get("content_type") or item.get("note_type") or item.get("type")) != "video":
            continue
        note_id = str(item.get("id") or "").strip()
        if video_ids is not None and note_id not in video_ids:
            continue
        if note_id in result_by_id:
            continue
        if max_videos is not None and processed >= max_videos:
            break
        row = acquire(item)
        if is_suspicious_yt_dlp_page_failure(row):
            if control_note_id is None:
                raise BatchInfrastructureError(
                    "yt-dlp 页面提取失败，但没有有效的成功文字稿可作对照；已中止批次且不写入当前失败。"
                )
            if control_probe is None:
                raise BatchInfrastructureError(
                    "yt-dlp 页面提取失败，但未配置已有成功文字稿的对照探针；已中止批次且不写入当前失败。"
                )
            try:
                control_ok = control_probe(video_items_by_id[control_note_id]) is True
            except Exception:
                control_ok = False
            if not control_ok:
                raise BatchInfrastructureError(
                    f"yt-dlp 当前视频与已有成功文字稿的对照视频 {control_note_id} 同时提取失败；"
                    "判定为共享访问链路故障，已中止批次且不写入当前失败。"
                )
        result_by_id[note_id] = row
        if control_note_id is None and is_valid_success_transcript(row):
            control_note_id = note_id
        processed += 1
        if on_row is not None:
            on_row(ordered_rows())
    return ordered_rows()


def main() -> int:
    parser = argparse.ArgumentParser(description="把明确的视频收藏转成带覆盖率校验的文字稿，不生成额外报告。")
    parser.add_argument("src", help="visible_items.json 路径")
    parser.add_argument("out", help="video_transcripts.json 输出路径")
    parser.add_argument("--browser", choices=("arc", "chrome", "safari", "edge", "brave", "firefox"), required=True)
    parser.add_argument("--arc-profile", default="Default")
    parser.add_argument("--extractor-root")
    parser.add_argument("--cache-dir")
    parser.add_argument("--keep-cache", action="store_true")
    parser.add_argument("--max-videos", type=int)
    parser.add_argument("--video-id", action="append", help="只处理指定视频 ID；可重复传入，用于可复现抽样测试")
    parser.add_argument("--resume", action="store_true", help="复用输出文件中已经完成的条目，只处理缺失视频")
    parser.add_argument("--allow-video-access", action="store_true", help="明确同意本次访问所选视频；默认低风险模式不会请求视频页面或媒体")
    parser.add_argument("--safety-state", default="", help="共享安全状态文件；默认继承输入文件旁已有状态，否则使用输出同目录的 xhs_safety_state.json")
    parser.add_argument("--subtitle-timeout", type=int, default=180)
    parser.add_argument("--audio-timeout", type=int, default=900)
    parser.add_argument("--transcribe-timeout", type=int, default=3600)
    parser.add_argument("--chunk-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.max_videos is not None and args.video_id:
        parser.error("--max-videos 与 --video-id 不能同时使用")
    if not args.allow_video_access:
        parser.error("默认低风险模式不会访问视频；请先人工确认本次范围，再明确传 --allow-video-access。")
    if args.max_videos is None and not args.video_id:
        parser.error("视频访问必须明确传 --max-videos 或至少一个 --video-id；不会默认处理全部视频。")
    if args.max_videos is not None and (not isinstance(args.max_videos, int) or isinstance(args.max_videos, bool) or not 1 <= args.max_videos <= 200):
        parser.error("--max-videos 必须是 1 到 200 的整数")

    src = Path(args.src)
    out = Path(args.out)
    safety_state = resolve_safety_state_path(args.safety_state, out, predecessors=(src,))
    ensure_active_session(
        safety_state,
        stage="video_transcription",
        policy={
            "auto_scroll": False,
            "auto_navigation": False,
            "auto_retry": False,
            "video_access_enabled": True,
            "video_selection": "video_id" if args.video_id else "max_videos",
            "video_limit": len(set(args.video_id or [])) if args.video_id else args.max_videos,
        },
    )
    items = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise SystemExit("visible_items.json 必须是数组")
    environment = video_content_environment(
        extractor_root=args.extractor_root,
        browser=args.browser,
        check_login_state=args.browser == "arc",
    )
    if not environment["video_content_ready"]:
        print(json.dumps(environment, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    module = load_video_transcript_module(args.extractor_root)
    runtime = module.ensure_environment()
    cache_root = Path(args.cache_dir).expanduser() if args.cache_dir else out.parent / ".video-content-cache"
    arc_contexts = load_arc_collection_note_contexts(profile=args.arc_profile) if args.browser == "arc" else None
    initial_rows: list[dict[str, Any]] = []
    if args.resume and out.exists():
        existing_payload = json.loads(out.read_text(encoding="utf-8"))
        if not isinstance(existing_payload, list):
            raise SystemExit("已有 video_transcripts.json 必须是数组")
        initial_rows = existing_payload

    shared_cookie_file: Path | None = None
    mimo_worker: PersistentMiMoWorker | None = None
    try:
        if args.browser == "arc":
            cache_root.mkdir(parents=True, exist_ok=True)
            shared_cookie_file = cache_root / "arc-cookies.txt"
            module.export_arc_cookies(shared_cookie_file, profile=args.arc_profile)
            shared_cookie_file.chmod(0o600)
        mimo_worker = PersistentMiMoWorker(runtime)

        def acquire(item: dict[str, Any]) -> dict[str, Any]:
            return acquire_video_transcript(
                item,
                module=module,
                browser=args.browser,
                arc_profile=args.arc_profile,
                work_dir=cache_root / str(item.get("id") or "unknown"),
                subtitle_timeout=args.subtitle_timeout,
                audio_timeout=args.audio_timeout,
                transcribe_timeout=args.transcribe_timeout,
                chunk_seconds=args.chunk_seconds,
                keep_cache=args.keep_cache,
                arc_contexts=arc_contexts,
                runtime=runtime,
                shared_cookie_file=shared_cookie_file,
                mimo_worker=mimo_worker,
            )

        def control_probe(item: dict[str, Any]) -> bool:
            return probe_known_success_video(
                item,
                module=module,
                browser=args.browser,
                arc_profile=args.arc_profile,
                arc_contexts=arc_contexts,
                shared_cookie_file=shared_cookie_file,
                timeout=args.subtitle_timeout,
            )

        def checkpoint(current: list[dict[str, Any]]) -> None:
            write_json(out, current)
            last = current[-1] if current else {}
            classified = classify_safety_error(last.get("error") if isinstance(last, dict) else "")
            if classified:
                reason_code, message = classified
                mark_security_halted(
                    safety_state,
                    stage="video_transcription",
                    reason_code=reason_code,
                    message=message,
                )
                raise SafetyHaltedError("视频访问返回安全异常；已保存当前结果并停止本次会话。")

        rows = build_transcript_rows(
            items,
            acquire,
            max_videos=args.max_videos,
            video_ids=set(args.video_id) if args.video_id else None,
            initial_rows=initial_rows,
            on_row=checkpoint,
            control_probe=control_probe,
        )
    finally:
        if mimo_worker is not None:
            mimo_worker.close()
        if shared_cookie_file is not None:
            shared_cookie_file.unlink(missing_ok=True)
        if not args.keep_cache and cache_root.is_dir():
            try:
                cache_root.rmdir()
            except OSError:
                pass
    write_json(out, rows)
    explicit_video_count = sum(
        normalize_content_type(item.get("content_type") or item.get("note_type") or item.get("type")) == "video"
        for item in items
    )
    if args.video_id:
        expected_count = len(set(args.video_id))
    elif args.max_videos is not None:
        expected_count = min(args.max_videos, explicit_video_count)
    else:
        expected_count = explicit_video_count
    print(json.dumps({
        "video_count": len(rows),
        "success_count": sum(row.get("status") == "success" for row in rows),
        "failed_count": sum(row.get("status") != "success" for row in rows),
        "expected_count": expected_count,
        "complete": len(rows) == expected_count,
        "output": str(out),
        "safety_state": str(safety_state),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
