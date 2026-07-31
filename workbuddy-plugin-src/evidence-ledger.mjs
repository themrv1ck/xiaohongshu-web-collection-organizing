import {
  createHash,
  createHmac,
  randomBytes,
  randomUUID,
  timingSafeEqual,
} from 'node:crypto';
import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
} from 'node:fs';
import path from 'node:path';


const RECEIPT_PREFIX = 'xhs1';
const RUN_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const ARTIFACT_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const STAGE_TRANSITIONS = new Map([
  ['capture', 'inventory'],
  ['inventory', 'plan'],
]);


function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, stableValue(value[key])]),
    );
  }
  return value;
}


function canonicalJson(value) {
  return JSON.stringify(stableValue(value));
}


function normalizedBindings(value) {
  const bindings = {
    user_id: String(value?.user_id || '').trim().toLowerCase(),
    page_binding: String(value?.page_binding || '').trim(),
    source: String(value?.source || '').trim(),
    organizing_depth: String(value?.organizing_depth || '').trim(),
  };
  if (!/^[0-9a-f]{24}$/.test(bindings.user_id)) {
    throw new Error('receipt_binding_invalid:user_id');
  }
  if (!bindings.page_binding.startsWith('https://')) {
    throw new Error('receipt_binding_invalid:page_binding');
  }
  if (!['collection', 'liked'].includes(bindings.source)) {
    throw new Error('receipt_binding_invalid:source');
  }
  if (!['quick', 'light'].includes(bindings.organizing_depth)) {
    throw new Error('receipt_binding_invalid:organizing_depth');
  }
  return bindings;
}


function expectedBindingsMatch(actual, expected) {
  if (!expected || typeof expected !== 'object' || Array.isArray(expected)) {
    throw new Error('receipt_binding_invalid');
  }
  const keys = Object.keys(expected);
  if (keys.length === 0) throw new Error('receipt_binding_invalid');
  const allowed = new Set([
    'user_id',
    'page_binding',
    'source',
    'organizing_depth',
  ]);
  if (keys.some((key) => !allowed.has(key))) {
    throw new Error('receipt_binding_invalid');
  }
  const candidate = normalizedBindings({ ...actual, ...expected });
  return keys.every((key) => candidate[key] === actual[key]);
}


function pathInside(candidate, parent) {
  const relative = path.relative(parent, candidate);
  return Boolean(relative) && !relative.startsWith('..') && !path.isAbsolute(relative);
}


function checkedRunId(value) {
  const runId = String(value || '').trim();
  if (!RUN_ID_RE.test(runId) || runId.includes('..')) {
    throw new Error('receipt_run_id_invalid');
  }
  return runId;
}


function checkedArtifactNames(values) {
  if (!Array.isArray(values) || values.length === 0) {
    throw new Error('receipt_artifacts_missing');
  }
  const names = values.map((value) => String(value || '').trim());
  if (names.some((name) => (
    !ARTIFACT_RE.test(name)
    || name.includes('..')
    || path.basename(name) !== name
  ))) {
    throw new Error('receipt_artifact_name_invalid');
  }
  if (new Set(names).size !== names.length) {
    throw new Error('receipt_artifact_duplicate');
  }
  return names.sort();
}


function sameOpenFile(before, after) {
  return (
    before.dev === after.dev
    && before.ino === after.ino
    && before.size === after.size
    && before.mtimeMs === after.mtimeMs
  );
}


function hashRegularFile(runDirectory, name) {
  const filePath = path.join(runDirectory, name);
  const pathStat = lstatSync(filePath);
  if (pathStat.isSymbolicLink() || !pathStat.isFile()) {
    throw new Error(`receipt_artifact_unsafe:${name}`);
  }
  const realFile = realpathSync(filePath);
  if (path.dirname(realFile) !== runDirectory) {
    throw new Error(`receipt_artifact_path_escape:${name}`);
  }
  const noFollow = Number(constants.O_NOFOLLOW || 0);
  const descriptor = openSync(realFile, constants.O_RDONLY | noFollow);
  try {
    const before = fstatSync(descriptor);
    const bytes = readFileSync(descriptor);
    const after = fstatSync(descriptor);
    if (!sameOpenFile(before, after)) {
      throw new Error(`evidence_changed:${name}`);
    }
    if (realpathSync(filePath) !== realFile) {
      throw new Error(`evidence_changed:${name}`);
    }
    const artifact = {
      name,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      size: bytes.length,
    };
    if (name === 'xhs_safety_state.json') {
      try {
        artifact.safety_state = JSON.parse(bytes.toString('utf8'));
      } catch {
        throw new Error('receipt_safety_state_invalid');
      }
      if (
        !artifact.safety_state
        || typeof artifact.safety_state !== 'object'
        || Array.isArray(artifact.safety_state)
      ) {
        throw new Error('receipt_safety_state_invalid');
      }
    }
    return artifact;
  } finally {
    closeSync(descriptor);
  }
}


function immutableReceiptBasis(record) {
  return {
    version: 1,
    instance_id: record.instanceId,
    receipt_id: record.id,
    parent_receipt_id: record.parentId,
    run_id: record.runId,
    stage: record.stage,
    bindings: record.bindings,
    artifacts: record.artifacts,
  };
}


function trustedEvidence(record) {
  return {
    schema: 'xhs_workbuddy_trusted_evidence_v1',
    receipt_id: record.id,
    run_id: record.runId,
    stage: record.stage,
    bindings: { ...record.bindings },
    artifacts: Object.fromEntries(
      record.artifacts.map((artifact) => [artifact.name, {
        sha256: artifact.sha256,
        size: artifact.size,
      }]),
    ),
  };
}


export function createEvidenceLedger({ runsRoot }) {
  if (!runsRoot) throw new Error('receipt_runs_root_missing');
  mkdirSync(runsRoot, { recursive: true });
  const trustedRunsRoot = realpathSync(runsRoot);
  const secret = randomBytes(32);
  const instanceId = randomUUID();
  const records = new Map();
  const runHeads = new Map();

  function runDirectory(runId) {
    const checked = checkedRunId(runId);
    const candidate = path.join(trustedRunsRoot, checked);
    const stat = lstatSync(candidate);
    if (stat.isSymbolicLink() || !stat.isDirectory()) {
      throw new Error('receipt_run_directory_unsafe');
    }
    const resolved = realpathSync(candidate);
    if (!pathInside(resolved, trustedRunsRoot)) {
      throw new Error('receipt_run_path_escape');
    }
    return resolved;
  }

  function currentArtifacts(runId, artifactNames) {
    const directory = runDirectory(runId);
    return checkedArtifactNames(artifactNames).map(
      (name) => hashRegularFile(directory, name),
    );
  }

  function macFor(record) {
    return createHmac('sha256', secret)
      .update(canonicalJson(immutableReceiptBasis(record)))
      .digest();
  }

  function tokenFor(record) {
    return `${RECEIPT_PREFIX}.${record.id}.${macFor(record).toString('base64url')}`;
  }

  function recordFor(receipt) {
    const [prefix, id, encodedMac, ...extra] = String(receipt || '').split('.');
    if (prefix !== RECEIPT_PREFIX || !id || !encodedMac || extra.length) {
      throw new Error('receipt_invalid');
    }
    const record = records.get(id);
    if (!record || record.instanceId !== instanceId) {
      throw new Error('receipt_expired_or_foreign');
    }
    let provided;
    try {
      provided = Buffer.from(encodedMac, 'base64url');
    } catch {
      throw new Error('receipt_invalid');
    }
    if (provided.toString('base64url') !== encodedMac) {
      throw new Error('receipt_invalid');
    }
    const expected = macFor(record);
    if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) {
      throw new Error('receipt_invalid');
    }
    return record;
  }

  function assertExpected(record, { expectedStage, runId, bindings }) {
    const checkedRun = checkedRunId(runId);
    if (record.stage !== expectedStage) throw new Error('receipt_stage_mismatch');
    if (record.runId !== checkedRun) throw new Error('receipt_run_mismatch');
    if (!expectedBindingsMatch(record.bindings, bindings)) {
      throw new Error('receipt_binding_mismatch');
    }
    if (runHeads.get(record.runId) !== record.id) throw new Error('receipt_not_current');
  }

  function validSafetyStateProgress(before, after) {
    if (
      before?.state !== 'active'
      || before?.security_halted !== false
      || after?.state !== 'active'
      || after?.security_halted !== false
      || typeof before.session_id !== 'string'
      || !before.session_id
      || after.session_id !== before.session_id
      || after.schema_version !== before.schema_version
      || after.created_at !== before.created_at
      || after.halt !== null
      || after.last_stage !== 'board_snapshot'
      || typeof after.updated_at !== 'string'
      || after.updated_at < String(before.updated_at || '')
    ) {
      return false;
    }
    const beforeCheckpoints = before.checkpoints;
    const afterCheckpoints = after.checkpoints;
    if (
      !Array.isArray(beforeCheckpoints)
      || !Array.isArray(afterCheckpoints)
      || afterCheckpoints.length !== beforeCheckpoints.length + 1
      || canonicalJson(afterCheckpoints.slice(0, -1)) !== canonicalJson(beforeCheckpoints)
    ) {
      return false;
    }
    const added = afterCheckpoints.at(-1);
    if (
      !added
      || added.stage !== 'board_snapshot'
      || added.event !== 'operation_started'
    ) {
      return false;
    }
    const policy = after.policy;
    if (
      !policy
      || typeof policy !== 'object'
      || Array.isArray(policy)
      || policy.auto_scroll !== false
      || policy.auto_navigation !== false
      || policy.auto_retry !== false
      || policy.read_only !== true
    ) {
      return false;
    }
    const allowedChangedKeys = new Set([
      'updated_at',
      'last_stage',
      'policy',
      'checkpoints',
    ]);
    const allKeys = new Set([...Object.keys(before), ...Object.keys(after)]);
    for (const key of allKeys) {
      if (
        !allowedChangedKeys.has(key)
        && canonicalJson(before[key]) !== canonicalJson(after[key])
      ) {
        return false;
      }
    }
    return true;
  }

  function assertArtifactsUnchanged(record, { allowSafetyStateProgress = false } = {}) {
    const actual = currentArtifacts(
      record.runId,
      record.artifacts.map((artifact) => artifact.name),
    );
    const actualByName = new Map(actual.map((artifact) => [artifact.name, artifact]));
    for (const expected of record.artifacts) {
      const current = actualByName.get(expected.name);
      if (canonicalJson(current) === canonicalJson(expected)) continue;
      if (
        allowSafetyStateProgress
        && expected.name === 'xhs_safety_state.json'
        && validSafetyStateProgress(expected.safety_state, current?.safety_state)
      ) {
        continue;
      }
      throw new Error('evidence_changed');
    }
    return actual;
  }

  function issueRecord({ runId, stage, bindings, artifactNames, parentId = null }) {
    const checkedRun = checkedRunId(runId);
    const record = {
      id: randomBytes(18).toString('base64url'),
      instanceId,
      parentId,
      runId: checkedRun,
      stage,
      bindings: normalizedBindings(bindings),
      artifacts: currentArtifacts(checkedRun, artifactNames),
      state: 'issued',
    };
    records.set(record.id, record);
    runHeads.set(checkedRun, record.id);
    return {
      receipt: tokenFor(record),
      trustedEvidence: trustedEvidence(record),
    };
  }

  return {
    issue({ runId, stage, bindings, artifactNames }) {
      if (stage !== 'capture') throw new Error('receipt_root_stage_invalid');
      const checkedRun = checkedRunId(runId);
      if (runHeads.has(checkedRun)) throw new Error('receipt_run_already_attested');
      return issueRecord({ runId: checkedRun, stage, bindings, artifactNames });
    },

    begin({ receipt, expectedStage, runId, bindings }) {
      const record = recordFor(receipt);
      assertExpected(record, { expectedStage, runId, bindings });
      if (record.state !== 'issued') throw new Error('receipt_already_used');
      assertArtifactsUnchanged(record);
      record.state = 'in_flight';
      return trustedEvidence(record);
    },

    abort(receipt) {
      const record = recordFor(receipt);
      if (record.state === 'in_flight') record.state = 'issued';
    },

    advance({
      receipt,
      nextStage,
      artifactNames,
      allowSafetyStateProgress = false,
    }) {
      const parent = recordFor(receipt);
      if (parent.state !== 'in_flight') throw new Error('receipt_transition_not_started');
      if (runHeads.get(parent.runId) !== parent.id) throw new Error('receipt_not_current');
      if (STAGE_TRANSITIONS.get(parent.stage) !== nextStage) {
        throw new Error('receipt_stage_transition_invalid');
      }
      assertArtifactsUnchanged(parent, { allowSafetyStateProgress });
      const issued = issueRecord({
        runId: parent.runId,
        stage: nextStage,
        bindings: parent.bindings,
        artifactNames,
        parentId: parent.id,
      });
      parent.state = 'consumed';
      return issued;
    },

    commit(receipt) {
      const record = recordFor(receipt);
      if (record.state !== 'in_flight') {
        throw new Error('receipt_commit_not_started');
      }
      if (runHeads.get(record.runId) !== record.id) {
        throw new Error('receipt_not_current');
      }
      assertArtifactsUnchanged(record);
      record.state = 'consumed';
    },

    consume({ receipt, expectedStage, runId, bindings }) {
      const evidence = this.begin({
        receipt,
        expectedStage,
        runId,
        bindings,
      });
      this.commit(receipt);
      return evidence;
    },
  };
}
