# WorkBuddy Plugin + Playwright 专用浏览器

## 目的

WorkBuddy 不再通过宿主进程向 Safari 发送 Apple Events，也不依赖 macOS“自动化”开关。插件把 Skill、MCP 服务器、固定工作流桥接器和浏览器策略一起分发；模型只负责填写明确参数，浏览器选择和写入闸门由代码执行。

## 确定性宿主识别

- WorkBuddy 加载 `.mcp.json` 后，MCP 子进程收到 `XHS_HOST=workbuddy`。
- Skill 侧只把六个 `xhs_workbuddy_*` 工具同时存在视为插件已加载。
- 不读取进程名，不检查窗口标题，不根据“WorkBuddy”字样猜宿主。
- 插件工具缺失时停止，不自动退回 Safari、Arc、系统 Chrome 或 CDP。

## 一次性启用

WorkBuddy 的非交互 MCP 宿主必须显式信任服务器。普通用户不需要寻找连接器页面或手动编辑配置：

1. Skill 检测到六个工具不完整且存在 `WORKBUDDY_CONFIG_DIR` 时，只询问一次是否启用。
2. 用户回复“启用”后，运行 `python3 scripts/enable_workbuddy_mcp.py`。
3. 脚本只把 `xiaohongshu-organizer` 加入 `enabledMcpjsonServers`，保留全部其他设置，并返回是否需要重启。
4. 用户完全退出并重开 WorkBuddy 后，重新执行原请求。

Skill 不得静默信任自己；这一次用户确认是 WorkBuddy 的安全边界。

## 浏览器合同

WorkBuddy 模式固定为：

- backend：`playwright`
- channel：Playwright 自带 `chromium`
- headless：`false`
- profile：WorkBuddy 插件数据目录下的 `playwright-profile`
- CDP：禁止
- Safari / Arc / Chrome / Edge 系统后端：禁止

`scripts/workbuddy_runtime.py` 同时接入 `extract_visible_items.py`、`capture_board_snapshot.py` 和 `run_reassign_batch.py`。即使模型传错参数，三个真实浏览器入口也会在接触浏览器前拒绝。

WorkBuddy 5.3.5 不会可靠展开 `.mcp.json` 的插件数据目录变量。`bin/run-node` 必须从宿主注入的 `WORKBUDDY_CONFIG_DIR` 计算固定插件数据目录，并作为位置参数传给 MCP 服务器；不得把未展开的变量当作路径使用。

## 工具顺序

1. `xhs_workbuddy_status`：纯离线，返回插件 profile 和 Playwright/Chromium 是否就绪。
2. `xhs_workbuddy_setup`：仅在用户明确同意依赖下载后调用；在 `${CODEBUDDY_PLUGIN_DATA}/python-venv` 安装 `requirements-workbuddy.txt` 和 Playwright Chromium。
3. `xhs_workbuddy_login`：仅在用户当前回合明确授权打开浏览器后调用，并传入已选择的 `source=collection|liked`。用户只需完成登录；工具自动从前端“我”入口取得当前账号、进入所选范围、返回无敏感参数的 `target_page_url`，随后关闭自己的浏览器并等待 profile 锁释放。禁止要求用户关窗口或复制 URL。
4. `xhs_workbuddy_capture`：直接复用上一步返回的 `target_page_url`；不得再次向用户索取地址。收藏 URL 必须带 `tab=fav`，点赞 URL 必须带 `tab=liked`；打开精确 URL 后只读取当前可见段。启动前若 profile 仍被占用，必须在创建 run 目录前停止。
5. `xhs_workbuddy_prepare`：要求同一 run 目录已有真实 `classification.json`；顺序固定为只读专辑快照、目标专辑核验、硬闸门 dry-run。未通过或 `planned_move_count=0` 时都不生成 `approval_digest`；零计划时直接报告已正确归档与待复核条目。
6. `xhs_workbuddy_execute`：必须同时收到 `browser_authorized=true`、`user_confirmed=true`、用户确认的移动上限和 prepare 原样返回的 `approval_digest`；digest 绑定 classification、board snapshot、created boards 和逐条计划。

用户只感知两次安全决策：

1. 首次只读整理前，确认范围、深度、上限和打开专用浏览器；这一次授权覆盖 `login → capture → prepare`。
2. 真正移动前，确认逐条映射和移动上限。

`login`、`capture`、`prepare` 是代码内部的安全边界，不得变成让用户关窗口、抄 URL 或理解 profile 的操作说明。

## 安装

在 WorkBuddy 对话中执行：

```text
/plugin marketplace add themrv1ck/xiaohongshu-web-collection-organizing
/plugin install xiaohongshu-organizer@xiaohongshu-skill-marketplace
/reload-plugins
```

如果只能使用本机终端，在 macOS Terminal.app 执行：

```bash
export CODEBUDDY_CONFIG_DIR="$HOME/.workbuddy"
WB_CODEBUDDY="/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy"
"$WB_CODEBUDDY" plugin marketplace add themrv1ck/xiaohongshu-web-collection-organizing
"$WB_CODEBUDDY" plugin install xiaohongshu-organizer@xiaohongshu-skill-marketplace --scope user
```

然后完全退出并重新打开 WorkBuddy，或在 WorkBuddy 中执行 `/reload-plugins`。

## 验收

```bash
WB_CODEBUDDY="/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy"
"$WB_CODEBUDDY" plugin validate .
"$WB_CODEBUDDY" plugin validate .codebuddy-plugin/marketplace.json
cd workbuddy-plugin-src
npm ci
npm run build
npm run test:mcp
```

`test:mcp` 必须列出六个工具，并返回 `host=workbuddy`、`browser_backend=playwright`。这只是离线/MCP 验收；真实登录、抓取、专辑快照和移动仍需用户在当前回合逐步授权。
