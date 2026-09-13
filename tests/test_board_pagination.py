#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from xhs_visible_ui import (  # noqa: E402
    VisibleUiContractError,
    read_visible_album_list,
    validate_album_scroll_snapshots,
)


def board(index: int) -> dict:
    return {
        "id": f"{index:024x}",
        "name": f"board-{index}",
        "declared_total": index,
        "path": f"/board/{index:024x}",
    }


def accumulating(total: int) -> list[list[dict]]:
    rows = [board(index + 1) for index in range(total)]
    stops = list(range(100, total, 100)) + [total]
    return [rows[:stop] for stop in stops]


class BoardPaginationTests(unittest.TestCase):
    def test_reads_all_required_album_counts_from_visible_pages(self):
        for total in (100, 101, 181, 200, 201):
            with self.subTest(total=total):
                result = validate_album_scroll_snapshots(total, accumulating(total))
                self.assertEqual(len(result), total)
                self.assertEqual(result[-1]["id"], f"{total:024x}")

    def test_missing_page_is_a_hard_error(self):
        with self.assertRaisesRegex(VisibleUiContractError, "缺页"):
            validate_album_scroll_snapshots(181, accumulating(180))

    def test_duplicate_album_is_a_hard_error(self):
        rows = accumulating(101)[-1]
        rows[-1] = dict(rows[0], name="duplicate-id")
        with self.assertRaisesRegex(VisibleUiContractError, "重复"):
            validate_album_scroll_snapshots(101, [rows])

    def test_album_identity_change_is_a_hard_error(self):
        first = accumulating(100)[-1]
        changed = [dict(row) for row in first]
        changed[0]["name"] = "changed"
        with self.assertRaisesRegex(VisibleUiContractError, "绑定发生变化"):
            validate_album_scroll_snapshots(100, [first, changed])

    def test_declared_count_change_is_a_hard_error(self):
        class ChangingCountSession:
            user_id = "f" * 24
            tab_marker = "test-marker"

            def __init__(self):
                self.wait_count = 0
                self.snapshots = [
                    {"declared_board_count": 201, "boards": accumulating(100)[-1]},
                    {"declared_board_count": 202, "boards": accumulating(101)[-1]},
                ]

            def navigate(self, path, query=None):
                return None

            def wait_for(self, script, *, timeout_sec=20.0):
                self.wait_count += 1
                if self.wait_count == 1:
                    return {"ok": True}
                return self.snapshots.pop(0)

            def run_json(self, script):
                return {"ok": True}

        with patch("xhs_visible_ui.time.sleep", return_value=None):
            with self.assertRaisesRegex(VisibleUiContractError, "专辑总数变化"):
                read_visible_album_list(ChangingCountSession())


if __name__ == "__main__":
    unittest.main()
