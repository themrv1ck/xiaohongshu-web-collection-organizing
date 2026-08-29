#!/usr/bin/env python3

from __future__ import annotations

import io
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from mimo_vl_worker import ensure_no_think, parse_model_output, run_worker  # noqa: E402
from video_analysis_provider import (  # noqa: E402
    ProviderError,
    build_analysis_provider,
)


def effective_schema_sha256(boards: list[str] | None = None) -> str:
    schema_path = ROOT / "schemas" / "video_analysis.schema.json"
    if boards is None:
        payload = schema_path.read_bytes()
    else:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema["properties"]["target_board"]["enum"] = ["", *boards]
        schema["anyOf"] = [
            {
                "properties": {
                    "target_board": {"const": ""},
                    "confidence": {"const": "low"},
                },
                "required": ["target_board", "confidence"],
            },
            *[
                {
                    "properties": {
                        "target_board": {"const": board},
                    },
                    "required": ["target_board"],
                }
                for board in boards
            ],
        ]
        payload = (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class NonClosingStringIO(io.StringIO):
    def close(self):
        pass


class FakeWorkerProcess:
    def __init__(self, responses: list[dict]):
        self.stdin = NonClosingStringIO()
        self.stdout = NonClosingStringIO(
            "".join(json.dumps(response, ensure_ascii=False) + "\n" for response in responses)
        )
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class ProviderErrorTests(unittest.TestCase):
    def test_provider_error_is_structured_and_serializable(self):
        error = ProviderError("provider_timeout", "timed out", {"timeout": 3})
        self.assertEqual(error.reason_code, "provider_timeout")
        self.assertEqual(error.message, "timed out")
        self.assertEqual(error.metadata, {"timeout": 3})
        self.assertEqual(
            error.to_dict(),
            {"reason_code": "provider_timeout", "message": "timed out", "metadata": {"timeout": 3}},
        )


class CodexProviderTests(unittest.TestCase):
    def test_codex_provider_reuses_current_exec_protocol_and_all_images(self):
        captured = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            schema = temp / "schema.json"
            schema.write_text("{}", encoding="utf-8")
            images = [temp / "one.jpg", temp / "two.jpg"]
            for image in images:
                image.write_bytes(b"image")

            def fake_run(command, *, input, capture_output, text, timeout, cwd):
                captured.update(command=command, input=input, timeout=timeout, cwd=cwd)
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text('{"main_topic":"滑雪"}', encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            provider = build_analysis_provider(
                "codex-cli",
                model="gpt-test",
                timeout=17,
                codex_bin="codex-test",
                output_schema=schema,
                working_directory=temp,
            )
            with patch("video_analysis_provider.subprocess.run", fake_run):
                result = provider.analyze("只根据画面分类", images)

        self.assertEqual(result, {"main_topic": "滑雪"})
        self.assertEqual(captured["command"][:2], ["codex-test", "exec"])
        self.assertIn("--ephemeral", captured["command"])
        self.assertIn("--ignore-rules", captured["command"])
        self.assertEqual(captured["command"].count("--image"), 2)
        self.assertEqual(captured["command"][-1], "-")
        self.assertEqual(captured["input"], "只根据画面分类")
        self.assertEqual(
            provider.identity(),
            {"provider": "codex-cli", "model": "gpt-test", "version": "codex-exec-v1"},
        )

    def test_codex_invalid_json_is_a_provider_error_not_a_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            schema = temp / "schema.json"
            schema.write_text("{}", encoding="utf-8")

            def fake_run(command, **kwargs):
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text("不是 JSON", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            provider = build_analysis_provider("codex-cli", output_schema=schema)
            with patch("video_analysis_provider.subprocess.run", fake_run):
                with self.assertRaises(ProviderError) as caught:
                    provider.analyze("分类")
        self.assertEqual(caught.exception.reason_code, "provider_invalid_response")


class CommandProviderTests(unittest.TestCase):
    def test_command_provider_uses_json_stdin_stdout_without_shell(self):
        captured = {}

        def fake_run(command, *, input, capture_output, text, timeout, cwd):
            captured.update(command=command, input=input, cwd=cwd)
            return SimpleNamespace(returncode=0, stdout='{"target_board":"滑雪"}\n', stderr="")

        provider = build_analysis_provider(
            "command",
            command=["other-agent", "analyze"],
            model="remote-vlm",
        )
        with patch("video_analysis_provider.subprocess.run", fake_run):
            result = provider.analyze("观察真实画面")

        request = json.loads(captured["input"])
        self.assertEqual(captured["command"], ["other-agent", "analyze"])
        self.assertEqual(
            request,
            {"protocol_version": 1, "prompt": "观察真实画面", "image_paths": []},
        )
        self.assertEqual(result, {"target_board": "滑雪"})
        self.assertEqual(provider.identity()["provider"], "command")

    def test_command_nonzero_exit_preserves_diagnostics_in_structured_error(self):
        provider = build_analysis_provider("command", command=["broken-agent"])
        completed = SimpleNamespace(returncode=7, stdout="partial", stderr="boom")
        with patch("video_analysis_provider.subprocess.run", return_value=completed):
            with self.assertRaises(ProviderError) as caught:
                provider.analyze("分类")
        self.assertEqual(caught.exception.reason_code, "provider_process_failed")
        self.assertEqual(caught.exception.metadata["returncode"], 7)
        self.assertEqual(caught.exception.metadata["stdout"], "partial")
        self.assertEqual(caught.exception.metadata["stderr"], "boom")

    def test_command_identity_changes_with_argv_without_exposing_argv(self):
        first = build_analysis_provider("command", command=["agent", "--mode", "one"])
        second = build_analysis_provider("command", command=["agent", "--mode", "two"])
        self.assertNotEqual(first.identity()["version"], second.identity()["version"])
        self.assertNotIn("one", first.identity()["version"])
        self.assertNotIn("two", second.identity()["version"])


class MimoWorkerProviderTests(unittest.TestCase):
    def test_mimo_worker_constrains_target_board_to_exact_taxonomy(self):
        ready = {
            "type": "ready",
            "ok": True,
            "identity": {
                "provider": "mimo-vl-mlx",
                "model": "/models/mimo",
                "version": "mlx-vlm-0.5.0-max-pixels-401408-output-schema-v5",
                "schema_sha256": effective_schema_sha256(["运动训练与体态", "体态纠正与康复"]),
            },
        }
        process = FakeWorkerProcess([ready, {"ok": True, "closed": True}])
        boards = ["运动训练与体态", "体态纠正与康复"]

        with patch("video_analysis_provider.subprocess.Popen", return_value=process) as popen:
            with patch("video_analysis_provider.select.select", side_effect=lambda read, *_: (read, [], [])):
                provider = build_analysis_provider(
                    "mimo-vl-mlx",
                    model="/models/mimo",
                    python_bin="/runtime/python",
                    worker_script="/worker/mimo_vl_worker.py",
                    allowed_boards=boards,
                )
                command = popen.call_args.args[0]
                schema_path = Path(command[command.index("--output-schema") + 1])
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                self.assertEqual(schema["x-guidance"], {"whitespace_flexible": False})
                self.assertEqual(
                    schema["anyOf"][0]["properties"]["confidence"]["const"],
                    "low",
                )
                self.assertEqual(schema["anyOf"][1]["properties"]["target_board"]["const"], boards[0])
                self.assertEqual(schema["properties"]["target_board"]["enum"], ["", *boards])
                self.assertEqual(schema["properties"]["main_topic"]["maxLength"], 48)
                self.assertEqual(schema["properties"]["main_topic"]["minLength"], 1)
                self.assertEqual(
                    schema["properties"]["main_topic"]["pattern"],
                    "^[\\u3400-\\u4DBF\\u4E00-\\u9FFF]",
                )
                self.assertNotIn("enum", schema["properties"]["main_topic"])
                self.assertEqual(schema["properties"]["content_summary"]["maxLength"], 180)
                self.assertEqual(schema["properties"]["content_summary"]["minLength"], 1)
                self.assertEqual(
                    schema["properties"]["content_summary"]["pattern"],
                    "^[\\u3400-\\u4DBF\\u4E00-\\u9FFF]",
                )
                self.assertEqual(schema["properties"]["reason"]["items"]["maxLength"], 100)
                self.assertEqual(schema["properties"]["reason"]["items"]["minLength"], 1)
                self.assertEqual(
                    schema["properties"]["reason"]["items"]["pattern"],
                    "^[\\u3400-\\u4DBF\\u4E00-\\u9FFF]",
                )
                self.assertEqual(len(schema["anyOf"]), len(boards) + 1)
                self.assertNotIn("main_topic", schema["anyOf"][0]["properties"])
                self.assertEqual(provider.identity()["schema_sha256"], effective_schema_sha256(boards))
                self.assertTrue(schema_path.is_file())
                provider.close()

        self.assertFalse(schema_path.exists())

    def test_mimo_worker_is_persistent_and_model_is_loaded_once(self):
        ready = {
            "type": "ready",
            "ok": True,
            "identity": {
                "provider": "mimo-vl-mlx",
                "model": "/models/mimo",
                "version": "mlx-vlm-0.5.0-max-pixels-401408-output-schema-v5",
                "schema_sha256": effective_schema_sha256(),
            },
        }
        process = FakeWorkerProcess([
            ready,
            {"ok": True, "result": {"main_topic": "滑雪"}},
            {"ok": True, "result": {"main_topic": "收纳"}},
        ])

        with patch("video_analysis_provider.subprocess.Popen", return_value=process) as popen:
            with patch("video_analysis_provider.select.select", side_effect=lambda read, *_: (read, [], [])):
                provider = build_analysis_provider(
                    "mimo-vl-mlx",
                    model="/models/mimo",
                    python_bin="/runtime/python",
                    worker_script="/worker/mimo_vl_worker.py",
                )
                first = provider.analyze("分析第一条")
                second = provider.analyze("分析第二条")
                provider.close()

        self.assertEqual(first, {"main_topic": "滑雪"})
        self.assertEqual(second, {"main_topic": "收纳"})
        popen.assert_called_once()
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], ["/runtime/python", "-u", "/worker/mimo_vl_worker.py"])
        self.assertEqual(command[command.index("--max-image-pixels") + 1], "401408")
        self.assertIn("--output-schema", command)
        schema_index = command.index("--output-schema") + 1
        self.assertEqual(Path(command[schema_index]), ROOT / "schemas" / "video_analysis.schema.json")
        sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual([row["action"] for row in sent], ["analyze", "analyze", "close"])

    def test_mimo_worker_failure_keeps_reason_code_and_metadata(self):
        process = FakeWorkerProcess([
            {
                "type": "ready",
                "ok": True,
                "identity": {
                    "provider": "mimo-vl-mlx",
                    "model": "/models/mimo",
                    "version": "mlx-vlm-0.5.0-max-pixels-401408-output-schema-v5",
                    "schema_sha256": effective_schema_sha256(),
                },
            },
            {
                "ok": False,
                "reason_code": "mimo_vl_invalid_json",
                "message": "model output is not strict JSON",
                "metadata": {"output_length": 12},
            },
        ])
        with patch("video_analysis_provider.subprocess.Popen", return_value=process):
            with patch("video_analysis_provider.select.select", side_effect=lambda read, *_: (read, [], [])):
                provider = build_analysis_provider(
                    "mimo-vl-mlx",
                    model="/models/mimo",
                    python_bin="/runtime/python",
                )
                with self.assertRaises(ProviderError) as caught:
                    provider.analyze("分类")
                provider.close()
        self.assertEqual(caught.exception.reason_code, "mimo_vl_invalid_json")
        self.assertEqual(caught.exception.metadata, {"output_length": 12})


class MimoOutputParsingTests(unittest.TestCase):
    def test_no_think_is_always_the_last_user_text(self):
        first = ensure_no_think("请分类")
        second = ensure_no_think("请分类\n/no_think\n")
        for value in (first, second):
            self.assertTrue(value.endswith("/no_think"))
            self.assertIn("第一个字符必须是 {", value)
            self.assertIn("禁止输出 Markdown 代码块", value)
            self.assertEqual(value.count("/no_think"), 1)

    def test_parser_accepts_only_strict_json(self):
        self.assertEqual(parse_model_output('{"main_topic":"滑雪"}'), {"main_topic": "滑雪"})

    def test_parser_rejects_markdown_or_prose_instead_of_extracting_json(self):
        for output in (
            '```json\n{"main_topic":"滑雪"}\n```',
            '结果如下：{"main_topic":"滑雪"}',
            '<think>\n本应禁用思考\n</think>\n{"main_topic":"滑雪"}',
            '<think>\n</think>\n```json\n{"main_topic":"滑雪"}\n```',
            '<think></think>\n结果：{"main_topic":"滑雪"}',
            '<think></think>\n说明\n```json\n{"main_topic":"滑雪"}\n```',
            '[{"main_topic":"滑雪"}]',
        ):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    parse_model_output(output)

    def test_worker_uses_official_sampling_chat_template_and_multiple_images(self):
        calls = {"load": 0}
        fake_package = ModuleType("mlx_vlm")
        fake_prompt_utils = ModuleType("mlx_vlm.prompt_utils")
        fake_structured = ModuleType("mlx_vlm.structured")
        model = SimpleNamespace(config={"model_type": "qwen2_5_vl"})
        processor = SimpleNamespace(
            tokenizer=object(),
            image_processor=SimpleNamespace(max_pixels=12_845_056),
        )
        structured_clone = object()

        class FakeStructuredProcessor:
            def clone(self):
                calls["structured_clone_count"] = calls.get("structured_clone_count", 0) + 1
                return structured_clone

        structured_processor = FakeStructuredProcessor()

        def fake_load(model_path):
            calls["load"] += 1
            calls["model_path"] = model_path
            return model, processor

        def fake_template(actual_processor, config, prompt, *, num_images):
            calls["template"] = {
                "processor": actual_processor,
                "config": config,
                "prompt": prompt,
                "num_images": num_images,
            }
            return "FORMATTED"

        def fake_generate(actual_model, actual_processor, formatted, **kwargs):
            calls["generate_count"] = calls.get("generate_count", 0) + 1
            calls["generate"] = {
                "model": actual_model,
                "processor": actual_processor,
                "formatted": formatted,
                **kwargs,
            }
            if calls["generate_count"] == 1:
                return SimpleNamespace(text='{"main_topic":"滑雪"}', generation_tokens=24)
            return SimpleNamespace(text='{"main_topic":"未闭合"', generation_tokens=512)

        fake_package.load = fake_load
        fake_package.generate = fake_generate
        fake_prompt_utils.apply_chat_template = fake_template

        def fake_build_json_schema_logits_processor(actual_tokenizer, schema):
            calls["structured"] = {"tokenizer": actual_tokenizer, "schema": schema}
            return structured_processor

        fake_structured.build_json_schema_logits_processor = fake_build_json_schema_logits_processor

        with tempfile.TemporaryDirectory() as temp_dir:
            images = [Path(temp_dir) / "one.jpg", Path(temp_dir) / "two.jpg"]
            for image in images:
                image.write_bytes(b"image")
            output_schema = Path(temp_dir) / "schema.json"
            output_schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
            requests = "".join([
                json.dumps({
                    "action": "analyze",
                    "prompt": "只按真实画面分类",
                    "image_paths": [str(path) for path in images],
                }, ensure_ascii=False) + "\n",
                json.dumps({
                    "action": "analyze",
                    "prompt": "验证截断判定",
                    "image_paths": [str(path) for path in images],
                }, ensure_ascii=False) + "\n",
                json.dumps({"action": "close"}) + "\n",
            ])
            protocol = io.StringIO()
            with patch.dict(sys.modules, {
                "mlx_vlm": fake_package,
                "mlx_vlm.prompt_utils": fake_prompt_utils,
                "mlx_vlm.structured": fake_structured,
            }):
                with patch("mimo_vl_worker.importlib.metadata.version", return_value="0.5.0"):
                    with patch("mimo_vl_worker.sys.stdin", io.StringIO(requests)):
                        exit_code = run_worker(
                            "XiaomiMiMo/MiMo-VL-7B-RL-2508",
                            512,
                            output_schema=output_schema,
                            protocol=protocol,
                        )

        responses = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertEqual(calls["load"], 1)
        self.assertEqual(calls["template"]["num_images"], 2)
        self.assertTrue(calls["template"]["prompt"].endswith("/no_think"))
        self.assertEqual(calls["generate"]["image"], [str(path) for path in images])
        self.assertEqual(calls["generate"]["temperature"], 0.0)
        self.assertEqual(calls["generate"]["top_p"], 1.0)
        self.assertEqual(calls["generate"]["max_tokens"], 512)
        self.assertEqual(processor.image_processor.max_pixels, 401_408)
        self.assertEqual(calls["generate"]["logits_processors"], [structured_clone])
        self.assertEqual(calls["structured_clone_count"], 2)
        self.assertIs(calls["structured"]["tokenizer"], processor.tokenizer)
        self.assertEqual(calls["structured"]["schema"], {"type": "object"})
        self.assertEqual(responses[1], {"ok": True, "result": {"main_topic": "滑雪"}})
        self.assertEqual(responses[2]["reason_code"], "mimo_vl_output_truncated")
        self.assertEqual(responses[2]["metadata"]["generation_tokens"], 512)


class FactoryValidationTests(unittest.TestCase):
    def test_unknown_provider_and_missing_required_config_fail_early(self):
        with self.assertRaises(ProviderError) as unknown:
            build_analysis_provider("magic")
        self.assertEqual(unknown.exception.reason_code, "provider_unknown")

        with self.assertRaises(ProviderError) as no_command:
            build_analysis_provider("command")
        self.assertEqual(no_command.exception.reason_code, "provider_config_invalid")

        with self.assertRaises(ProviderError) as no_python:
            build_analysis_provider("mimo-vl-mlx", model="model")
        self.assertEqual(no_python.exception.reason_code, "provider_config_invalid")


if __name__ == "__main__":
    unittest.main()
