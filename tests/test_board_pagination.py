#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from create_board import build_create_board_job  # noqa: E402
from run_reassign_batch import (  # noqa: E402
    BOARD_LIST_PAGINATION_JS,
    build_browser_job,
)
from verify_board_membership import build_snapshot_job  # noqa: E402


class BoardPaginationTests(unittest.TestCase):
    USER_ID = 'f' * 24

    def run_js(self, scenario):
        completed = subprocess.run(
            ['node', '-e', BOARD_LIST_PAGINATION_JS + '\n' + scenario],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_reads_complete_board_counts_at_page_boundaries(self):
        for total in (100, 101, 181, 200, 201):
            with self.subTest(total=total):
                result = self.run_js(f'''
function boardAt(index) {{
  return {{
    id: index.toString(16).padStart(24, '0'),
    name: 'board-' + index,
    privacy: 0,
    total: index
  }};
}}
(async function() {{
  const total = {total};
  const calls = [];
  const api = {{
    yC: async function(options) {{
      calls.push(options.params);
      const start = (options.params.page - 1) * options.params.num;
      const end = Math.min(total, start + options.params.num);
      const boards = [];
      for (let index = start; index < end; index += 1) boards.push(boardAt(index));
      return {{boards, boardCount: total}};
    }}
  }};
  const result = await loadAllBoardsStrict(api, '{self.USER_ID}');
  console.log(JSON.stringify({{
    boardCount: result.boardCount,
    boardLength: result.boards.length,
    pageCount: result.pageCount,
    firstId: result.boards[0].id,
    lastId: result.boards[result.boards.length - 1].id,
    calls
  }}));
}})().catch(function(error) {{ console.error(error); process.exit(1); }});
''')
                expected_pages = (total + 99) // 100
                self.assertEqual(result['boardCount'], total)
                self.assertEqual(result['boardLength'], total)
                self.assertEqual(result['pageCount'], expected_pages)
                self.assertEqual(
                    result['calls'],
                    [
                        {'userId': self.USER_ID, 'num': 100, 'page': page}
                        for page in range(1, expected_pages + 1)
                    ],
                )

    def failure_for(self, api_body):
        return self.run_js(f'''
function boardAt(index) {{
  return {{id: index.toString(16).padStart(24, '0'), name: 'board-' + index}};
}}
(async function() {{
  const api = {{yC: async function(options) {{ {api_body} }} }};
  try {{
    await loadAllBoardsStrict(api, '{self.USER_ID}');
    console.log(JSON.stringify({{error: ''}}));
  }} catch (error) {{
    console.log(JSON.stringify({{error: error.message}}));
  }}
}})();
''')['error']

    def test_rejects_missing_page_data(self):
        error = self.failure_for('''
const total = 181;
const start = (options.params.page - 1) * 100;
const end = options.params.page === 2 ? 180 : Math.min(total, start + 100);
const boards = [];
for (let index = start; index < end; index += 1) boards.push(boardAt(index));
return {boards, boardCount: total};
''')
        self.assertIn('page 2 length mismatch: expected 81, got 80', error)

    def test_rejects_duplicate_data_across_pages(self):
        error = self.failure_for('''
const total = 101;
if (options.params.page === 1) {
  return {boards: Array.from({length: 100}, (_, index) => boardAt(index)), boardCount: total};
}
return {boards: [boardAt(0)], boardCount: total};
''')
        self.assertIn('duplicate board id or name on page 2', error)

    def test_rejects_board_count_changes(self):
        error = self.failure_for('''
const first = options.params.page === 1;
const total = first ? 181 : 180;
const start = (options.params.page - 1) * 100;
const end = Math.min(181, start + (first ? 100 : 81));
const boards = [];
for (let index = start; index < end; index += 1) boards.push(boardAt(index));
return {boards, boardCount: total};
''')
        self.assertIn('boardCount changed during pagination: expected 181, got 180', error)

    def test_rejects_missing_authoritative_board_count(self):
        error = self.failure_for('''
return {boards: Array.from({length: 100}, (_, index) => boardAt(index))};
''')
        self.assertIn('boardCount must be a non-negative integer', error)

    def test_read_create_and_move_jobs_share_the_strict_paginator(self):
        create_args = argparse.Namespace(
            name='其他', desc='', privacy=0, execute=False,
            user_id=self.USER_ID, verify_pages=10,
            arc_tab_marker='marker',
            arc_expected_url_substring='/user/profile/',
        )
        move_args = argparse.Namespace(
            allow_low_confidence=False,
            verify_pages=10,
            user_id=self.USER_ID,
            arc_tab_marker='',
            expected_url_substring='',
            arc_expected_url_substring='',
        )
        jobs = {
            'read': build_snapshot_job(
                self.USER_ID, 10, '', '/user/profile/'
            ),
            'create': build_create_board_job(create_args),
            'move': build_browser_job([], move_args),
        }
        for label, job in jobs.items():
            with self.subTest(path=label):
                self.assertIn(BOARD_LIST_PAGINATION_JS, job)
                self.assertIn('loadAllBoardsStrict(', job)
                self.assertNotIn('boardsFromInitialState', job)
                subprocess.run(
                    ['node', '-e', 'new Function(process.argv[1]);', job],
                    cwd=str(ROOT),
                    check=True,
                    capture_output=True,
                    text=True,
                )


if __name__ == '__main__':
    unittest.main()
