import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';

import {
  parseBridgeResult,
  safeBridgeError,
} from './bridge-security.mjs';
import { createEvidenceLedger } from './evidence-ledger.mjs';
import { createLaunchAttestation } from './launch-attestation.mjs';
import {
  canonicalPageBinding,
  receiptBindingsForPage,
} from './page-binding.mjs';


const skillRoot = path.resolve(
  process.env.CODEBUDDY_PLUGIN_ROOT || path.join(import.meta.dirname, '..'),
);
const pluginDataArgument = process.argv[2];
const pluginData = pluginDataArgument ? path.resolve(pluginDataArgument) : '';
const playwrightProfile = path.join(pluginData, 'playwright-profile');
const pythonVenv = path.join(pluginData, 'python-venv');
const playwrightBrowsers = path.join(pluginData, 'playwright-browsers');
const bridge = path.join(skillRoot, 'scripts', 'workbuddy_bridge.py');
let evidenceLedger;


const RECEIPT_PATTERN = /^xhs1\.[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{43}$/;
const APPROVAL_DIGEST_PATTERN = /^[0-9a-f]{64}$/;
const PLUGIN_VERSION = '2.0.7';
const MCP_LAUNCH_KEY_FD = 3;
const MCP_EXECUTE_READY_FD = 4;
const MCP_EXECUTE_COMMIT_FD = 5;


function pageUserId(pageUrl) {
  const match = new URL(pageUrl).pathname.match(
    /^\/user\/profile\/([0-9a-fA-F]{24})\/?$/,
  );
  if (!match) throw new Error('page_url 必须绑定当前账号的 profile 页。');
  return match[1].toLowerCase();
}


function captureArtifactNames(organizingDepth) {
  const names = [
    'visible_items.json',
    'crawl_manifest.json',
    'xhs_safety_state.json',
  ];
  if (organizingDepth === 'light') {
    names.push('image_items.json', 'ocr_results.json');
  }
  return names;
}


function inventoryArtifactNames(organizingDepth) {
  return [...captureArtifactNames(organizingDepth), 'board_snapshot.json'];
}


function planArtifactNames(organizingDepth) {
  return [
    ...inventoryArtifactNames(organizingDepth),
    'classification.json',
    'created_boards.json',
    'run_report.json',
    'approval.json',
  ];
}


function receiptBindings(userId, pageUrl) {
  return receiptBindingsForPage(userId, pageUrl);
}


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


function runBridge(
  action,
  args = [],
  timeoutMs = 600_000,
  abortSignal = undefined,
  inputPayload = undefined,
  launchOptions = {},
) {
  requirePluginEnvironment();
  return new Promise((resolve, reject) => {
    const python = pythonFor(action);
    const attested = ['prepare', 'execute'].includes(action);
    const bridgeArgs = attested
      ? [...args, '--mcp-launch-fd', String(MCP_LAUNCH_KEY_FD)]
      : [...args];
    const launch = attested
      ? createLaunchAttestation({
        action,
        args: bridgeArgs,
        inputPayload,
      })
      : null;
    const bridgeInput = launch ? launch.payload : inputPayload;
    const stdio = action === 'execute'
      ? ['pipe', 'pipe', 'pipe', 'pipe', 'pipe', 'pipe']
      : (attested
        ? ['pipe', 'pipe', 'pipe', 'pipe']
        : [inputPayload === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe']);
    const child = spawn(python, [bridge, action, ...bridgeArgs], {
      cwd: skillRoot,
      env: {
        ...process.env,
        XHS_HOST: 'workbuddy',
        XHS_WORKBUDDY_PLATFORM: process.platform,
        XHS_SKILL_ROOT: skillRoot,
        CODEBUDDY_PLUGIN_DATA: pluginData,
        XHS_PLAYWRIGHT_PROFILE: playwrightProfile,
        XHS_PYTHON_VENV: pythonVenv,
        PLAYWRIGHT_BROWSERS_PATH: playwrightBrowsers,
      },
      stdio,
      windowsHide: true,
      detached: process.platform !== 'win32',
    });
    let stdout = '';
    let stderr = '';
    let finished = false;
    let terminationError;
    let forceKillTimer;
    let executeReady = false;
    const cleanup = () => {
      clearTimeout(timer);
      clearTimeout(forceKillTimer);
      abortSignal?.removeEventListener('abort', onAbort);
    };
    const fail = (error) => {
      if (finished) return;
      finished = true;
      cleanup();
      reject(safeBridgeError(error));
    };
    const signalProcessTree = (signalName) => {
      try {
        if (process.platform === 'win32') {
          const result = spawnSync(
            'taskkill.exe',
            ['/PID', String(child.pid), '/T', '/F'],
            { windowsHide: true, stdio: 'ignore' },
          );
          if (result.error) throw result.error;
        } else {
          process.kill(-child.pid, signalName);
        }
      } catch (error) {
        if (error?.code !== 'ESRCH') throw error;
      }
    };
    const terminate = (error) => {
      if (finished || terminationError) return;
      terminationError = error;
      try {
        signalProcessTree('SIGTERM');
      } catch (signalError) {
        fail(signalError);
        return;
      }
      forceKillTimer = setTimeout(() => {
        try {
          signalProcessTree('SIGKILL');
        } catch (signalError) {
          fail(signalError);
        }
      }, 5_000);
    };
    const onAbort = () => {
      terminate(new Error(`固定工作流已取消：${action}`));
    };
    const timer = setTimeout(() => {
      terminate(new Error(`固定工作流超时：${action}`));
    }, timeoutMs);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', (error) => {
      fail(error);
    });
    child.on('close', (code) => {
      if (finished) return;
      finished = true;
      cleanup();
      if (terminationError) {
        reject(safeBridgeError(terminationError));
        return;
      }
      try {
        resolve(parseBridgeResult(stdout, stderr, code));
      } catch (error) {
        reject(safeBridgeError(error));
        return;
      }
    });
    if (launch) {
      const keyPipe = child.stdio[MCP_LAUNCH_KEY_FD];
      keyPipe.on('error', terminate);
      keyPipe.end(launch.key, () => launch.key.fill(0));
    }
    if (action === 'execute') {
      if (typeof launchOptions.onExecutePreflightReady !== 'function') {
        terminate(new Error('execute 缺少 MCP 提交闸门。'));
        return;
      }
      const readyPipe = child.stdio[MCP_EXECUTE_READY_FD];
      const commitPipe = child.stdio[MCP_EXECUTE_COMMIT_FD];
      let readyText = '';
      readyPipe.setEncoding('utf8');
      readyPipe.on('error', terminate);
      commitPipe.on('error', terminate);
      readyPipe.on('data', (chunk) => {
        if (executeReady) return;
        readyText += chunk;
        if (readyText.length > 16) {
          terminate(new Error('execute 预检握手无效。'));
          return;
        }
        if (!readyText.includes('\n')) return;
        if (readyText !== 'READY\n') {
          terminate(new Error('execute 预检握手无效。'));
          return;
        }
        try {
          launchOptions.onExecutePreflightReady();
          executeReady = true;
          commitPipe.end('COMMIT\n');
        } catch (error) {
          terminate(error);
        }
      });
    }
    if (abortSignal?.aborted) {
      onAbort();
      return;
    }
    abortSignal?.addEventListener('abort', onAbort, { once: true });
    if (bridgeInput !== undefined) {
      child.stdin.on('error', terminate);
      child.stdin.end(JSON.stringify(bridgeInput));
    }
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
  const safeError = safeBridgeError(error);
  return {
    isError: true,
    content: [{
      type: 'text',
      text: safeError.message,
    }],
  };
}


const server = new McpServer(
  {
    name: 'xiaohongshu-workbuddy',
    version: PLUGIN_VERSION,
  },
  {
    instructions:
      '在 WorkBuddy 中只能调用本服务器管理小红书浏览器阶段。' +
      '先 status；缺依赖时经用户同意后 setup；首次登录用 login；' +
      '抓取在同一浏览器会话中自动翻页，默认每 200 条一组、组间暂停 3 分钟；' +
      'capture 必须显式传 organizing_depth；quick 不做 OCR，light 在关闭同一浏览器前完成登录态详情补齐并在本地 OCR；' +
      'deep 因尚无视频语音和完整时轴画面证据入口而在浏览器启动前停止；' +
      '禁止在 WorkBuddy 中运行无登录态 enrich_note_images.py 或静默改用元数据分类；' +
      'capture 后先调用不带 classification 的 prepare 读取真实已有专辑；' +
      '分类优先选择真实已有专辑；没有合适专辑时只能依据本次真实内容提议新名称，不得使用预设主题；' +
      'capture、两次 prepare 和 execute 之间必须自动原样传递 evidence_receipt，用户无需处理；' +
      '没有已有专辑时，把提议名称和公开或私密设置与逐条移动方案一起交给用户一次确认；' +
      '只有 prepare 返回 ' +
      'ready_for_execute=true、blockers=[] 且用户确认映射和上限后才可 execute。' +
      '禁止调用 Safari、Arc、系统 Chrome、CDP 或 osascript。',
  },
);


server.registerTool(
  'xhs_workbuddy_status',
  {
    title: '检查 WorkBuddy 小红书运行环境',
    description:
      '离线检查插件宿主、独立 Playwright profile 和依赖；Windows 使用独立 Edge profile，macOS/Linux 使用独立 Chromium；不打开浏览器、不访问小红书。',
    inputSchema: z.object({}),
  },
  async () => {
    try {
      return toolResult({
        ...(await runBridge('status', [], 30_000)),
        plugin_version: PLUGIN_VERSION,
      });
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
      '仅在用户明确同意安装依赖后调用。Windows 复用系统 Edge 程序但使用插件独立登录目录，不下载 Chromium；macOS/Linux 安装独立 Chromium；不打开浏览器。',
    inputSchema: z.object({
      install_dependencies: z.boolean().describe('用户是否明确同意安装 Playwright 运行依赖'),
    }),
  },
  async ({ install_dependencies }, extra) => {
    try {
      requireTrue(install_dependencies, 'install_dependencies');
      return toolResult(await runBridge(
        'setup',
        [],
        1_800_000,
        extra.signal,
      ));
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
      '在用户本轮明确授权后打开可见的 WorkBuddy 专用浏览器；Windows 为独立 Edge profile，macOS/Linux 为独立 Chromium。用户只需完成登录；插件自动定位范围并关闭自己创建的窗口。',
    inputSchema: z.object({
      browser_authorized: z.boolean().describe('用户是否在当前回合明确同意打开专用浏览器'),
      source: z.enum(['collection', 'liked']).describe('用户已选择的整理范围'),
      timeout_seconds: z.number().int().min(60).max(900).default(600),
    }),
  },
  async ({ browser_authorized, source, timeout_seconds }, extra) => {
    try {
      requireTrue(browser_authorized, 'browser_authorized');
      return toolResult(await runBridge(
        'login',
        ['--source', source, '--timeout-sec', String(timeout_seconds)],
        (timeout_seconds + 30) * 1000,
        extra.signal,
      ));
    } catch (error) {
      return toolError(error);
    }
  },
);


server.registerTool(
  'xhs_workbuddy_capture',
  {
    title: '分组读取小红书完整范围',
    description:
      '只用插件独立 Playwright Chromium 打开精确页面并在同一会话中自动翻页；organizing_depth 必填，quick 不做 OCR，light 用同一登录态读取全部详情、下载本地图片字节并 OCR，deep 在视频证据入口接入前于浏览器启动前停止。固定每 200 条独立保存一组、非末组真实暂停 3 分钟；完成后由插件签发会话 receipt，用户无需处理；不导出 Cookie、签名图片 URL 或 xsec。',
    inputSchema: z.object({
      browser_authorized: z.boolean().describe('用户是否在当前回合明确授权此精确页面'),
      run_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/).optional(),
      source: z.enum(['collection', 'liked']),
      page_url: z.string().url(),
      organizing_depth: z.enum(['quick', 'light', 'deep']).describe('必填：quick=快速整理，light=图文完整 OCR；deep 在视频证据入口接入前会于浏览器启动前明确停止'),
    }),
  },
  async ({
    browser_authorized,
    run_id,
    source,
    page_url,
    organizing_depth,
  }, extra) => {
    try {
      requireTrue(browser_authorized, 'browser_authorized');
      const safePageUrl = canonicalPageBinding(page_url, source);
      const args = [
        '--source', source,
        '--page-url', safePageUrl,
        '--batch-size', '200',
        '--pause-minutes', '3',
        '--organizing-depth', organizing_depth,
      ];
      if (organizing_depth === 'deep') {
        throw new Error(
          'WorkBuddy Plugin 当前只支持快速或轻度整理；深度整理需要视频语音和完整时轴画面证据，尚未接入，未打开浏览器。',
        );
      }
      if (run_id) args.push('--run-id', run_id);
      const payload = await runBridge(
        'capture',
        args,
        86_400_000,
        extra.signal,
      );
      if (payload.ready_for_classification !== true) {
        return toolResult({
          ...payload,
          evidence_receipt: null,
          receipt_stage: null,
        });
      }
      const capturedRunId = String(payload.run_id || '').trim();
      const boundPage = receiptBindingsForPage(pageUserId(page_url), page_url);
      canonicalPageBinding(page_url, source);
      const issued = evidenceLedger.issue({
        runId: capturedRunId,
        stage: 'capture',
        bindings: {
          ...boundPage,
          organizing_depth: organizing_depth,
        },
        artifactNames: captureArtifactNames(organizing_depth),
      });
      return toolResult({
        ...payload,
        evidence_receipt: issued.receipt,
        receipt_stage: 'capture',
        receipt_notice: '由 WorkBuddy 自动传给下一阶段，用户无需查看或复制。',
      });
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
      '两阶段固定入口：第一次只读返回真实已有专辑和完整 classification_inputs；第二次提交覆盖全部真实 ID 的 classification。若需新专辑，同时提交仅依据本次内容生成的 proposed_board_names 与明确的公开或私密设置；专辑创建和逐条移动合并为一次用户确认并写入 approval_digest。两阶段 receipt 均由 WorkBuddy 自动传递，用户无需处理。',
    inputSchema: z.object({
      browser_authorized: z.boolean().describe('用户是否在当前回合授权只读核验此页面'),
      run_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/),
      evidence_receipt: z.string().regex(RECEIPT_PATTERN).describe(
        '插件自动传递的上一阶段 receipt；用户无需查看或复制',
      ),
      user_id: z.string().regex(/^[0-9a-fA-F]{24}$/),
      page_url: z.string().url(),
      verify_pages: z.number().int().min(1).max(200).default(100),
      max_moves_per_session: z.number().int().min(1).max(200).optional().describe(
        '第二阶段传 classification 时必填；将写入 approval_digest，execute 必须原样使用',
      ),
      classification: z.array(z.object({
        id: z.string().regex(/^[0-9a-fA-F]{24}$/),
        target_board: z.string().default(''),
        confidence: z.enum(['low', 'medium', 'high']).default('low'),
        reason: z.array(z.string()).default([]),
        review_state: z.string().default(''),
        main_topic: z.string().default(''),
        content_summary: z.string().default(''),
      })).optional(),
      proposed_board_names: z.array(z.string().min(1)).max(20).optional().describe(
        '仅依据本次 classification_inputs 提议的新专辑名称；不得使用插件预设类别',
      ),
      new_board_privacy: z.enum(['public', 'private']).optional().describe(
        '存在 proposed_board_names 时必填，并与创建及移动方案一起由用户确认',
      ),
    }),
  },
  async ({
    browser_authorized,
    run_id,
    evidence_receipt,
    user_id,
    page_url,
    verify_pages,
    max_moves_per_session,
    classification,
    proposed_board_names,
    new_board_privacy,
  }, extra) => {
    try {
      requireTrue(browser_authorized, 'browser_authorized');
      const safePageUrl = canonicalPageBinding(page_url);
      const args = [
        '--run-id', run_id,
        '--user-id', user_id,
        '--page-url', safePageUrl,
        '--expected-url-substring', safePageUrl,
        '--verify-pages', String(verify_pages),
        '--trusted-evidence-stdin',
      ];
      if (classification !== undefined) {
        if (max_moves_per_session === undefined) {
          throw new Error(
            '提交 classification 时必须同时提供用户将确认的 max_moves_per_session。',
          );
        }
        args.push(
          '--classification-stdin',
          '--max-moves-per-session', String(max_moves_per_session),
        );
      }
      const expectedStage = classification === undefined ? 'capture' : 'inventory';
      const trustedEvidence = evidenceLedger.begin({
        receipt: evidence_receipt,
        expectedStage,
        runId: run_id,
        bindings: receiptBindings(user_id, page_url),
      });
      try {
        const inputPayload = { trusted_evidence: trustedEvidence };
        if (classification !== undefined) {
          inputPayload.classification = classification;
          if (proposed_board_names !== undefined) {
            inputPayload.proposed_board_names = proposed_board_names;
          }
          if (new_board_privacy !== undefined) {
            inputPayload.new_board_privacy = new_board_privacy;
          }
        }
        const payload = await runBridge(
          'prepare',
          args,
          600_000,
          extra.signal,
          inputPayload,
        );
        if (classification === undefined) {
          const issued = evidenceLedger.advance({
            receipt: evidence_receipt,
            nextStage: 'inventory',
            allowSafetyStateProgress: true,
            artifactNames: inventoryArtifactNames(
              trustedEvidence.bindings.organizing_depth,
            ),
          });
          return toolResult({
            ...payload,
            evidence_receipt: issued.receipt,
            receipt_stage: 'inventory',
            receipt_notice: '由 WorkBuddy 自动传给下一阶段，用户无需查看或复制。',
          });
        }
        if (
          payload.mode === 'dry_run'
          && payload.ready_for_execute === true
          && Array.isArray(payload.blockers)
          && payload.blockers.length === 0
          && Number.isInteger(payload.planned_move_count)
          && payload.planned_move_count > 0
          && APPROVAL_DIGEST_PATTERN.test(payload.approval_digest || '')
        ) {
          const issued = evidenceLedger.advance({
            receipt: evidence_receipt,
            nextStage: 'plan',
            artifactNames: planArtifactNames(
              trustedEvidence.bindings.organizing_depth,
            ),
          });
          return toolResult({
            ...payload,
            evidence_receipt: issued.receipt,
            receipt_stage: 'plan',
            receipt_notice: '由 WorkBuddy 在用户确认后自动传给 execute，用户无需查看或复制。',
          });
        }
        evidenceLedger.abort(evidence_receipt);
        return toolResult({
          ...payload,
          evidence_receipt,
          receipt_stage: 'inventory',
        });
      } catch (error) {
        evidenceLedger.abort(evidence_receipt);
        throw error;
      }
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
      '真实写入工具。只有用户已看到待创建专辑及隐私、逐条“当前专辑→目标专辑”和移动上限并明确确认后才可调用。若有新专辑，插件会在同一受管浏览器中先创建并核验为空，再移动收藏；任何证据或参数变化都会在打开浏览器前拒绝。receipt 由 WorkBuddy 自动传递，用户无需处理。',
    inputSchema: z.object({
      browser_authorized: z.boolean().describe('用户是否在当前回合明确授权专用浏览器写入'),
      user_confirmed: z.boolean().describe('用户是否明确确认待创建专辑及隐私、逐条映射和本次移动上限'),
      run_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/),
      evidence_receipt: z.string().regex(RECEIPT_PATTERN).describe(
        '插件自动传递的 plan receipt；用户无需查看或复制',
      ),
      user_id: z.string().regex(/^[0-9a-fA-F]{24}$/),
      page_url: z.string().url(),
      approval_digest: z.string().regex(/^[0-9a-f]{64}$/),
      max_moves_per_session: z.number().int().min(1).max(200),
      verify_pages: z.number().int().min(1).max(200).describe(
        '必须原样使用 prepare 返回的 verify_pages',
      ),
    }),
  },
  async ({
    browser_authorized,
    user_confirmed,
    run_id,
    evidence_receipt,
    user_id,
    page_url,
    approval_digest,
    max_moves_per_session,
    verify_pages,
  }, extra) => {
    try {
      requireTrue(browser_authorized, 'browser_authorized');
      requireTrue(user_confirmed, 'user_confirmed');
      const safePageUrl = canonicalPageBinding(page_url);
      const trustedEvidence = evidenceLedger.begin({
        receipt: evidence_receipt,
        expectedStage: 'plan',
        runId: run_id,
        bindings: receiptBindings(user_id, page_url),
      });
      try {
        return toolResult(await runBridge('execute', [
          '--run-id', run_id,
          '--user-id', user_id,
          '--page-url', safePageUrl,
          '--expected-url-substring', safePageUrl,
          '--approval-digest', approval_digest,
          '--max-moves-per-session', String(max_moves_per_session),
          '--verify-pages', String(verify_pages),
          '--trusted-evidence-stdin',
        ], 1_800_000, extra.signal, {
          trusted_evidence: trustedEvidence,
        }, {
          onExecutePreflightReady: () => evidenceLedger.commit(evidence_receipt),
        }));
      } catch (error) {
        evidenceLedger.abort(evidence_receipt);
        throw error;
      }
    } catch (error) {
      return toolError(error);
    }
  },
);


requirePluginEnvironment();
evidenceLedger = createEvidenceLedger({
  runsRoot: path.join(pluginData, 'runs'),
});
const transport = new StdioServerTransport();
await server.connect(transport);
