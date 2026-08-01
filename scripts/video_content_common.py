#!/usr/bin/env python3
"""Shared helpers for optional video-content classification."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from dynamic_module_loader import load_module_from_path
from verify_mimo_vl_install import EXPECTED_SHARD_SIZES
from xhs_safety import redact_sensitive_text


VIDEO_TRANSCRIPT_SCRIPT = Path("scripts/video_transcript_cli.py")
ARC_COLLECTION_CACHE_NEEDLE = b"/api/sns/web/v2/note/collect/page"
ANALYSIS_PROVIDERS = ("codex-cli", "mimo-vl-mlx", "command")
MIMO_VL_REQUIRED_VERSION = "0.5.0"
MIMO_VL_FORBIDDEN_VERSION = "0.6.4"
MIMO_VL_REVISION = "4bfb270765825d2fa059011deb4c96fdd579be6f"
MIMO_VL_MODEL_NAME = "XiaomiMiMo/MiMo-VL-7B-RL-2508"
MIMO_VL_MODEL_SUBDIR = Path("models/MiMo-VL-7B-RL-2508")
MIMO_VL_MODEL_FILES = (
    "config.json",
    "model.safetensors.index.json",
    "model-00001-of-00004.safetensors",
    "model-00002-of-00004.safetensors",
    "model-00003-of-00004.safetensors",
    "model-00004-of-00004.safetensors",
)
MIMO_ASR_MODEL_SUBDIR = Path("models/MiMo-V2.5-ASR-MLX")
MIMO_ASR_TOKENIZER_SUBDIR = Path("models/MiMo-Audio-Tokenizer")
MIMO_ASR_REQUIRED_FILES = (
    MIMO_ASR_MODEL_SUBDIR / "config.json",
    MIMO_ASR_MODEL_SUBDIR / "model.safetensors",
    MIMO_ASR_TOKENIZER_SUBDIR / "config.json",
    MIMO_ASR_TOKENIZER_SUBDIR / "model.safetensors",
)
DEFAULT_EXTRACTOR_ROOTS = (
    Path(os.environ["XHS_VIDEO_TRANSCRIPT_EXTRACTOR_ROOT"]).expanduser()
    if os.environ.get("XHS_VIDEO_TRANSCRIPT_EXTRACTOR_ROOT")
    else None,
    Path.home() / "Documents" / "测试" / "video-transcript-extractor",
    Path.home() / "video-transcript-extractor",
    Path.home() / ".hermes" / "skills" / "video-transcript-extractor",
)


def resolve_mimo_asr_root(explicit: str | Path | None = None) -> Path:
    """Resolve only configured or documented MiMo ASR locations; never scan the disk."""
    if explicit:
        return Path(explicit).expanduser()
    configured = os.environ.get("XHS_MIMO_ASR_ROOT")
    if configured:
        return Path(configured).expanduser()
    candidates = (
        Path.home() / "Documents" / "MiMo-V2.5-ASR-MLX",
        Path.home() / "MiMo-V2.5-ASR-MLX",
    )
    return next((path for path in candidates if path.exists()), candidates[0])


def check_mimo_asr_environment(explicit_root: str | Path | None = None) -> dict[str, Any]:
    """Perform a static, offline MiMo ASR installation check without loading the model."""
    root = resolve_mimo_asr_root(explicit_root)
    python = root / ".venv" / "bin" / "python"
    runner = root / "run_mimo_asr_mlx.py"
    missing_files = [str(path) for path in MIMO_ASR_REQUIRED_FILES if not (root / path).is_file()]
    missing: list[str] = []
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        missing.append("mimo-asr-apple-silicon")
    if not python.is_file() or not os.access(python, os.X_OK):
        missing.append("mimo-asr-venv-python")
    if not runner.is_file():
        missing.append("mimo-asr-runner")
    if missing_files:
        missing.append("mimo-asr-model-files")
    return {
        "checked": True,
        "check_level": "static_offline",
        "model_loaded": False,
        "root": str(root),
        "root_source": (
            "explicit"
            if explicit_root
            else "environment"
            if os.environ.get("XHS_MIMO_ASR_ROOT")
            else "documented_default"
        ),
        "python": str(python),
        "runner": str(runner),
        "required_files": [str(path) for path in MIMO_ASR_REQUIRED_FILES],
        "missing_files": missing_files,
        "ready": not missing,
        "missing": missing,
        "download_size_gb": 6.6,
    }


def resolve_mimo_vl_root(explicit: str | Path | None = None) -> Path:
    """Return the one configured MiMo-VL installation root without searching unrelated paths."""
    if explicit:
        return Path(explicit).expanduser()
    configured = os.environ.get("XHS_MIMO_VL_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Documents" / "MiMo-VL-7B-RL-2508"


def check_mimo_vl_environment(explicit_root: str | Path | None = None) -> dict[str, Any]:
    root = resolve_mimo_vl_root(explicit_root)
    model_dir = root / MIMO_VL_MODEL_SUBDIR
    python = root / ".venv" / "bin" / "python"
    python_ready = python.is_file() and os.access(python, os.X_OK)
    missing_model_files = [name for name in MIMO_VL_MODEL_FILES if not (model_dir / name).is_file()]
    wrong_shard_sizes = {
        name: (model_dir / name).stat().st_size
        for name, expected in EXPECTED_SHARD_SIZES.items()
        if (model_dir / name).is_file() and (model_dir / name).stat().st_size != expected
    }
    version_check = (
        run_status(
            [
                str(python),
                "-c",
                "from importlib.metadata import version; print(version('mlx-vlm'))",
            ],
            timeout=15,
        )
        if python_ready
        else {"ok": False, "returncode": None, "stdout": "", "stderr": "mimo_vl_python_missing"}
    )
    mlx_vlm_version = version_check["stdout"] if version_check["ok"] else ""
    missing: list[str] = []
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        missing.append("mimo-vl-apple-silicon")
    if not python_ready:
        missing.append("mimo-vl-venv-python")
    if missing_model_files or wrong_shard_sizes:
        missing.append("mimo-vl-official-bf16-model")
    if mlx_vlm_version != MIMO_VL_REQUIRED_VERSION:
        missing.append("mlx-vlm-0.5.0")
    return {
        "checked": True,
        "root": str(root),
        "root_source": (
            "explicit"
            if explicit_root
            else "environment"
            if os.environ.get("XHS_MIMO_VL_ROOT")
            else "default"
        ),
        "model_dir": str(model_dir),
        "python": str(python),
        "python_ready": python_ready,
        "required_model_files": list(MIMO_VL_MODEL_FILES),
        "missing_model_files": missing_model_files,
        "wrong_shard_sizes": wrong_shard_sizes,
        "model_ready": not missing_model_files and not wrong_shard_sizes,
        "mlx_vlm_version": mlx_vlm_version,
        "required_mlx_vlm_version": MIMO_VL_REQUIRED_VERSION,
        "forbidden_mlx_vlm_version": MIMO_VL_FORBIDDEN_VERSION,
        "forbidden_version_detected": mlx_vlm_version == MIMO_VL_FORBIDDEN_VERSION,
        "version_check": version_check,
        "official_revision": MIMO_VL_REVISION,
        "official_bf16_download_size_gb": 16.6,
        "measured_peak_unified_memory_gb": 17.6,
        "recommended_unified_memory_gb": 32,
        "memory_note": "24 GB 统一内存可能紧张；128 GB 只是已验收机器的配置，不是模型必需条件",
        "ready": not missing,
        "missing": missing,
    }


def check_analysis_command(command: str) -> dict[str, Any]:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return {"checked": True, "ok": False, "executable": "", "error": str(exc)}
    if not parts:
        return {"checked": True, "ok": False, "executable": "", "error": "analysis_command_empty"}
    executable_name = parts[0]
    expanded = Path(executable_name).expanduser()
    if "/" in executable_name:
        executable = str(expanded) if expanded.is_file() and os.access(expanded, os.X_OK) else ""
    else:
        executable = shutil.which(executable_name) or ""
    return {
        "checked": True,
        "ok": bool(executable),
        "executable": executable,
        "error": "" if executable else "analysis_command_executable_missing",
    }


def run_status(args: list[str], timeout: int = 15) -> dict[str, Any]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": str(result.stdout or "").strip(),
        "stderr": str(result.stderr or "").strip(),
    }


def find_video_transcript_extractor_root(explicit: str | Path | None = None) -> Path | None:
    candidates = [Path(explicit).expanduser()] if explicit else []
    candidates.extend(root for root in DEFAULT_EXTRACTOR_ROOTS if root is not None)
    seen: set[str] = set()
    for root in candidates:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        if (root / VIDEO_TRANSCRIPT_SCRIPT).is_file():
            return root
    return None


def local_capability_preflight(
    *,
    extractor_root: str | Path | None = None,
    mimo_asr_root: str | Path | None = None,
    mimo_vl_root: str | Path | None = None,
    host_visual_capability: str = "unknown",
    host_visual_name: str = "",
) -> dict[str, Any]:
    """Inspect local OCR-adjacent video capabilities without browser, network, install, or inference."""
    if host_visual_capability not in {"ready", "unavailable", "unknown"}:
        raise ValueError("host_visual_capability 必须是 ready、unavailable 或 unknown")
    root = find_video_transcript_extractor_root(extractor_root)
    tools = {name: shutil.which(name) or "" for name in ("yt-dlp", "ffmpeg", "ffprobe")}
    asr = check_mimo_asr_environment(mimo_asr_root)
    local_visual = check_mimo_vl_environment(mimo_vl_root)
    host_ready = True if host_visual_capability == "ready" else False if host_visual_capability == "unavailable" else None
    host_status = (
        "declared_ready"
        if host_visual_capability == "ready"
        else "declared_unavailable"
        if host_visual_capability == "unavailable"
        else "unknown"
    )
    subtitle_components_ready = bool(root and tools["yt-dlp"])
    audio_media_ready = bool(tools["ffmpeg"] and tools["ffprobe"])
    audio_ready = bool(subtitle_components_ready and audio_media_ready and asr["ready"])
    visual_ready = bool(local_visual["ready"] or host_ready is True)
    return {
        "checked": True,
        "mode": "local_read_only",
        "policy": {
            "browser_accessed": False,
            "network_accessed": False,
            "software_installed": False,
            "large_model_loaded": False,
            "disk_scan_scope": "configured_and_documented_paths_only",
        },
        "video_audio": {
            "status": "ready" if audio_ready else "partial" if subtitle_components_ready or asr["ready"] else "missing",
            "ready": audio_ready,
            "extractor_root": str(root) if root else "",
            "subtitle_components_ready": subtitle_components_ready,
            "media_tools_ready": audio_media_ready,
            "tools": tools,
            "local_asr": asr,
            "note": "这里只检查本地组件；具体视频是否有平台字幕，要在用户授权浏览器后逐条确认。",
        },
        "video_visual": {
            "status": "ready" if visual_ready else "unknown" if host_ready is None else "missing",
            "ready": visual_ready,
            "local_mimo_vl": local_visual,
            "host_visual_ai": {
                "status": host_status,
                "ready": host_ready,
                "name": str(host_visual_name or "").strip(),
                "source": "host_declaration",
                "note": "本地脚本不能可靠推断任意宿主 Agent 的看图能力；宿主无法证明时必须保持 unknown。",
            },
        },
    }


def load_video_transcript_module(extractor_root: str | Path | None = None):
    root = find_video_transcript_extractor_root(extractor_root)
    if root is None:
        raise RuntimeError("video_transcript_extractor_missing")
    script_path = root / VIDEO_TRANSCRIPT_SCRIPT
    try:
        return load_module_from_path("xhs_video_transcript_cli", script_path)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("video_transcript_extractor_import_failed") from exc


def arc_running() -> bool:
    if platform.system() != "Darwin" or shutil.which("pgrep") is None:
        return False
    return run_status(["pgrep", "-x", "Arc"], timeout=5)["ok"]


def arc_cookie_status() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {"ok": False, "cookie_count": 0, "has_session_cookie": False, "error": "macos_required"}
    try:
        import browser_cookie3  # type: ignore

        jar = browser_cookie3.arc(domain_name="xiaohongshu.com")
        cookies = list(jar)
        names = {str(getattr(cookie, "name", "") or "") for cookie in cookies}
        has_login_cookie = "web_session" in names
        return {
            "ok": bool(cookies) and has_login_cookie,
            "cookie_count": len(cookies),
            "has_session_cookie": has_login_cookie,
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "cookie_count": 0,
            "has_session_cookie": False,
            "error": f"{exc.__class__.__name__}: {str(exc)[:240]}",
        }


def arc_xiaohongshu_page_status() -> dict[str, Any]:
    if platform.system() != "Darwin" or not arc_running():
        return {"ok": False, "tab_found": False, "path": "", "login_required": True, "error": "arc_not_running"}
    script = r'''
tell application "Arc"
  set targetTab to missing value
  repeat with browserWindow in windows
    repeat with browserTab in tabs of browserWindow
      if (URL of browserTab as text) contains "xiaohongshu.com" then
        set targetTab to browserTab
        exit repeat
      end if
    end repeat
    if targetTab is not missing value then exit repeat
  end repeat
  if targetTab is missing value then return "{\"tab_found\":false}"
  return execute targetTab javascript "JSON.stringify({tab_found:true,path:location.pathname,login_required:location.pathname.indexOf('/login')===0||/手机号登录|扫码登录|登录后推荐|马上登录即可/.test((document.body&&document.body.innerText)||'')})"
end tell
'''.strip()
    result = run_status(["osascript", "-e", script], timeout=15)
    if not result["ok"]:
        return {
            "ok": False,
            "tab_found": False,
            "path": "",
            "login_required": True,
            "error": f"arc_page_probe_failed: {redact_sensitive_text(result['stderr'])[:240]}",
        }
    payload: Any = result["stdout"]
    for _ in range(2):
        if not isinstance(payload, str):
            break
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            break
    if not isinstance(payload, dict):
        return {"ok": False, "tab_found": False, "path": "", "login_required": True, "error": "arc_page_probe_invalid"}
    tab_found = payload.get("tab_found") is True
    login_required = payload.get("login_required") is not False
    return {
        "ok": tab_found and not login_required,
        "tab_found": tab_found,
        "path": str(payload.get("path") or ""),
        "login_required": login_required,
        "error": "" if tab_found and not login_required else "xiaohongshu_login_required",
    }


def combine_arc_login_status(cookie: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    ok = cookie.get("ok") is True and page.get("ok") is True
    errors = [str(value) for value in (cookie.get("error"), page.get("error")) if value]
    return {
        "ok": ok,
        "cookie_count": cookie.get("cookie_count", 0),
        "has_session_cookie": cookie.get("has_session_cookie", False),
        "tab_found": page.get("tab_found", False),
        "page_path": page.get("path", ""),
        "page_login_required": page.get("login_required", True),
        "error": "; ".join(errors),
    }


def arc_login_status() -> dict[str, Any]:
    return combine_arc_login_status(arc_cookie_status(), arc_xiaohongshu_page_status())


def video_content_environment(
    *,
    extractor_root: str | Path | None = None,
    browser: str = "arc",
    check_login_state: bool = False,
    analysis_provider: str | None = None,
    analysis_command: str | None = None,
    mimo_vl_root: str | Path | None = None,
    visual_analysis: bool = False,
) -> dict[str, Any]:
    if analysis_provider is not None and analysis_provider not in ANALYSIS_PROVIDERS:
        raise ValueError(f"analysis_provider 必须是 {', '.join(ANALYSIS_PROVIDERS)} 之一")
    if analysis_provider == "command" and not str(analysis_command or "").strip():
        raise ValueError("analysis_provider=command 时必须提供 analysis_command")

    root = find_video_transcript_extractor_root(extractor_root)
    tools = {name: shutil.which(name) or "" for name in ("yt-dlp", "ffmpeg", "ffprobe")}
    browser_cookie3_available = importlib.util.find_spec("browser_cookie3") is not None
    extractor_check = {"ok": False, "returncode": None, "stdout": "", "stderr": "not_checked"}
    if root is not None:
        extractor_check = run_status(
            [shutil.which("python3") or "python3", str(root / VIDEO_TRANSCRIPT_SCRIPT), "--check-env"],
            timeout=30,
        )

    analysis_missing: list[str] = []
    codex_login = {"checked": False, "ok": None, "returncode": None, "stdout": "", "stderr": "not_selected"}
    mimo_vl_check: dict[str, Any] = {"checked": False, "ready": None, "missing": []}
    analysis_command_check: dict[str, Any] = {"checked": False, "ok": None, "executable": "", "error": "not_selected"}
    if analysis_provider == "codex-cli":
        codex_path = shutil.which("codex") or ""
        tools["codex"] = codex_path
        if not codex_path:
            analysis_missing.append("codex")
            codex_login = {
                "checked": True,
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": "codex_missing",
            }
        else:
            codex_login = {
                "checked": True,
                **run_status([codex_path, "login", "status"], timeout=15),
            }
            if not codex_login["ok"]:
                analysis_missing.append("codex-login")
    elif analysis_provider == "mimo-vl-mlx":
        mimo_vl_check = check_mimo_vl_environment(mimo_vl_root)
        analysis_missing.extend(mimo_vl_check["missing"])
    elif analysis_provider == "command":
        analysis_command_check = check_analysis_command(str(analysis_command))
        if not analysis_command_check["ok"]:
            analysis_missing.append("analysis-command-executable")

    arc_app = Path("/Applications/Arc.app").exists() if platform.system() == "Darwin" else False
    arc_is_running = arc_running() if browser == "arc" else None
    login = (
        arc_login_status()
        if browser == "arc" and check_login_state
        else {
            "ok": None,
            "cookie_count": None,
            "has_session_cookie": None,
            "tab_found": None,
            "page_path": "",
            "page_login_required": None,
            "error": "not_checked",
        }
    )

    asr_missing: list[str] = []
    if root is None:
        asr_missing.append("video-transcript-extractor")
    for name in ("yt-dlp", "ffmpeg", "ffprobe"):
        if not tools[name]:
            asr_missing.append(name)
    media_tools_ready = all(tools[name] for name in ("yt-dlp", "ffmpeg", "ffprobe"))
    if root is not None and not extractor_check["ok"] and media_tools_ready:
        asr_missing.append("mimo-mlx-runtime-or-model")

    browser_missing: list[str] = []
    if browser == "arc":
        if not arc_app:
            browser_missing.append("arc")
        elif not arc_is_running:
            browser_missing.append("arc-running")
        if check_login_state:
            if not browser_cookie3_available:
                browser_missing.append("browser-cookie3")
            elif login["ok"] is not True:
                browser_missing.append("arc-xiaohongshu-login")

    missing = [*asr_missing, *analysis_missing, *browser_missing]
    analysis_required = analysis_provider is not None
    analysis_ready: bool | None = not analysis_missing if analysis_required else None
    capabilities = {
        "text_analysis": {
            "required": analysis_required,
            "provider": analysis_provider or "",
            "ready": analysis_ready,
            "missing": list(analysis_missing),
        },
        "visual_analysis": {
            "required": visual_analysis,
            "provider": (analysis_provider or "") if visual_analysis else "",
            "ready": bool(analysis_ready) if visual_analysis else False,
            "missing": list(analysis_missing) if visual_analysis else [],
            "status": "ready" if visual_analysis and analysis_ready else "unavailable" if visual_analysis else "not_enabled",
        },
        "asr": {
            "required": True,
            "provider": "mimo-v2.5-asr-mlx",
            "ready": not asr_missing,
            "missing": list(asr_missing),
        },
    }

    local_mimo_missing_keys = {
        "mimo-mlx-runtime-or-model",
        "mimo-vl-venv-python",
        "mimo-vl-official-bf16-model",
        "mlx-vlm-0.5.0",
    }
    non_installable_blockers = [key for key in missing if key == "mimo-vl-apple-silicon"]
    mimo_missing = any(key in local_mimo_missing_keys for key in missing)
    missing_requiring_additional_confirmation = [
        key for key in missing if key not in local_mimo_missing_keys and key not in non_installable_blockers
    ]

    install_steps = [
        {
            "component": "yt-dlp-and-ffmpeg",
            "missing_keys": ["yt-dlp", "ffmpeg", "ffprobe"],
            "purpose": "获取平台字幕、下载音频、转换音频并读取时长",
            "command": "brew install yt-dlp ffmpeg",
            "where": "本机终端",
        },
        {
            "component": "video-transcript-extractor",
            "missing_keys": ["video-transcript-extractor"],
            "purpose": "复用已经验证的 Arc 登录态导出和 MiMo 转写链路",
            "command": "git clone https://github.com/themrv1ck/video-transcript-extractor.git \"$HOME/video-transcript-extractor\"",
            "where": "本机终端",
        },
        {
            "component": "mimo-mlx",
            "missing_keys": ["mimo-mlx-runtime-or-model"],
            "purpose": "没有平台字幕时在 Apple Silicon Mac 上把视频语音转成文字，模型与 tokenizer 约 6.6 GB",
            "command": "git clone https://github.com/ailuntx/MiMo-V2.5-ASR-MLX.git \"$HOME/MiMo-V2.5-ASR-MLX\" && cd \"$HOME/MiMo-V2.5-ASR-MLX\" && python3 -m venv .venv && .venv/bin/python -m pip install --upgrade pip huggingface-hub && .venv/bin/python -m pip install -r requirements-mlx.txt && .venv/bin/hf download mlx-community/MiMo-Audio-Tokenizer --local-dir models/MiMo-Audio-Tokenizer && .venv/bin/hf download mlx-community/MiMo-V2.5-ASR-MLX --local-dir models/MiMo-V2.5-ASR-MLX",
            "where": "macOS Apple Silicon 本机终端",
        },
        {
            "component": "python",
            "missing_keys": ["python", "python-version"],
            "purpose": "运行检测、转写编排与分类脚本，要求 Python 3.9+",
            "command": "brew install python@3.12",
            "where": "本机终端；如果没有 Homebrew，先停止并询问是否安装 Homebrew",
        },
    ]
    if analysis_provider == "codex-cli":
        install_steps.extend(
            [
                {
                    "component": "codex-cli",
                    "missing_keys": ["codex"],
                    "purpose": "使用用户选择的 Codex CLI 分析文字与完整时轴画面",
                    "command": "npm install -g @openai/codex",
                    "where": "本机终端",
                },
                {
                    "component": "codex-login",
                    "missing_keys": ["codex-login"],
                    "purpose": "让用户选择的 Codex CLI 可以执行内容分析",
                    "command": "codex login",
                    "where": "本机终端",
                },
            ]
        )
    elif analysis_provider == "mimo-vl-mlx":
        install_steps.append(
            {
                "component": "mimo-vl-mlx",
                "missing_keys": [
                    "mimo-vl-venv-python",
                    "mimo-vl-official-bf16-model",
                    "mlx-vlm-0.5.0",
                ],
                "purpose": "用 MiMo-VL-7B-RL-2508 官方 BF16 权重读取完整时轴画面并结合文字稿分类",
                "size": "官方 BF16 下载约 16.6 GB",
                "necessity": "只有选择 mimo-vl-mlx 作为分析 provider 时必需",
                "hardware": "仅支持 Apple Silicon；本机实测推理峰值约 17.6 GB，建议 32 GB 统一内存或以上，24 GB 可能紧张",
                "revision": MIMO_VL_REVISION,
                "version_policy": "mlx-vlm 必须精确为 0.5.0；禁止 0.6.4，因本机视觉实测回归",
                "command": "./scripts/install_mimo_vl_mlx.sh",
                "where": "macOS Apple Silicon 本机终端；在小红书 Skill 根目录执行",
            }
        )
        install_steps.append(
            {
                "component": "mimo-vl-apple-silicon",
                "missing_keys": ["mimo-vl-apple-silicon"],
                "purpose": "MLX-VLM 本地推理需要 Apple Silicon",
                "command": "该限制无法通过安装修复；当前机器不能使用 mimo-vl-mlx provider",
                "where": "无可执行安装命令",
            }
        )
    elif analysis_provider == "command":
        install_steps.append(
            {
                "component": "analysis-command",
                "missing_keys": ["analysis-command-executable"],
                "purpose": "使用用户明确提供的 Agent/模型命令分析文字与完整时轴画面",
                "command": "请先安装 --analysis-command 的可执行程序，再重新检测",
                "where": "该命令所在的本机终端环境",
            }
        )
    if browser == "arc":
        install_steps.extend(
            [
                {
                    "component": "browser-cookie3",
                    "missing_keys": ["browser-cookie3"],
                    "purpose": "只读检测并临时导出 Arc 的小红书登录态",
                    "command": "python3 -m pip install browser-cookie3",
                    "where": "本机终端",
                },
                {
                    "component": "arc",
                    "missing_keys": ["arc"],
                    "purpose": "使用用户明确授权的小红书网页登录态",
                    "command": "brew install --cask arc",
                    "where": "本机终端；如果没有 Homebrew，先停止并询问是否安装 Homebrew",
                },
                {
                    "component": "arc-running",
                    "missing_keys": ["arc-running"],
                    "purpose": "防止脚本隐式启动外部浏览器",
                    "command": "请用户手动打开 Arc；脚本不会代为启动",
                    "where": "Arc 桌面应用",
                },
                {
                    "component": "arc-xiaohongshu-login",
                    "missing_keys": ["arc-xiaohongshu-login"],
                    "purpose": "让收藏接口和视频访问使用用户自己的登录态",
                    "command": "请用户在 Arc 打开 https://www.xiaohongshu.com/ 并完成登录",
                    "where": "Arc 浏览器",
                },
            ]
        )
    mimo_authorized_components = ["mimo-v2.5-asr-mlx"]
    if analysis_provider == "mimo-vl-mlx":
        mimo_authorized_components.append("mimo-vl-mlx")
    return {
        "enabled_feature": "classify_video_by_content",
        "platform": platform.system(),
        "apple_silicon": platform.system() == "Darwin" and platform.machine() == "arm64",
        "browser": browser,
        "analysis_provider": analysis_provider or "",
        "visual_analysis_enabled": visual_analysis,
        "analysis_command_configured": bool(str(analysis_command or "").strip()),
        "tools": tools,
        "extractor_root": str(root) if root else "",
        "extractor_check": extractor_check,
        "codex_login": codex_login,
        "mimo_vl_check": mimo_vl_check,
        "analysis_command_check": analysis_command_check,
        "arc_app_installed": arc_app,
        "arc_running": arc_is_running,
        "arc_xiaohongshu_login": login,
        "browser_cookie3_available": browser_cookie3_available,
        "capabilities": capabilities,
        "video_content_ready": not missing,
        "missing": missing,
        "mimo_install_consent": "granted_by_enable_response",
        "mimo_install_authorized": True,
        "mimo_install_authorized_components": mimo_authorized_components,
        "mimo_install_required": mimo_missing,
        "non_installable_blockers": non_installable_blockers,
        "missing_requiring_additional_confirmation": missing_requiring_additional_confirmation,
        "should_ask_user_to_install_video_content": bool(missing_requiring_additional_confirmation),
        "install_steps": install_steps,
        "not_required": ["Qwen", "LM Studio"],
    }


def canonical_xiaohongshu_note_url(item: dict[str, Any]) -> str:
    note_id = str(item.get("id") or "").strip()
    if note_id:
        return f"https://www.xiaohongshu.com/explore/{note_id}"
    raw = str(item.get("href") or item.get("url") or "").strip()
    parsed = urlparse(raw)
    parts = [part for part in parsed.path.split("/") if part]
    for marker in ("explore", "profile"):
        if marker in parts:
            index = parts.index(marker)
            candidates = parts[index + 1 :]
            for candidate in reversed(candidates):
                if len(candidate) == 24:
                    return f"https://www.xiaohongshu.com/explore/{candidate}"
    return ""


def arc_cache_dirs(profile: str = "Default") -> list[Path]:
    root = Path.home() / "Library" / "Caches" / "Arc" / "User Data"
    requested = root / profile
    candidates = [requested]
    if profile == "Default":
        candidates.append(root / "Profile 1")
    return [path / "Cache" / "Cache_Data" for path in candidates if (path / "Cache" / "Cache_Data").is_dir()]


def parse_arc_collection_cache_entry(path: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if ARC_COLLECTION_CACHE_NEEDLE not in data:
        return []
    payload: Any = None
    gzip_offset = data.find(b"\x1f\x8b")
    if gzip_offset >= 0:
        try:
            decompressor = zlib.decompressobj(31)
            body = decompressor.decompress(data[gzip_offset:]) + decompressor.flush()
            payload = json.loads(body)
        except Exception:
            payload = None
    if payload is None:
        decoder = json.JSONDecoder()
        for match in re.finditer(rb"\{\"", data):
            try:
                candidate, _ = decoder.raw_decode(data[match.start():].decode("utf-8", errors="ignore"))
            except Exception:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("data"), dict):
                payload = candidate
                break
    if not isinstance(payload, dict) or payload.get("success") is not True or payload.get("code") != 0:
        return []
    notes = (payload.get("data") or {}).get("notes")
    return [note for note in notes if isinstance(note, dict)] if isinstance(notes, list) else []


def load_arc_collection_note_contexts(
    *,
    profile: str = "Default",
    cache_directories: list[Path] | None = None,
) -> dict[str, dict[str, Any]]:
    candidates = cache_directories if cache_directories is not None else arc_cache_dirs(profile)
    files: list[Path] = []
    for directory in candidates:
        if directory.is_dir():
            files.extend(path for path in directory.glob("*_0") if path.is_file())
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    contexts: dict[str, dict[str, Any]] = {}
    now = time.time()
    for path in files:
        try:
            notes = parse_arc_collection_cache_entry(path)
        except OSError:
            continue
        for note in notes:
            note_id = str(note.get("note_id") or "")
            if not note_id:
                continue
            token = str(note.get("xsec_token") or "")
            existing = contexts.get(note_id)
            if existing and existing.get("found") is True:
                continue
            contexts[note_id] = {
                "found": bool(token),
                "xsec_token": token,
                "xsec_source": "pc_user",
                "content_type": normalize_content_type(note.get("type")),
                "cache_age_seconds": max(0.0, now - path.stat().st_mtime),
            }
    return contexts


def find_arc_collection_note_context(
    note_id: str,
    *,
    profile: str = "Default",
    cache_directories: list[Path] | None = None,
) -> dict[str, Any]:
    return load_arc_collection_note_contexts(
        profile=profile,
        cache_directories=cache_directories,
    ).get(
        note_id,
        {"found": False, "xsec_token": "", "xsec_source": "", "content_type": "unknown", "cache_age_seconds": None},
    )


def xiaohongshu_access_url(canonical_url: str, context: dict[str, Any]) -> str:
    token = str(context.get("xsec_token") or "")
    source = str(context.get("xsec_source") or "")
    if not canonical_url or not token or not source:
        raise ValueError("Arc 收藏会话参数不完整")
    return f"{canonical_url}?{urlencode({'xsec_token': token, 'xsec_source': source})}"


def normalize_content_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"video", "视频"}:
        return "video"
    if text in {"normal", "image", "images", "图文", "note"}:
        return "image"
    return "unknown"


def transcript_sha256(segments: list[dict[str, Any]]) -> str:
    blob = json.dumps(segments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def safe_error(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {redact_sensitive_text(exc)[:500]}"
