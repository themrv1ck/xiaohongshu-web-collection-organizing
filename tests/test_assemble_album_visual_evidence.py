#!/usr/bin/env python3

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from assemble_album_visual_evidence import assemble_bundle, stable_sha256  # noqa: E402


VIDEO_HASH = "d" * 64
NORMAL_ID = "normal-video"
SCREEN_ID = "screen-video"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def manifest(note_id: str) -> dict:
    return {
        "id": note_id,
        "evidence_manifest": {
            "schema_version": 1,
            "video_sha256": VIDEO_HASH,
            "duration_sec": 2.0,
            "sampling": {
                "method": "uniform_full_timeline_endpoints_v1",
                "requested_max_gap_sec": 2.0,
                "observed_max_gap_sec": 2.0,
                "includes_start": True,
                "includes_end": True,
                "timestamps_sec": [0.0, 2.0],
            },
            "frames": [
                {
                    "index": 0,
                    "timestamp_sec": 0.0,
                    "endpoint": "start",
                    "filename": "frame_0000.jpg",
                    "sha256": "a" * 64,
                    "ocr_status": "ok",
                    "ocr_text": "开始画面原文",
                    "ocr_lines": ["开始画面原文"],
                    "ocr_confidence": 0.9,
                    "ocr_provider": "macos_vision",
                    "ocr_error": "",
                },
                {
                    "index": 1,
                    "timestamp_sec": 2.0,
                    "endpoint": "end",
                    "filename": "frame_0001.jpg",
                    "sha256": "b" * 64,
                    "ocr_status": "ok",
                    "ocr_text": "结束画面原文",
                    "ocr_lines": ["结束画面原文"],
                    "ocr_confidence": 0.8,
                    "ocr_provider": "macos_vision",
                    "ocr_error": "",
                },
            ],
        },
    }


def transcript(note_id: str) -> dict:
    segments = [{"start": 0.0, "end": 2.0, "text": "MiMo 听觉原文"}]
    return {
        "id": note_id,
        "status": "success",
        "source_kind": "mimo_audio",
        "segments": segments,
        "transcript_sha256": stable_sha256(segments),
        "coverage": {"transcript_quality_passed": True},
    }


def timeline() -> dict:
    frames = [
        {
            "index": 0,
            "timestamp_sec": 0.0,
            "sha256": "a" * 64,
            "observation": "开始画面。",
            "visible_text": ["开始画面原文"],
            "actions": [],
            "uncertainty": "",
        },
        {
            "index": 1,
            "timestamp_sec": 2.0,
            "sha256": "b" * 64,
            "observation": "结束画面。",
            "visible_text": ["结束画面原文"],
            "actions": [],
            "uncertainty": "",
        },
    ]
    return {
        "contract": "xiaohongshu.mimo_vl_timeline.v1",
        "provider": {"provider": "mimo-vl-mlx", "model": "test", "version": "mlx-vlm-test"},
        "video_sha256": VIDEO_HASH,
        "sampling": {
            "method": "uniform_full_timeline_endpoints_v1",
            "requested_max_gap_sec": 2.0,
            "observed_max_gap_sec": 2.0,
            "includes_start": True,
            "includes_end": True,
            "timestamps_sec": [0.0, 2.0],
        },
        "frames": frames,
        "batch_summaries": [{"batch_index": 1, "summary": "完整两帧。"}],
        "overall_visual_summary": "完整时轴已分析。",
        "visual_caveats": [],
    }


def prebuilt_screen_evidence(transcript_row: dict) -> dict:
    timestamps = [0.0, 1.0, 2.0]
    hashes = ["1" * 64, "2" * 64, "3" * 64]
    frames = [
        {
            "index": index,
            "timestamp_seconds": timestamp,
            "endpoint": "start" if index == 0 else "end" if index == 2 else "",
            "sha256": hashes[index],
            "ocr_text": f"屏幕原文{index}",
            "ocr_provider": "macos_vision",
        }
        for index, timestamp in enumerate(timestamps)
    ]
    analyzed = [
        {
            "index": index,
            "timestamp_seconds": timestamp,
            "sha256": hashes[index],
            "observation": f"画面{index}",
            "visible_text": [f"屏幕原文{index}"],
            "actions": [],
            "uncertainty": "",
        }
        for index, timestamp in enumerate(timestamps)
    ]
    evidence = {
        "evidence_version": "watchbrief_v5.visual_evidence.v1",
        "prompt_version": "watchbrief_v5.mimo_visual_prompt.v1",
        "provider": {"provider": "mimo-vl-mlx", "model": "test", "version": "mlx-vlm-test"},
        "video_sha256": VIDEO_HASH,
        "duration_seconds": 2.0,
        "sampling": {
            "includes_start": True,
            "includes_end": True,
            "timestamps_seconds": timestamps,
        },
        "frames": frames,
        "analysis": {"frames": analyzed},
        "screen_text_timeline": {
            "provider": "macos_vision",
            "verbatim_visible_text": True,
            "text_detected": True,
            "segments": [{
                "start": 0.0,
                "end": 2.0,
                "text": "屏幕原文",
                "sample_frame_sha256": hashes[0],
            }],
        },
        "transcript_sha256": transcript_row["transcript_sha256"],
        "audio_evidence": {
            "provider": "mimo_audio",
            "transcript_sha256": transcript_row["transcript_sha256"],
            "segments": copy.deepcopy(transcript_row["segments"]),
        },
    }
    evidence["visual_evidence_hash"] = stable_sha256(evidence)
    return evidence


class AssembleAlbumVisualEvidenceTests(unittest.TestCase):
    def fixtures(self, directory: Path):
        analyses = [manifest(NORMAL_ID), manifest(SCREEN_ID)]
        transcripts = [transcript(NORMAL_ID), transcript(SCREEN_ID)]
        write_json(directory / NORMAL_ID / "mimo_vl_timeline.json", timeline())
        prebuilt = {
            "contract": "xiaohongshu.album.visual_evidence.v1",
            "items": {SCREEN_ID: prebuilt_screen_evidence(transcripts[1])},
        }
        return analyses, transcripts, prebuilt

    def test_assembles_audio_and_screen_tracks_without_reducing_dense_prebuilt_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            analyses, transcripts, prebuilt = self.fixtures(directory)
            bundle = assemble_bundle(analyses, transcripts, directory, prebuilt, {SCREEN_ID})

            normal = bundle["items"][NORMAL_ID]
            self.assertEqual(normal["report_text_track"], "mimo_audio")
            self.assertEqual(normal["frames"][0]["ocr_text"], "开始画面原文")
            self.assertEqual(normal["analysis"]["frames"][1]["sha256"], "b" * 64)
            self.assertEqual(
                normal["screen_text_timeline"]["segments"][0]["sample_frame_sha256"],
                "a" * 64,
            )
            normal_material = copy.deepcopy(normal)
            normal_hash = normal_material.pop("visual_evidence_hash")
            self.assertEqual(normal_hash, stable_sha256(normal_material))

            screen = bundle["items"][SCREEN_ID]
            self.assertEqual(screen["report_text_track"], "screen_text")
            self.assertEqual(len(screen["frames"]), 3)
            self.assertEqual(len(analyses[1]["evidence_manifest"]["frames"]), 2)
            screen_material = copy.deepcopy(screen)
            old_hash = prebuilt["items"][SCREEN_ID]["visual_evidence_hash"]
            new_hash = screen_material.pop("visual_evidence_hash")
            self.assertNotEqual(new_hash, old_hash)
            self.assertEqual(new_hash, stable_sha256(screen_material))

    def test_prebuilt_bundle_is_optional_for_a_normal_timeline_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            analyses, transcripts, _prebuilt = self.fixtures(directory)

            bundle = assemble_bundle(
                analyses[:1],
                transcripts[:1],
                directory,
                None,
                set(),
            )

            self.assertEqual(list(bundle["items"]), [NORMAL_ID])
            self.assertEqual(bundle["items"][NORMAL_ID]["report_text_track"], "mimo_audio")

    def test_successful_ocr_with_no_visible_text_is_valid_empty_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            analyses, transcripts, _prebuilt = self.fixtures(directory)
            for frame in analyses[0]["evidence_manifest"]["frames"]:
                frame["ocr_text"] = ""
                frame["ocr_lines"] = []

            bundle = assemble_bundle(
                analyses[:1],
                transcripts[:1],
                directory,
                None,
                set(),
            )

            track = bundle["items"][NORMAL_ID]["screen_text_timeline"]
            self.assertFalse(track["text_detected"])
            self.assertEqual(track["segments"], [])

    def test_rejects_any_timeline_frame_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            analyses, transcripts, prebuilt = self.fixtures(directory)
            mismatched = timeline()
            mismatched["frames"][1]["sha256"] = "f" * 64
            write_json(directory / NORMAL_ID / "mimo_vl_timeline.json", mismatched)

            with self.assertRaisesRegex(ValueError, "index/timestamp/hash"):
                assemble_bundle(analyses, transcripts, directory, prebuilt, {SCREEN_ID})

    def test_rejects_corrupt_prebuilt_hash_before_adding_report_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            analyses, transcripts, prebuilt = self.fixtures(directory)
            prebuilt["items"][SCREEN_ID]["visual_evidence_hash"] = "f" * 64

            with self.assertRaisesRegex(ValueError, "既有视觉证据 hash 不一致"):
                assemble_bundle(analyses, transcripts, directory, prebuilt, {SCREEN_ID})


if __name__ == "__main__":
    unittest.main()
