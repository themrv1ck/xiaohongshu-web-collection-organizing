import assert from 'node:assert/strict';
import {
  mkdtempSync,
  mkdirSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { createEvidenceLedger } from './evidence-ledger.mjs';


function fixture() {
  const dataRoot = mkdtempSync(path.join(os.tmpdir(), 'xhs-evidence-ledger-'));
  const runsRoot = path.join(dataRoot, 'runs');
  const runId = 'run-1';
  const runDir = path.join(runsRoot, runId);
  mkdirSync(runDir, { recursive: true });
  writeFileSync(path.join(runDir, 'visible_items.json'), '[{"id":"note-1"}]');
  writeFileSync(path.join(runDir, 'crawl_manifest.json'), '{"item_count":1}');
  return { runsRoot, runId, runDir };
}


const bindings = {
  user_id: '66d19b54000000001d03a93d',
  page_binding: 'https://www.xiaohongshu.com/user/profile/66d19b54000000001d03a93d',
  source: 'collection',
  organizing_depth: 'quick',
};


test('signed capture evidence rejects data plus manifest hash tampering', () => {
  const { runsRoot, runId, runDir } = fixture();
  const ledger = createEvidenceLedger({ runsRoot });
  const issued = ledger.issue({
    runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });

  writeFileSync(path.join(runDir, 'visible_items.json'), '[{"id":"forged"}]');
  writeFileSync(
    path.join(runDir, 'crawl_manifest.json'),
    '{"item_count":1,"visible_items_sha256":"forged-too"}',
  );

  assert.throws(
    () => ledger.begin({
      receipt: issued.receipt,
      expectedStage: 'capture',
      runId,
      bindings,
    }),
    /evidence_changed/,
  );
});


test('advance cannot re-sign parent evidence changed after begin', () => {
  const { runsRoot, runId, runDir } = fixture();
  const ledger = createEvidenceLedger({ runsRoot });
  const capture = ledger.issue({
    runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });
  ledger.begin({
    receipt: capture.receipt,
    expectedStage: 'capture',
    runId,
    bindings,
  });
  writeFileSync(path.join(runDir, 'visible_items.json'), '[{"id":"forged"}]');
  writeFileSync(path.join(runDir, 'board_snapshot.json'), '{}');

  assert.throws(
    () => ledger.advance({
      receipt: capture.receipt,
      nextStage: 'inventory',
      artifactNames: [
        'visible_items.json',
        'crawl_manifest.json',
        'board_snapshot.json',
      ],
    }),
    /evidence_changed/,
  );
});


test('advance permits only the declared monotonic safety-state progress', () => {
  const { runsRoot, runId, runDir } = fixture();
  const safetyPath = path.join(runDir, 'xhs_safety_state.json');
  const initialSafety = {
    schema_version: 1,
    session_id: 'session-1',
    state: 'active',
    security_halted: false,
    created_at: '2026-07-31T00:00:00Z',
    updated_at: '2026-07-31T00:00:00Z',
    last_stage: 'capture',
    policy: {capture_mode: 'workbuddy_segmented'},
    halt: null,
    checkpoints: [{
      at: '2026-07-31T00:00:00Z',
      stage: 'capture',
      event: 'session_started',
    }],
  };
  writeFileSync(safetyPath, JSON.stringify(initialSafety));
  const ledger = createEvidenceLedger({ runsRoot });
  const capture = ledger.issue({
    runId,
    stage: 'capture',
    bindings,
    artifactNames: [
      'visible_items.json',
      'crawl_manifest.json',
      'xhs_safety_state.json',
    ],
  });
  ledger.begin({
    receipt: capture.receipt,
    expectedStage: 'capture',
    runId,
    bindings,
  });
  writeFileSync(safetyPath, JSON.stringify({
    ...initialSafety,
    updated_at: '2026-07-31T00:01:00Z',
    last_stage: 'board_snapshot',
    policy: {
      ...initialSafety.policy,
      auto_scroll: false,
      auto_navigation: false,
      auto_retry: false,
      read_only: true,
    },
    checkpoints: [...initialSafety.checkpoints, {
      at: '2026-07-31T00:01:00Z',
      stage: 'board_snapshot',
      event: 'operation_started',
    }],
  }));
  writeFileSync(path.join(runDir, 'board_snapshot.json'), '{}');

  const inventory = ledger.advance({
    receipt: capture.receipt,
    nextStage: 'inventory',
    artifactNames: [
      'visible_items.json',
      'crawl_manifest.json',
      'xhs_safety_state.json',
      'board_snapshot.json',
    ],
    allowSafetyStateProgress: true,
  });
  assert.equal(inventory.trustedEvidence.stage, 'inventory');
});


test('capture inventory and plan receipts form a one-way single-use chain', () => {
  const { runsRoot, runId, runDir } = fixture();
  const ledger = createEvidenceLedger({ runsRoot });
  const capture = ledger.issue({
    runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });

  ledger.begin({
    receipt: capture.receipt,
    expectedStage: 'capture',
    runId,
    bindings,
  });
  writeFileSync(path.join(runDir, 'board_snapshot.json'), '{"mode":"read_only"}');
  const inventory = ledger.advance({
    receipt: capture.receipt,
    nextStage: 'inventory',
    artifactNames: [
      'visible_items.json',
      'crawl_manifest.json',
      'board_snapshot.json',
    ],
  });

  ledger.begin({
    receipt: inventory.receipt,
    expectedStage: 'inventory',
    runId,
    bindings,
  });
  writeFileSync(path.join(runDir, 'classification.json'), '[]');
  writeFileSync(path.join(runDir, 'created_boards.json'), '{"boards":[]}');
  writeFileSync(path.join(runDir, 'run_report.json'), '{"mode":"dry_run"}');
  const plan = ledger.advance({
    receipt: inventory.receipt,
    nextStage: 'plan',
    artifactNames: [
      'visible_items.json',
      'crawl_manifest.json',
      'board_snapshot.json',
      'classification.json',
      'created_boards.json',
      'run_report.json',
    ],
  });

  const trusted = ledger.consume({
    receipt: plan.receipt,
    expectedStage: 'plan',
    runId,
    bindings,
  });
  assert.equal(trusted.stage, 'plan');
  assert.equal(trusted.run_id, runId);
  assert.throws(
    () => ledger.consume({
      receipt: plan.receipt,
      expectedStage: 'plan',
      runId,
      bindings,
    }),
    /receipt_already_used/,
  );
});


test('an in-flight receipt can retry until preflight commits it', () => {
  const { runsRoot, runId } = fixture();
  const ledger = createEvidenceLedger({ runsRoot });
  const issued = ledger.issue({
    runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });

  ledger.begin({
    receipt: issued.receipt,
    expectedStage: 'capture',
    runId,
    bindings,
  });
  ledger.abort(issued.receipt);
  assert.doesNotThrow(() => ledger.begin({
    receipt: issued.receipt,
    expectedStage: 'capture',
    runId,
    bindings,
  }));
  ledger.commit(issued.receipt);
  assert.throws(
    () => ledger.begin({
      receipt: issued.receipt,
      expectedStage: 'capture',
      runId,
      bindings,
    }),
    /receipt_already_used/,
  );
});


test('commit rejects artifacts changed after begin without consuming the receipt', () => {
  const { runsRoot, runId, runDir } = fixture();
  const artifactPath = path.join(runDir, 'visible_items.json');
  const originalArtifact = '[{"id":"note-1"}]';
  const ledger = createEvidenceLedger({ runsRoot });
  const issued = ledger.issue({
    runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });

  ledger.begin({
    receipt: issued.receipt,
    expectedStage: 'capture',
    runId,
    bindings,
  });
  writeFileSync(artifactPath, '[{"id":"changed-after-begin"}]');

  assert.throws(
    () => ledger.commit(issued.receipt),
    /evidence_changed/,
  );

  ledger.abort(issued.receipt);
  writeFileSync(artifactPath, originalArtifact);
  assert.doesNotThrow(() => ledger.begin({
    receipt: issued.receipt,
    expectedStage: 'capture',
    runId,
    bindings,
  }));
  assert.doesNotThrow(() => ledger.commit(issued.receipt));
});


test('a receipt cannot be edited or used for another run', () => {
  const first = fixture();
  const secondRun = path.join(first.runsRoot, 'run-2');
  mkdirSync(secondRun);
  writeFileSync(path.join(secondRun, 'visible_items.json'), '[]');
  writeFileSync(path.join(secondRun, 'crawl_manifest.json'), '{}');
  const ledger = createEvidenceLedger({ runsRoot: first.runsRoot });
  const issued = ledger.issue({
    runId: first.runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  const canonicalLast = issued.receipt.at(-1);
  const canonicalIndex = alphabet.indexOf(canonicalLast);
  const tampered = `${issued.receipt.slice(0, -1)}${alphabet[(canonicalIndex + 4) % 64]}`;

  assert.throws(
    () => ledger.begin({
      receipt: tampered,
      expectedStage: 'capture',
      runId: first.runId,
      bindings,
    }),
    /receipt_invalid/,
  );
  assert.throws(
    () => ledger.begin({
      receipt: issued.receipt,
      expectedStage: 'capture',
      runId: 'run-2',
      bindings,
    }),
    /receipt_run_mismatch/,
  );
});


test('a non-canonical base64url alias of the same MAC is rejected', () => {
  const first = fixture();
  const ledger = createEvidenceLedger({ runsRoot: first.runsRoot });
  const issued = ledger.issue({
    runId: first.runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  const [prefix, id, encodedMac] = issued.receipt.split('.');
  const canonicalIndex = alphabet.indexOf(encodedMac.at(-1));
  assert.equal(canonicalIndex & 3, 0);
  const aliasMac = `${encodedMac.slice(0, -1)}${alphabet[canonicalIndex + 1]}`;
  assert.deepEqual(Buffer.from(aliasMac, 'base64url'), Buffer.from(encodedMac, 'base64url'));

  assert.throws(
    () => ledger.begin({
      receipt: `${prefix}.${id}.${aliasMac}`,
      expectedStage: 'capture',
      runId: first.runId,
      bindings,
    }),
    /receipt_invalid/,
  );
});


test('receipts expire when the MCP ledger restarts', () => {
  const { runsRoot, runId } = fixture();
  const firstLedger = createEvidenceLedger({ runsRoot });
  const issued = firstLedger.issue({
    runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });
  const restartedLedger = createEvidenceLedger({ runsRoot });

  assert.throws(
    () => restartedLedger.begin({
      receipt: issued.receipt,
      expectedStage: 'capture',
      runId,
      bindings,
    }),
    /receipt_expired_or_foreign/,
  );
});


test('the next stage may verify account and page while source and depth stay private', () => {
  const { runsRoot, runId } = fixture();
  const ledger = createEvidenceLedger({ runsRoot });
  const issued = ledger.issue({
    runId,
    stage: 'capture',
    bindings,
    artifactNames: ['visible_items.json', 'crawl_manifest.json'],
  });

  assert.doesNotThrow(() => ledger.begin({
    receipt: issued.receipt,
    expectedStage: 'capture',
    runId,
    bindings: {
      user_id: bindings.user_id,
      page_binding: bindings.page_binding,
    },
  }));
  ledger.abort(issued.receipt);
  assert.throws(
    () => ledger.begin({
      receipt: issued.receipt,
      expectedStage: 'capture',
      runId,
      bindings: {
        user_id: '66d19b54000000001d03a93e',
        page_binding: bindings.page_binding,
      },
    }),
    /receipt_binding_mismatch/,
  );
});


test('artifact and run-directory symlinks are never attested', () => {
  const first = fixture();
  const outside = path.join(path.dirname(first.runsRoot), 'outside.json');
  writeFileSync(outside, '{}');
  symlinkSync(outside, path.join(first.runDir, 'linked.json'));
  const ledger = createEvidenceLedger({ runsRoot: first.runsRoot });

  assert.throws(
    () => ledger.issue({
      runId: first.runId,
      stage: 'capture',
      bindings,
      artifactNames: ['linked.json'],
    }),
    /receipt_artifact_unsafe/,
  );

  const linkedRun = path.join(first.runsRoot, 'linked-run');
  symlinkSync(first.runDir, linkedRun);
  assert.throws(
    () => ledger.issue({
      runId: 'linked-run',
      stage: 'capture',
      bindings,
      artifactNames: ['visible_items.json'],
    }),
    /receipt_run_directory_unsafe/,
  );
});
