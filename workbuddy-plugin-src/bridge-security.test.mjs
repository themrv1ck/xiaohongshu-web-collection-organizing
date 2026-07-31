import assert from 'node:assert/strict';
import test from 'node:test';

import {
  parseBridgeResult,
  safeBridgeError,
} from './bridge-security.mjs';


const forbidden = [
  'xsec-json-secret',
  'sign-json-secret',
  'cookie-stderr-secret',
  'authorization-exception-secret',
  'a1-bridge-secret',
  'web-session-bridge-secret',
];


function assertSecretsRemoved(message) {
  for (const secret of forbidden) {
    assert.doesNotMatch(message, new RegExp(secret));
  }
}


test('bridge error JSON is redacted before it reaches MCP', () => {
  assert.throws(
    () => parseBridgeResult(
      `${JSON.stringify({
        ok: false,
        error: (
          'failed https://www.xiaohongshu.com/explore/abc'
          + '?xsec_token=xsec-json-secret&sign=sign-json-secret'
        ),
      })}\n`,
      '',
      1,
    ),
    (error) => {
      assertSecretsRemoved(error.message);
      assert.match(error.message, /<redacted_query>/);
      return true;
    },
  );
});


test('bridge stderr fallback is redacted before it reaches MCP', () => {
  assert.throws(
    () => parseBridgeResult('{"ok":true}\n', 'Cookie: cookie-stderr-secret\n', 2),
    (error) => {
      assertSecretsRemoved(error.message);
      assert.match(error.message, /<redacted>/);
      return true;
    },
  );
});


test('bridge exceptions are redacted before they reach MCP', () => {
  const error = safeBridgeError(
    new Error('Authorization: Bearer authorization-exception-secret'),
  );
  assertSecretsRemoved(error.message);
  assert.match(error.message, /<redacted>/);
});


test('invalid bridge stdout never returns credentials verbatim', () => {
  assert.throws(
    () => parseBridgeResult(
      'not-json xsec_token=xsec-json-secret sign=sign-json-secret '
        + 'a1=a1-bridge-secret web_session=web-session-bridge-secret',
      '',
      1,
    ),
    (error) => {
      assertSecretsRemoved(error.message);
      assert.match(error.message, /<redacted>/);
      return true;
    },
  );
});


test('ordinary a1 text is not treated as a cookie without assignment syntax', () => {
  const error = safeBridgeError(new Error('型号 a1 适合日常使用'));
  assert.equal(error.message, '型号 a1 适合日常使用');
});
