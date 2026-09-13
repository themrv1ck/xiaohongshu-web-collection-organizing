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
    combine_live_protected_note_maps,
    protected_note_map_from_snapshot,
)
from classify_items import main as classify_items_main  # noqa: E402
from enrich_note_images import main as enrich_note_images_main  # noqa: E402
from ocr_note_images import main as ocr_note_images_main  # noqa: E402
from transcribe_video_items import main as transcribe_video_items_main  # noqa: E402


class ArchiveExclusionTests(unittest.TestCase):
    @staticmethod
    def write_registry(path: Path, confirmed: list[tuple[str, str]], pending=None) -> None:
        pending = pending or []
        grouped = {}
        for note_id, board in confirmed:
            grouped.setdefault(board, []).append(note_id)
        path.write_text(json.dumps({
            "contract": "xhs-skill-archive-registry-v2",
            "user_id": "f0f0f0f0f0f0f0f0f0f0f0f0",
            "archived_board_count": len(grouped),
            "archived_boards": [
                {
                    "id": f"{index + 1:024x}",
                    "name": board,
                    "note_ids": note_ids,
                }
                for index, (board, note_ids) in enumerate(grouped.items())
            ],
            "confirmed_archived_count": len(confirmed),
            "pending_count": len(pending),
            "pending_not_archived": pending,
        }, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def write_snapshot(path: Path, registry_path: Path) -> None:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        boards = [
            {
                "id": row["id"],
                "name": row["name"],
                "note_ids": list(row["note_ids"]),
                "declared_total": len(row["note_ids"]),
            }
            for row in registry["archived_boards"]
        ]
        path.write_text(json.dumps({
            "source": {
                "user_id": registry["user_id"],
                "live_account_user_id": registry["user_id"],
            },
            "boards": boards,
        }, ensure_ascii=False), encoding="utf-8")

    def test_registry_without_current_board_snapshot_is_a_hard_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            self.write_registry(registry, [("a" * 24, "Skill专辑")])
            with self.assertRaisesRegex(ArchiveExclusionError, "必须同时提供"):
                combine_live_protected_note_maps(
                    [registry],
                    board_snapshot_path=None,
                )

    def test_registry_builder_archives_only_skill_completed_target_album(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            report = directory / "run_report.json"
            snapshot = directory / "snapshot.json"
            output = directory / "registry.json"
            archived_id = "a" * 24
            manual_id = "b" * 24
            report.write_text(json.dumps({
                "mode": "execute",
                "errors": [],
                "processed": [{
                    "id": archived_id,
                    "target_board": "专辑一",
                    "status": "success",
                    "verified": True,
                    "archive_lifecycle_state": "first_archive_confirmed",
                }],
            }, ensure_ascii=False), encoding="utf-8")
            snapshot.write_text(json.dumps({
                "source": {
                    "user_id": "f0f0f0f0f0f0f0f0f0f0f0f0",
                    "live_account_user_id": "f0f0f0f0f0f0f0f0f0f0f0f0",
                    "expected_url_substring": "https://www.xiaohongshu.com/user/profile/f0f0f0f0f0f0f0f0f0f0f0f0?tab=fav",
                    "live_page_binding": "https://www.xiaohongshu.com/user/profile/f0f0f0f0f0f0f0f0f0f0f0f0?tab=fav",
                    "writes_performed": False,
                },
                "boards": [{
                    "id": "1" * 24,
                    "name": "专辑一",
                    "privacy": 0,
                    "note_ids": [archived_id],
                    "declared_total": 1,
                    "accessible_unique_count": 1,
                    "declared_vs_accessible_delta": 0,
                }, {
                    "id": "2" * 24,
                    "name": "手工专辑",
                    "privacy": 0,
                    "note_ids": [manual_id],
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
                    "board_count": 2,
                    "accessible_note_occurrences": 2,
                    "accessible_unique_note_ids_across_boards": 2,
                },
            }, ensure_ascii=False), encoding="utf-8")

            completed = subprocess.run([
                sys.executable,
                str(SCRIPTS / "build_archived_notes_registry.py"),
                str(report),
                str(snapshot),
                str(output),
                "--user-id",
                "f0f0f0f0f0f0f0f0f0f0f0f0",
            ], cwd=str(ROOT), text=True, capture_output=True, check=False)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["contract"], "xhs-skill-archive-registry-v2")
        self.assertEqual(payload["archived_board_count"], 1)
        self.assertEqual(payload["confirmed_archived_count"], 1)
        self.assertEqual(payload["archived_boards"][0]["note_ids"], [archived_id])
        self.assertNotIn(
            manual_id,
            payload["archived_boards"][0]["note_ids"],
        )

    def test_only_registered_album_members_are_protected(self):
        archived_id = "a" * 24
        manual_id = "b" * 24
        current_member_id = "c" * 24
        snapshot = {
            "boards": [
                {
                    "id": "1" * 24,
                    "name": "Skill专辑",
                    "note_ids": [archived_id, current_member_id],
                    "declared_total": 2,
                },
                {"id": "2" * 24, "name": "手工专辑", "note_ids": [manual_id], "declared_total": 1},
            ]
        }
        registry = {
            "contract": "xhs-skill-archive-registry-v2",
            "user_id": "f0f0f0f0f0f0f0f0f0f0f0f0",
            "archived_board_count": 1,
            "archived_boards": [{"id": "1" * 24, "name": "Skill专辑", "note_ids": [archived_id]}],
            "confirmed_archived_count": 1,
        }
        protected = protected_note_map_from_snapshot(
            snapshot,
            registry,
            expected_user_id="f0f0f0f0f0f0f0f0f0f0f0f0",
        )
        self.assertEqual(protected, {
            archived_id: "Skill专辑",
            current_member_id: "Skill专辑",
        })
        self.assertNotIn(manual_id, protected)

    def test_registry_note_moved_out_of_archived_album_is_not_protected(self):
        moved_id = "a" * 24
        current_member_id = "c" * 24
        snapshot = {
            "boards": [
                {
                    "id": "1" * 24,
                    "name": "Skill专辑",
                    "note_ids": [current_member_id],
                    "declared_total": 1,
                },
                {
                    "id": "2" * 24,
                    "name": "手工专辑",
                    "note_ids": [moved_id],
                    "declared_total": 1,
                },
            ]
        }
        registry = {
            "contract": "xhs-skill-archive-registry-v2",
            "user_id": "f0f0f0f0f0f0f0f0f0f0f0f0",
            "archived_board_count": 1,
            "archived_boards": [{
                "id": "1" * 24,
                "name": "Skill专辑",
                "note_ids": [moved_id],
            }],
            "confirmed_archived_count": 1,
        }
        protected = protected_note_map_from_snapshot(
            snapshot,
            registry,
            expected_user_id="f0f0f0f0f0f0f0f0f0f0f0f0",
        )

        self.assertEqual(protected, {current_member_id: "Skill专辑"})
        self.assertNotIn(moved_id, protected)

    def test_missing_or_renamed_skill_archived_album_is_a_hard_error(self):
        registry = {
            "contract": "xhs-skill-archive-registry-v2",
            "user_id": "f0f0f0f0f0f0f0f0f0f0f0f0",
            "archived_board_count": 1,
            "archived_boards": [{
                "id": "1" * 24,
                "name": "Skill专辑",
                "note_ids": [],
            }],
            "confirmed_archived_count": 0,
        }
        for snapshot in (
            {"boards": []},
            {"boards": [{
                "id": "1" * 24,
                "name": "已改名",
                "note_ids": [],
                "declared_total": 0,
            }]},
        ):
            with self.subTest(snapshot=snapshot):
                with self.assertRaisesRegex(ArchiveExclusionError, "身份变化或缺失"):
                    protected_note_map_from_snapshot(
                        snapshot,
                        registry,
                        expected_user_id="f0f0f0f0f0f0f0f0f0f0f0f0",
                    )

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
            snapshot = directory / "snapshot.json"
            src.write_text(json.dumps([
                {"id": archived_id, "content_type": "image", "title": "已归档"},
                {"id": new_id, "content_type": "image", "title": "新增"},
            ], ensure_ascii=False), encoding="utf-8")
            self.write_registry(registry, [
                (archived_id, "专辑一"),
                ("d" * 24, "专辑一"),
            ])
            self.write_snapshot(snapshot, registry)
            argv = [
                "enrich_note_images.py", str(src), str(out),
                "--archive-registry", str(registry),
                "--board-snapshot", str(snapshot),
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
            snapshot = directory / "snapshot.json"
            src.write_text(json.dumps([
                {"id": archived_id, "content_type": "image"},
                {"id": new_id, "content_type": "image"},
                {"id": "c" * 24, "content_type": "video"},
            ]), encoding="utf-8")
            self.write_registry(registry, [(archived_id, "专辑一")])
            self.write_snapshot(snapshot, registry)
            argv = [
                "ocr_note_images.py", str(src), str(out),
                "--archive-registry", str(registry),
                "--board-snapshot", str(snapshot),
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
            snapshot = directory / "snapshot.json"
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
            self.write_snapshot(snapshot, registry)
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
                "--board-snapshot", str(snapshot),
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
            snapshot = directory / "snapshot.json"
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
            self.write_snapshot(snapshot, registry)

            transcribe_argv = [
                "transcribe_video_items.py", str(visible), str(transcript_out),
                "--browser", "safari", "--allow-video-access", "--max-videos", "1",
                "--archive-registry", str(registry),
                "--board-snapshot", str(snapshot),
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
                "--board-snapshot", str(snapshot),
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
