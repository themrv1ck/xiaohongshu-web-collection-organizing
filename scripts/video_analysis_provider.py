#!/usr/bin/env python3
"""Strict, agent-neutral providers for video frame analysis.

The provider boundary intentionally accepts only an analysis prompt and real
image paths.  It has no title/description metadata input and never invents a
classification when its backend fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import subprocess
import tempfile
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_SCHEMA = ROOT / "schemas" / "video_analysis.schema.json"
DEFAULT_MIMO_WORKER = Path(__file__).resolve().with_name("mimo_vl_worker.py")
MIMO_VLM_VERSION = "0.5.0"


class ProviderError(RuntimeError):
    """An analysis-provider failure with a stable machine-readable contract."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.reason_code = str(reason_code)
        self.message = str(message)
        self.metadata = dict(metadata or {})
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "message": self.message,
            "metadata": dict(self.metadata),
        }


def _compact(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _validate_timeout(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProviderError(
            "provider_config_invalid",
            f"{label} 必须是正整数秒",
            {"field": label},
        )
    return value


def _normalize_request(
    prompt: str,
    image_paths: Sequence[str | os.PathLike[str]],
) -> tuple[str, list[str]]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProviderError(
            "provider_request_invalid",
            "prompt 必须是非空字符串",
            {"field": "prompt"},
        )
    if isinstance(image_paths, (str, bytes, os.PathLike)):
        raise ProviderError(
            "provider_request_invalid",
            "image_paths 必须是路径序列",
            {"field": "image_paths"},
        )
    normalized: list[str] = []
    for index, raw_path in enumerate(image_paths):
        if not isinstance(raw_path, (str, os.PathLike)):
            raise ProviderError(
                "provider_request_invalid",
                "image_paths 只能包含文件路径",
                {"field": "image_paths", "index": index},
            )
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ProviderError(
                "provider_image_missing",
                "视频帧文件不存在",
                {"index": index, "path": str(path)},
            )
        normalized.append(str(path))
    return prompt, normalized


def _strict_json_object(raw: str, *, source: str) -> dict[str, Any]:
    def reject_nonstandard_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    try:
        payload = json.loads(raw, parse_constant=reject_nonstandard_constant)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProviderError(
            "provider_invalid_response",
            f"{source} 返回的不是严格 JSON 对象",
            {"response_length": len(raw or "")},
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderError(
            "provider_invalid_response",
            f"{source} 返回的 JSON 不是对象",
            {"response_type": type(payload).__name__},
        )
    return payload


class AnalysisProvider(ABC):
    """Common lifecycle and request contract for analysis backends."""

    def __init__(self) -> None:
        self._closed = False

    def analyze(
        self,
        prompt: str,
        image_paths: Sequence[str | os.PathLike[str]] = (),
    ) -> dict[str, Any]:
        if self._closed:
            raise ProviderError("provider_closed", "analysis provider 已关闭")
        normalized_prompt, normalized_images = _normalize_request(prompt, image_paths)
        return self._analyze(normalized_prompt, normalized_images)

    @abstractmethod
    def _analyze(self, prompt: str, image_paths: list[str]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def identity(self) -> dict[str, str]:
        raise NotImplementedError

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "AnalysisProvider":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


class CodexCliProvider(AnalysisProvider):
    """Invoke the same ephemeral, read-only ``codex exec`` JSON protocol."""

    def __init__(
        self,
        *,
        codex_bin: str,
        model: str | None,
        timeout: int,
        output_schema: str | os.PathLike[str] | None,
        working_directory: str | os.PathLike[str] | None,
    ) -> None:
        super().__init__()
        if not isinstance(codex_bin, str) or not codex_bin.strip():
            raise ProviderError(
                "provider_config_invalid",
                "codex_bin 必须是非空命令",
                {"field": "codex_bin"},
            )
        self.codex_bin = codex_bin
        self.model = str(model).strip() if model else None
        self.timeout = _validate_timeout(timeout, "timeout")
        self.output_schema = Path(output_schema or DEFAULT_OUTPUT_SCHEMA).expanduser().resolve()
        if not self.output_schema.is_file():
            raise ProviderError(
                "provider_config_invalid",
                "Codex 输出 Schema 不存在",
                {"field": "output_schema", "path": str(self.output_schema)},
            )
        self.working_directory = Path(working_directory or ROOT).expanduser().resolve()
        if not self.working_directory.is_dir():
            raise ProviderError(
                "provider_config_invalid",
                "Codex 工作目录不存在",
                {"field": "working_directory", "path": str(self.working_directory)},
            )

    def identity(self) -> dict[str, str]:
        return {
            "provider": "codex-cli",
            "model": self.model or "default",
            "version": "codex-exec-v1",
        }

    def _analyze(self, prompt: str, image_paths: list[str]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="xhs-analysis-codex-") as temp_dir:
            output_path = Path(temp_dir) / "analysis.json"
            command = [
                self.codex_bin,
                "exec",
                "--ephemeral",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "--output-schema",
                str(self.output_schema),
                "--output-last-message",
                str(output_path),
                "--cd",
                str(self.working_directory),
            ]
            if self.model:
                command.extend(["--model", self.model])
            for image_path in image_paths:
                command.extend(["--image", image_path])
            command.append("-")
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=str(self.working_directory),
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderError(
                    "provider_timeout",
                    "Codex 视频分析超时",
                    {"timeout": self.timeout},
                ) from exc
            except OSError as exc:
                raise ProviderError(
                    "provider_process_failed",
                    "无法启动 Codex CLI",
                    {"exception_type": type(exc).__name__, "error": _compact(exc)},
                ) from exc
            if completed.returncode != 0 or not output_path.is_file():
                raise ProviderError(
                    "provider_process_failed",
                    "Codex CLI 没有生成有效分析结果",
                    {
                        "returncode": completed.returncode,
                        "stdout": _compact(completed.stdout),
                        "stderr": _compact(completed.stderr),
                        "output_exists": output_path.is_file(),
                    },
                )
            return _strict_json_object(
                output_path.read_text(encoding="utf-8"),
                source="Codex CLI",
            )


class CommandProvider(AnalysisProvider):
    """Run an arbitrary agent command using a strict one-request JSON contract."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        model: str | None,
        timeout: int,
        working_directory: str | os.PathLike[str] | None,
    ) -> None:
        super().__init__()
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            raise ProviderError(
                "provider_config_invalid",
                "command 必须是 argv 序列，不能是 shell 字符串",
                {"field": "command"},
            )
        self.command = [str(value) for value in command]
        if not self.command or any(not value for value in self.command):
            raise ProviderError(
                "provider_config_invalid",
                "command 不能为空",
                {"field": "command"},
            )
        self.model = str(model).strip() if model else Path(self.command[0]).name
        command_material = json.dumps(self.command, ensure_ascii=False, separators=(",", ":"))
        self.command_sha256 = hashlib.sha256(command_material.encode("utf-8")).hexdigest()
        self.timeout = _validate_timeout(timeout, "timeout")
        self.working_directory = (
            Path(working_directory).expanduser().resolve() if working_directory else None
        )
        if self.working_directory is not None and not self.working_directory.is_dir():
            raise ProviderError(
                "provider_config_invalid",
                "command 工作目录不存在",
                {"field": "working_directory", "path": str(self.working_directory)},
            )

    def identity(self) -> dict[str, str]:
        return {
            "provider": "command",
            "model": self.model,
            "version": f"json-stdin-stdout-v1-{self.command_sha256[:16]}",
        }

    def _analyze(self, prompt: str, image_paths: list[str]) -> dict[str, Any]:
        request = {
            "protocol_version": 1,
            "prompt": prompt,
            "image_paths": image_paths,
        }
        try:
            completed = subprocess.run(
                self.command,
                input=json.dumps(request, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.working_directory) if self.working_directory else None,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                "provider_timeout",
                "外部分析命令超时",
                {"timeout": self.timeout},
            ) from exc
        except OSError as exc:
            raise ProviderError(
                "provider_process_failed",
                "无法启动外部分析命令",
                {"exception_type": type(exc).__name__, "error": _compact(exc)},
            ) from exc
        if completed.returncode != 0:
            raise ProviderError(
                "provider_process_failed",
                "外部分析命令执行失败",
                {
                    "returncode": completed.returncode,
                    "stdout": _compact(completed.stdout),
                    "stderr": _compact(completed.stderr),
                },
            )
        return _strict_json_object(completed.stdout, source="command provider")


class MimoVlMlxProvider(AnalysisProvider):
    """Keep one MLX-VLM worker alive so the BF16 model loads only once."""

    def __init__(
        self,
        *,
        model: str,
        python_bin: str | os.PathLike[str],
        worker_script: str | os.PathLike[str] | None,
        timeout: int,
        startup_timeout: int,
        max_tokens: int,
        working_directory: str | os.PathLike[str] | None,
    ) -> None:
        super().__init__()
        if not isinstance(model, str) or not model.strip():
            raise ProviderError(
                "provider_config_invalid",
                "mimo-vl-mlx 必须指定 model",
                {"field": "model"},
            )
        if not isinstance(python_bin, (str, os.PathLike)) or not str(python_bin):
            raise ProviderError(
                "provider_config_invalid",
                "mimo-vl-mlx 必须指定外部 Python",
                {"field": "python_bin"},
            )
        self.model = model.strip()
        self.python_bin = str(Path(python_bin).expanduser())
        self.worker_script = str(Path(worker_script or DEFAULT_MIMO_WORKER).expanduser())
        self.timeout = _validate_timeout(timeout, "timeout")
        self.startup_timeout = _validate_timeout(startup_timeout, "startup_timeout")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ProviderError(
                "provider_config_invalid",
                "max_tokens 必须是正整数",
                {"field": "max_tokens"},
            )
        self.max_tokens = max_tokens
        self.working_directory = Path(working_directory or ROOT).expanduser().resolve()
        if not self.working_directory.is_dir():
            raise ProviderError(
                "provider_config_invalid",
                "MiMo worker 工作目录不存在",
                {"field": "working_directory", "path": str(self.working_directory)},
            )
        self.process: subprocess.Popen[str] | None = None
        self._io_lock = threading.Lock()
        self._start_worker()

    def identity(self) -> dict[str, str]:
        return {
            "provider": "mimo-vl-mlx",
            "model": self.model,
            "version": f"mlx-vlm-{MIMO_VLM_VERSION}",
        }

    def _start_worker(self) -> None:
        command = [
            self.python_bin,
            "-u",
            self.worker_script,
            "--model",
            self.model,
            "--max-tokens",
            str(self.max_tokens),
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                cwd=str(self.working_directory),
            )
        except OSError as exc:
            raise ProviderError(
                "provider_startup_failed",
                "无法启动 MiMo-VL MLX worker",
                {"exception_type": type(exc).__name__, "error": _compact(exc)},
            ) from exc
        try:
            ready = self._read_response(self.startup_timeout)
            if ready.get("type") != "ready" or ready.get("ok") is not True:
                raise ProviderError(
                    str(ready.get("reason_code") or "provider_startup_failed"),
                    str(ready.get("message") or "MiMo-VL MLX worker 启动失败"),
                    ready.get("metadata") if isinstance(ready.get("metadata"), dict) else {},
                )
            if ready.get("identity") != self.identity():
                raise ProviderError(
                    "provider_protocol_error",
                    "MiMo-VL MLX worker 身份与配置不一致",
                    {"expected": self.identity(), "actual": ready.get("identity")},
                )
        except Exception:
            self._terminate()
            raise

    def _read_response(self, timeout: int) -> dict[str, Any]:
        process = self.process
        if process is None or process.stdout is None:
            raise ProviderError("provider_process_failed", "MiMo-VL MLX worker 不可用")
        try:
            readable, _, _ = select.select([process.stdout], [], [], timeout)
        except (OSError, ValueError) as exc:
            self._terminate()
            raise ProviderError(
                "provider_process_failed",
                "无法读取 MiMo-VL MLX worker",
                {"exception_type": type(exc).__name__, "error": _compact(exc)},
            ) from exc
        if not readable:
            self._terminate()
            raise ProviderError(
                "provider_timeout",
                "MiMo-VL MLX worker 响应超时",
                {"timeout": timeout},
            )
        line = process.stdout.readline()
        if not line:
            returncode = process.poll()
            self._terminate()
            raise ProviderError(
                "provider_process_failed",
                "MiMo-VL MLX worker 意外退出",
                {"returncode": returncode},
            )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self._terminate()
            raise ProviderError(
                "provider_protocol_error",
                "MiMo-VL MLX worker 返回了非 JSON 协议数据",
                {"response_length": len(line)},
            ) from exc
        if not isinstance(payload, dict):
            self._terminate()
            raise ProviderError(
                "provider_protocol_error",
                "MiMo-VL MLX worker 协议数据不是对象",
                {"response_type": type(payload).__name__},
            )
        return payload

    def _write_request(self, payload: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise ProviderError(
                "provider_process_failed",
                "MiMo-VL MLX worker 已退出",
                {"returncode": process.poll() if process is not None else None},
            )
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._terminate()
            raise ProviderError(
                "provider_process_failed",
                "无法向 MiMo-VL MLX worker 发送请求",
                {"exception_type": type(exc).__name__, "error": _compact(exc)},
            ) from exc

    def _analyze(self, prompt: str, image_paths: list[str]) -> dict[str, Any]:
        with self._io_lock:
            self._write_request({
                "action": "analyze",
                "prompt": prompt,
                "image_paths": image_paths,
            })
            response = self._read_response(self.timeout)
        if response.get("ok") is not True:
            raise ProviderError(
                str(response.get("reason_code") or "provider_process_failed"),
                str(response.get("message") or "MiMo-VL 分析失败"),
                response.get("metadata") if isinstance(response.get("metadata"), dict) else {},
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise ProviderError(
                "provider_invalid_response",
                "MiMo-VL worker 结果不是 JSON 对象",
                {"response_type": type(result).__name__},
            )
        return result

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
        if self._closed:
            return
        process = self.process
        if process is not None and process.poll() is None:
            with self._io_lock:
                try:
                    self._write_request({"action": "close"})
                    self._read_response(min(self.timeout, 30))
                except ProviderError:
                    pass
        self._terminate()
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
        self.process = None
        super().close()


def build_analysis_provider(
    name: str,
    *,
    model: str | None = None,
    timeout: int = 300,
    codex_bin: str = "codex",
    output_schema: str | os.PathLike[str] | None = None,
    command: Sequence[str] | None = None,
    python_bin: str | os.PathLike[str] | None = None,
    worker_script: str | os.PathLike[str] | None = None,
    startup_timeout: int = 1800,
    max_tokens: int = 1024,
    working_directory: str | os.PathLike[str] | None = None,
) -> AnalysisProvider:
    """Build one strict analysis provider; unsupported names never downgrade."""

    normalized_name = str(name or "").strip().lower()
    if normalized_name == "codex-cli":
        return CodexCliProvider(
            codex_bin=codex_bin,
            model=model,
            timeout=timeout,
            output_schema=output_schema,
            working_directory=working_directory,
        )
    if normalized_name == "command":
        if command is None:
            raise ProviderError(
                "provider_config_invalid",
                "command provider 必须指定 command argv",
                {"field": "command"},
            )
        return CommandProvider(
            command=command,
            model=model,
            timeout=timeout,
            working_directory=working_directory,
        )
    if normalized_name == "mimo-vl-mlx":
        if not model:
            raise ProviderError(
                "provider_config_invalid",
                "mimo-vl-mlx 必须指定 model",
                {"field": "model"},
            )
        if python_bin is None:
            raise ProviderError(
                "provider_config_invalid",
                "mimo-vl-mlx 必须指定外部 Python",
                {"field": "python_bin"},
            )
        return MimoVlMlxProvider(
            model=model,
            python_bin=python_bin,
            worker_script=worker_script,
            timeout=timeout,
            startup_timeout=startup_timeout,
            max_tokens=max_tokens,
            working_directory=working_directory,
        )
    raise ProviderError(
        "provider_unknown",
        "不支持的 analysis provider",
        {
            "provider": normalized_name,
            "supported": ["codex-cli", "command", "mimo-vl-mlx"],
        },
    )


__all__ = [
    "AnalysisProvider",
    "CodexCliProvider",
    "CommandProvider",
    "MimoVlMlxProvider",
    "ProviderError",
    "build_analysis_provider",
]
