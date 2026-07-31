import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';


const skillRoot = path.resolve(
  process.env.CODEBUDDY_PLUGIN_ROOT || path.join(import.meta.dirname, '..'),
);
const pluginDataArgument = process.argv[2];
const pluginData = pluginDataArgument ? path.resolve(pluginDataArgument) : '';
const playwrightProfile = path.join(pluginData, 'playwright-profile');
const pythonVenv = path.join(pluginData, 'python-venv');
const playwrightBrowsers = path.join(pluginData, 'playwright-browsers');
const bridge = path.join(skillRoot, 'scripts', 'workbuddy_bridge.py');


function requirePluginEnvironment() {
  if (process.env.XHS_HOST !== 'workbuddy') {
    throw new Error('XHS_HOST 必须由 WorkBuddy Plugin 设置为 workbuddy。');
  }
  if (!pluginDataArgument) {
    throw new Error('WorkBuddy Plugin 持久化目录未注入。');
  }
  if (!existsSync(bridge)) {
    throw new Error(`找不到固定工作流桥接器：${bridge}`);
  }
}


function commandReady(command) {
  const result = spawnSync(command, ['--version'], {
    stdio: 'ignore',
    windowsHide: true,
  });
  return result.status === 0;
}


function venvPython() {
  const executable = process.platform === 'win32'
    ? path.join(pythonVenv, 'Scripts', 'python.exe')
    : path.join(pythonVenv, 'bin', 'python');
  return existsSync(executable) ? executable : null;
}


function bootstrapPython() {
  const configured = process.env.XHS_BOOTSTRAP_PYTHON;
  const candidates = [
    configured,
    process.platform === 'win32' ? 'python' : 'python3',
    'python',
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (commandReady(candidate)) return candidate;
  }
  throw new Error('未找到 Python 3；请先在本机安装 Python 3.9+。');
}


function pythonFor(action) {
  if (action !== 'setup') {
    const installed = venvPython();
    if (installed) return installed;
  }
  return bootstrapPython();
}


function runBridge(action, args = [], timeoutMs = 600_000) {
  requirePluginEnvironment();
  return new Promise((resolve, reject) => {
    const python = pythonFor(action);
    const child = spawn(python, [bridge, action, ...args], {
      cwd: skillRoot,
      env: {
        ...process.env,
        XHS_HOST: 'workbuddy',
        XHS_SKILL_ROOT: skillRoot,
        CODEBUDDY_PLUGIN_DATA: pluginData,
        XHS_PLAYWRIGHT_PROFILE: playwrightProfile,
        XHS_PYTHON_VENV: pythonVenv,
        PLAYWRIGHT_BROWSERS_PATH: playwrightBrowsers,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`固定工作流超时：${action}`));
    }, timeoutMs);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
      let payload;
      try {
        payload = JSON.parse(lines.at(-1) || '{}');
      } catch {
        reject(new Error(`桥接器返回了无效 JSON：${stdout.trim() || stderr.trim()}`));
        return;
      }
      if (code !== 0 || payload.ok === false) {
        reject(new Error(payload.error || stderr.trim() || `bridge exit=${code}`));
        return;
      }
      resolve(payload);
    });
  });
}


function requireTrue(value, label) {
  if (value !== true) {
    throw new Error(`${label} 必须为 true；请先取得用户当前回合的明确授权。`);
  }
}


function toolResult(payload) {
  return {
    content: [{
      type: 'text',
      text: JSON.stringify(payload, null, 2),
    }],
    structuredContent: payload,
  };
}


function toolError(error) {
  return {
    isError: true,
    content: [{
      type: 'text',
      text: error instanceof Error ? error.message : String(error),
    }],
  };
}


const server = new McpServer(
  {
    name: 'xiaohongshu-workbuddy',
    version: '2.0.0',
  },
  {
    instructions:
      '在 WorkBuddy 中只能调用本服务器管理小红书浏览器阶段。' +
      '先 status；缺依赖时经用户同意后 setup；首次登录用 login；' +
      '抓取用 capture；真实分类完成后用 prepare；只有 prepare 返回 ' +
      'ready_for_execute=true、blockers=[] 且用户确认映射和上限后才可 execute。' +
      '禁止调用 Safari、Arc、系统 Chrome、CDP 或 osascript。',
  },
);


server.registerTool(
  'xhs_workbuddy_status',
  {
    title: '检查 WorkBuddy 小红书运行环境',
    description:
      '离线检查插件宿主、独立 Playwright profile 和依赖；不打开浏览器、不访问小红书。',
    inputSchema: z.object({}),
  },
  async () => {
    try {
      return toolResult(await runBridge('status', [], 30_000));
    } catch (error) {
      return toolError(error);
    }
  },
);


server.registerTool(
  'xhs_workbuddy_setup',
  {
    title: '安装 WorkBuddy 专用 Playwright',
    description:
      '仅在用户明确同意下载依赖后调用。把 Python Playwright 和 Chromium 安装到插件持久化目录；不打开浏览器。',
    inputSchema: z.object({
      install_dependencies: z.boolean().describe('用户是否明确同意安装和下载 Playwright Chromium'),
    }),
  },
  async ({ install_dependencies }) => {
    try {
      requireTrue(install_dependencies, 'install_dependencies');
      return toolResult(await runBridge('setup', [], 1_800_000));
    } catch (error) {
      return toolError(error);
    }
  },
);


server.registerTool(
  'xhs_workbuddy_login',
  {
    title: '登录并定位小红书整理范围',
    description:
      '在用户本轮明确授权后打开可见的专用 Chromium。用户只需完成登录；插件自动找到当前账号、进入所选收藏或点赞页、返回精确 URL 并关闭自己的浏览器。',
    inputSchema: z.object({
      browser_authorized: z.boolean().describe('用户是否在当前回合明确同意打开专用浏览器'),
      source: z.enum(['collection', 'liked']).describe('用户已选择的整理范围'),
      timeout_seconds: z.number().int().min(60).max(900).default(600),
    }),
  },
  async ({ browser_authorized, source, timeout_seconds }) => {
    try {
      requireTrue(browser_authorized, 'browser_authorized');
      return toolResult(await runBridge(
        'login',
        ['--source', source, '--timeout-sec', String(timeout_seconds)],
        (timeout_seconds + 30) * 1000,
      ));
    } catch (error) {
      return toolError(error);
    }
  },
);


server.registerTool(
  'xhs_workbuddy_capture',
  {
    title: '被动抓取小红书当前范围',
    description:
      '只用插件独立 Playwright Chromium 打开用户提供的精确小红书 URL，并被动读取当前可见段；不滚动、不点击、不写账号。快速整理可同时生成 skip-OCR classification.json。',
    inputSchema: z.object({
      browser_authorized: z.boolean().describe('用户是否在当前回合明确授权此精确页面'),
      run_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/).optional(),
      source: z.enum(['collection', 'liked', 'custom']),
      page_url: z.string().url(),
      segment_limit: z.number().int().min(1).max(200).default(10),
      quick_classify: z.boolean().default(false),
    }),
  },
  async ({
    browser_authorized,
    run_id,
    source,
    page_url,
    segment_limit,
    quick_classify,
  }) => {
    try {
      requireTrue(browser_authorized, 'browser_authorized');
      const args = [
        '--source', source,
        '--page-url', page_url,
        '--segment-limit', String(segment_limit),
      ];
      if (run_id) args.push('--run-id', run_id);
      if (quick_classify) args.push('--quick-classify');
      return toolResult(await runBridge('capture', args, 180_000));
    } catch (error) {
      return toolError(error);
    }
  },
);


server.registerTool(
  'xhs_workbuddy_prepare',
  {
    title: '生成真实专辑证据与硬闸门 dry-run',
    description:
      '要求 run 目录已有真实 classification.json。用插件独立 Playwright 只读生成 board_snapshot.json，再机械生成 created_boards.json 和 run_report.json。只有返回 ready_for_execute=true、blockers=[] 才能请求用户执行确认。',
    inputSchema: z.object({
      browser_authorized: z.boolean().describe('用户是否在当前回合授权只读核验此页面'),
      run_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/),
      user_id: z.string().regex(/^[0-9a-fA-F]{24}$/),
      page_url: z.string().url(),
      expected_url_substring: z.string().min(1),
      verify_pages: z.number().int().min(1).max(200).default(100),
    }),
  },
  async ({
    browser_authorized,
    run_id,
    user_id,
    page_url,
    expected_url_substring,
    verify_pages,
  }) => {
    try {
      requireTrue(browser_authorized, 'browser_authorized');
      return toolResult(await runBridge('prepare', [
        '--run-id', run_id,
        '--user-id', user_id,
        '--page-url', page_url,
        '--expected-url-substring', expected_url_substring,
        '--verify-pages', String(verify_pages),
      ], 600_000));
    } catch (error) {
      return toolError(error);
    }
  },
);


server.registerTool(
  'xhs_workbuddy_execute',
  {
    title: '执行用户已确认的小红书整理方案',
    description:
      '真实写入工具。只有用户已看到逐条“当前专辑→目标专辑”、明确确认本次移动上限，并原样提供 prepare 返回的 approval_digest 时才可调用。任何证据变化都会在打开浏览器前拒绝。',
    inputSchema: z.object({
      browser_authorized: z.boolean().describe('用户是否在当前回合明确授权专用浏览器写入'),
      user_confirmed: z.boolean().describe('用户是否明确确认逐条映射和本次移动上限'),
      run_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/),
      user_id: z.string().regex(/^[0-9a-fA-F]{24}$/),
      page_url: z.string().url(),
      expected_url_substring: z.string().min(1),
      approval_digest: z.string().regex(/^[0-9a-f]{64}$/),
      max_moves_per_session: z.number().int().min(1).max(200),
    }),
  },
  async ({
    browser_authorized,
    user_confirmed,
    run_id,
    user_id,
    page_url,
    expected_url_substring,
    approval_digest,
    max_moves_per_session,
  }) => {
    try {
      requireTrue(browser_authorized, 'browser_authorized');
      requireTrue(user_confirmed, 'user_confirmed');
      return toolResult(await runBridge('execute', [
        '--run-id', run_id,
        '--user-id', user_id,
        '--page-url', page_url,
        '--expected-url-substring', expected_url_substring,
        '--approval-digest', approval_digest,
        '--max-moves-per-session', String(max_moves_per_session),
      ], 1_800_000));
    } catch (error) {
      return toolError(error);
    }
  },
);


requirePluginEnvironment();
const transport = new StdioServerTransport();
await server.connect(transport);
