import assert from 'node:assert/strict';
import { createHmac, timingSafeEqual } from 'node:crypto';
import test from 'node:test';

import {
  canonicalJson,
  createLaunchAttestation,
} from './launch-attestation.mjs';


function verifies({ key, action, args, payload }) {
  const attestation = payload.launch_attestation;
  const basis = {
    schema: attestation.schema,
    nonce: attestation.nonce,
    action,
    args,
    trusted_evidence: payload.trusted_evidence,
  };
  const expected = createHmac('sha256', key)
    .update(canonicalJson(basis))
    .digest();
  const provided = Buffer.from(attestation.signature, 'base64url');
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}


test('launch attestation binds action args and trusted evidence to one private key', () => {
  const args = ['--run-id', 'run-1', '--trusted-evidence-stdin', '--mcp-launch-fd', '3'];
  const inputPayload = {
    trusted_evidence: {
      schema: 'xhs_workbuddy_trusted_evidence_v1',
      receipt_id: 'receipt-1',
    },
  };
  const launch = createLaunchAttestation({
    action: 'prepare',
    args,
    inputPayload,
  });

  assert.equal(launch.key.length, 32);
  assert.equal(verifies({
    key: launch.key,
    action: 'prepare',
    args,
    payload: launch.payload,
  }), true);
  assert.equal(verifies({
    key: launch.key,
    action: 'execute',
    args,
    payload: launch.payload,
  }), false);
  assert.equal(verifies({
    key: launch.key,
    action: 'prepare',
    args: [...args, '--max-moves-per-session', '200'],
    payload: launch.payload,
  }), false);
  assert.equal(verifies({
    key: launch.key,
    action: 'prepare',
    args,
    payload: {
      ...launch.payload,
      trusted_evidence: {...inputPayload.trusted_evidence, receipt_id: 'forged'},
    },
  }), false);
});
