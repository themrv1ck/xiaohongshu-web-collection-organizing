import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_video_visuals import visual_evidence_sha256  # noqa: E402
from apply_reviewed_assignments import (  # noqa: E402
    ReviewContractError,
    apply_reviewed_assignments,
)


class ApplyReviewedAssignmentsTests(unittest.TestCase):
    def manifest(self):
        timestamps = [0.0, 10.0, 20.0, 30.0, 40.0]
        return {
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

    def fixtures(self):
        existing_id = "existing"
        image_id = "image-reviewed"
        video_id = "video-reviewed"
        raw = [
            {
                "id": existing_id,
                "content_type": "image",
                "excluded": True,
                "exclude_reason": "existing_board_member_protected",
                "source_board": "专辑甲",
                "target_board": "",
                "confidence": "high",
            },
            {
                "id": image_id,
                "content_type": "image",
                "classification_basis": "metadata_and_ocr",
                "ocr_status": "ok",
                "ocr_image_set_complete": True,
                "target_board": "",
                "confidence": "low",
                "review_state": "ocr_reviewed",
            },
            {
                "id": video_id,
                "content_type": "video",
                "classification_basis": "video_content",
                "video_analysis_status": "success",
                "visual_status": "analyzed",
                "target_board": "",
                "confidence": "low",
                "review_state": "video_content_needs_review",
            },
        ]
        review = {
            "contract": "xhs-deep-classification-independent-review-v1",
            "scope_count": 2,
            "policy": {
                "existing_board_members_are_excluded": True,
                "new_boards_allowed": False,
            },
            "items": [
                {
                    "id": video_id,
                    "content_type": "video",
                    "target_board": "专辑乙",
                    "confidence": "high",
                },
                {
                    "id": image_id,
                    "content_type": "image",
                    "target_board": "专辑甲",
                    "confidence": "medium",
                },
            ],
        }
        inventory = {
            "boards": ["专辑甲", "专辑乙"],
            "note_to_board": {existing_id: "专辑甲"},
            "excluded_note_ids": [existing_id],
        }
        scope = {"note_ids": [existing_id, image_id, video_id]}
        manifest = self.manifest()
        video = [{
            "id": video_id,
            "status": "success",
            "visual_status": "analyzed",
            "analysis_basis": "full_timeline_visual",
            "main_topic": "训练动作",
            "content_summary": "展示完整训练动作",
            "target_board": "",
            "confidence": "low",
            "reason": ["画面展示训练动作"],
            "evidence_manifest": manifest,
            "visual_evidence_sha256": visual_evidence_sha256(manifest),
        }]
        ocr = [{
            "id": image_id,
            "status": "ok",
            "image_set_complete": True,
            "ocr_run_fingerprint": "fingerprint",
        }]
        return raw, review, inventory, scope, video, ocr

    def test_applies_by_id_and_preserves_existing_row(self):
        raw, review, inventory, scope, video, ocr = self.fixtures()
        existing_before = copy.deepcopy(raw[0])
        result, audit = apply_reviewed_assignments(raw, review, inventory, scope, video, ocr)

        self.assertEqual([row["id"] for row in result], scope["note_ids"])
        self.assertEqual(result[0], existing_before)
        self.assertEqual(result[1]["target_board"], "专辑甲")
        self.assertEqual(result[1]["confidence"], "medium")
        self.assertEqual(result[2]["target_board"], "专辑乙")
        self.assertEqual(result[2]["review_state"], "video_content_classified")
        self.assertEqual(audit["assertions"]["reviewed_rows"], 2)
        self.assertEqual(audit["counts"]["content_type"], {"image": 1, "video": 1})

    def test_rejects_review_that_is_not_exact_complement(self):
        raw, review, inventory, scope, video, ocr = self.fixtures()
        review["items"] = review["items"][:1]
        review["scope_count"] = 1
        with self.assertRaisesRegex(ReviewContractError, "scope 减现有专辑成员"):
            apply_reviewed_assignments(raw, review, inventory, scope, video, ocr)


if __name__ == "__main__":
    unittest.main()
