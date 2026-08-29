import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_video_visuals import canonical_sha256, visual_evidence_sha256  # noqa: E402
from audit_video_analysis import VideoAuditError, audit_video_analysis  # noqa: E402


class AuditVideoAnalysisTests(unittest.TestCase):
    def success_row(self):
        timestamps = [0.0, 10.0, 20.0, 30.0, 40.0]
        manifest = {
            "schema_version": 1,
            "video_sha256": "a" * 64,
            "duration_sec": 40.0,
            "sampling": {
                "method": "uniform_full_timeline_endpoints_v1",
                "requested_max_gap_sec": 10.0,
                "observed_max_gap_sec": 10.0,
                "includes_start": True,
                "includes_end": True,
                "timestamps_sec": timestamps,
            },
            "frames": [
                {
                    "index": index,
                    "timestamp_sec": timestamp,
                    "endpoint": "start" if index == 0 else "end" if index == 4 else "",
                    "filename": f"frame-{index}.jpg",
                    "sha256": f"{index + 1:064x}",
                    "ocr_status": "ok",
                    "ocr_text": "动作",
                    "ocr_confidence": 1.0,
                    "ocr_provider": "macos_vision",
                    "ocr_error": "",
                }
                for index, timestamp in enumerate(timestamps)
            ],
        }
        evidence_hash = visual_evidence_sha256(manifest)
        schema_hash = "1c3e55fea4c2baf16501ae4ee510c2e17b5e8ae123ad1a5a179f39ac8d4508e6"
        provider = {
            "provider": "mimo-vl-mlx",
            "model": "/model",
            "version": "mlx-vlm-output-schema-v5",
            "schema_sha256": schema_hash,
        }
        input_hash = canonical_sha256({
            "prompt_contract_version": 9,
            "visual_evidence_sha256": evidence_hash,
            "transcript_sha256": "b" * 64,
            "allowed_boards": ["运动"],
            "analysis_provider": provider,
        })
        return {
            "id": "video-1",
            "status": "success",
            "visual_status": "analyzed",
            "analysis_basis": "full_timeline_visual_with_transcript",
            "main_topic": "训练动作",
            "content_summary": "展示完整训练动作",
            "target_board": "运动",
            "confidence": "high",
            "reason": ["画面展示训练动作"],
            "evidence_manifest": manifest,
            "visual_evidence_sha256": evidence_hash,
            "transcript_sha256": "b" * 64,
            "analysis_provider": provider["provider"],
            "analysis_model": provider["model"],
            "analysis_provider_version": provider["version"],
            "analysis_schema_sha256": schema_hash,
            "analysis_input_sha256": input_hash,
        }

    def test_accepts_completed_prefix_and_pending_tail(self):
        success = self.success_row()
        pending = {
            "id": "video-2",
            "status": "failed",
            "visual_status": "not_enabled",
            "analysis_basis": "transcript_only",
        }
        items = [
            {"id": "image-1", "content_type": "image"},
            {"id": "video-1", "content_type": "video"},
            {"id": "video-2", "content_type": "video"},
        ]
        report = audit_video_analysis(items, [success, pending], {"boards": ["运动"]}, 1)
        self.assertTrue(report["passed"])
        self.assertEqual(report["success_count"], 1)
        self.assertEqual(report["pending_count"], 1)
        self.assertEqual(report["sampled_frame_count"], 5)

    def test_rejects_unprocessed_row_inside_completed_prefix(self):
        success = self.success_row()
        pending = {
            "id": "video-2",
            "status": "failed",
            "visual_status": "not_enabled",
            "analysis_basis": "transcript_only",
        }
        items = [
            {"id": "video-1", "content_type": "video"},
            {"id": "video-2", "content_type": "video"},
        ]
        with self.assertRaises(VideoAuditError):
            audit_video_analysis(items, [copy.deepcopy(success), pending], {"boards": ["运动"]}, 2)


if __name__ == "__main__":
    unittest.main()
