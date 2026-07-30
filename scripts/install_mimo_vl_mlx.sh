#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
ROOT="${XHS_MIMO_VL_ROOT:-$HOME/Documents/MiMo-VL-7B-RL-2508}"
MODEL="$ROOT/models/MiMo-VL-7B-RL-2508"
MODEL_ID="XiaomiMiMo/MiMo-VL-7B-RL-2508"
REVISION="4bfb270765825d2fa059011deb4c96fdd579be6f"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || {
  print -u2 "MiMo-VL 的 MLX 安装路径只支持 Apple Silicon Mac。"
  exit 2
}
[[ -n "$PYTHON_BIN" ]] || {
  print -u2 "缺少 python3。请先安装 Python 3.12。"
  exit 2
}

mkdir -p "$ROOT/models"
"$PYTHON_BIN" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -r "$SCRIPT_DIR/../requirements-mimo-vl-mlx.txt"
"$ROOT/.venv/bin/hf" download "$MODEL_ID" \
  --revision "$REVISION" \
  --local-dir "$MODEL"

"$ROOT/.venv/bin/python" "$SCRIPT_DIR/verify_mimo_vl_install.py" \
  --root "$ROOT" \
  --run-inference
