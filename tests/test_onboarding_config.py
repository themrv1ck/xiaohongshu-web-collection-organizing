#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from onboarding_config import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    DEFAULT_PAUSE_MINUTES,
    MAX_BATCH_SIZE,
    build_onboarding_config,
    build_weekly_task,
    main,
)


class OnboardingConfigTests(unittest.TestCase):
    def test_defaults_are_the_v2_recommended_local_settings(self):
        config = build_onboarding_config()

        self.assertEqual(config["startup_mode"], "quick")
        self.assertEqual(config["source"], "collection")
        self.assertFalse(config["ocr_enabled"])
        self.assertEqual(config["collection"]["batch_size"], DEFAULT_BATCH_SIZE)
        self.assertEqual(config["collection"]["hard_max_batch_size"], MAX_BATCH_SIZE)
        self.assertEqual(config["collection"]["pause_minutes"], DEFAULT_PAUSE_MINUTES)
        self.assertTrue(config["collection"]["auto_continue_after_pause"])
        self.assertFalse(config["review"]["expand_album_items"])
        self.assertFalse(config["weekly_task"]["enabled"])
        self.assertEqual(config["weekly_task"]["processed_note_ids_file"], "weekly_processed_note_ids.json")

    def test_group_size_over_200_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能超过 200"):
            build_onboarding_config(batch_size=201)

    def test_pause_must_be_a_positive_integer(self):
        for value in (0, -1, "3.5", "three", True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "暂停分钟数必须是正整数"):
                    build_onboarding_config(pause_minutes=value)

    def test_enabled_weekly_task_requires_valid_weekday_and_time(self):
        with self.assertRaisesRegex(ValueError, "每周任务星期"):
            build_weekly_task(enabled=True)
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            build_weekly_task(enabled=True, weekday="monday", time="9:30")
        weekly = build_weekly_task(enabled=True, weekday="MONDAY", time="09:30")
        self.assertTrue(weekly["enabled"])
        self.assertEqual(weekly["weekday"], "monday")
        self.assertEqual(weekly["time"], "09:30")

    def test_weekly_task_never_has_automatic_move_permission(self):
        disabled = build_weekly_task()
        enabled = build_weekly_task(enabled=True, weekday="sunday", time="21:00")

        for weekly in (disabled, enabled):
            self.assertTrue(weekly["creates_classification_plan_only"])
            self.assertTrue(weekly["requires_current_browser_confirmation"])
            self.assertFalse(weekly["automatic_move"])
            self.assertEqual(weekly["processed_note_ids_file"], "weekly_processed_note_ids.json")

    def test_cli_writes_persistent_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "nested" / "onboarding.json"
            main([
                str(output),
                "--startup-mode", "complete",
                "--source", "all",
                "--ocr", "enabled",
                "--batch-size", "100",
                "--pause-minutes", "5",
                "--expand-album-items",
                "--weekly-enabled",
                "--weekly-weekday", "friday",
                "--weekly-time", "18:45",
            ])
            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(saved["startup_mode"], "complete")
        self.assertEqual(saved["source"], "all")
        self.assertTrue(saved["ocr_enabled"])
        self.assertEqual(saved["collection"]["batch_size"], 100)
        self.assertEqual(saved["collection"]["pause_minutes"], 5)
        self.assertTrue(saved["review"]["expand_album_items"])
        self.assertEqual(saved["weekly_task"]["weekday"], "friday")
        self.assertEqual(saved["weekly_task"]["time"], "18:45")


if __name__ == "__main__":
    unittest.main()
