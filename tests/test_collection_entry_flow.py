import json
import subprocess
import sys
import unittest
import argparse
import tempfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from xhs_visible_ui import build_assign_collected_note_js, build_collect_into_board_js, build_album_entry_preview_js
from run_reassign_batch import apply_batch, build_browser_job, validate_live_assignment_membership


class CollectionEntryFlowTests(unittest.TestCase):
    def item(self, **extra):
        return dict(id='2'*24,target_board='目标专辑',confidence='high',
            membership_state='not_in_any_board',archive_lifecycle_state='first_archive_pending',
            source_primary='collection',source_board_id='',**extra)

    def test_consent_missing_stops_batch_before_browser_launch(self):
        args = argparse.Namespace(browser='safari',verify_pages=1,max_moves_per_session=1,
            allow_low_confidence=False,user_id='1'*24,allow_recollect=False)
        with tempfile.TemporaryDirectory() as temp, patch('run_reassign_batch.BrowserRunner') as browser:
            with self.assertRaisesRegex(RuntimeError,'allow-recollect'):
                apply_batch([self.item()],{},args,Path(temp)/'report.json')
            browser.assert_not_called()
            self.assertEqual(json.loads((Path(temp)/'report.json').read_text())['blockers'],
                             ['recollect_consent_required'])

    def test_protected_or_excluded_row_cannot_generate_write_job(self):
        args = argparse.Namespace(user_id='1'*24,allow_recollect=True)
        for extra in ({'excluded':True},{'exclude_reason':'skill_archived_board_member_protected'}):
            with self.assertRaisesRegex(RuntimeError,'首次归档'):
                build_browser_job([self.item(**extra)],args)

    def test_new_live_membership_stops_before_any_toggle(self):
        snapshot = {'boards':[{'id':'3'*24,'note_ids':['2'*24]}]}
        with patch('run_reassign_batch.capture_visible_album_snapshot',return_value=snapshot):
            with self.assertRaisesRegex(RuntimeError,'实时专辑关系已变化'):
                validate_live_assignment_membership(object(),self.item())

    def test_unchanged_single_manual_album_membership_is_allowed(self):
        snapshot = {'boards':[{'id':'3'*24,'note_ids':['2'*24]}]}
        item = self.item()
        item['source_board_id'] = '3'*24
        with patch('run_reassign_batch.capture_visible_album_snapshot',return_value=snapshot):
            self.assertEqual(validate_live_assignment_membership(object(),item),snapshot)

    def replay(self, collected=False, authorized=True, entry_preview=False, **scenario):
        kwargs = dict(note_id='2'*24, target_board='目标专辑', timeout_ms=3000)
        if entry_preview:
            job = build_album_entry_preview_js('1'*24, 'marker', initial_collected=collected,
                allow_recollect=authorized, **kwargs)
        elif collected:
            job = build_assign_collected_note_js('1'*24, 'marker', allow_recollect=authorized, **kwargs)
        else:
            job = build_collect_into_board_js('1'*24, 'marker', **kwargs)
        proc = subprocess.run(['node', str(ROOT / 'tests/fixtures/album_assignment_dom.cjs')],
            input=json.dumps(dict(job=job,user='1'*24,note='2'*24,target='目标专辑',collected=collected,**scenario)),
            text=True,capture_output=True,timeout=5)
        self.assertEqual(proc.returncode,0,proc.stderr)
        return json.loads(proc.stdout)

    def test_preview_recollects_but_never_selects_album_or_reports_assignment_success(self):
        result = self.replay(collected=True,entry_preview=True)
        self.assertEqual(result['clicks'], ['uncollect','collect','join'])
        self.assertTrue(result['collected'])
        self.assertTrue(result['state']['preview_only'])
        self.assertFalse(result['state']['ok'])
        self.assertNotIn('result',result['state'])
        self.assertEqual(result['state']['phase'],'picker_verified')

    def test_preview_scrolls_to_target_without_selecting_it(self):
        result = self.replay(entry_preview=True,nestedScroll=True)
        self.assertEqual(result['clicks'], ['collect','join'])
        self.assertTrue(result['state']['preview_only'])

    def test_preview_still_requires_recollect_consent(self):
        result = self.replay(collected=True,entry_preview=True,authorized=False)
        self.assertEqual(result['clicks'], [])
        self.assertIn('recollect',result['error'])

    def test_new_note_collects_once_and_uses_immediate_entry(self):
        result = self.replay()
        self.assertEqual(result['clicks'], ['collect','join','board'])
        self.assertTrue(result['state']['ok'])

    def test_selector_scrolls_actual_inner_container_not_outer_modal(self):
        result = self.replay(nestedScroll=True)
        self.assertEqual(result['clicks'], ['collect','join','board'])
        self.assertTrue(result['state']['ok'])

    def test_existing_note_recollects_once_then_uses_entry(self):
        result = self.replay(collected=True)
        self.assertEqual(result['clicks'], ['uncollect','collect','join','board'])
        self.assertTrue(result['state']['ok'])

    def test_observed_platform_500ms_gate_with_optimistic_icon(self):
        result = self.replay(collected=True,platformGateMs=500,optimisticIcon=True)
        self.assertEqual(result['ignoredClicks'], [])
        self.assertEqual(result['clicks'], ['uncollect','collect','join','board'])
        self.assertTrue(result['state']['ok'])
        clicks = result['state']['diagnostics']['clicks']
        self.assertGreaterEqual(clicks[1]['elapsed_ms']-clicks[0]['elapsed_ms'],500)

    def test_entry_preview_respects_platform_gate_and_never_selects_album(self):
        result = self.replay(collected=True,entry_preview=True,platformGateMs=500,optimisticIcon=True)
        self.assertEqual(result['ignoredClicks'], [])
        self.assertEqual(result['clicks'], ['uncollect','collect','join'])
        self.assertTrue(result['state']['preview_only'])

    def test_wall_clock_change_cannot_shorten_platform_click_gate(self):
        result = self.replay(collected=True,platformGateMs=500,optimisticIcon=True,wallClockJump=True)
        self.assertEqual(result['ignoredClicks'], [])
        self.assertTrue(result['state']['ok'])

    def test_new_collection_does_not_wait_for_a_nonexistent_previous_click(self):
        result = self.replay(platformGateMs=500,optimisticIcon=True)
        self.assertTrue(result['state']['ok'])
        self.assertEqual(result['state']['diagnostics']['clicks'],[{'action':'collect','elapsed_ms':0}])

    def test_waits_for_busy_control_after_uncollected_icon_before_recollecting(self):
        result = self.replay(collected=True,busyAfterUncollect=True)
        self.assertEqual(result['clicks'], ['uncollect','collect','join','board'])
        self.assertTrue(result['state']['ok'])
        diagnostics = result['state']['diagnostics']
        self.assertGreaterEqual(diagnostics['clicks'][1]['elapsed_ms'],850)
        self.assertTrue(any(row['blocked_by']=='disabled_or_busy'
                            for row in diagnostics['observations']))

    def test_does_not_bypass_pointer_events_block_on_recollect(self):
        result = self.replay(collected=True,busyAfterUncollect=True,busyKind='pointer')
        self.assertEqual(result['clicks'], ['uncollect','collect','join','board'])
        self.assertTrue(result['state']['ok'])

    def test_busy_control_that_never_recovers_does_not_receive_second_click(self):
        result = self.replay(collected=True,busyAfterUncollect=True,busyNeverClears=True)
        self.assertEqual(result['clicks'], ['uncollect'])
        self.assertIn('HIGH_RISK_STATE_UNCERTAIN',result['state']['error'])
        self.assertEqual(result['state']['phase'],'await_recollect_ready')
        self.assertFalse(result['state']['diagnostics']['observations'][-1]['actionable'])

    def test_initial_busy_control_is_rejected_before_any_write(self):
        result = self.replay(collected=True,initialBusy=True)
        self.assertEqual(result['clicks'], [])
        self.assertIn('not actionable',result['error'])

    def test_existing_note_without_recollect_consent_never_clicks(self):
        result = self.replay(collected=True,authorized=False)
        self.assertEqual(result['clicks'], [])
        self.assertIn('recollect',result['error'])

    def test_uncollect_not_observed_never_blindly_clicks_again(self):
        result = self.replay(collected=True,stuck='uncollect')
        self.assertEqual(result['clicks'], ['uncollect'])
        self.assertIn('HIGH_RISK_STATE_UNCERTAIN',result['state']['error'])

    def test_recollect_failure_stops_without_join_or_retry(self):
        result = self.replay(collected=True,stuck='collect')
        self.assertEqual(result['clicks'], ['uncollect','collect'])
        self.assertFalse(result['collected'])
        self.assertIn('HIGH_RISK_STATE_UNCERTAIN',result['state']['error'])

    def test_missing_entry_after_new_collect_is_write_uncertainty(self):
        result = self.replay(missingJoin=True)
        self.assertEqual(result['clicks'], ['collect'])
        self.assertIn('HIGH_RISK_STATE_UNCERTAIN',result['state']['error'])

    def test_security_after_uncollect_stops_before_recollect(self):
        result = self.replay(collected=True,securityAfterUncollect=True)
        self.assertEqual(result['clicks'], ['uncollect'])
        self.assertIn('SAFETY_BREAKER',result['state']['error'])

    def test_album_selection_without_confirmation_is_not_success(self):
        result = self.replay(missingConfirmation=True)
        self.assertEqual(result['clicks'], ['collect','join','board'])
        self.assertFalse(result['state']['ok'])
        self.assertIn('HIGH_RISK_STATE_UNCERTAIN',result['state']['error'])
