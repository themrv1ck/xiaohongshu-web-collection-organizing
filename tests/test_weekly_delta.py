#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from weekly_delta import (  # noqa: E402
    BASELINE_SCHEMA_VERSION,
    build_delta,
    commit_baseline,
    load_baseline,
)


def note(note_id: str) -> dict[str, str]:
    return {"id": note_id, "title": f"标题 {note_id}"}


class WeeklyDeltaTests(unittest.TestCase):
    def test_first_preview_contains_all_notes_and_never_moves(self):
        result = build_delta([note("a"), note("b")], set())

        self.assertEqual(result["new_note_ids"], ["a", "b"])
        self.assertEqual(result["new_note_count"], 2)
        self.assertFalse(result["automatic_move"])
        self.assertEqual(result["status"], "preview_only")

    def test_commit_requires_a_complete_classification_then_future_run_only_returns_new_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "weekly-baseline.json"
            classification = root / "classification.json"
            classification.write_text(json.dumps([note("a"), note("b")]), encoding="utf-8")
            candidate = build_delta([note("a"), note("b")], set())

            commit_baseline(candidate, classification, baseline)
            self.assertEqual(load_baseline(baseline), {"a", "b"})
            next_candidate = build_delta([note("a"), note("b"), note("c")], load_baseline(baseline))

        self.assertEqual(next_candidate["new_note_ids"], ["c"])

    def test_incomplete_plan_cannot_advance_the_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "weekly-baseline.json"
            classification = root / "classification.json"
            classification.write_text(json.dumps([note("a")]), encoding="utf-8")
            candidate = build_delta([note("a"), note("b")], set())

            with self.assertRaisesRegex(ValueError, "缺少 1 条新笔记"):
                commit_baseline(candidate, classification, baseline)

            self.assertFalse(baseline.exists())

    def test_baseline_schema_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly-baseline.json"
            path.write_text(json.dumps({
                "schema_version": BASELINE_SCHEMA_VERSION,
                "processed_note_ids": ["a"],
            }), encoding="utf-8")
            self.assertEqual(load_baseline(path), {"a"})


if __name__ == "__main__":
    unittest.main()
