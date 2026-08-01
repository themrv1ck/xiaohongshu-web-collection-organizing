#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from extract_visible_items import capture_current_segment_with_js, extract_playwright  # noqa: E402
from build_retry_queue import main as build_retry_queue_main  # noqa: E402
from run_reassign_batch import apply_batch, filter_classification_for_resume  # noqa: E402
from xhs_safety import (  # noqa: E402
    SafetyHaltedError,
    classify_safety_error,
    ensure_active_session,
    load_safety_state,
    mark_security_halted,
    redact_sensitive_text,
    resolve_safety_state_path,
)


class SafetySessionTests(unittest.TestCase):
    def capture_payload(self, *, count=1, security_marker=""):
        return {
            "scrollY": 200,
            "innerHeight": 800,
            "scrollHeight": 5000,
            "location": "https://www.xiaohongshu.com/user/profile/test/collection",
            "title": "收藏",
            "declaredItemCount": count,
            "loginRequired": False,
            "securityMarker": security_marker,
            "items": [
                {
                    "id": f"note-{index}",
                    "title": f"条目 {index}",
                    "href": f"https://www.xiaohongshu.com/explore/note-{index}",
                }
                for index in range(count)
            ],
        }

    def move_args(self, **overrides):
        values = {
            "browser": "safari",
            "arc_tab_marker": "",
            "inter_item_delay_sec": 0,
            "max_moves_per_session": 2,
            "allow_low_confidence": False,
            "verify_pages": 1,
            "user_id": "",
            "timeout_sec": 10,
            "safety_state": "",
        }
        values.update(overrides)
        return type("Args", (), values)()

    def test_passive_capture_caps_at_200_without_scroll_or_repeat_read(self):
        calls = []
        payload = self.capture_payload(count=205)

        def js_eval(script):
            calls.append(script)
            return json.dumps(payload, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "segment-001-visible.json"
            manifest = root / "segment-001-manifest.json"
            result = capture_current_segment_with_js(js_eval, out, 200, manifest)
            rows = json.loads(out.read_text(encoding="utf-8"))
            saved_manifest = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 200)
        self.assertEqual(result["capture_mode"], "passive")
        self.assertEqual(result["stopped_reason"], "segment_limit_reached")
        self.assertFalse(saved_manifest["auto_scroll"])
        self.assertFalse(saved_manifest["auto_continue"])
        self.assertTrue(all("window.scrollTo" not in call and "window.scrollBy" not in call for call in calls))

    def test_security_capture_halts_state_and_next_call_does_not_touch_page(self):
        first_calls = []
        second_calls = []

        def first(script):
            first_calls.append(script)
            return json.dumps(self.capture_payload(security_marker="安全验证"), ensure_ascii=False)

        def second(script):
            second_calls.append(script)
            return json.dumps(self.capture_payload(), ensure_ascii=False)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "segment.json"
            state = root / "xhs_safety_state.json"
            with self.assertRaises(SafetyHaltedError):
                capture_current_segment_with_js(first, out, 200, safety_state=state)
            payload = load_safety_state(state)
            self.assertEqual(payload["state"], "security_halted")
            with self.assertRaises(SafetyHaltedError):
                capture_current_segment_with_js(second, out, 200, safety_state=state)

        self.assertEqual(len(first_calls), 1)
        self.assertEqual(second_calls, [])

    def test_passive_playwright_capture_rejects_automatic_navigation_before_opening_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, '不会自动打开 URL'):
                extract_playwright(
                    Path(tmp) / 'visible.json', 30, 1.5,
                    'https://www.xiaohongshu.com/explore', 'chromium', None, None, False,
                    capture_mode='passive',
                )

    def test_page_binding_markers_are_security_halts(self):
        for error in (
            'current page is not xiaohongshu.com',
            'Arc worker expected URL no longer matches',
            'Arc 中找到多个符合 xiaohongshu.com 的标签页',
        ):
            with self.subTest(error=error):
                classified = classify_safety_error(error)
                self.assertIsNotNone(classified)
                self.assertEqual(classified[0], 'page_binding_lost')

    def test_downstream_stage_inherits_halted_state_from_input_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'capture' / 'visible_items.json'
            out = root / 'analysis' / 'image_items.json'
            source.parent.mkdir()
            source.write_text('[]', encoding='utf-8')
            state = source.parent / 'xhs_safety_state.json'
            mark_security_halted(
                state,
                stage='capture',
                reason_code='security_challenge',
                message='安全验证',
            )

            resolved = resolve_safety_state_path(None, out, predecessors=(source,))
            self.assertEqual(resolved, state.resolve())
            with self.assertRaises(SafetyHaltedError):
                ensure_active_session(resolved, stage='image_enrichment')

    def test_multiple_active_upstream_sessions_require_explicit_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / 'first' / 'visible_items.json'
            second = root / 'second' / 'video_transcripts.json'
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text('[]', encoding='utf-8')
            second.write_text('[]', encoding='utf-8')
            ensure_active_session(first.parent / 'xhs_safety_state.json', stage='capture')
            ensure_active_session(second.parent / 'xhs_safety_state.json', stage='video_transcription')

            with self.assertRaises(SafetyHaltedError):
                resolve_safety_state_path(None, root / 'out' / 'analysis.json', predecessors=(first, second))

    def test_move_safety_error_writes_report_then_stops_before_second_item(self):
        calls = {"eval": 0, "closed": False}

        class Runner:
            def run_javascript(self, _script):
                calls["eval"] += 1
                return "xhs_skill_123_456"

            def close(self):
                calls["closed"] = True

        classification = [
            {"id": "note-1", "title": "一", "target_board": "滑雪", "confidence": "high"},
            {"id": "note-2", "title": "二", "target_board": "滑雪", "confidence": "high"},
        ]
        report = {"processed": [], "errors": [], "missing_boards": [], "board_counts_before": {}, "board_counts_after": {}}
        with tempfile.TemporaryDirectory() as tmp, \
                patch("run_reassign_batch.BrowserRunner", return_value=Runner()), \
                patch("run_reassign_batch.poll_browser_job", side_effect=SafetyHaltedError("SAFETY_BREAKER: 安全验证")) as poll:
            report_path = Path(tmp) / "run_report.json"
            with self.assertRaises(SafetyHaltedError):
                apply_batch(classification, report, self.move_args(), report_path)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            state = load_safety_state(Path(tmp) / "xhs_safety_state.json")

        self.assertEqual(calls["eval"], 1)
        self.assertEqual(poll.call_count, 1)
        self.assertTrue(calls["closed"])
        self.assertEqual(saved["processed"][0]["status"], "security_halted")
        self.assertEqual(saved["safety_state"], "security_halted")
        self.assertEqual(state["state"], "security_halted")

    def test_persisted_move_and_safety_errors_redact_all_credential_formats(self):
        class Runner:
            def run_javascript(self, _script):
                return "xhs_skill_123_456"

            def close(self):
                pass

        secrets = (
            "url-secret",
            "plain-secret",
            "json-secret",
            "cli-secret",
            "signature-secret",
            "cookie-secret",
            "set-cookie-secret",
            "auth-secret",
            "session-secret",
            "encoded-secret",
            "a1-session-secret",
        )
        unsafe_error = (
            "SAFETY_BREAKER "
            "https://example.test/note?xsec_token=url-secret&xsec_source=pc_user "
            "xsec_token=plain-secret "
            '\"xsec_source\":\"json-secret\" '
            "--sign cli-secret "
            "signature=signature-secret "
            "Cookie:cookie-secret "
            "Set-Cookie:set-cookie-secret "
            "Authorization:Bearer auth-secret "
            "web_session=session-secret "
            "xsec%5Ftoken%3Dencoded-secret "
            "a1=a1-session-secret"
        )
        classification = [{
            "id": "note-1",
            "title": "一",
            "target_board": "阅读",
            "confidence": "high",
        }]
        report = {
            "processed": [],
            "errors": [],
            "missing_boards": [],
            "board_counts_before": {},
            "board_counts_after": {},
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch("run_reassign_batch.BrowserRunner", return_value=Runner()), \
                patch(
                    "run_reassign_batch.poll_browser_job",
                    side_effect=SafetyHaltedError(unsafe_error),
                ):
            root = Path(tmp)
            report_path = root / "run_report.json"
            with self.assertRaises(SafetyHaltedError):
                apply_batch(classification, report, self.move_args(), report_path)
            persisted = (
                report_path.read_text(encoding="utf-8")
                + (root / "xhs_safety_state.json").read_text(encoding="utf-8")
            )

        for secret in secrets:
            self.assertNotIn(secret, persisted)
        self.assertIn("<redacted", persisted)

    def test_shared_redactor_covers_each_supported_credential_format(self):
        cases = {
            "url-secret": (
                "https://example.test/note?xsec_token=url-secret&xsec_source=pc_user"
            ),
            "plain-secret": "xsec_token=plain-secret",
            "json-secret": '\"xsec_source\":\"json-secret\"',
            "cli-secret": "--sign cli-secret",
            "signature-secret": "signature=signature-secret",
            "cookie-secret": "Cookie:cookie-secret",
            "set-cookie-secret": "Set-Cookie:set-cookie-secret",
            "auth-secret": "Authorization:Bearer auth-secret",
            "session-secret": "web_session=session-secret",
            "encoded-secret": "xsec%5Ftoken%3Dencoded-secret",
            "a1-session-secret": "a1=a1-session-secret",
        }
        for secret, value in cases.items():
            with self.subTest(value=value):
                redacted = redact_sensitive_text(value)
                self.assertNotIn(secret, redacted)
                self.assertIn("<redacted", redacted)

        self.assertEqual(
            redact_sensitive_text("型号 a1 适合日常使用"),
            "型号 a1 适合日常使用",
        )

    def test_move_limit_saves_checkpoint_without_automatically_starting_next_item(self):
        calls = {"eval": 0}

        class Runner:
            def run_javascript(self, _script):
                calls["eval"] += 1
                return "xhs_skill_123_456"

            def close(self):
                pass

        result = {"processed": [], "errors": [], "missing_boards": [], "board_counts_before": {}, "board_counts_after": {}}
        classification = [
            {"id": "note-1", "title": "一", "target_board": "滑雪", "confidence": "high"},
            {"id": "note-2", "title": "二", "target_board": "滑雪", "confidence": "high"},
        ]
        report = {"processed": [], "errors": [], "missing_boards": [], "board_counts_before": {}, "board_counts_after": {}}
        with tempfile.TemporaryDirectory() as tmp, \
                patch("run_reassign_batch.BrowserRunner", return_value=Runner()), \
                patch("run_reassign_batch.poll_browser_job", return_value=result):
            report_path = Path(tmp) / "run_report.json"
            apply_batch(classification, report, self.move_args(max_moves_per_session=1), report_path)
            saved = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(calls["eval"], 1)
        self.assertEqual(saved["session_status"], "move_limit_reached")
        self.assertEqual(saved["remaining_count"], 1)

    def test_move_limit_skips_non_actionable_first_row_and_moves_next_planned_row(self):
        scripts = []

        class Runner:
            def run_javascript(self, script):
                scripts.append(script)
                return "xhs_skill_123_456"

            def close(self):
                pass

        result = {
            "processed": [],
            "errors": [],
            "missing_boards": [],
            "board_counts_before": {},
            "board_counts_after": {},
        }
        classification = [
            {
                "id": "note-already-there",
                "title": "已在目标专辑",
                "target_board": "阅读",
                "confidence": "high",
                "excluded": True,
                "exclude_reason": "already_in_target",
                "membership_state": "already_in_target",
            },
            {
                "id": "note-to-move",
                "title": "真正待移动",
                "target_board": "阅读",
                "confidence": "high",
                "membership_state": "in_other_board",
            },
        ]
        report = {
            "processed": [],
            "errors": [],
            "missing_boards": [],
            "board_counts_before": {},
            "board_counts_after": {},
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch("run_reassign_batch.BrowserRunner", return_value=Runner()), \
                patch("run_reassign_batch.poll_browser_job", return_value=result):
            report_path = Path(tmp) / "run_report.json"
            apply_batch(
                classification,
                report,
                self.move_args(max_moves_per_session=1),
                report_path,
            )
            saved = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(len(scripts), 1)
        self.assertIn("note-to-move", scripts[0])
        self.assertNotIn("note-already-there", scripts[0])
        self.assertEqual(saved["session_status"], "completed")
        self.assertNotIn("remaining_count", saved)

    def test_remaining_count_only_counts_unexecuted_actionable_moves(self):
        scripts = []

        class Runner:
            def run_javascript(self, script):
                scripts.append(script)
                return "xhs_skill_123_456"

            def close(self):
                pass

        empty_result = {
            "processed": [],
            "errors": [],
            "missing_boards": [],
            "board_counts_before": {},
            "board_counts_after": {},
        }
        classification = [
            {
                "id": "note-excluded",
                "target_board": "阅读",
                "confidence": "high",
                "excluded": True,
                "exclude_reason": "already_in_target",
            },
            {
                "id": "note-move-one",
                "target_board": "阅读",
                "confidence": "high",
            },
            {
                "id": "note-no-target",
                "target_board": "",
                "confidence": "high",
            },
            {
                "id": "note-low-confidence",
                "target_board": "阅读",
                "confidence": "low",
            },
            {
                "id": "note-move-two",
                "target_board": "阅读",
                "confidence": "high",
            },
        ]
        report = {
            "processed": [],
            "errors": [],
            "missing_boards": [],
            "board_counts_before": {},
            "board_counts_after": {},
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch("run_reassign_batch.BrowserRunner", return_value=Runner()), \
                patch("run_reassign_batch.poll_browser_job", return_value=empty_result):
            report_path = Path(tmp) / "run_report.json"
            apply_batch(
                classification,
                report,
                self.move_args(max_moves_per_session=1),
                report_path,
            )
            saved = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(len(scripts), 1)
        self.assertIn("note-move-one", scripts[0])
        self.assertNotIn("note-move-two", scripts[0])
        self.assertEqual(saved["session_status"], "move_limit_reached")
        self.assertEqual(saved["remaining_count"], 1)

    def test_resume_rejects_a_security_halted_report(self):
        classification = [{"id": "note-1", "target_board": "滑雪"}]
        with self.assertRaises(SafetyHaltedError):
            filter_classification_for_resume(classification, {"safety_state": "security_halted", "processed": []})

    def test_retry_queue_marks_security_halt_as_manual_new_session_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "run_report.json"
            out = root / "retry_queue.json"
            report.write_text(json.dumps({
                "safety_state": "security_halted",
                "processed": [{
                    "id": "note-1",
                    "title": "一",
                    "target_board": "滑雪",
                    "status": "security_halted",
                    "error": "SAFETY_BREAKER: 安全验证",
                    "events": [],
                }],
                "errors": [],
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(sys, "argv", ["build_retry_queue.py", str(report), str(out)]):
                build_retry_queue_main()
            rows = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(rows[0]["retry_eligible"], False)
        self.assertEqual(rows[0]["next_action"], "manual_complete_platform_verification_then_start_new_session")


if __name__ == "__main__":
    unittest.main()
