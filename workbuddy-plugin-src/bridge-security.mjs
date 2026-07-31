const SENSITIVE_KEY = (
  '(?:xsec_token|xsec_source|sign|signature|authorization|cookie|set-cookie|web_session|a1)'
);
const ENCODED_SENSITIVE_KEY = (
  '(?:xsec(?:_|%5f)(?:token|source)|sign|signature|authorization|cookie|set-cookie|web(?:_|%5f)session|a1)'
);


export function redactSensitiveText(value) {
  let text = value instanceof Error
    ? String(value.message || value.name || 'Error')
    : String(value ?? '');

  text = text.replace(/https?:\/\/[^\s"'<>]+/gi, (url) => {
    const queryIndex = url.search(/[?#]/);
    return queryIndex === -1
      ? url
      : `${url.slice(0, queryIndex)}?<redacted_query>`;
  });
  text = text.replace(
    new RegExp(`(["']?${SENSITIVE_KEY}["']?\\s*:\\s*)(?:"[^"]*"|'[^']*')`, 'gi'),
    '$1"<redacted>"',
  );
  text = text.replace(
    new RegExp(`(["']?${SENSITIVE_KEY}["']?\\s*:\\s*)[^,}\\r\\n]+`, 'gi'),
    '$1<redacted>',
  );
  text = text.replace(
    new RegExp(`(${SENSITIVE_KEY}\\s*=\\s*)[^&\\s,}]+`, 'gi'),
    '$1<redacted>',
  );
  text = text.replace(
    new RegExp(`(${ENCODED_SENSITIVE_KEY}%3d)[^&\\s,}]+`, 'gi'),
    '$1<redacted>',
  );
  text = text.replace(
    new RegExp(`(--(?:xsec-token|xsec-source|sign|signature|authorization|cookie)\\s+)[^\\s]+`, 'gi'),
    '$1<redacted>',
  );
  return text.replace(/\s+/g, ' ').trim().slice(0, 1000);
}


export function safeBridgeError(value, fallback = '固定工作流失败。') {
  const message = redactSensitiveText(value) || fallback;
  return new Error(message);
}


export function parseBridgeResult(stdout, stderr, code) {
  const stdoutText = String(stdout ?? '');
  const stderrText = String(stderr ?? '');
  const lines = stdoutText.trim().split(/\r?\n/).filter(Boolean);
  let payload;
  try {
    payload = JSON.parse(lines.at(-1) || '{}');
  } catch {
    throw safeBridgeError(
      `桥接器返回了无效 JSON：${stdoutText.trim() || stderrText.trim()}`,
      '桥接器返回了无效 JSON。',
    );
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw safeBridgeError('桥接器返回了无效 JSON 对象。');
  }
  if (code !== 0 || payload.ok === false) {
    throw safeBridgeError(
      payload.error || stderrText.trim() || stdoutText.trim() || `bridge exit=${code}`,
      `bridge exit=${code}`,
    );
  }
  return payload;
}
