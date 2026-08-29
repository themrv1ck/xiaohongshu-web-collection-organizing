#!/usr/bin/env python3
import json
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analyze_video_visuals import main as analyze_video_visuals_main  # noqa: E402
from archive_exclusion import (  # noqa: E402
    ArchiveExclusionError,
    combine_archived_note_maps,
    load_archived_note_map,
)
from classify_items import main as classify_items_main  # noqa: E402
from enrich_note_images import main as enrich_note_images_main  # noqa: E402
from ocr_note_images import main as ocr_note_images_main  # noqa: E402
from transcribe_video_items import main as transcribe_video_items_main  # noqa: E402


class ArchiveExclusionTests(unittest.TestCase):
    @staticmethod
    def write_registry(path: Path, confirmed: list[tuple[str, str]], pending=None) -> None:
        pending = pending or []
        path.write_text(json.dumps({
            "contract": "xhs-archived-notes-registry-v1",
            "user_id": "556f1a8bc2bdeb527a6fa010",
            "confirmed_archived_count": len(confirmed),
            "confirmed_archived": [
                {"id": note_id, "board": board} for note_id, board in confirmed
            ],
            "pending_count": len(pending),
            "pending_not_archived": pending,
        }, ensure_ascii=False), encoding="utf-8")

    def test_registry_excludes_only_confirmed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            self.write_registry(
                registry,
                [("a" * 24, "运动训练与体态")],
                [{"id": "b" * 24, "target_board": "杂项灵感", "state": "pending_not_moved"}],
            )

            archived = load_archived_note_map(registry)

        self.assertEqual(archived, {"a" * 24: "运动训练与体态"})
        self.assertNotIn("b" * 24, archived)

    def test_conflicting_archived_boards_are_a_hard_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            self.write_registry(first, [("a" * 24, "专辑一")])
            self.write_registry(second, [("a" * 24, "专辑二")])

            with self.assertRaisesRegex(ArchiveExclusionError, "不同专辑"):
                combine_archived_note_maps([first, second])

    def test_registry_account_must_match_the_current_scope_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            self.write_registry(registry, [("a" * 24, "专辑一")])

            with self.assertRaisesRegex(ArchiveExclusionError, "账号"):
                load_archived_note_map(
                    registry,
                    expected_user_id="c" * 24,
                )

    def test_registry_builder_keeps_unmoved_review_rows_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            inventory = directory / "inventory.json"
            snapshot = directory / "snapshot.json"
            review = directory / "review.json"
            output = directory / "registry.json"
            archived_id = "a" * 24
            pending_id = "b" * 24
            inventory.write_text(json.dumps({
                "boards": ["专辑一"],
                "note_to_board": {archived_id: "专辑一"},
                "excluded_note_ids": [archived_id],
            }, ensure_ascii=False), encoding="utf-8")
            snapshot.write_text(json.dumps({
                "source": {
                    "user_id": "556f1a8bc2bdeb527a6fa010",
                    "live_account_user_id": "556f1a8bc2bdeb527a6fa010",
                    "expected_url_substring": "https://www.xiaohongshu.com/user/profile/556f1a8bc2bdeb527a6fa010?tab=fav",
                    "live_page_binding": "https://www.xiaohongshu.com/user/profile/556f1a8bc2bdeb527a6fa010?tab=fav",
                    "writes_performed": False,
                },
                "boards": [{
                    "id": "board-1",
                    "name": "专辑一",
                    "privacy": 0,
                    "note_ids": [archived_id],
                    "declared_total": 1,
                    "accessible_unique_count": 1,
                    "declared_vs_accessible_delta": 0,
                }],
                "validation": {
                    "full_membership_complete": True,
                    "board_names_unique": True,
                    "pagination_cursor_invariants_passed": True,
                    "duplicate_note_ids": [],
                    "multi_board_note_ids": [],
                    "within_board_duplicates": [],
                    "count_mismatch_boards": [],
                    "board_count": 1,
                    "accessible_note_occurrences": 1,
                    "accessible_unique_note_ids_across_boards": 1,
                },
            }, ensure_ascii=False), encoding="utf-8")
            review.write_text(json.dumps({
                "items": [{"id": pending_id, "target_board": "专辑一"}],
            }, ensure_ascii=False), encoding="utf-8")

            completed = subprocess.run([
                sys.executable,
                str(SCRIPTS / "build_archived_notes_registry.py"),
                str(inventory),
                str(snapshot),
                str(review),
                str(output),
                "--user-id",
                "556f1a8bc2bdeb527a6fa010",
            ], cwd=str(ROOT), text=True, capture_output=True, check=False)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["confirmed_archived_count"], 1)
        self.assertEqual(payload["confirmed_archived"][0]["id"], archived_id)
        self.assertEqual(
            payload["confirmed_archived"][0]["archive_lifecycle_state"],
            "first_archive_confirmed",
        )
        self.assertEqual(payload["pending_count"], 1)
        self.assertEqual(payload["pending_not_archived"][0], {
            "id": pending_id,
            "target_board": "专辑一",
            "state": "pending_not_moved",
            "archive_lifecycle_state": "first_archive_pending",
        })

    def test_detail_enrichment_never_fetches_an_archived_note(self):
        archived_id = "a" * 24
        new_id = "b" * 24
        html = (
            "<script>window.__SETUP_SERVER_STATE__="
            + json.dumps({"LAUNCHER_SSR_STORE_PAGE_DATA": {"noteData": {
                "noteId": new_id,
                "type": "normal",
                "imageList": [{"urlDefault": "https://ci.xiaohongshu.com/new.jpg"}],
            }}}, ensure_ascii=False)
            + ";</script>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            src = directory / "visible.json"
            out = directory / "image_items.json"
            registry = directory / "registry.json"
            src.write_text(json.dumps([
                {"id": archived_id, "content_type": "image", "title": "已归档"},
                {"id": new_id, "content_type": "image", "title": "新增"},
            ], ensure_ascii=False), encoding="utf-8")
            self.write_registry(registry, [
                (archived_id, "专辑一"),
                ("d" * 24, "专辑一"),
            ])
            argv = [
                "enrich_note_images.py", str(src), str(out),
                "--archive-registry", str(registry),
                "--allow-detail-requests", "--max-items", "1", "--request-interval", "0",
            ]
            with patch.object(sys, "argv", argv), patch(
                "enrich_note_images.fetch_note_html", return_value=html
            ) as fetch:
                enrich_note_images_main()
            rows = json.loads(out.read_text(encoding="utf-8"))

        fetch.assert_called_once()
        self.assertTrue(rows[0]["archive_excluded"])
        self.assertEqual(rows[0]["archive_source_board"], "专辑一")
        self.assertEqual(rows[1]["image_enrichment_status"], "ok")

    def test_ocr_receives_only_unarchived_images(self):
        archived_id = "a" * 24
        new_id = "b" * 24
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            src = directory / "images.json"
            out = directory / "ocr.json"
            registry = directory / "registry.json"
            src.write_text(json.dumps([
                {"id": archived_id, "content_type": "image"},
                {"id": new_id, "content_type": "image"},
                {"id": "c" * 24, "content_type": "video"},
            ]), encoding="utf-8")
            self.write_registry(registry, [(archived_id, "专辑一")])
            argv = [
                "ocr_note_images.py", str(src), str(out),
                "--archive-registry", str(registry),
            ]
            stdout = io.StringIO()
            with redirect_stdout(stdout), patch.object(sys, "argv", argv), patch(
                    "ocr_note_images.perform_ocr_for_items",
                    return_value=[{"id": new_id, "status": "ok"}],
                ) as perform:
                    ocr_note_images_main()
            summary = json.loads(stdout.getvalue())

        self.assertEqual(
            [row["id"] for row in perform.call_args.args[0]],
            [new_id],
        )
        self.assertEqual(summary["archived_excluded"], 1)
        self.assertEqual(summary["skipped_non_image_count"], 1)

    def test_classifier_requires_analysis_and_ocr_only_for_unarchived_notes(self):
        archived_id = "a" * 24
        video_id = "b" * 24
        image_id = "c" * 24
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            visible = directory / "visible.json"
            analysis = directory / "analysis.json"
            taxonomy = directory / "taxonomy.json"
            registry = directory / "registry.json"
            output = directory / "classification.json"
            visible.write_text(json.dumps([
                {"id": archived_id, "title": "已归档视频", "content_type": "video"},
                {"id": video_id, "title": "新增视频", "content_type": "video"},
                {"id": image_id, "title": "新增图文", "content_type": "image"},
            ], ensure_ascii=False), encoding="utf-8")
            analysis.write_text(json.dumps([{
                "id": video_id,
                "status": "success",
                "main_topic": "新增视频主题",
                "content_summary": "根据真实视频内容生成的摘要",
                "target_board": "专辑二",
                "confidence": "high",
                "reason": ["真实视频内容匹配"],
                "analysis_basis": "transcript_only",
                "visual_status": "not_enabled",
                "analysis_provider": "command",
                "analysis_model": "test",
                "analysis_provider_version": "v1",
            }], ensure_ascii=False), encoding="utf-8")
            taxonomy.write_text(json.dumps({"boards": ["专辑一", "专辑二"]}, ensure_ascii=False), encoding="utf-8")
            self.write_registry(registry, [
                (archived_id, "专辑一"),
                ("d" * 24, "专辑一"),
            ])
            ocr_entry = {
                "id": image_id,
                "status": "ok",
                "image_set_complete": True,
                "ocr_run_fingerprint": "fingerprint",
                "image_count_processed": 1,
                "ocr_text": "新增图文内容",
                "ocr_confidence": 0.9,
                "images": [],
            }
            argv = [
                "classify_items.py", str(visible), str(output),
                "--taxonomy", str(taxonomy),
                "--archive-registry", str(registry),
                "--classify-video-by-content", "--video-analysis", str(analysis),
            ]
            with patch.object(sys, "argv", argv), patch(
                "classify_items.perform_ocr_for_items", return_value=[ocr_entry]
            ) as perform:
                classify_items_main()
            rows = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual([row["id"] for row in perform.call_args.args[0]], [image_id])
        self.assertTrue(rows[0]["excluded"])
        self.assertEqual(rows[0]["classification_basis"], "archive_excluded")
        self.assertEqual(rows[0]["target_board"], "")
        self.assertEqual(rows[1]["target_board"], "专辑二")

    def test_video_stages_do_not_touch_browser_or_provider_when_all_videos_are_archived(self):
        archived_id = "a" * 24
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            visible = directory / "visible.json"
            registry = directory / "registry.json"
            transcripts = directory / "transcripts.json"
            analysis = directory / "analysis.json"
            transcript_out = directory / "transcript-out.json"
            visual_out = directory / "visual-out.json"
            visible.write_text(json.dumps([
                {"id": archived_id, "content_type": "video"},
            ]), encoding="utf-8")
            transcripts.write_text("[]", encoding="utf-8")
            analysis.write_text("[]", encoding="utf-8")
            self.write_registry(registry, [
                (archived_id, "专辑一"),
                ("d" * 24, "专辑一"),
            ])

            transcribe_argv = [
                "transcribe_video_items.py", str(visible), str(transcript_out),
                "--browser", "safari", "--allow-video-access", "--max-videos", "1",
                "--archive-registry", str(registry),
            ]
            transcribe_stdout = io.StringIO()
            with redirect_stdout(transcribe_stdout), patch.object(sys, "argv", transcribe_argv), patch(
                    "transcribe_video_items.video_content_environment"
                ) as transcribe_environment, patch(
                    "transcribe_video_items.load_video_transcript_module"
                ) as load_extractor:
                    self.assertEqual(transcribe_video_items_main(), 0)

            visual_argv = [
                "analyze_video_visuals.py",
                str(visible), str(transcripts), str(analysis), str(visual_out),
                "--all-videos", "--max-videos", "1",
                "--analysis-provider", "command", "--analysis-command", "/bin/false",
                "--allow-video-access", "--archive-registry", str(registry),
                "--arc-window-id", "window", "--arc-tab-id", "tab",
                "--arc-tab-marker", "marker", "--arc-expected-url-substring", "tab=fav",
            ]
            visual_stdout = io.StringIO()
            with redirect_stdout(visual_stdout), patch.object(sys, "argv", visual_argv), patch(
                    "analyze_video_visuals.video_content_environment"
                ) as visual_environment, patch(
                    "analyze_video_visuals.build_analysis_provider"
                ) as build_provider:
                    self.assertEqual(analyze_video_visuals_main(), 0)

            transcript_rows = json.loads(transcript_out.read_text(encoding="utf-8"))
            visual_rows = json.loads(visual_out.read_text(encoding="utf-8"))

        transcribe_environment.assert_not_called()
        load_extractor.assert_not_called()
        visual_environment.assert_not_called()
        build_provider.assert_not_called()
        self.assertEqual(transcript_rows, [])
        self.assertEqual(visual_rows, [])
        self.assertEqual(json.loads(transcribe_stdout.getvalue())["archived_excluded"], 1)
        self.assertEqual(json.loads(visual_stdout.getvalue())["archived_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
