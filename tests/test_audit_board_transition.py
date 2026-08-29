import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_board_transition import BoardTransitionError, audit_board_transition  # noqa: E402


class AuditBoardTransitionTests(unittest.TestCase):
    def snapshot(self, a_notes, b_notes):
        boards = [
            {
                "id": "board-a",
                "name": "专辑甲",
                "privacy": 0,
                "declared_total": len(a_notes),
                "accessible_unique_count": len(a_notes),
                "declared_vs_accessible_delta": 0,
                "note_ids": a_notes,
            },
            {
                "id": "board-b",
                "name": "专辑乙",
                "privacy": 0,
                "declared_total": len(b_notes),
                "accessible_unique_count": len(b_notes),
                "declared_vs_accessible_delta": 0,
                "note_ids": b_notes,
            },
        ]
        occurrence_count = len(a_notes) + len(b_notes)
        return {
            "source": {},
            "boards": boards,
            "validation": {
                "board_count": 2,
                "board_names_unique": True,
                "pagination_cursor_invariants_passed": True,
                "accessible_note_occurrences": occurrence_count,
                "accessible_unique_note_ids_across_boards": occurrence_count,
                "duplicate_note_ids": [],
                "multi_board_note_ids": [],
                "within_board_duplicates": [],
                "count_mismatch_boards": [],
                "full_membership_complete": True,
            },
        }

    def fixtures(self):
        inventory = {
            "boards": ["专辑甲", "专辑乙"],
            "excluded_note_ids": ["existing"],
            "note_to_board": {"existing": "专辑甲"},
        }
        review = {"items": [{"id": "reviewed", "target_board": "专辑乙"}]}
        return inventory, review, self.snapshot(["existing"], []), self.snapshot(["existing"], ["reviewed"])

    def test_exact_pre_post_set_equation_passes(self):
        inventory, review, pre, post = self.fixtures()
        report = audit_board_transition(inventory, review, pre, post)
        self.assertTrue(report["passed"])
        self.assertEqual(report["post"]["existing_members_preserved"], 1)
        self.assertEqual(report["post"]["reviewed_exactly_once_in_target"], 1)

    def test_rejects_board_identity_change(self):
        inventory, review, pre, post = self.fixtures()
        changed = copy.deepcopy(post)
        changed["boards"][1]["privacy"] = 1
        with self.assertRaisesRegex(BoardTransitionError, "隐私"):
            audit_board_transition(inventory, review, pre, changed)


if __name__ == "__main__":
    unittest.main()
