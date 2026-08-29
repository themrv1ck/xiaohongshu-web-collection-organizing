#!/usr/bin/env python3
"""Persistent JSONL worker for official MiMo-VL BF16 through MLX-VLM 0.5.0."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REQUIRED_MLX_VLM_VERSION = "0.5.0"
OUTPUT_SCHEMA_CONTRACT_VERSION = 5
# One Qwen merged visual token covers 28x28 pixels.  Capping each timeline
# frame at 512 tokens keeps even 100+ real frames inside MiMo's 128k context.
DEFAULT_MAX_IMAGE_PIXELS = 28 * 28 * 512
TEMPERATURE = 0.0
TOP_P = 1.0
NO_THINK_SUFFIX = "/no_think"
JSON_ONLY_CONTRACT = (
    "这是机器到机器的 JSON 接口。回复第一个字符必须是 {，最后一个字符必须是 }。"
    "禁止输出 Markdown 代码块、```、解释文字或任何 JSON 之外的字符。"
)
def ensure_no_think(prompt: str) -> str:
    """Append the strict JSON protocol and put MiMo's control command last."""
    stripped = prompt.rstrip()
    if stripped.endswith(NO_THINK_SUFFIX):
        stripped = stripped[: -len(NO_THINK_SUFFIX)].rstrip()
    return f"{stripped}\n{JSON_ONLY_CONTRACT}\n{NO_THINK_SUFFIX}"


def _load_json_object(raw: str) -> dict[str, Any]:
    def reject_nonstandard_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    payload = json.loads(raw, parse_constant=reject_nonstandard_constant)
    if not isinstance(payload, dict):
        raise ValueError("MiMo-VL output must be a JSON object")
    return payload


def parse_model_output(raw: str) -> dict[str, Any]:
    """Parse exactly one strict JSON object; never strip, search, or repair."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("MiMo-VL output is empty")
    try:
        return _load_json_object(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("MiMo-VL output is not strict JSON") from exc


def _write(protocol: TextIO, payload: dict[str, Any]) -> None:
    protocol.write(json.dumps(payload, ensure_ascii=False) + "\n")
    protocol.flush()


def _error_payload(reason_code: str, message: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "reason_code": reason_code,
        "message": message,
        "metadata": dict(metadata or {}),
    }


def _validate_request(payload: Any) -> tuple[str, list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    prompt = payload.get("prompt")
    image_paths = payload.get("image_paths")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if not isinstance(image_paths, list) or not all(isinstance(path, str) for path in image_paths):
        raise ValueError("image_paths must be a string array")
    for path in image_paths:
        if not Path(path).is_file():
            raise ValueError(f"image file does not exist: {path}")
    return prompt, image_paths


def _load_output_schema(path: str | Path) -> dict[str, Any]:
    schema_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read output schema: {schema_path}") from exc
    if not isinstance(payload, dict) or payload.get("type") != "object":
        raise ValueError("output schema must describe a JSON object")
    return payload


def run_worker(
    model_path: str,
    max_tokens: int,
    *,
    output_schema: str | Path,
    max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
    protocol: TextIO = sys.stdout,
) -> int:
    identity = {
        "provider": "mimo-vl-mlx",
        "model": model_path,
        "version": (
            f"mlx-vlm-{REQUIRED_MLX_VLM_VERSION}-max-pixels-{max_image_pixels}"
            f"-output-schema-v{OUTPUT_SCHEMA_CONTRACT_VERSION}"
        ),
    }
    try:
        schema_path = Path(output_schema).expanduser().resolve()
        identity["schema_sha256"] = hashlib.sha256(schema_path.read_bytes()).hexdigest()
        installed_version = importlib.metadata.version("mlx-vlm")
        if installed_version != REQUIRED_MLX_VLM_VERSION:
            raise RuntimeError(
                f"mlx-vlm version mismatch: expected {REQUIRED_MLX_VLM_VERSION}, got {installed_version}"
            )
        # Keeping imports and model logs away from stdout is mandatory because
        # stdout is the JSONL protocol channel.
        with contextlib.redirect_stdout(sys.stderr):
            from mlx_vlm import generate, load
            from mlx_vlm.prompt_utils import apply_chat_template
            from mlx_vlm.structured import build_json_schema_logits_processor

            model, processor = load(model_path)
            image_processor = getattr(processor, "image_processor", None)
            if image_processor is None or not hasattr(image_processor, "max_pixels"):
                raise RuntimeError("MiMo image processor does not expose max_pixels")
            image_processor.max_pixels = max_image_pixels
            schema = _load_output_schema(schema_path)
            tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
            structured_processor = build_json_schema_logits_processor(tokenizer, schema)
        _write(protocol, {"type": "ready", "ok": True, "identity": identity})
    except Exception as exc:
        _write(protocol, _error_payload(
            "mimo_vl_startup_failed",
            f"{type(exc).__name__}: {exc}",
            {"expected_mlx_vlm_version": REQUIRED_MLX_VLM_VERSION},
        ) | {"type": "ready"})
        return 1

    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            _write(protocol, _error_payload(
                "provider_protocol_error",
                f"invalid request JSON: {exc}",
            ))
            continue
        if isinstance(request, dict) and request.get("action") == "close":
            _write(protocol, {"ok": True, "closed": True})
            return 0
        if not isinstance(request, dict) or request.get("action") != "analyze":
            _write(protocol, _error_payload(
                "provider_protocol_error",
                "unsupported worker action",
            ))
            continue
        try:
            prompt, image_paths = _validate_request(request)
        except ValueError as exc:
            _write(protocol, _error_payload(
                "provider_request_invalid",
                str(exc),
            ))
            continue

        try:
            user_prompt = ensure_no_think(prompt)
            with contextlib.redirect_stdout(sys.stderr):
                formatted_prompt = apply_chat_template(
                    processor,
                    model.config,
                    user_prompt,
                    num_images=len(image_paths),
                )
                generated = generate(
                    model,
                    processor,
                    formatted_prompt,
                    image=image_paths or None,
                    verbose=False,
                    max_tokens=max_tokens,
                    temperature=TEMPERATURE,
                    top_p=TOP_P,
                    logits_processors=[structured_processor.clone()],
                )
            raw_output = generated.text if hasattr(generated, "text") else generated
        except Exception as exc:
            _write(protocol, _error_payload(
                "mimo_vl_generation_failed",
                f"{type(exc).__name__}: {exc}",
            ))
            continue
        try:
            result = parse_model_output(raw_output)
        except ValueError as exc:
            generation_tokens = int(getattr(generated, "generation_tokens", 0) or 0)
            truncated = generation_tokens >= max_tokens
            _write(protocol, _error_payload(
                "mimo_vl_output_truncated" if truncated else "mimo_vl_invalid_json",
                str(exc),
                {
                    "output_length": len(raw_output) if isinstance(raw_output, str) else 0,
                    "generation_tokens": generation_tokens,
                    "max_tokens": max_tokens,
                },
            ))
            continue
        _write(protocol, {"ok": True, "result": result})
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent MiMo-VL MLX JSONL worker")
    parser.add_argument("--model", required=True, help="Official BF16 model directory or Hugging Face ID")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--max-image-pixels", type=int, default=DEFAULT_MAX_IMAGE_PIXELS)
    parser.add_argument("--output-schema", required=True, help="JSON schema used for constrained decoding")
    args = parser.parse_args(argv)
    if args.max_tokens <= 0:
        parser.error("--max-tokens must be positive")
    if args.max_image_pixels <= 0:
        parser.error("--max-image-pixels must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_worker(
        args.model,
        args.max_tokens,
        output_schema=args.output_schema,
        max_image_pixels=args.max_image_pixels,
    )


if __name__ == "__main__":
    raise SystemExit(main())
