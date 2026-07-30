#!/usr/bin/env python3
"""Verify the pinned official BF16 MiMo-VL + MLX-VLM installation."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any


MODEL_SUBDIR = Path("models/MiMo-VL-7B-RL-2508")
REQUIRED_MLX_VLM_VERSION = "0.5.0"
EXPECTED_SHARD_SIZES = {
    "model-00001-of-00004.safetensors": 4_612_695_408,
    "model-00002-of-00004.safetensors": 4_937_303_136,
    "model-00003-of-00004.safetensors": 4_982_109_888,
    "model-00004-of-00004.safetensors": 2_080_418_376,
}


def run(args: list[str], *, timeout: int, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{exc.__class__.__name__}: {exc}",
        }
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": str(result.stdout or "").strip(),
        "stderr": str(result.stderr or "").strip(),
    }


def verify(root: Path, *, run_inference: bool) -> dict[str, Any]:
    root = root.expanduser().resolve()
    model = root / MODEL_SUBDIR
    python = root / ".venv" / "bin" / "python"
    cli = root / ".venv" / "bin" / "mlx_vlm.generate"
    errors: list[str] = []

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        errors.append("需要 Apple Silicon Mac")
    if not python.is_file() or not os.access(python, os.X_OK):
        errors.append(f"缺少可执行 Python：{python}")

    version_result = (
        run(
            [str(python), "-c", "from importlib.metadata import version; print(version('mlx-vlm'))"],
            timeout=30,
        )
        if python.is_file()
        else {"ok": False, "returncode": None, "stdout": "", "stderr": "python_missing"}
    )
    version = version_result["stdout"] if version_result["ok"] else ""
    if version != REQUIRED_MLX_VLM_VERSION:
        errors.append(f"mlx-vlm 必须精确为 {REQUIRED_MLX_VLM_VERSION}，当前为 {version or '不可用'}")

    missing_files = [
        name for name in ("config.json", "model.safetensors.index.json", *EXPECTED_SHARD_SIZES)
        if not (model / name).is_file()
    ]
    if missing_files:
        errors.append("模型文件缺失：" + ", ".join(missing_files))
    wrong_sizes = {
        name: (model / name).stat().st_size
        for name, expected in EXPECTED_SHARD_SIZES.items()
        if (model / name).is_file() and (model / name).stat().st_size != expected
    }
    if wrong_sizes:
        errors.append("官方 BF16 权重分片大小不匹配：" + json.dumps(wrong_sizes, ensure_ascii=False))

    inference: dict[str, Any] = {"checked": False, "ok": None}
    if run_inference and not errors:
        metric_image = model / "metric.jpeg"
        if not metric_image.is_file():
            errors.append(f"缺少官方视觉验收图片：{metric_image}")
        elif not cli.is_file() or not os.access(cli, os.X_OK):
            errors.append(f"缺少 MLX-VLM 命令：{cli}")
        else:
            inference_result = run(
                [
                    str(cli),
                    "--model", str(model),
                    "--image", str(metric_image),
                    "--prompt",
                    "读取表格。只回答 Video-MME (w/o sub.) 这一行在 MiMo-VL-7B-RL-2508 (Thinking) 这一列的数值。 /no_think",
                    "--max-tokens", "48",
                    "--temperature", "0.0",
                ],
                timeout=900,
                env={**os.environ, "HF_HUB_OFFLINE": "1"},
            )
            inference = {"checked": True, **inference_result}
            if not inference_result["ok"] or "70.8" not in inference_result["stdout"]:
                errors.append("视觉推理验收失败：官方表格题预期答案为 70.8")

    return {
        "ok": not errors,
        "root": str(root),
        "model": str(model),
        "mlx_vlm_version": version,
        "required_mlx_vlm_version": REQUIRED_MLX_VLM_VERSION,
        "official_bf16_weight_bytes": sum(EXPECTED_SHARD_SIZES.values()),
        "missing_files": missing_files,
        "wrong_shard_sizes": wrong_sizes,
        "version_check": version_result,
        "inference": inference,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="验收官方 BF16 MiMo-VL + MLX-VLM 0.5.0")
    parser.add_argument(
        "--root",
        default=os.environ.get("XHS_MIMO_VL_ROOT", str(Path.home() / "Documents" / "MiMo-VL-7B-RL-2508")),
    )
    parser.add_argument("--run-inference", action="store_true", help="额外执行一次真实图片推理")
    args = parser.parse_args()
    result = verify(Path(args.root), run_inference=args.run_inference)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
