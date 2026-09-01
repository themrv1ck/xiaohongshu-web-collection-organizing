import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import path from 'node:path';
import process from 'node:process';


const root = path.resolve(import.meta.dirname, '..');
const data = path.resolve(
  process.env.XHS_SMOKE_DATA || path.join('/tmp', 'xhs-workbuddy-mcp-smoke'),
);
const transport = new StdioClientTransport({
  command: 'node',
  args: [
    path.join(root, 'server', 'xhs-workbuddy-mcp.mjs'),
    data,
  ],
  cwd: root,
  env: {
    ...process.env,
    XHS_HOST: 'workbuddy',
    XHS_WORKBUDDY_PLATFORM: process.platform,
    XHS_SKILL_ROOT: root,
    CODEBUDDY_PLUGIN_DATA: data,
    XHS_PLAYWRIGHT_PROFILE: path.join(data, 'playwright-profile'),
    XHS_PYTHON_VENV: path.join(data, 'python-venv'),
    PLAYWRIGHT_BROWSERS_PATH: path.join(data, 'playwright-browsers'),
  },
});
const client = new Client({
  name: 'xiaohongshu-workbuddy-smoke',
  version: '1.0.0',
});

try {
  await client.connect(transport);
  const listed = await client.listTools();
  const names = listed.tools.map((tool) => tool.name).sort();
  const expected = [
    'xhs_workbuddy_capture',
    'xhs_workbuddy_execute',
    'xhs_workbuddy_login',
    'xhs_workbuddy_prepare',
    'xhs_workbuddy_setup',
    'xhs_workbuddy_status',
  ];
  if (JSON.stringify(names) !== JSON.stringify(expected)) {
    throw new Error(`MCP tools mismatch: ${JSON.stringify(names)}`);
  }
  const login = listed.tools.find((tool) => tool.name === 'xhs_workbuddy_login');
  const loginRequired = login?.inputSchema?.required || [];
  const loginSources = login?.inputSchema?.properties?.source?.enum || [];
  if (
    !loginRequired.includes('browser_authorized')
    || !loginRequired.includes('source')
    || JSON.stringify(loginSources) !== JSON.stringify(['collection', 'liked'])
    || login.description.includes('关闭窗口')
  ) {
    throw new Error(`MCP login contract mismatch: ${JSON.stringify(login)}`);
  }
  const capture = listed.tools.find((tool) => tool.name === 'xhs_workbuddy_capture');
  const captureProperties = capture?.inputSchema?.properties || {};
  const captureRequired = capture?.inputSchema?.required || [];
  const captureSources = captureProperties.source?.enum || [];
  if (
    'batch_size' in captureProperties
    || 'pause_minutes' in captureProperties
    || JSON.stringify(captureSources) !== JSON.stringify(['collection', 'liked'])
    || !captureRequired.includes('organizing_depth')
    || !captureRequired.includes('generate_report')
    || captureProperties.generate_report?.type !== 'boolean'
    || JSON.stringify(captureProperties.organizing_depth?.enum)
      !== JSON.stringify(['quick', 'light', 'deep'])
    || 'image_ocr_enabled' in captureProperties
    || 'segment_limit' in captureProperties
    || 'controlled_groups_authorized' in captureProperties
    || 'quick_classify' in captureProperties
  ) {
    throw new Error(`MCP capture group contract mismatch: ${JSON.stringify(capture)}`);
  }
  const prepare = listed.tools.find((tool) => tool.name === 'xhs_workbuddy_prepare');
  const prepareProperties = prepare?.inputSchema?.properties || {};
  const prepareRequired = prepare?.inputSchema?.required || [];
  if (
    prepareProperties.classification?.type !== 'array'
    || prepareRequired.includes('classification')
    || prepareProperties.evidence_receipt?.type !== 'string'
    || !prepareRequired.includes('evidence_receipt')
    || prepareProperties.max_moves_per_session?.minimum !== 1
    || prepareProperties.max_moves_per_session?.maximum !== 200
    || prepareRequired.includes('max_moves_per_session')
    || prepareProperties.proposed_board_names?.type !== 'array'
    || prepareProperties.proposed_board_names?.maxItems !== 20
    || JSON.stringify(prepareProperties.new_board_privacy?.enum)
      !== JSON.stringify(['public', 'private'])
    || prepareRequired.includes('proposed_board_names')
    || prepareRequired.includes('new_board_privacy')
    || 'expected_url_substring' in prepareProperties
    || !prepare.description.includes('用户无需处理')
  ) {
    throw new Error(`MCP prepare classification contract mismatch: ${JSON.stringify(prepare)}`);
  }
  const execute = listed.tools.find((tool) => tool.name === 'xhs_workbuddy_execute');
  const executeProperties = execute?.inputSchema?.properties || {};
  const executeRequired = execute?.inputSchema?.required || [];
  if (
    executeProperties.evidence_receipt?.type !== 'string'
    || !executeRequired.includes('evidence_receipt')
    || executeProperties.approval_digest?.pattern !== '^[0-9a-f]{64}$'
    || executeProperties.verify_pages?.minimum !== 1
    || executeProperties.verify_pages?.maximum !== 200
    || !executeRequired.includes('verify_pages')
    || 'expected_url_substring' in executeProperties
    || !execute.description.includes('用户无需处理')
  ) {
    throw new Error(`MCP execute receipt contract mismatch: ${JSON.stringify(execute)}`);
  }
  const result = await client.callTool({
    name: 'xhs_workbuddy_status',
    arguments: {},
  });
  if (
    result.isError
    || result.structuredContent?.runtime?.host !== 'workbuddy'
    || result.structuredContent?.plugin_version !== '2.2.2'
  ) {
    throw new Error(`MCP status failed: ${JSON.stringify(result)}`);
  }
  process.stdout.write(JSON.stringify({
    ok: true,
    tools: names,
    host: result.structuredContent.runtime.host,
    browser_backend: result.structuredContent.runtime.browser_backend,
    plugin_version: result.structuredContent.plugin_version,
  }, null, 2) + '\n');
} finally {
  await transport.close();
}
