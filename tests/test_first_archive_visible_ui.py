#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_reassign_batch import build_browser_job, prepare_write_preflight  # noqa: E402
from xhs_visible_ui import (  # noqa: E402
    build_assign_collected_note_js,
    build_find_and_open_note_card_js,
    source_tab_for_item,
)


class FirstArchiveVisibleUiTests(unittest.TestCase):
    USER_ID = "1" * 24
    NOTE_ID = "2" * 24

    def args(self):
        return argparse.Namespace(
            allow_low_confidence=False,
            verify_pages=100,
            user_id=self.USER_ID,
            arc_tab_marker="marker",
            expected_url_substring=(
                f"https://www.xiaohongshu.com/user/profile/{self.USER_ID}?tab=fav"
            ),
            arc_expected_url_substring="",
        )

    def item(self, **overrides):
        value = {
            "id": self.NOTE_ID,
            "title": "不允许用来搜索的标题",
            "target_board": "健身",
            "confidence": "high",
            "membership_state": "not_in_any_board",
            "archive_lifecycle_state": "first_archive_pending",
            "source_board_id": "",
            "source_lists": ["collection"],
            "source_primary": "collection",
        }
        value.update(overrides)
        return value

    def test_collection_items_are_located_by_note_id_without_title_search(self):
        job = build_find_and_open_note_card_js(
            self.USER_ID,
            "marker",
            note_id=self.NOTE_ID,
            source_tab="fav",
        )
        self.assertIn(self.NOTE_ID, job)
        self.assertIn('a.cover', job)
        self.assertNotIn("不允许用来搜索的标题", job)
        for forbidden in (
            "search-input",
            "搜索",
            "webpackChunkxhs_pc_web",
            "req.m",
            "/api/sns/web/v1/note/move",
        ):
            self.assertNotIn(forbidden, job)

    def test_visible_cover_is_clicked_instead_of_hidden_explore_anchor(self):
        job = build_find_and_open_note_card_js(self.USER_ID, 'marker', note_id=self.NOTE_ID, source_tab='fav')
        harness = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const origin = 'https://www.xiaohongshu.com';
let clicked = '';
function element(href, visible, kind) {
  return {href: origin + href, textContent: '', innerText: '',
    getAttribute: key => key === 'href' ? href : null,
    getBoundingClientRect: () => ({width: visible ? 220 : 0, height: visible ? 180 : 0}),
    scrollIntoView() {}, click() { clicked = kind; }};
}
const hidden = element('/explore/' + input.note, false, 'hidden');
const cover = element('/user/profile/' + input.user + '/' + input.note, true, 'cover');
const own = element('/user/profile/' + input.user, true, 'own');
own.textContent = '我';
const section = {dataset: {noteId: input.note},
  getAttribute: key => key === 'data-note-id' ? input.note : null,
  querySelector: selector => selector === 'a.cover' ? cover : hidden,
  querySelectorAll: () => [hidden, cover]};
const location = new URL(origin + '/user/profile/' + input.user + '?tab=fav');
const sandbox = {URL, getComputedStyle: () => ({display:'block',visibility:'visible'}),
  window: {location, name:'marker', scrollY:0, innerHeight:720},
  document: {body:{innerText:''}, scrollingElement:{scrollHeight:900},
    querySelectorAll: selector => selector === 'a[href*="/user/profile/"]' ? [own]
      : selector.includes('a[href') ? [hidden] : [section]}};
const result = JSON.parse(vm.runInNewContext(input.job, sandbox));
process.stdout.write(JSON.stringify({result, clicked}));
'''
        completed = subprocess.run(['node', '-e', harness], input=json.dumps({
            'job': job, 'user': self.USER_ID, 'note': self.NOTE_ID,
        }), text=True, capture_output=True, timeout=5)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result['clicked'], 'cover')
        self.assertEqual(result['result']['note_id'], self.NOTE_ID)

    def test_collected_note_assignment_requires_explicit_recollect_consent(self):
        job = build_assign_collected_note_js(
            self.USER_ID,
            "marker",
            note_id=self.NOTE_ID,
            target_board="健身",
        )
        self.assertIn("#collected", job)
        self.assertIn("加入专辑", job)
        self.assertIn('"allow_recollect": false', job)
        self.assertNotIn('collected_control_hovered', job)
        self.assertNotIn("webpackChunkxhs_pc_web", job)
        self.assertNotIn("/api/sns/web/v1/note/move", job)

    def test_batch_job_accepts_first_archive_pending_collection_item(self):
        job = build_browser_job([self.item()], self.args())
        self.assertIn(self.NOTE_ID, job)
        self.assertIn("not_in_any_board", job)
        self.assertIn("first_archive_pending", job)
        self.assertNotIn("内部模块探测已禁用", job)
        self.assertNotIn("不允许用来搜索的标题", job)
        for forbidden in (
            "webpackChunkxhs_pc_web",
            "req.m",
            "/api/sns/web/v1/note/move",
            "search-input",
            "搜索",
        ):
            self.assertNotIn(forbidden, job)

    def test_source_tab_is_determined_without_guessing(self):
        self.assertEqual(source_tab_for_item(self.item()), "fav")
        self.assertEqual(
            source_tab_for_item(self.item(
                source_lists=["liked"], source_primary="liked"
            )),
            "liked",
        )
        self.assertEqual(
            source_tab_for_item(self.item(
                source_lists=["collection", "liked"], source_primary="collection"
            )),
            "fav",
        )
        with self.assertRaisesRegex(ValueError, "来源"):
            source_tab_for_item(self.item(source_lists=[], source_primary=""))

    def test_protection_requires_skill_archive_registry_not_collect_or_any_album(self):
        archived_id = "a" * 24
        manual_id = "b" * 24
        favorite_id = "c" * 24
        current_member_id = "f" * 24
        snapshot = {
            "mode": "read_only",
            "source": {
                "writes_performed": False,
                "user_id": self.USER_ID,
            },
            "validation": {
                "pagination_cursor_invariants_passed": True,
                "board_names_unique": True,
                "within_board_duplicates": [],
                "full_membership_complete": True,
            },
            "boards": [
                {
                    "id": "d" * 24,
                    "name": "Skill归档",
                    "note_ids": [archived_id, current_member_id],
                    "declared_total": 2,
                    "page_count": 1,
                },
                {"id": "e" * 24, "name": "手工专辑", "note_ids": [manual_id], "declared_total": 1, "page_count": 1},
            ],
        }
        registry = {
            "contract": "xhs-skill-archive-registry-v2",
            "user_id": self.USER_ID,
            "archived_board_count": 1,
            "archived_boards": [{"id": "d" * 24, "name": "Skill归档", "note_ids": [archived_id]}],
            "confirmed_archived_count": 1,
        }
        rows = [
            self.item(id=archived_id, target_board="Skill归档"),
            self.item(id=current_member_id, target_board="Skill归档"),
            self.item(id=manual_id, target_board="健身"),
            self.item(id=favorite_id, target_board="健身"),
        ]
        result = prepare_write_preflight(
            rows,
            snapshot,
            {"confirmed": ["Skill归档", "手工专辑", "健身"], "missing": [], "planned": []},
            allow_low_confidence=False,
            archive_registry=registry,
        )
        by_id = {row["id"]: row for row in result["resolved_items"]}
        self.assertTrue(by_id[archived_id]["excluded"])
        self.assertEqual(
            by_id[archived_id]["membership_state"],
            "skill_archived_board_member_protected",
        )
        self.assertEqual(by_id[archived_id]["archive_lifecycle_state"], "first_archive_confirmed")
        self.assertTrue(by_id[current_member_id]["excluded"])
        self.assertEqual(
            by_id[current_member_id]["membership_state"],
            "skill_archived_board_member_protected",
        )
        self.assertEqual(
            by_id[current_member_id]["archive_lifecycle_state"],
            "first_archive_confirmed",
        )
        self.assertEqual(by_id[manual_id]["membership_state"], "unarchived_board_member")
        self.assertEqual(by_id[manual_id]["archive_lifecycle_state"], "first_archive_pending")
        self.assertFalse(by_id[manual_id].get("excluded", False))
        self.assertEqual(by_id[favorite_id]["membership_state"], "not_in_any_board")
        self.assertEqual(by_id[favorite_id]["archive_lifecycle_state"], "first_archive_pending")


if __name__ == "__main__":
    unittest.main()
