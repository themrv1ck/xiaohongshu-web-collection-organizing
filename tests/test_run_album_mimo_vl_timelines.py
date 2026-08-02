#!/usr/bin/env python3

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_album_mimo_vl_timelines import (  # noqa: E402
    bind_model_batch,
    load_json,
    resolve_frame_paths,
    run_note,
)


NOTE_ID = "video-1"
VIDEO_HASH = "d" * 64


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeProvider:
    def __init__(self, *, fail_on_call=None, provide_coordinate=False):
        self.calls = []
        self.fail_on_call = fail_on_call
        self.provide_coordinate = provide_coordinate

    def identity(self):
        return {"provider": "mimo-vl-mlx", "model": "test", "version": "mlx-vlm-0.5.0"}

    def analyze(self, prompt, image_paths):
        self.calls.append({"prompt": prompt, "image_paths": [str(path) for path in image_paths]})
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("inference failed")
        items = []
        for slot, _path in enumerate(image_paths):
            item = {
                "slot": slot,
                "observation": f"直接可见画面 {len(self.calls)}-{slot}",
                "actions": [],
                "uncertainty": "",
            }
            if self.provide_coordinate:
                item["sha256"] = "f" * 64
            items.append(item)
        return {
            "batch_summary": f"批次 {len(self.calls)}",
            "items": items,
        }


def make_manifest(cache_root: Path) -> dict:
    frames_dir = cache_root / NOTE_ID / "frames"
    frames_dir.mkdir(parents=True)
    frames = []
    for index in range(3):
        data = f"frame-{index}".encode()
        filename = f"frame_{index:04d}.jpg"
        (frames_dir / filename).write_bytes(data)
        frames.append({
            "index": index,
            "timestamp_sec": float(index),
            "endpoint": "start" if index == 0 else "end" if index == 2 else "",
            "filename": filename,
            "sha256": sha256(data),
            "ocr_status": "ok",
            "ocr_text": f"OCR {index}",
            "ocr_confidence": 0.9,
            "ocr_provider": "macos_vision",
            "ocr_error": "",
        })
    return {
        "schema_version": 1,
        "video_sha256": VIDEO_HASH,
        "duration_sec": 2.0,
        "sampling": {
            "method": "uniform_full_timeline_endpoints_v1",
            "requested_max_gap_sec": 1.0,
            "observed_max_gap_sec": 1.0,
            "includes_start": True,
            "includes_end": True,
            "timestamps_sec": [0.0, 1.0, 2.0],
        },
        "frames": frames,
    }


class AlbumMimoVlTimelineRunnerTests(unittest.TestCase):
    def test_host_derives_batch_caveats_from_bound_frame_uncertainty(self):
        trusted = [{"index": 0, "timestamp_sec": 1.0, "sha256": "a" * 64}]
        payload = {
            "batch_summary": "展示一个动作",
            "items": [{
                "slot": 0,
                "observation": "人物正在做动作",
                "actions": ["抬手"],
                "uncertainty": "无法确认动作次数",
            }],
        }

        bound = bind_model_batch(payload, trusted)

        self.assertEqual(bound["visual_caveats"], ["无法确认动作次数"])
        self.assertEqual(bound["frames"][0]["sha256"], "a" * 64)
        with self.assertRaisesRegex(ValueError, "批次字段与严格合同"):
            bind_model_batch({**payload, "caveats": []}, trusted)
        duplicated = json.loads(json.dumps(payload))
        duplicated["items"][0]["actions"] = ["同一动作", "同一动作"]
        with self.assertRaisesRegex(ValueError, "不得包含重复字符串"):
            bind_model_batch(duplicated, trusted)

    def test_failure_keeps_atomic_prefix_and_next_run_resumes_with_single_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            output = root / "output"
            manifest = make_manifest(cache)
            paths = resolve_frame_paths(NOTE_ID, manifest, cache)
            first = FakeProvider(fail_on_call=2)

            with self.assertRaisesRegex(RuntimeError, "inference failed"):
                run_note(NOTE_ID, manifest, paths, output, first, 1)

            state = load_json(output / NOTE_ID / "batches.json")
            self.assertEqual(state["completed_frames"], 1)
            self.assertEqual(len(state["batches"]), 1)
            self.assertFalse((output / NOTE_ID / "mimo_vl_timeline.json").exists())

            second = FakeProvider()
            result = run_note(NOTE_ID, manifest, paths, output, second, 1)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(second.calls), 2)
            self.assertIn('"index":1', second.calls[0]["prompt"])
            self.assertIn('"timestamp_sec":1.0', second.calls[0]["prompt"])
            self.assertIn(manifest["frames"][1]["sha256"], second.calls[0]["prompt"])
            self.assertIn("模型不得输出 index", second.calls[0]["prompt"])

            timeline = load_json(output / NOTE_ID / "mimo_vl_timeline.json")
            self.assertEqual(timeline["contract"], "xiaohongshu.mimo_vl_timeline.v1")
            self.assertEqual(len(timeline["frames"]), 3)
            self.assertEqual(
                [frame["sha256"] for frame in timeline["frames"]],
                [frame["sha256"] for frame in manifest["frames"]],
            )

    def test_model_supplied_coordinates_are_rejected_without_saving_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            output = root / "output"
            manifest = make_manifest(cache)
            paths = resolve_frame_paths(NOTE_ID, manifest, cache)
            provider = FakeProvider(provide_coordinate=True)

            with self.assertRaisesRegex(ValueError, "不得由模型提供坐标或 hash"):
                run_note(NOTE_ID, manifest, paths, output, provider, 1)
            self.assertFalse((output / NOTE_ID / "batches.json").exists())

    def test_cache_frame_hash_mismatch_stops_before_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            manifest = make_manifest(cache)
            frame_path = cache / NOTE_ID / "frames" / manifest["frames"][1]["filename"]
            frame_path.write_bytes(b"corrupt")

            with self.assertRaisesRegex(ValueError, "帧文件 hash"):
                resolve_frame_paths(NOTE_ID, manifest, cache)


if __name__ == "__main__":
    unittest.main()
