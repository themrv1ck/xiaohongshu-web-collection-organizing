#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from collection_scope import (  # noqa: E402
    CollectionScopeError,
    FULL_COLLECTION,
    INCREMENTAL,
    USER_CONFIRMED_ACCESSIBLE,
    build_collection_scope,
    build_incremental_collection_scope,
    load_scope,
    validate_scope_input,
)


class CollectionScopeTests(unittest.TestCase):
    note_a = 'a' * 24
    note_b = 'b' * 24
    note_c = 'c' * 24
    note_d = 'd' * 24

    def write_run(self, directory, *, rows=None, declared_count=3, session_id='session-1', user_id=None):
        state = {'state': 'active', 'security_halted': False, 'session_id': session_id}
        (directory / 'xhs_safety_state.json').write_text(json.dumps(state), encoding='utf-8')
        location = 'https://www.xiaohongshu.com/user/profile/' + (user_id or ('1' * 24)) + '?tab=fav&subTab=note'
        if rows is None:
            rows = [
                {'id': self.note_a, 'page_index': 0, 'source_primary': '收藏', 'source_lists': ['收藏'], 'title': '一'},
                {'id': self.note_b, 'page_index': 1, 'source_primary': '收藏', 'source_lists': ['收藏'], 'title': '二'},
            ]
        collection = directory / 'segment-001-collection.json'
        collection.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        manifest = {
            'capture_mode': 'passive', 'segment_limit': 200,
            'auto_scroll': False, 'auto_continue': False, 'source': '收藏',
            'output': str(collection),
            'safety_state': str(directory / 'xhs_safety_state.json'),
            'count': len(rows), 'item_count': len(rows), 'newly_seen_count': len(rows),
            'observed_card_count': len(rows), 'existing_count': 0,
            'page': {'declaredItemCount': declared_count, 'location': location},
        }
        (directory / 'segment-001-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    def test_user_confirmed_accessible_scope_records_unidentified_count_and_binds_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_run(directory)
            visible = directory / 'visible_items.json'
            scope_path = directory / 'collection_scope.json'
            scope = build_collection_scope(
                run_dir=directory, visible_out=visible, scope_out=scope_path,
                scope_kind=USER_CONFIRMED_ACCESSIBLE,
                expected_accessible_count=2, expected_unidentified_count=1,
            )
            self.assertEqual(scope['unidentified_count'], 1)
            self.assertEqual(load_scope(scope_path)['note_ids'], [self.note_a, self.note_b])
            items = json.loads(visible.read_text(encoding='utf-8'))
            validate_scope_input(scope_path, items, stage='test', require_original_visible_hash=True, items_path=visible)

    def test_full_scope_rejects_declared_count_gap_and_tampered_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_run(directory)
            with self.assertRaisesRegex(CollectionScopeError, '全量收藏范围'):
                build_collection_scope(
                    run_dir=directory, visible_out=directory / 'full.json',
                    scope_out=directory / 'full.scope.json', scope_kind=FULL_COLLECTION,
                    expected_accessible_count=2, expected_unidentified_count=None,
                )
            visible = directory / 'visible.json'
            scope_path = directory / 'scope.json'
            build_collection_scope(
                run_dir=directory, visible_out=visible, scope_out=scope_path,
                scope_kind=USER_CONFIRMED_ACCESSIBLE,
                expected_accessible_count=2, expected_unidentified_count=1,
            )
            tampered = json.loads(visible.read_text(encoding='utf-8'))
            tampered.reverse()
            with self.assertRaisesRegex(CollectionScopeError, '集合或顺序'):
                validate_scope_input(scope_path, tampered, stage='tampered')

    def test_incremental_scope_unions_passive_delta_before_confirmed_base_without_claiming_full_current_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / 'base'
            delta_dir = root / 'delta'
            base_dir.mkdir()
            delta_dir.mkdir()
            self.write_run(base_dir, declared_count=2, session_id='old-session')
            base_visible = base_dir / 'visible_items.json'
            base_scope = base_dir / 'collection_scope.json'
            build_collection_scope(
                run_dir=base_dir,
                visible_out=base_visible,
                scope_out=base_scope,
                scope_kind=USER_CONFIRMED_ACCESSIBLE,
                expected_accessible_count=2,
                expected_unidentified_count=0,
            )

            # The former session may be halted later; it remains historical,
            # hash-validated base evidence and must not block an incremental run.
            (base_dir / 'xhs_safety_state.json').write_text(
                json.dumps({'state': 'security_halted', 'security_halted': True, 'session_id': 'old-session'}),
                encoding='utf-8',
            )
            delta_rows = [
                {'id': self.note_d, 'page_index': 1, 'source_primary': '收藏', 'source_lists': ['收藏'], 'title': '新二'},
                {'id': self.note_c, 'page_index': 0, 'source_primary': '收藏', 'source_lists': ['收藏'], 'title': '新一'},
            ]
            self.write_run(delta_dir, rows=delta_rows, declared_count=5, session_id='new-active-session')
            visible = delta_dir / 'incremental_visible_items.json'
            scope_path = delta_dir / 'incremental_collection_scope.json'
            scope = build_incremental_collection_scope(
                run_dir=delta_dir,
                visible_out=visible,
                scope_out=scope_path,
                base_scope=base_scope,
                expected_delta_count=2,
            )

            self.assertEqual(scope['scope_kind'], INCREMENTAL)
            self.assertEqual(scope['base_count'], 2)
            self.assertEqual(scope['delta_count'], 2)
            self.assertEqual(scope['union_count'], 4)
            self.assertEqual(scope['current_declared_count'], 5)
            self.assertEqual(scope['current_unidentified_count'], 1)
            self.assertNotIn('declared_count', scope)
            self.assertEqual(scope['note_ids'], [self.note_c, self.note_d, self.note_a, self.note_b])
            self.assertEqual(scope['base_note_ids'], [self.note_a, self.note_b])
            self.assertEqual(scope['delta_note_ids'], [self.note_c, self.note_d])
            self.assertEqual(scope['current_safety_state']['state'], 'active')
            self.assertEqual(scope['current_safety_state']['session_id'], 'new-active-session')
            self.assertTrue(scope['delta_segment_evidence'][0]['manifest_sha256'])
            union_items = json.loads(visible.read_text(encoding='utf-8'))
            validate_scope_input(scope_path, union_items, stage='incremental union', require_original_visible_hash=True, items_path=visible)
            with self.assertRaisesRegex(CollectionScopeError, '集合或顺序'):
                validate_scope_input(scope_path, list(reversed(union_items)), stage='incremental reordered union')

    def test_incremental_scope_rejects_overlap_wrong_delta_count_and_tampered_diagnostic_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / 'base'
            delta_dir = root / 'delta'
            base_dir.mkdir()
            delta_dir.mkdir()
            self.write_run(base_dir, declared_count=2)
            base_visible = base_dir / 'visible_items.json'
            base_scope = base_dir / 'collection_scope.json'
            build_collection_scope(
                run_dir=base_dir,
                visible_out=base_visible,
                scope_out=base_scope,
                scope_kind=USER_CONFIRMED_ACCESSIBLE,
                expected_accessible_count=2,
                expected_unidentified_count=0,
            )
            delta_rows = [
                {'id': self.note_c, 'page_index': 0, 'source_primary': '收藏', 'source_lists': ['收藏']},
                {'id': self.note_d, 'page_index': 1, 'source_primary': '收藏', 'source_lists': ['收藏']},
            ]
            self.write_run(delta_dir, rows=delta_rows, declared_count=5, session_id='new-session')
            with self.assertRaisesRegex(CollectionScopeError, '期待计数'):
                build_incremental_collection_scope(
                    run_dir=delta_dir,
                    visible_out=delta_dir / 'wrong-count-visible.json',
                    scope_out=delta_dir / 'wrong-count-scope.json',
                    base_scope=base_scope,
                    expected_delta_count=3,
                )

            overlapping_rows = [
                {'id': self.note_a, 'page_index': 0, 'source_primary': '收藏', 'source_lists': ['收藏']},
                {'id': self.note_c, 'page_index': 1, 'source_primary': '收藏', 'source_lists': ['收藏']},
            ]
            self.write_run(delta_dir, rows=overlapping_rows, declared_count=5, session_id='new-session')
            with self.assertRaisesRegex(CollectionScopeError, '不能与 base_scope 重叠'):
                build_collection_scope(
                    run_dir=delta_dir,
                    visible_out=delta_dir / 'overlap-visible.json',
                    scope_out=delta_dir / 'overlap-scope.json',
                    scope_kind=INCREMENTAL,
                    base_scope=base_scope,
                    expected_delta_count=2,
                )

            self.write_run(delta_dir, rows=delta_rows, declared_count=5, session_id='new-session')
            visible = delta_dir / 'visible.json'
            scope_path = delta_dir / 'scope.json'
            build_incremental_collection_scope(
                run_dir=delta_dir,
                visible_out=visible,
                scope_out=scope_path,
                base_scope=base_scope,
                expected_delta_count=2,
            )
            tampered_scope = json.loads(scope_path.read_text(encoding='utf-8'))
            tampered_scope['current_unidentified_count'] = 0
            scope_path.write_text(json.dumps(tampered_scope), encoding='utf-8')
            with self.assertRaisesRegex(CollectionScopeError, '当前页面诊断计数'):
                load_scope(scope_path)


if __name__ == '__main__':
    unittest.main()
