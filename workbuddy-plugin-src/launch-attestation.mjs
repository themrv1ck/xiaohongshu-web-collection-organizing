import {
  createHmac,
  randomBytes,
} from 'node:crypto';


export const LAUNCH_ATTESTATION_SCHEMA = 'xhs_workbuddy_launch_attestation_v1';


function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, stableValue(value[key])]),
    );
  }
  return value;
}


export function canonicalJson(value) {
  return JSON.stringify(stableValue(value));
}


export function createLaunchAttestation({ action, args, inputPayload }) {
  if (!['prepare', 'execute'].includes(action)) {
    throw new Error('mcp_launch_attestation_action_invalid');
  }
  if (!Array.isArray(args) || args.some((value) => typeof value !== 'string')) {
    throw new Error('mcp_launch_attestation_args_invalid');
  }
  if (
    !inputPayload
    || typeof inputPayload !== 'object'
    || Array.isArray(inputPayload)
    || !inputPayload.trusted_evidence
    || typeof inputPayload.trusted_evidence !== 'object'
    || Array.isArray(inputPayload.trusted_evidence)
  ) {
    throw new Error('mcp_launch_attestation_evidence_missing');
  }
  const key = randomBytes(32);
  const nonce = randomBytes(18).toString('base64url');
  const basis = {
    schema: LAUNCH_ATTESTATION_SCHEMA,
    nonce,
    action,
    args: [...args],
    trusted_evidence: inputPayload.trusted_evidence,
  };
  const signature = createHmac('sha256', key)
    .update(canonicalJson(basis))
    .digest('base64url');
  return {
    key,
    payload: {
      ...inputPayload,
      launch_attestation: {
        schema: LAUNCH_ATTESTATION_SCHEMA,
        nonce,
        signature,
      },
    },
  };
}
