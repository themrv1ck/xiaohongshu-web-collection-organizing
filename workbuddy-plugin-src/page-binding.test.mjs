import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canonicalPageBinding,
  receiptBindingsForPage,
} from './page-binding.mjs';


const userId = '66d19b54000000001d03a93d';
const base = `https://www.xiaohongshu.com/user/profile/${userId}`;


test('page binding preserves tab and enforces its source', () => {
  assert.equal(
    canonicalPageBinding(`${base}?tab=fav&subTab=note`, 'collection'),
    `${base}?tab=fav`,
  );
  assert.equal(
    canonicalPageBinding(`${base}?tab=like`, 'liked'),
    `${base}?tab=like`,
  );
  assert.throws(
    () => canonicalPageBinding(`${base}?tab=liked`, 'collection'),
    /page_source_tab_mismatch/,
  );
  assert.throws(
    () => canonicalPageBinding(`${base}?tab=fav`, 'liked'),
    /page_source_tab_mismatch/,
  );
  assert.deepEqual(
    receiptBindingsForPage(userId, `${base}?tab=liked`),
    {
      user_id: userId,
      page_binding: `${base}?tab=liked`,
      source: 'liked',
    },
  );
});
