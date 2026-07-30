import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import path from 'node:path';
import process from 'node:process';


const root = path.resolve(import.meta.dirname, '..');
const data = path.join('/tmp', 'xhs-workbuddy-mcp-smoke');
const transport = new StdioClientTransport({
  command: 'node',
  args: [path.join(root, 'server', 'xhs-workbuddy-mcp.mjs')],
  cwd: root,
  env: {
    ...process.env,
    XHS_HOST: 'workbuddy',
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
  const result = await client.callTool({
    name: 'xhs_workbuddy_status',
    arguments: {},
  });
  if (result.isError || result.structuredContent?.runtime?.host !== 'workbuddy') {
    throw new Error(`MCP status failed: ${JSON.stringify(result)}`);
  }
  process.stdout.write(JSON.stringify({
    ok: true,
    tools: names,
    host: result.structuredContent.runtime.host,
    browser_backend: result.structuredContent.runtime.browser_backend,
  }, null, 2) + '\n');
} finally {
  await transport.close();
}
