# WorkBuddy Plugin + Playwright 受管浏览器

## 目的

WorkBuddy 不再通过宿主进程向 Safari 发送 Apple Events，也不依赖 macOS“自动化”开关。插件把 Skill、MCP 服务器、固定工作流桥接器和浏览器策略一起分发；模型只负责填写明确参数，浏览器选择和写入闸门由代码执行。

## 2.3.0 安全停机

专辑读取、创建和移动当前全部停用。模型必须在打开浏览器前停止，不调用 login、capture、prepare 或 execute。旧版网页内部模块探测与自动化会话进入安全验证错误页高度相关，现已删除；下面的浏览器与工具合同只作为未来非注入式实现的约束记录，不代表当前可执行。

## 确定性宿主识别

- WorkBuddy 加载 `.mcp.json` 后，MCP 子进程收到 `XHS_HOST=workbuddy`。
- Skill 侧只把六个 `xhs_workbuddy_*` 工具同时存在视为插件已加载。
- 不读取进程名，不检查窗口标题，不根据“WorkBuddy”字样猜宿主。
- 插件工具缺失时停止，不自动退回 Safari、Arc、系统 Chrome 或 CDP。

## 一次性启用

WorkBuddy 的非交互 MCP 宿主必须显式信任服务器。SkillHub 只安装 Skill，不会代替 Plugin 安装流程；普通用户仍不需要寻找连接器页面、粘贴命令或手动编辑配置：

1. Skill 检测到六个工具不完整且存在 `WORKBUDDY_CONFIG_DIR` 时，只询问一次是否启用。
2. 用户回复“启用”后，运行 `python3 scripts/enable_workbuddy_mcp.py --install-plugin`。
3. 脚本只通过 `WORKBUDDY_RESOURCES_PATH` 指向的 WorkBuddy 官方 `codebuddy` CLI，按固定 GitHub marketplace 安装或启用 `xiaohongshu-organizer@xiaohongshu-skill-marketplace`，随后只把 `xiaohongshu-organizer` 加入 `enabledMcpjsonServers`。已有本地开发 marketplace 不更新、不覆盖；全部其他设置原样保留。任一步失败时不写成功状态。
4. 用户完全退出并重开 WorkBuddy 后，重新执行原请求。

Skill 不得静默信任自己；这一次用户确认是 WorkBuddy 的安全边界。

## 浏览器合同

WorkBuddy 模式固定为：

- backend：`playwright`
- channel：Windows 为系统 `msedge` 程序，macOS/Linux 为 Playwright `chromium`
- headless：`false`
- profile：WorkBuddy 插件数据目录下的 `playwright-profile`
- CDP：禁止
- 用户日常 Safari / Arc / Chrome / Edge profile：禁止

Windows 只复用 Edge 程序文件，不接管其日常登录目录；小红书登录态始终写入插件数据目录下的独立 `playwright-profile`。setup 不下载 Chromium，只检查系统 Edge 是否存在。所有平台都拒绝 CDP、headless 和外部 `user-data-dir`。

`scripts/workbuddy_runtime.py` 同时接入 `extract_visible_items.py`、`capture_board_snapshot.py` 和 `run_reassign_batch.py`。即使模型传错参数，三个真实浏览器入口也会在接触浏览器前拒绝。

WorkBuddy 5.3.5 不会可靠展开 `.mcp.json` 的插件数据目录变量。`bin/run-node.sh` 必须从宿主注入的 `WORKBUDDY_CONFIG_DIR` 计算固定插件数据目录，并作为位置参数传给 MCP 服务器；不得把未展开的变量当作路径使用。

## 工具顺序

1. `xhs_workbuddy_status`：纯离线，返回 `plugin_version=2.3.0`、独立 profile、平台 channel 和依赖状态；缺失或版本不同必须先更新 Plugin 并重开 WorkBuddy。
2. `xhs_workbuddy_setup`：仅在用户明确同意后调用；在 `${CODEBUDDY_PLUGIN_DATA}/python-venv` 安装 `requirements-workbuddy.txt`。Windows 只检查系统 Edge，不下载 Chromium；macOS/Linux 安装独立 Chromium。
3. `xhs_workbuddy_login`：仅在用户当前回合明确授权打开浏览器后调用，并传入已选择的 `source=collection|liked`。用户只需完成登录；工具自动从前端“我”入口取得当前账号、进入所选范围、返回无敏感参数的 `target_page_url`，随后关闭自己的浏览器并等待 profile 锁释放。禁止要求用户关窗口或复制 URL。
   轻度整理调用 capture 前必须已经询问是否需要最终桌面 HTML，并显式传 `generate_report=true|false`；快速整理固定传 `false`。该选择写入抓取 manifest 并随证据哈希绑定。
4. `xhs_workbuddy_capture`：直接复用上一步返回的 `target_page_url`；不得再次向用户索取地址。收藏 URL 必须带 `tab=fav`，点赞 URL 必须带 `tab=liked`。`organizing_depth` 必填：快速整理传 `quick`，轻度整理传 `light`；`deep` 会因尚无视频语音和完整时轴画面证据入口而在浏览器启动前明确停止，禁止冒充深度结果。分组参数不暴露给模型：插件固定每 200 条保存一组，非末组真实等待 3 分钟。只有声明总数每次连读均不变化、实际唯一条数完全相等、且 `page_index ↔ note_id` 双向唯一并连续覆盖 `0..总数-1` 才允许分类；否则保存现有数据并硬停，不能把约 10 条首屏数据标为完整。轻度整理在关闭同一次 context 前，用进程内卡片链接打开全部条目详情，再以同一 BrowserContext 下载图片字节；`image_items.json` 只保存相对本地路径与内容 SHA256，不保存签名图片 URL。Cookie、卡片原始 query、签名 URL 与 xsec 不得落盘、进入错误或返回模型。只有 `ready_for_classification=true` 时，MCP 才对本次文件哈希签发 capture receipt。
5. `xhs_workbuddy_prepare`（专辑清单阶段）：第一次不传 `classification`，只读生成完整 `board_snapshot.json`，把当前属于任一专辑的全部 note id 视为首次归档已确认并标记为永久保护，只返回尚未首次归档的专辑外笔记脱敏 `classification_inputs`，并返回 `protected_existing_board_member_count`。账号没有专辑时也继续分类，不再返回死路 blocker。
6. 分类：模型只能使用 `classification_inputs`，不得补回或纠正已保护专辑成员。优先选择真实已有专辑；确需新增时，只能依据本次内容提出最多 20 个 `proposed_board_names`，不得使用模板或插件预设，并明确统一的 `new_board_privacy=public|private`。每个提议名称必须至少被一条真实分类使用。空目标由工具机械转入固定“无法确定”；若该专辑不存在，工具把它加入同一次待创建确认，不占用模型的 20 个内容专辑提议。
7. `xhs_workbuddy_prepare`（dry-run 阶段）：第二次自动传入 classification、可选提议名称及隐私、移动上限、`verify_pages` 和 inventory receipt。工具机械核验所有输入，把待创建专辑、隐私、逐条移动与上限写入同一个 approval digest；此阶段不创建任何专辑。
8. `xhs_workbuddy_execute`：用户一次确认上述完整方案后执行。MCP `COMMIT` 之后，Python 在同一个受管 BrowserContext 中先创建确认过的专辑，逐个核验名称、隐私和空成员，再只移动 `not_in_any_board + first_archive_pending` 且空 `source_board_id` 的笔记；只有 `U_` + `Ks` 回读确认后才转为 `first_archive_confirmed` 并永久保护。已有专辑成员在 Python 与页面 JavaScript 两层跳过。任一创建结果不确定即写入安全停机并停止后续移动。WorkBuddy 中直接运行抓取脚本或 `run_reassign_batch.py --execute` 会被拒绝。

## 证据 receipt

- MCP 启动时用 Node 内置 `crypto` 生成 32 字节 HMAC 密钥；密钥和阶段账本只存在 MCP 进程内存，不写磁盘、环境变量、stdin 或日志。
- receipt 绑定 `run_id`、阶段、账号、带 `tab` 的页面、来源、整理档位和下一阶段会读取的文件 SHA256。`collection` 只接受 `tab=fav`，`liked` 只接受 `tab=liked|like`；来源与 tab 不一致会在浏览器前拒绝。
- `advance` 签发下一阶段 receipt 前会再次核验父阶段不可变文件，不能把被改过的 visible/manifest/OCR 重新签白。唯一允许的阶段内变化是 capture 到 inventory 时由只读专辑快照产生的同 session、仍为 active、检查点单调追加的 `xhs_safety_state.json`；安全停机、回退或其它变化一律拒绝。
- MCP 先验签和重算哈希，Python 再在任何删除、修改或浏览器启动前二次核验。对 prepare/execute，Node 还会为每次子进程启动生成独立 32 字节密钥，对 `action + args + trusted_evidence` 的规范化载荷做 HMAC；密钥只经额外继承 pipe/fd 送入该 Python 子进程，不进入 argv、环境变量、stdin、磁盘或日志。普通误调用缺少这个 fd 会提前拒绝；该启动绑定不是抵御同一系统用户恶意代码的独立可信根。
- `run_dir` 内 manifest 和哈希仅用于内部一致性，不再是真实性来源；目录或证据文件是符号链接时直接拒绝。
- WorkBuddy/MCP 重启后旧 receipt 自然失效。旧产物可以查看，但要继续真实写入必须重新 capture 和两阶段 prepare，不得从可写目录恢复信任。
- 能控制同一 macOS 用户任意进程的恶意代码仍可能自行构造 pipe/fd；这不属于应用层 receipt 能解决的边界。正式分发必须依赖 WorkBuddy 的 OS 进程沙箱/最小终端权限隔离 MCP 与任意同用户代码。

用户只感知两次安全决策：

1. 首次只读整理前，确认范围、深度和打开专用浏览器；列表与详情固定每组最多 200 条、非末组间隔 3 分钟，不要求用户配置。一次确认覆盖同一浏览器里的列表读取和详情补齐。
2. 真正写入前，一次确认待创建专辑及隐私、逐条映射和移动上限。

`login`、`capture`、`prepare` 是代码内部的安全边界，不得变成让用户关窗口、抄 URL 或理解 profile 的操作说明。

普通整理完成后直接在当前对话里给出简短纯文本结果。若整理前记录 `generate_report=true`，最终完整成员回读通过后，额外运行固定静态生成器，把完整专辑报告写到桌面；缺页、重复、数量变化或分类/成员不一致时停止，不得补分析。`generate_report=false` 时不生成。

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
