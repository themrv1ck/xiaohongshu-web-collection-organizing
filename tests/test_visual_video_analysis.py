#!/usr/bin/env python3

import copy
import inspect
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analyze_video_visuals import (  # noqa: E402
    analysis_input_sha256,
    analyze_with_provider,
    build_visual_prompt,
    deterministic_sample_timestamps,
    export_arc_cookie_file,
    failure_row,
    merge_selected_analysis,
    observed_max_gap,
    qualified_transcript,
    resume_row_matches,
    select_explicit_videos,
    select_videos,
    validate_visual_evidence_manifest,
    visual_evidence_sha256,
)
from video_content_common import transcript_sha256  # noqa: E402


def evidence_manifest(frame_hashes=("a" * 64, "b" * 64)):
    timestamps = [0.0, 20.0]
    return {
        "schema_version": 1,
        "video_sha256": "d" * 64,
        "duration_sec": 20.0,
        "sampling": {
            "method": "uniform_full_timeline_endpoints_v1",
            "requested_max_gap_sec": 20.0,
            "observed_max_gap_sec": 20.0,
            "includes_start": True,
            "includes_end": True,
            "timestamps_sec": timestamps,
        },
        "frames": [
            {
                "index": index,
                "timestamp_sec": timestamp,
                "endpoint": "start" if index == 0 else "end",
                "filename": f"frame_{index:04d}.jpg",
                "sha256": frame_hashes[index],
                "ocr_status": "ok",
                "ocr_text": "真实画面文字" if index == 0 else "",
                "ocr_confidence": 0.91 if index == 0 else None,
                "ocr_provider": "macos_vision",
                "ocr_error": "",
            }
            for index, timestamp in enumerate(timestamps)
        ],
    }


class VisualVideoAnalysisTests(unittest.TestCase):
    def test_prompt_has_no_metadata_input_and_excludes_forbidden_metadata(self):
        metadata_secrets = {
            "title": "TITLE_SECRET",
            "desc": "DESC_SECRET",
            "user": "USER_SECRET",
            "tags": "TAGS_SECRET",
            "cover": "COVER_SECRET",
        }
        signature = inspect.signature(build_visual_prompt)
        self.assertEqual(list(signature.parameters), ["evidence_manifest", "transcript", "boards"])

        prompt = build_visual_prompt(evidence_manifest(), None, ["滑雪", "杂项灵感"])
        for secret in metadata_secrets.values():
            self.assertNotIn(secret, prompt)
        self.assertIn("真实画面文字", prompt)
        self.assertIn("严禁根据标题、简介、作者、标签、热度、封面", prompt)
        for field in ("main_topic", "content_summary", "target_board", "confidence", "reason"):
            self.assertIn(field, prompt)

    def test_full_timeline_sampling_includes_both_endpoints_and_respects_gap(self):
        timestamps = deterministic_sample_timestamps(25.0, 10.0)
        self.assertEqual(timestamps[0], 0.0)
        self.assertEqual(timestamps[-1], 25.0)
        self.assertEqual(len(timestamps), 5)
        self.assertLessEqual(observed_max_gap(timestamps), 10.0)
        self.assertEqual(timestamps, deterministic_sample_timestamps(25.0, 10.0))

        short_video = deterministic_sample_timestamps(4.0, 10.0)
        self.assertEqual(short_video, [0.0, 1.0, 2.0, 3.0, 4.0])
        self.assertGreaterEqual(len(short_video), 5)

    def test_resume_requires_current_visual_hash_and_full_analysis_input_hash(self):
        manifest = evidence_manifest()
        boards = ["滑雪", "杂项灵感"]
        evidence_hash = visual_evidence_sha256(manifest)
        input_hash = analysis_input_sha256(
            evidence_hash=evidence_hash,
            transcript_hash="transcript-hash",
            boards=boards,
            provider_identity={"provider": "mimo-vl-mlx", "model": "official-bf16", "version": "0.5.0"},
        )
        row = {
            "id": "v1",
            "status": "success",
            "main_topic": "单板滑雪",
            "content_summary": "画面展示单板滑行动作",
            "target_board": "滑雪",
            "confidence": "high",
            "reason": ["完整时轴画面持续展示滑雪"],
            "visual_evidence_sha256": evidence_hash,
            "analysis_input_sha256": input_hash,
            "evidence_manifest": manifest,
        }
        self.assertTrue(resume_row_matches(
            row,
            current_manifest=manifest,
            current_input_hash=input_hash,
            boards=boards,
        ))

        changed = copy.deepcopy(manifest)
        changed["frames"][1]["sha256"] = "c" * 64
        self.assertFalse(resume_row_matches(
            row,
            current_manifest=changed,
            current_input_hash=input_hash,
            boards=boards,
        ))
        self.assertFalse(resume_row_matches(
            row,
            current_manifest=manifest,
            current_input_hash="changed-input",
            boards=boards,
        ))

    def test_evidence_manifest_rejects_missing_tail_or_invalid_frame_hash(self):
        missing_tail = evidence_manifest()
        missing_tail["sampling"]["timestamps_sec"][-1] = 19.0
        missing_tail["frames"][-1]["timestamp_sec"] = 19.0
        with self.assertRaisesRegex(ValueError, "首尾"):
            validate_visual_evidence_manifest(missing_tail)

        invalid_hash = evidence_manifest()
        invalid_hash["frames"][0]["sha256"] = "not-a-sha256"
        with self.assertRaisesRegex(ValueError, "sha256"):
            validate_visual_evidence_manifest(invalid_hash)

    def test_failure_replaces_old_classification_without_metadata_fallback(self):
        base = [
            {
                "id": "v1",
                "status": "success",
                "target_board": "滑雪",
                "confidence": "high",
                "main_topic": "旧结果",
                "content_summary": "旧摘要",
                "reason": ["旧证据"],
            },
            {"id": "v2", "status": "success", "target_board": "杂项灵感"},
        ]
        failed = failure_row("v1", "video_download_failed", "download failed")
        merged = merge_selected_analysis(base, {"v1": failed}, {"v1"})
        self.assertEqual(merged[0]["target_board"], "")
        self.assertEqual(merged[0]["confidence"], "low")
        self.assertEqual(merged[0]["reason_code"], "video_download_failed")
        self.assertEqual(merged[0]["analysis_basis"], "full_timeline_visual")
        self.assertEqual(merged[0]["visual_status"], "failed")
        self.assertEqual(merged[1], base[1])

    def test_merge_preserves_full_order_and_all_unselected_rows(self):
        base = [
            {"id": "v1", "marker": "unchanged-1"},
            {"id": "v2", "marker": "old"},
            {"id": "v3", "marker": "unchanged-3"},
        ]
        replacement = {"id": "v2", "marker": "new"}
        merged = merge_selected_analysis(base, {"v2": replacement}, {"v2"})
        self.assertEqual([row["id"] for row in merged], ["v1", "v2", "v3"])
        self.assertEqual(merged[0], base[0])
        self.assertEqual(merged[1], replacement)
        self.assertEqual(merged[2], base[2])

    def test_selection_requires_explicit_confirmed_video_ids(self):
        items = [
            {"id": "v1", "content_type": "video", "title": "TITLE_SECRET"},
            {"id": "i1", "content_type": "image"},
        ]
        self.assertEqual([row["id"] for row in select_explicit_videos(items, ["v1"])], ["v1"])
        with self.assertRaisesRegex(ValueError, "至少传一个"):
            select_explicit_videos(items, [])
        with self.assertRaisesRegex(ValueError, "不是已确认视频"):
            select_explicit_videos(items, ["i1"])

    def test_all_videos_is_an_explicit_selection_of_every_confirmed_video(self):
        items = [
            {"id": "v1", "content_type": "video"},
            {"id": "i1", "content_type": "image"},
            {"id": "v2", "content_type": "video"},
        ]
        self.assertEqual(
            [row["id"] for row in select_videos(items, [], all_videos=True)],
            ["v1", "v2"],
        )
        with self.assertRaisesRegex(ValueError, "不能与"):
            select_videos(items, ["v1"], all_videos=True)

    def test_only_quality_gated_transcript_enters_visual_prompt(self):
        segments = [{"start": 0, "end": 5, "text": "合格转写"}]
        valid = {
            "status": "success",
            "source_kind": "mimo_audio",
            "segments": segments,
            "coverage": {"transcript_quality_passed": True},
            "transcript_sha256": transcript_sha256(segments),
        }
        self.assertEqual(qualified_transcript(valid)["segments"], segments)
        invalid = dict(valid, transcript_sha256="wrong")
        self.assertIsNone(qualified_transcript(invalid))
        self.assertIsNone(qualified_transcript({"status": "failed", "segments": segments}))

    def test_arc_cookie_file_is_mode_600(self):
        class FakeModule:
            @staticmethod
            def export_arc_cookies(path, profile):
                self.assertEqual(profile, "Default")
                path.write_text("cookie-secret", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = export_arc_cookie_file(FakeModule(), Path(temp_dir) / "cache", profile="Default")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_failed_arc_cookie_export_leaves_no_cookie_file(self):
        class FailingModule:
            @staticmethod
            def export_arc_cookies(path, profile):
                path.write_text("partial-cookie-secret", encoding="utf-8")
                raise RuntimeError("export failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = Path(temp_dir) / "cache"
            with self.assertRaisesRegex(RuntimeError, "export failed"):
                export_arc_cookie_file(FailingModule(), cache, profile="Default")
            self.assertFalse((cache / "arc-cookies.txt").exists())

    def test_provider_receives_every_real_frame(self):
        manifest = evidence_manifest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frames = [root / "frame0.jpg", root / "frame1.jpg"]
            for index, frame in enumerate(frames):
                frame.write_bytes(f"frame-{index}".encode())
            class FakeProvider:
                def __init__(self):
                    self.prompt = ""
                    self.image_paths = []

                def analyze(self, prompt, *, image_paths=()):
                    self.prompt = prompt
                    self.image_paths = list(image_paths)
                    return {
                    "main_topic": "家居收纳",
                    "content_summary": "画面展示柜体内部收纳布局",
                    "target_board": "家居装修与收纳",
                    "confidence": "high",
                    "reason": ["多帧持续展示柜体和收纳动作"],
                    }

            provider = FakeProvider()
            result = analyze_with_provider(
                manifest=manifest,
                frame_paths=frames,
                transcript=None,
                boards=["家居装修与收纳", "杂项灵感"],
                provider=provider,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(provider.image_paths, frames)
        self.assertIn("逐帧 Vision OCR", provider.prompt)


if __name__ == "__main__":
    unittest.main()
