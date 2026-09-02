#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from xhs_visible_ui import (  # noqa: E402
    ArcVisibleUiSession,
    VisibleUiContractError,
    build_album_list_snapshot_js,
    build_board_snapshot_js,
    build_collect_into_board_js,
    build_note_collect_probe_js,
    build_open_create_modal_js,
    build_submit_create_board_js,
    poll_collect_into_board,
    validate_album_scroll_snapshots,
    validate_board_note_snapshots,
    validate_new_collection_transition,
)


def row(index, prefix="board"):
    return {
        "id": f"{index:024x}",
        "name": f"{prefix}-{index}",
        "privacy": 0,
        "declared_total": index,
        "path": f"/board/{index:024x}",
    }


class VisibleUiAlbumTests(unittest.TestCase):
    USER_ID = "f" * 24
    MARKER = "arc-visible-ui-test"

    def accumulating(self, total):
        all_rows = [row(index + 1) for index in range(total)]
        stops = list(range(100, total, 100)) + [total]
        return [all_rows[:stop] for stop in stops]

    def test_complete_album_counts_at_all_required_boundaries(self):
        for total in (100, 101, 181, 200, 201):
            with self.subTest(total=total):
                result = validate_album_scroll_snapshots(total, self.accumulating(total))
                self.assertEqual(len(result), total)
                self.assertEqual(result[0]["id"], f"{1:024x}")
                self.assertEqual(result[-1]["id"], f"{total:024x}")

    def test_album_missing_duplicate_and_identity_change_hard_stop(self):
        with self.assertRaisesRegex(VisibleUiContractError, "缺页"):
            validate_album_scroll_snapshots(101, self.accumulating(100))

        duplicate = [row(index + 1) for index in range(100)]
        duplicate.append(dict(duplicate[0], name="different-name"))
        with self.assertRaisesRegex(VisibleUiContractError, "重复"):
            validate_album_scroll_snapshots(101, [duplicate])

        first = [row(index + 1) for index in range(100)]
        changed = [dict(item) for item in first]
        changed[0]["name"] = "changed"
        with self.assertRaisesRegex(VisibleUiContractError, "绑定发生变化"):
            validate_album_scroll_snapshots(100, [first, changed])

    def test_member_missing_duplicate_and_non_monotonic_hard_stop(self):
        notes = [{"id": f"{index + 1:024x}", "title": str(index)} for index in range(101)]
        result = validate_board_note_snapshots(101, [notes[:100], notes])
        self.assertEqual(len(result), 101)
        with self.assertRaisesRegex(VisibleUiContractError, "缺页"):
            validate_board_note_snapshots(101, [notes[:100]])
        with self.assertRaisesRegex(VisibleUiContractError, "重复"):
            validate_board_note_snapshots(101, [notes[:100] + [notes[0]]])
        with self.assertRaisesRegex(VisibleUiContractError, "已有笔记消失"):
            validate_board_note_snapshots(100, [notes[:100], notes[1:101]])

    def test_generated_jobs_use_only_visible_dom_controls(self):
        jobs = (
            build_album_list_snapshot_js(self.USER_ID, self.MARKER),
            build_board_snapshot_js(self.USER_ID, self.MARKER, "a" * 24),
            build_open_create_modal_js(self.USER_ID, self.MARKER),
            build_submit_create_board_js(
                self.USER_ID,
                self.MARKER,
                name="无法确定",
                description="",
                privacy=0,
            ),
            build_collect_into_board_js(
                self.USER_ID,
                self.MARKER,
                note_id="b" * 24,
                target_board="无法确定",
            ),
            build_note_collect_probe_js(self.USER_ID, self.MARKER, "c" * 24),
        )
        forbidden = (
            "webpackChunkxhs_pc_web",
            "/api/sns/web/v1/board",
            "/api/sns/web/v1/note/move",
            "req.m",
        )
        for job in jobs:
            for value in forbidden:
                with self.subTest(value=value):
                    self.assertNotIn(value, job)
        self.assertIn("section.note-item", jobs[1])
        self.assertIn(".reds-modal-open .modal", jobs[3])
        self.assertIn("#note-page-collect-board-guide", jobs[4])
        self.assertIn(".board-list-container", jobs[4])
        self.assertIn("container.scrollTop = container.scrollHeight", jobs[4])
        self.assertIn("HIGH_RISK_STATE_UNCERTAIN", jobs[4])
        self.assertIn("historical collected notes cannot be reassigned", jobs[4])
        self.assertIn("#collected", jobs[5])

    def test_new_collection_transition_requires_one_exact_append(self):
        before = {
            "id": "a" * 24,
            "name": "无法确定",
            "declared_total": 2,
            "note_ids": ["1" * 24, "2" * 24],
        }
        after = {
            **before,
            "declared_total": 3,
            "note_ids": ["1" * 24, "2" * 24, "3" * 24],
        }
        validate_new_collection_transition(before, after, "3" * 24)
        with self.assertRaisesRegex(VisibleUiContractError, "不是原集合加新笔记"):
            validate_new_collection_transition(
                before,
                {**after, "note_ids": ["1" * 24, "3" * 24, "4" * 24]},
                "3" * 24,
            )
        with self.assertRaisesRegex(VisibleUiContractError, "写入前已属于"):
            validate_new_collection_transition(before, after, "2" * 24)

    def test_arc_navigation_reuses_exact_open_tab_and_rejects_session_query(self):
        session = ArcVisibleUiSession("window", "tab", self.MARKER, self.USER_ID)
        with patch("xhs_visible_ui.require_macos_app_running") as running, patch(
            "xhs_visible_ui.jxa_osascript",
            side_effect=[
                True,
                json.dumps({
                    "url": f"https://www.xiaohongshu.com/user/profile/{self.USER_ID}?tab=fav",
                    "title": "我 - 小红书",
                    "loading": False,
                }),
            ],
        ) as jxa:
            session.navigate(f"/user/profile/{self.USER_ID}", {"tab": "fav"})
        running.assert_called_once_with("Arc")
        script = jxa.call_args_list[0].args[0]
        self.assertIn("windows.byId", script)
        self.assertIn("tabs.byId", script)
        self.assertIn("?tab=fav", script)
        with self.assertRaisesRegex(VisibleUiContractError, "会话或签名参数"):
            session.navigate("/explore", {"xsec_token": "secret"})

    def test_collect_poll_requires_visible_success(self):
        class FakeSession:
            def __init__(self):
                self.states = [
                    {"done": False},
                    {"done": True, "ok": True, "result": {"visible_confirmation": "已加入无法确定"}},
                ]

            def run_json(self, _script):
                return self.states.pop(0)

        result = poll_collect_into_board(
            FakeSession(),
            "xhs_ui_123_456",
            timeout_sec=1,
        )
        self.assertEqual(result["visible_confirmation"], "已加入无法确定")


if __name__ == "__main__":
    unittest.main()
