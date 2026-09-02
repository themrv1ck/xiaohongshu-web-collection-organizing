---
name: xiaohongshu-web-collection-organizing
description: "Organize Xiaohongshu collections with strict first-archive protection. Version 2.3.0 reads album cards and members through visible Arc pages, creates albums through the visible form, and can archive only a currently-uncollected new note through the visible Collect then Add to album flow. Historical collected notes are never uncollected, re-collected, or moved."
---

# 小红书工作流 Skill

This is the umbrella for Xiaohongshu web workflows. Use the collection/liked organizing sections only to archive notes that are not in any album; use the single-note research section below for one shared note URL.

## 2.3.0 Arc 可见页面合同（最高优先级）

只有用户在当前回合明确授权 Arc 时，才可绑定已打开的唯一 Arc 标签页；不启动、切换或接管 Chrome、Edge、Safari 或 Playwright 浏览器。

- `capture_board_snapshot.py` 只读正式页面可见的专辑卡片和笔记卡片；专辑总数、成员总数、id 和名称必须完整一致。缺页、重复、数量变化或身份变化立即报错停止，不允许猜测。
- `create_board.py` 只可通过正式页面的“创建专辑”表单建立一个已明确批准的专辑；提交前后都要回读专辑清单，并确认总数精确增加 1、名称唯一、隐私一致、新专辑为空。
- `collect_new_note.py` 只处理“本次操作前尚未收藏”的新笔记：点击可见的收藏按钮，等待“加入专辑”，选择可见的唯一目标专辑，再回读目标成员必须精确等于原集合加该笔记。
- 只要笔记在本次操作前已经收藏，无论它是否已在专辑中，都不得取消收藏、重新收藏或移动。`run_reassign_batch.py --execute` 继续硬停。这是“第一次归档之后永久保护”的实现边界。
- 所有当前能生成或下发的页面任务禁止 `webpackChunkxhs_pc_web`、`req.m`、`/api/sns/web/v1/board`、`/api/sns/web/v1/note/move` 及任何同类私有模块/接口。历史离线合同代码不得进入浏览器。出现 300031、安全验证、登录页或页面绑定丢失时立即持久化停机。

WorkBuddy Plugin 2.3.0 没有接入上述 Arc 可见页面适配器，因此其 `login/capture/prepare/execute` 仍不可执行；不得把 Arc 直接路径冒充为 WorkBuddy 功能。下面的 WorkBuddy 和历史合同仅供数据结构参考，冲突时以本节为准。

## WorkBuddy 历史合同（当前不可执行）

先检查当前工具集中是否同时存在 `xhs_workbuddy_status`、`xhs_workbuddy_setup`、`xhs_workbuddy_login`、`xhs_workbuddy_capture`、`xhs_workbuddy_prepare`、`xhs_workbuddy_execute`。六个工具同时存在，才视为已检测到 WorkBuddy Plugin；不得用进程名、应用标题、环境猜测或模型自述判断宿主。

检测到 WorkBuddy Plugin 后：

1. 浏览器阶段只允许调用上述 `xhs_workbuddy_*` 工具。禁止直接运行 `osascript`、Safari/Arc/Chrome Apple Events、Computer Use、CDP，禁止调用脚本时传 `--browser safari|arc|chrome` 或 `--backend macos-*`。
2. 先调用 `xhs_workbuddy_status`，并确认返回 `plugin_version=2.3.0`；缺失或版本不同都按第 11 步走官方 Plugin 安装/更新入口，重开 WorkBuddy 后再继续。版本正确但 `install_required=true` 时，先取得一次依赖安装同意，再调用 `xhs_workbuddy_setup(install_dependencies=true)` 并复验 status。Windows 只安装 Python Playwright 并检查系统 Edge，不下载 Chromium；macOS/Linux 安装插件独立 Chromium。
3. 范围和整理深度确定后，WorkBuddy 的 capture 必须显式传唯一档位：快速整理为 `organizing_depth=quick`，轻度整理为 `organizing_depth=light`。轻度或深度整理必须在任何浏览器、采集或内容处理开始前，单独询问一次“整理完成并核验后，是否需要在桌面生成小红书专辑 HTML 报告？”并把回答固定为 `report_requested=true|false`；快速整理不询问并固定为 `false`。当前 WorkBuddy Plugin 尚未接入视频语音与完整时轴画面证据，也尚未在 capture 前接入 `archived_notes_registry.json`。若用户选择深度整理，或明确要求“已有收藏夹成员不再读取/识别/分类”，必须在打开浏览器前停止，不得把视频元数据冒充深度结果，也不得先 capture 全集再晚排除；只有用户另行授权非 WorkBuddy 的具体浏览器后，才可改走下面支持预分析归档排除的直接路径。其他快速或轻度场景确定后，只询问一次是否允许打开 WorkBuddy 专用浏览器完成本轮只读整理。WorkBuddy 固定每组 200 条、非末组间隔 3 分钟，这两个参数不暴露给模型或普通用户修改，也不得重复询问。用户同意后，这次授权覆盖本轮 `login → capture（轻度含同会话登录态详情补齐与本地 OCR）→ prepare` 三个只读阶段。
4. 获得授权后调用 `xhs_workbuddy_login(browser_authorized=true,source=collection|liked)`。首次使用时用户只需在可见的 WorkBuddy 专用浏览器完成登录：Windows 使用系统 Edge 程序和插件独立 profile，macOS/Linux 使用插件独立 Chromium。插件必须自动识别当前账号、进入所选范围、保存精确 URL、关闭自己的全部窗口并确认 profile 已释放；不得要求用户打开目标页、关闭窗口或复制 URL。
   调用 capture 时还必须显式传 `generate_report=true|false`，且与第 3 步记录的回答一致；不得在整理结束后临时改变选择。
5. 登录工具返回后，立即把其 `target_page_url` 原样传给 `xhs_workbuddy_capture(browser_authorized=true,organizing_depth=quick|light)`，不得再次询问或让用户粘贴地址。WorkBuddy 插件必须在同一个专用浏览器会话中自动翻页：固定每 200 条独立保存一组，非最后一组真实等待 3 分钟后继续。轻度整理必须在关闭这次浏览器前，用只存在于插件进程内的原始卡片链接逐条打开全部已授权详情，用权威 `noteData.type` 纠正列表类型并取得完整图片列表；随后必须用同一个登录态 BrowserContext 下载图片字节到权限为 0600 的运行目录，只把相对本地路径与内容 SHA256 写入 `image_items.json`，清除源图片 URL 后再关闭浏览器并运行本地 OCR。Cookie、卡片原始 query、签名图片 URL 和 xsec 不得落盘、返回模型或出现在错误中。页面到底后只能在前端声明总数每次连读均未变化、真实唯一条数完全相等、且 `page_index ↔ note_id` 双向唯一并精确覆盖 `0..总数-1` 时结束；任一条缺少索引、同一笔记占多个位置、同一位置换笔记、总数变化/缺失、数量不符或索引缺口，都要保存已读数据并硬停，禁止把首屏约 10 条称为完整范围。列表采集不得点击、刷新、自动重试或写账号。只有 `ready_for_classification=true` 时插件才签发 capture receipt；WorkBuddy 必须自动传递，不得让用户查看、复制或保存。
6. 抓取完成后，先调用一次不带 `classification` 的 `xhs_workbuddy_prepare(...)`，并自动传入 capture 返回的 `evidence_receipt`。这一次只读生成与本次账号、页面和 `verify_pages` 绑定的完整 `board_snapshot.json`。工具把所有已属于任一专辑的 note id 视为“首次归档已确认”并进入永久保护，只返回尚未首次归档的专辑外笔记脱敏 `classification_inputs`，同时返回 `phase=board_inventory`、`existing_board_names`、`protected_existing_board_member_count` 和新的 inventory receipt。`classification_required=true` 表示继续分类；即使 `existing_board_names=[]` 也不是失败，必须进入第 7 步，禁止生成任何固定默认类别。
7. 只能使用第一次 prepare 返回的 `classification_inputs` 为专辑外真实 note id 分类。不得补回、识别、评价或纠正已保护的专辑成员。优先选择 `existing_board_names`；只有真实内容确实需要且没有合适专辑时，才可从本次输入归纳最多 20 个 `proposed_board_names`，不得从模板、示例或插件注入类别。存在提议时必须明确 `new_board_privacy=public|private`，并让每个提议至少被一条真实分类使用；无法准确判断的条目保持 `target_board=""`，由第二次 prepare 机械转入固定专辑“无法确定”，不得猜入其他专辑。禁止模型读取运行目录或原始 URL/凭据；轻度 OCR 失败不得静默改用元数据。
8. 把只覆盖 `classification_inputs` 的完整逐条分类、可选的 `proposed_board_names`、对应隐私和 inventory receipt 自动传给第二次 `xhs_workbuddy_prepare(...)`。工具必须机械补入已保护行，写入 `archive_lifecycle_state=first_archive_confirmed` 并保持 `target_board=""`；专辑外空目标行必须写成 `target_board="无法确定"`、`uncertain_assignment=true`、`review_state=manual_reclassification_required`。若真实专辑清单中没有“无法确定”，工具自动把它加入同一次待创建清单并要求 `new_board_privacy=public|private`；不得生成其他默认类别。随后核验完整抓取、OCR、分类 ID、真实已有专辑和待创建名称；审批摘要只能包含专辑外笔记的待创建专辑、隐私、逐条移动与上限。第二次调用只生成 dry-run，不打开浏览器、不创建专辑。只有返回 `phase=dry_run`、`mode=dry_run`、`ready_for_execute=true`、`blockers=[]`、`planned_move_count>0`、非空 `approval_digest` 和 plan receipt，才可请求一次执行确认。
9. 普通整理结果直接在当前对话里用简短纯文本报告，不调用可视化 Skill、可视化指南、组件渲染、仪表盘或 `present_files`，也不为已有 JSON 产物额外生成展示文件。唯一固定例外是第 3 步已记录 `report_requested=true`：执行与最终只读成员核验全部成功后，先用 `scripts/analyze_image_ocr.py <同批完整分类> <image_analysis.json> --analysis-provider <已明确选用且可用的 provider>` 把完整图文 OCR 转成整体概括，再运行 `scripts/generate_collection_report.py --board-snapshot <最终完整快照> --classification <同批完整分类> --image-analysis <image_analysis.json> --output "$HOME/Desktop/我的小红书专辑整理报告.html"`。生成器按专辑列出主题、内容类型、已保存主题和全部笔记摘要；原始 OCR 只作为摘要证据，不得直接冒充报告正文或写进 HTML。图文摘要必须与当前标题、OCR、OCR 指纹和图片数的来源哈希完全一致，缺项、重复或来源变化直接停止。成员与分类缺页、重复、数量变化或目标不一致时同样直接停止，不得重读笔记或猜写内容。除此之外，只有用户明确要求图表、网页或文件交付时才允许生成展示文件。普通文本报告仍只列工具顺序、抓取/分类数量、`mode`、`ready_for_execute`、`blockers`、`warnings`、`planned_move_count`、是否写入账号、`run_dir`，以及已正确归档/待复核条目。
10. 用户明确确认待创建专辑及隐私、逐条映射与本次移动上限后，原样传回绑定这些内容的 `approval_digest` 并调用 `xhs_workbuddy_execute(...)`。Python 按 receipt 哈希把最终输入读入内存，启动本轮专用浏览器并核验精确 profile、`tab` 与前端“我”账号；MCP 重算哈希并回传 `COMMIT` 后，若有待创建专辑，必须在同一个 BrowserContext 中逐个创建，核验名称、隐私和空成员，再开始移动。若同名专辑已因前次不确定写入而存在，只有隐私一致且确认为空时才可继续；任一创建写入状态不确定都要安全停机，禁止继续移动。不得直接运行 `run_reassign_batch.py --execute` 或修改 JSON 绕过 receipt。
11. 如果六个工具缺少任何一个，且当前环境存在 `WORKBUDDY_CONFIG_DIR`，不要让普通用户寻找连接器页面、编辑 JSON、粘贴命令或处理 MCP 名词。只说明：“小红书插件需要一次性启用；回复‘启用’后，我会用 WorkBuddy 官方安装器安装或启用唯一的 `xiaohongshu-organizer`，然后你只需重开一次 WorkBuddy。”用户明确回复“启用”后，运行 `python3 scripts/enable_workbuddy_mcp.py --install-plugin`。脚本必须只从固定 GitHub marketplace `themrv1ck/xiaohongshu-web-collection-organizing` 安装/启用 `xiaohongshu-organizer@xiaohongshu-skill-marketplace`，再只把 `xiaohongshu-organizer` 加入 MCP 白名单；已有本地开发 marketplace 不得更新或覆盖，其他设置必须完整保留。任一步失败都不得写入成功状态。返回 `restart_required=true` 后，只让用户完全退出并重开 WorkBuddy，再重发原请求。其他宿主仍按“WorkBuddy Plugin 未安装或未加载”停止；不得退回 Safari、Arc、系统 Chrome，也不得用普通终端脚本冒充插件流程。

WorkBuddy 路径由插件显式注入 `XHS_HOST=workbuddy` 与真实平台；浏览器入口会在代码层强制 Windows 为 Playwright `msedge`、macOS/Linux 为 `chromium`，始终使用可见窗口和插件独立 profile，并拒绝 CDP、headless 与用户日常浏览器目录。这条约束与选择 GLM、Hy3 或其他模型无关。完整合同见 `references/workbuddy-plugin.md`。

## 单篇笔记研究 / note research

Use this subsection when the user gives one Xiaohongshu note URL and asks to “研究一下”, summarize, extract value, inspect comments, or evaluate product/market implications. Do not run account-changing collection scripts for this task.

Workflow:
1. Try the user-authorized browser only. If it shows login, IP-risk, security verification, or abnormal access, stop this session immediately; do not fall back to mobile HTML, reload, scroll, or retry.
2. Only after a fresh user-approved session may the note page be fetched and `window.__SETUP_SERVER_STATE__` / `LAUNCHER_SSR_STORE_PAGE_DATA.noteData` be parsed when present.
3. Extract title, desc, tags, user, interaction counts, image list, comments/subComments, and author context.
4. Download images only when needed for visual/OCR analysis, using normal browser headers and no credential leakage.
5. Synthesize value: what the post says, evidence from comments/engagement, implementation details, user pain points, competitor mentions, and actionable recommendations.
6. Answer in Chinese unless asked otherwise, with conclusion first and evidence compactly.

See `references/xiaohongshu-note-research.md` for the archived narrow workflow.

## 专辑整理历史合同（当前不可执行）

当用户要求整理“小红书收藏 / 点赞 / 我的收藏 / 专辑分类”且本轮尚未明确范围时，先显示首次使用欢迎卡片，说明 Skill 能读取用户选择的收藏/点赞范围、根据实际内容生成专辑分类建议，并在用户再次明确授权后执行移动；随后让用户回复“收藏”“点赞”或“我全都要”。用户回答“收藏”就只整理收藏里的笔记；回答“点赞”就只整理点赞里的笔记；回答“我全都要 / 全部 / 都要”就把点赞和收藏合并去重后一起整理。**首次归档锁定规则：当前不属于任何专辑的笔记处于 `first_archive_pending`，允许完成一次首次归档；只有移动成功且实时回读确认已进入目标专辑后，才转为 `first_archive_confirmed` 并永久保护。当前快照中已属于任一专辑的笔记视为首次归档已经完成，以用户现有整理结果为准，禁止重新分类、跨专辑移动、清理或模型纠错。dry-run、失败、中止或未回读确认都不能提前触发保护。该规则不提供开关。**采集按本地保存的 200 条被动分段进行：用户手动停在目标列表位置，Skill 只读取已显示卡片，不自动翻页、点击、刷新或进入下一段；全部已保存分段在本地合并、分类，再由用户明确开启写入会话。只有用户明确把范围改为“当前可访问的 N 条真实笔记”时，直接路径可使用受审计的 `collection_scope.json`；它不是“收藏”的静默降级。

允许的动作：
- 可核对目标专辑是否已存在，并把缺失专辑写入 `created_boards.json`；只可在用户明确授权后创建新专辑，删除、重命名、合并或清理现有专辑不属于本整理流程。
- 可通过 `capture_board_snapshot.py` 只读 Arc 正式页面可见的专辑卡片和成员卡片，完整一致时生成 `board_snapshot.json`。
- 可通过 `collect_new_note.py` 把尚未收藏的新笔记收藏后立即加入既有专辑；已收藏笔记一律拒绝写入。
- 只要下一步明确，就持续推进到全量完成、失败项入队、最终核验，不要在中间阶段只做总结就停止。

硬性边界：
- 所有已收藏笔记都不允许删除、取消收藏、重新收藏或移动；可见页面写入只允许处理本次操作前尚未收藏的新笔记。
- 选择“点赞”或“我全都要”时，不得取消点赞、删除互动记录或把点赞来源静默丢弃；抓取和报告中必须保留 `source_lists` / `source_primary`，能区分笔记来自收藏、点赞或二者都有。
- 不得把未分类、抓取失败或移动失败的笔记静默丢弃；必须写入 `retry_queue.json` / `run_report.json`。
- 没有 `board_snapshot.json` 和 `created_boards.json` 时，`run_reassign_batch.py` 只会生成 `mode=classification_preview`、`ready_for_execute=false`、`missing_boards=null`；不得把分类预览称为 dry-run，不得用 `missing_boards=null` 或空值声称目标专辑已存在。
- WorkBuddy 的最终说明必须逐字服从 `xhs_workbuddy_prepare` 返回值及其 `run_report.json` 的 `mode`、`ready_for_execute` 与 `blockers`。只有 `mode=dry_run`、`ready_for_execute=true`、`blockers=[]` 且存在 `approval_digest` 才能展示可执行计划；任何其他状态都必须停止，禁止声称产物可直接复用执行。
- 收藏整理流程不得删除、重命名、合并、清理现有专辑，也不得迁移其成员；这些动作不属于本 Skill 的整理权限。
- 不得把完整小红书 URL query、`xsec_token`、cookie、signed media URL、`sign` 参数或任何疑似凭据写入模型上下文、正式报告、Telegram/Discord 回复或日志摘要；只保留标准 `/explore/<note_id>`、标题、作者、公开计数和分类所需普通文本。历史上完整 `xsec_token` 链接曾触发 GPT `cyber_policy` 误判。

## 稳定工作流
0. 首次运行顺序固定为“整理范围 → 本地能力只读预检 → 快速启动档位 →（仅自定义时）图文 OCR / 视频内容分类”。只读预检在范围确认后自动执行，不需要单独开关；它不访问浏览器、不联网、不安装软件、不加载大模型。快速启动档位会直接确定两个功能开关；只有用户回复“自定义”时，才逐项询问 OCR 和视频。
   - 先显示欢迎和整理范围卡片：

     > **欢迎使用小红书收藏整理 Skill**
     >
     > 这个 Skill 可以帮助你：
     >
     > - 读取你选择的小红书收藏列表、点赞列表，或两者合并后的内容；
     > - 根据笔记实际内容生成专辑分类建议；图文可选择 OCR 逐张读取封面和全部内页图片中的可见文字，视频可选择结合语音文字和完整时轴画面；
     > - 先提供分类结果和模拟执行报告，只有再次得到你的明确授权后才会移动笔记。
     >
     > **首次使用，请在下方回复本次整理范围：**
     >
     > 1. 回复“收藏”：只整理收藏列表；
     > 2. 回复“点赞”：只整理点赞列表；
     > 3. 回复“我全都要”：合并收藏和点赞，并按笔记去重。

   - 收藏：抓取收藏页，`source=collection`。
   - 点赞：抓取点赞页，`source=liked`。
   - 我全都要：先抓收藏再抓点赞，按 note id 合并去重；同一笔记同时在收藏和点赞中出现时，`source_lists` 应包含两个来源。
   - 范围确认后，当前宿主 Agent 先如实声明自身视觉能力为 `ready`、`unavailable` 或 `unknown`；无法证明时必须使用 `unknown`。随后运行 `python3 scripts/check_environment.py --capability-preflight`；已明确知道宿主具备视觉能力时可加 `--host-visual-capability ready --host-visual-name "<名称>"`。不得同时传浏览器、`--video-content`、analysis provider 或安装参数。
   - 读取 `capabilities.ocr`、`capabilities.video_audio.local_asr`、`capabilities.local_visual` 和 `capabilities.host_visual`，先向用户显示：

     > **已完成本地能力检查**
     >
     > 这次检查只查看已有工具和已配置模型，不会打开浏览器、联网、安装软件或加载大型模型。
     >
     > - 图文 OCR：`<已找到：名称，可直接复用 / 未找到，推荐安装>`
     > - 声音识别：`<已找到：名称，可直接复用 / 未找到，按需安装>`
     > - 画面识别：`<已找到：名称，可直接复用 / 未找到，按需安装 / 当前 Agent 无法确认，按需检查>`
     >
     > “推荐安装”表示它是图文收藏整理的轻量基础能力；“按需安装”表示只有后续选择视频内容识别时才需要。
     >
     > 检查结果只说明电脑里有什么，不代表已经开启任何功能。

   - 显示预检结果后，先显示快速启动卡片：

     > **选择整理深度**
     >
     > 选一个档位即可。它只决定本次要不要使用内容识别，不会打开浏览器，也不会移动收藏。
     >
     > 1. **快速整理｜只按标题、正文、标签和作者；不做 OCR 或视频识别**
     > 2. **轻度整理｜读取图文全部图片文字；不分析视频**
     > 3. **深度整理｜图文 OCR + 视频语音 + 完整时轴画面**
     >
     > 请回复“快速整理”“轻度整理”或“深度整理”。如果想自己逐项决定，可以回复“自定义”。

   - 若宿主使用按钮或选择题工具显示上述卡片，三个按钮的 `label` 必须逐字使用上面粗体内的完整文案，让用户不点开其他说明也能看到差异；不得缩写成单独的“快速整理 / 轻度整理 / 深度整理”，不得给任何档位添加“推荐”，也不得默认选中某一档。
   - 用户选择“快速整理”：写入 `classify_images_with_ocr=false`、`classify_video_by_content=false`、`visual_analysis=false`；跳过图文 OCR 和视频卡片，不安装、不运行 OCR、语音或画面模型，分类时显式传 `--skip-ocr`。
   - 用户选择“轻度整理”：写入 `classify_images_with_ocr=true`、`classify_video_by_content=false`、`visual_analysis=false`；跳过视频卡片，按图文 OCR 的既有安装与复验规则执行，不安装、不运行语音或画面模型。
   - 用户选择“深度整理”：写入 `classify_images_with_ocr=true`、`classify_video_by_content=true`、`visual_analysis=true`；跳过两张逐项功能卡，按下方 OCR、语音和画面功能的既有安装与复验规则执行。若发现多个可用视觉能力，仍必须让用户选择 analysis provider；不得默认固定为 Codex CLI。
   - 用户选择轻度或深度后、开始任何整理步骤前，必须询问一次“整理完成并核验后，是否需要在桌面生成小红书专辑 HTML 报告？”。把肯定回答写成 `report_requested=true`，否定回答写成 `false`；快速整理固定为 `false`，不询问。该选择只控制最终静态报告，不扩大浏览器、分析或移动授权。
   - 选择“轻度整理”或“深度整理”仅授权所选内容识别及其既有缺失组件安装流程。它不授权打开外部浏览器，也不授权移动收藏；系统权限窗口仍由用户确认。
   - 只有用户回复“自定义”时，再显示图文 OCR 卡片：

     > **图文 OCR 识别**
     >
     > 是否开启？
     >
     > OCR 就是“图片文字识别”：把图片中看得见的文字转成可用于分类的文字资料。它会逐张读取图文笔记的封面和全部内页图片，而不是只看封面。
     >
     > 封面本身就是一张图片；只要图片里有清晰可见的文字，OCR 就能识别。
     >
     > 例如：标题只写“建议收藏”，但内页图片写着“上海两天一夜路线、餐厅和交通方式”。OCR 读出这些内容后，就能判断它更适合归入“旅行 / 上海攻略”。它也能补足标题、正文或标签没有写出的型号、地点、清单、步骤、菜名和商品名。
     >
     > **为什么推荐开启？** 在常见的小红书收藏整理场景中，按图文笔记占比较高的情况估算，OCR 约可覆盖 **60%** 的内容识别需求，而且默认组件通常很轻量。这里的 60% 指可参与分类的场景覆盖估算，不是 OCR 的固定识别准确率；实际比例取决于收藏中图文笔记的占比。
     >
     > OCR 只提取图片里的可见文字；没有文字的纯画面不属于 OCR，OCR 不能理解其中的人物、物体、场景或动作，也不负责分析视频画面。
     >
     > 回复“开启”：使用刚才检查到的中文 OCR；如果没有，则安装当前系统的推荐组件。安装前显示具体组件、预计下载量和磁盘占用；系统权限窗口仍需用户确认。
     >
     > - macOS：优先复用系统 Vision，OCR 模型本身通常是 **0 MB 额外下载**；缺少 Swift/Command Line Tools 时需要 GB 级系统组件，实际大小以 macOS 安装窗口为准。
     > - Windows：默认安装 Tesseract + 简体中文 `chi_sim`，通常为**几十到数百 MB**，实际大小以当前安装器为准。
     > - EasyOCR：只在用户明确选择时使用，通常是 **GB 级**，不自动作为默认方案。
     >
     > 回复“不开启”：不安装、不运行图文 OCR；刚才的只读检查结果不会被用于分类。仍可按标题、正文、标签和作者分类，但准确率可能下降。

   - 自定义时 OCR 回复“开启”：已有能力直接复用；缺失时展示实际安装项和体积，按当前平台安装，再运行 `python3 scripts/check_environment.py --ocr` 复验。该同意不包含浏览器、视频功能或移动笔记。
   - 自定义时 OCR 回复“不开启”：不安装、不运行 OCR，后续分类显式使用 `--skip-ocr`，不得把预检发现的 OCR 静默用于分类。
   - 只有用户回复“自定义”并得到 OCR 回答后，再显示下面的视频功能卡片：

     > **是否开启视频内容识别？**
     >
     > 开启后，Skill 会根据视频的实际声音和画面判断主要内容，再进行分类，不再只依赖标题和简介。
     >
     > 例如：
     >
     > - 标题没有说明主题，但视频讲解包含完整步骤。声音分析可以先识别真实主题，再与本次分类体系匹配。
     > - 视频里没人说话，只用画面演示一个过程。只分析声音时无法判断；同时分析画面后，才能依据真实内容分类。
     >
     > 这项功能包含两部分：
     >
     > - **语音识别（耳朵）**：把字幕或视频中的人声整理成文字。
     > - **画面识别（眼睛）**：从视频开头到结尾分段查看真实画面，识别物品、场景、动作和画面文字。
     >
     > 刚才的预检已经查过现有能力：已有能力直接复用。只有所选能力缺失时才安装；本地语音模型 `MiMo-V2.5-ASR-MLX` 约 **6.6 GB**，本地视觉模型 `MiMo-VL-7B-RL-2508` 约 **16.6 GB**，两者都缺失时合计约 **23.2 GB**。运行本地视觉模型建议电脑具备 **32 GB 内存**；如果已有可用的视觉 AI，就不需要重复安装。
     >
     > 请选择一个选项：
     >
     > 1. **声音和画面都分析**：使用字幕、视频语音和完整时轴真实画面，结果最完整；复用已有能力，缺少哪一项才安装哪一项。
     > 2. **只分析声音**：只使用字幕和视频语音，不查看画面；无声视频或声音与画面内容不同的视频可能无法准确分类。缺少本地语音能力时才安装语音模型。
     > 3. **不开启**：不安装、不运行视频内容识别，仍按标题、简介和标签分类。
     >
     > 此处只选择功能，不会打开浏览器，也不会移动任何收藏。

   - 用户不开启：不运行视频依赖检测或安装，沿用普通分类流程。
   - 用户回复“声音和画面都分析”（兼容“耳朵和眼睛都要”或裸回复“开启”）：视为同意在所选能力缺失时安装本地 ASR 与本地视觉模型。预检已发现可用视觉 Agent 或模型时优先复用；发现多个可用视觉能力时列出名称让用户选择，不得默认固定为 Codex CLI。随后取得当前回合对具体已登录浏览器的明确授权，再按 `references/video-content-classification.md` 做完整环境复验。其他缺失组件仍须逐项取得安装同意。
   - 用户回复“只分析声音”（兼容“只听声音”或“开启，仅文字稿”）：仅使用平台字幕或本地 ASR，缺失时同意安装本地 ASR，不安装 MiMo-VL；结果必须标为 `transcript_only`，不得声称检查过画面。
1. 确定快速启动档位，或在自定义路径完成两个功能开关后，再做所选路径的完整环境复验：操作系统、明确授权的浏览器网页登录态、浏览器自动化后端，以及用户已开启的能力。首次预检不能替代这里的完整验证。

   - 在第一次访问小红书前，向用户显示：

     > **本次低风险整理方式**
     >
     > 我会每次只读取你当前已经打开并显示出来的一段内容，最多 200 条；保存到本地后就停下，不会自己翻页、刷新、点开详情或进入下一段。分类会尽量在本地完成，移动也会单独等你确认。
     >
     > 这能减少不必要的连续请求，但不能保证平台一定不会要求验证。如果页面出现安全验证、异常访问或登录提示，我会立刻停止，不会尝试绕过；需要你在平台内处理后，再开始一个新的会话。

     > 同一轮整理必须共用同一份 `xhs_safety_state.json`。后续脚本会继承输入文件旁已有的状态；如果把文件分开放，所有访问小红书的命令都要显式传同一个 `--safety-state <路径>`。

   - **只有图文 OCR 开关已开启时**，运行 `python3 scripts/check_environment.py --ocr`（Windows：`python scripts/check_environment.py --ocr`），读取 `ocr_checked` / `ocr_status` / `ocr_ready` / `ocr_provider` / `tesseract_chi_sim` / `ocr_install_size`。若缺失，按 `references/image-ocr-classification.md` 安装并复验；不得重复询问安装同意，但系统权限窗口仍由用户确认。
   - 图文 OCR 开关未开启时，不得运行 `--ocr`、不得安装 OCR、不得执行 `enrich_note_images.py` 或 `ocr_note_images.py`。PaddleOCR 不是受支持 provider；Tesseract 缺少 `chi_sim` 时不得静默回退英文，EasyOCR 也不得自动作为默认替代。
   - WorkBuddy 先按“WorkBuddy 固定入口”分流，只能使用插件管理的独立浏览器：Windows Edge，macOS/Linux Chromium；不要检查或请求 Safari/Arc/Chrome/Edge 的系统自动化权限。
   - 非 WorkBuddy 的 macOS 直接使用路径不自动选择浏览器。先取得当前回合对具体浏览器的明确授权，再检查该浏览器的小红书登录态和自动化能力；用户选择 Arc 时使用 `--backend macos-arc` / `--browser arc`。
   - Windows 默认走 Chrome/Edge + Playwright 或已启动浏览器 CDP；OCR 走 Tesseract 或 EasyOCR，必须使用用户自己的网页登录态，不抓取或复制敏感 token。
   - 如果 Chrome 未登录但用户说“用 Safari”，立即切换 Safari，打开 `https://www.xiaohongshu.com/explore` 并验证 Safari 登录态，不要继续卡在 Chrome。
   - 如果目标浏览器未登录：明确告诉用户需要手动登录；不后台轮询、不自动续跑。用户确认完成后，必须从新的只读会话开始。
   - 登录态必须同时满足：存在有效会话 cookie，且现有小红书标签页不在 `/login`、不显示“手机号登录 / 登录后推荐 / 马上登录即可 / 扫码”等提示。仅有旧 cookie 或缓存页面不得判定为已登录。
   - 默认被动采集不执行 `window.scrollTo`、`window.scrollBy`、刷新或导航。每段最多 200 条，保存后停下；用户手动滚动到下一段并再次明确开始后，才采集下一段。
   - 不要把小红书 UI 收藏总数直接当作已抓取条目总量。必须同时记录页面声明数、已保存分段数和本地合并数；在所有分段完成前不得宣称已全量整理，也不得尝试移动未抓到的对象。
   - 页面声明数与真实 note id 数不一致时，默认仍是硬停。只有用户明确说“按当前可访问的 N 条真实笔记整理”时，才可运行 `scripts/collection_scope.py <run_dir> <visible_items.json> <collection_scope.json> --scope-kind user_confirmed_accessible_collection --expected-accessible-count N --expected-unidentified-count M`。它必须验证同一 active 安全会话、同一 passive source/page 绑定、分段哈希和精确 `page_index=0..N-1`，并记录 `M=页面声明数-N`；不得伪造 M 个 note id、不得把它们列为排除项或成功项。全量 `收藏` / `点赞` 仍只能用 `scope_kind=full_collection`，声明数必须完全一致。
   - Safari 自动化细节见 `references/safari-web-automation-notes.md`。
   - Safari 多标签页时，不要默认操作 front window/current tab；应优先定位 URL 包含 `xiaohongshu.com` 的标签页，否则容易在 B 站/其它网页上执行并误判失败。
   - **只有视频内容分类开关已开启时**，运行 `python3 scripts/check_environment.py --video-content --browser <用户授权浏览器> --analysis-provider <codex-cli|mimo-vl-mlx|command>`；用户选“声音和画面都分析”时必须再加 `--visual-analysis`，“只分析声音”时不加。Arc 还应加 `--check-login-state`。读取 `capabilities.asr.ready`、`capabilities.text_analysis.ready` 和 `capabilities.visual_analysis.ready/status`；所选路径需要的能力未就绪时不得继续。
   - 如果 `missing` 非空，只向用户展示实际缺失项、用途、执行位置和可复制安装命令。“声音和画面都分析”已覆盖缺失 ASR 与所选本地视觉模型的安装同意；“只分析声音”只覆盖缺失 ASR，不授权安装视觉模型。其他缺失项必须另行询问，未获明确同意不得安装。用户拒绝必要依赖时停止视频内容分类分支，不得改用简介给视频分类。
2. 抓取用户所选范围的当前已显示条目，单段最多 200 条，写入独立的 `segment-###-visible.json` 和 manifest；脚本默认 `--capture-mode passive`，一次只读一次 DOM，不自动滚动、点击、刷新或进入下一段。全部分段只在本地经完整覆盖校验合并成 `visible_items.json` 后再分类；用户确认的可访问范围必须由上一条的 `collection_scope.json` 建立，后续 `enrich_note_images.py`、`ocr_note_images.py`、视频、分类和 dry-run 都传同一个 `--collection-scope`。
   - 每段 manifest 必须写 `capture_mode=passive`、`segment_limit=200`、`stopped_reason` 和 `next_action`。`segment_limit_reached` 只表示本段已到上限，不表示完整列表已读完。
   - macOS Chrome 执行复杂/长 JS 时，不要把整段 JS 直接插入 AppleScript `execute javascript \"...\"` 字符串；这容易触发 `预期是“\\\"”，却找到未知的记号 (-2741)`。应把 JS 写入临时 `.js` 文件，用 AppleScript `read POSIX file ... as «class utf8»` 读入变量后 `execute javascript jsSource`，执行后删除临时文件。
   - 每次修浏览器抓取器后，只能在用户本轮明确授权的浏览器上跑一段被动只读探针；不得用自动滚动探针。
   - “我全都要”时，收藏与点赞各自按被动分段保存；本地按 note id 合并，并保留来源列表。
   - 抓取结果必须保留 `content_type`。开启视频内容分类后，只有明确的 `video` 进入视频链路；`unknown` 不得按简介猜测。用户已明确授权详情且给出上限时，直接路径可在 `enrich_note_images.py` 加 `--resolve-unknown-content-types`，从详情权威 `noteData.type` 确认后才进入图文或视频链路；否则保持人工复核。
   - 列表页取得的 `cover_image_url` / `image_urls` 和 `content_type` 都只是 observed 线索：图片必须写成 `image_urls_complete=false`，不得作为完整图片集合；类型只用于决定详情补齐候选。详情页 `LAUNCHER_SSR_STORE_PAGE_DATA.noteData.type` 才是图文/视频类型的权威来源。
3. 必须在任何详情补齐、OCR、视频下载、转写或视觉分析之前，先用本轮完整只读专辑快照和 `scripts/build_existing_boards_inventory.py` 建立 `existing_boards_inventory.json`。当前快照中的全部专辑成员是“首次归档已确认”的实时证据，统一进入保护集合；当前不在任何专辑的笔记仍是 `first_archive_pending`，不能提前保护。这是固定规则，不询问用户，也不存在重组开关。上一轮 `archived_notes_registry.json` 只用于审计连续性，不能替代本轮快照，也不能把本轮已不在任何专辑的笔记伪装成当前专辑成员。把本轮 inventory 作为 `--archive-registry <路径>` 传给 `enrich_note_images.py`、`ocr_note_images.py`、`transcribe_video_items.py`、`analyze_video_visuals.py` 和 `classify_items.py`。这些脚本必须先验证完整 `collection_scope`，再在访问详情、OCR 或视频前按 ID 排除；禁止先分析全集、到分类或移动阶段才排除。受保护 ID 不得进入 analysis provider，也不得因为缓存缺失而重跑。
4. 图文 OCR 开关开启时，先在本地确定本次详情补齐范围。WorkBuddy 轻度整理必须在 `xhs_workbuddy_capture` 中传 `organizing_depth=light`，由同一登录态前端会话按每组最多 200 条、组间默认 3 分钟补齐所选范围，并在浏览器关闭后运行本地 OCR；严禁运行 `enrich_note_images.py`。其他宿主的 `enrich_note_images.py` 默认不会访问详情；只有用户明确同意这一次请求且给出 1–200 条上限，才传 `--allow-detail-requests --max-items <n>`；需要纠正 `unknown` 时额外传 `--resolve-unknown-content-types`。用户已授权 Arc 时必须同时传 `--browser arc --arc-profile <当前资料目录>`：脚本只在内存中复用当前收藏 API 的单条访问上下文，绝不落盘或输出 xsec/query；缺少该上下文时失败，不回退成无登录态猜测。两条路径都必须从详情权威 `noteData.imageList` 为明确图文笔记取得按原顺序排列的封面和全部内页图片列表，并用详情 `noteData.type` 覆盖 observed 类型；只有权威 `image_list_source`、`image_enrichment_status=ok`、`image_urls_complete=true` 且声明数量一致时，才允许 `scripts/ocr_note_images.py` 逐张 OCR并写入 `ocr_results.json`。任一图文笔记的图片列表不完整时，必须保留 `incomplete_image_set`，不得只识别封面后声称完成。若详情触发 `security_blocked`，必须立即停止后续请求、写出未请求状态和 `xhs_safety_state.json`，不得继续 OCR 或用 `--resume` 重发。开关关闭时跳过这两步并在分类时显式传 `--skip-ocr`。视频内容分类开关开启时不能用视频封面 OCR 下结论。
5. 生成 `classification.json`。图文 OCR 开启时，只有 `status=ok`、完整图片集合哈希和本次 `ocr_run_fingerprint` 都一致才复用 `ocr_results.json`，否则补跑 OCR；关闭时必须传 `--skip-ocr`。必须传 `--existing-boards-inventory`；其中全部 note id 固定输出 `excluded=true`、`exclude_reason=existing_board_member_protected`、`archive_lifecycle_state=first_archive_confirmed`、空 `target_board`；经这份完整 inventory 证明仍在专辑外的行输出 `archive_lifecycle_state=first_archive_pending`。未归档且无法可靠选定目标的行必须机械写入固定 `target_board="无法确定"`、`confidence=low`、`uncertain_assignment=true`、`review_state=manual_reclassification_required`；若专辑不存在，`created_boards.json` 必须把它列为缺失并在取得创建确认前阻止执行。未做成员核对的普通分类预览只能标记 `not_checked`。`--include-existing-boards` 已删除，任何调用都不能覆盖保护。
   - 视频开关开启时，视频访问也必须先确定本次范围：`transcribe_video_items.py` 需要 `--allow-video-access` 与明确的 `--video-id` 或 `--max-videos <1–200>`；视觉分析的 `--all-videos` 还必须同时给 `--max-videos <1–200>`。每段保存后停下，下一段需用户再次明确开始。任何安全提示会写入同一份 `xhs_safety_state.json` 并阻止续跑。完成全部本地保存分段后，才生成文字 memo、完整时轴视觉 memo 和 `classification.json`。
   - analysis provider 必须由用户选择：`codex-cli` 是已安装 Codex CLI 的适配器；`mimo-vl-mlx` 是本地官方 BF16 MiMo-VL + MLX-VLM；`command` 用于已有宿主 Agent/API，不经 shell 执行固定 argv，stdin 每次一行 `{"protocol_version":1,"prompt":"...","image_paths":["..."]}`，stdout 必须且只能输出一个 JSON 对象。
   - MiMo-VL 的视频入口只分析画面，不读取音轨。声音信息始终来自平台字幕或 MiMo ASR，以文字稿形式与真实帧一起交给 provider。
   - 长批次中，ASR 与持久 provider worker 各自只加载一次模型；转写与分析 checkpoint 必须原子写盘。正式分类默认要求分析结果覆盖本轮未归档的全部明确视频；`--allow-partial-video-analysis` 只用于显式抽样测试。
   - `video_analysis.json` 只保存分类所需的极简内容 memo（主要内容、短摘要、目标专辑、置信度、理由）；视觉修复成功项另保存帧时间戳、帧/OCR 哈希和完整时轴覆盖证据，不生成额外报告或 HTML。
   - 视频转写、覆盖率校验或 analysis provider 失败时，分析结果先保留空目标；写入最终 `classification.json` 时再机械转入“无法确定”。开启视觉模块后，任一本轮未归档的明确视频，其真实帧证据不完整就不得标记为完成；未开启视觉模块时，成功项也必须标为 `transcript_only`。转写与所选分析路径都失败时，必须保留真实 `video_content_unavailable` 错误状态并进入“无法确定”；禁止回退标题、简介、作者、封面 OCR 或任何猜测。
6. 分析完成后必须在用户本轮明确授权的浏览器上重新运行 `scripts/capture_board_snapshot.py board_snapshot.json --browser <浏览器> --user-id <当前账号> --expected-url-substring <当前页面片段>`，通过前端 `yC + U_ + Ks` 完整分页读取全部专辑成员，并与第 3 步的预分析 inventory 比较；任何原成员、专辑身份或绑定变化都停止。随后运行 `scripts/build_created_boards.py classification.json board_snapshot.json created_boards.json`。缺失专辑只记录为 `missing`，不自动创建。`board_snapshot.validation.full_membership_complete` 不是 `true` 时停止。
7. 生成执行清单时必须同时传入上述两份证据：`scripts/run_reassign_batch.py classification.json run_report.json --board-snapshot board_snapshot.json --created-boards created_boards.json`。脚本必须先按本轮快照判定成员关系，再校验分类目标，WorkBuddy 不得自行猜测：
   - 已属于任一专辑：统一标记 `membership_state=existing_board_member_protected`、`archive_lifecycle_state=first_archive_confirmed`，强制 `excluded=true` 并保持零写入；即使模型目标不同、目标不存在、置信度低或同时出现在多个专辑，也不得进入移动清单。
   - 不在任何专辑：标记 `membership_state=not_in_any_board`、`archive_lifecycle_state=first_archive_pending`，保持空 `source_board_id`，才允许用 `d0` 完成首次归档。
   - 快照分页不完整、账号/页面绑定变化或无法证明“不在任何专辑”时停止；禁止推断、跨专辑迁移或静默放行。
   - 只有报告同时满足 `mode=dry_run`、`ready_for_execute=true`、`blockers=[]`，才是可执行 dry-run；否则停止。
8. 用户确认专辑外笔记的分类、目标专辑和风险后，才允许运行 `scripts/run_reassign_batch.py classification.json run_report.json --board-snapshot board_snapshot.json --created-boards created_boards.json --execute --browser <用户本轮明确授权的浏览器> --user-id <同一账号> --expected-url-substring <同一页面片段> --max-moves-per-session <1–200>`。执行浏览器、账号、页面片段和共享安全会话必须与快照完全一致；缺少证据或绑定变化会在接触浏览器前拒绝。`auto` 会直接拒绝执行，禁止自动控制 Chrome 或其他外部浏览器。移动上限是人工检查断点，不是平台安全保证；到上限后只落盘，不自动进入下一段。Arc 写入还必须绑定不变的 `window id + tab id + --arc-tab-marker（预先写入 window.name 的稳定标记）+ --arc-expected-url-substring`，少任一项就中止。
   - Arc execute JavaScript 必须注入页面 main world；隔离世界只负责创建/轮询隐藏 DOM 状态节点，结果通过 DOM bridge 返回，禁止把运行态挂到共享 `window` 全局。
   - 专辑列表必须通过 `yC` 按 `num=100` 从 `page=1` 连续读取到 `boardCount` 对应的最后一页，禁止使用可能只含首屏的 `window.__INITIAL_STATE__`。每页数量必须与总数精确对应，跨页总数变化、缺页、重复 id/名称或缺少权威 `boardCount` 都立即中止。前端 API 必须从 Rspack `req.m` 按精确 endpoint 字面量唯一解析 `d0/Ks/yC/U_`，匹配为 0 或多个都中止，禁止猜压缩后的导出名。整理执行器不解析、不调用取消收藏或重新收藏 endpoint。
   - 只有 `membership_state=not_in_any_board`、`archive_lifecycle_state=first_archive_pending` 且 `source_board_id=""` 的条目才可调用 `d0({targetBoardId, notesId})`。任何其他状态在 Python 和页面 JavaScript 两层都必须跳过。
   - `d0(...)` 返回空对象 `{}` 不能判成功；必须以 `U_` + `Ks` 查到 note id 为准。
9. Python 每次只提交一条；浏览器返回首个错误行后，先合并并写入 `run_report.json`，再停止整批。页面出现安全验证、异常访问、频繁访问、登录页、执行页绑定失效或状态不确定时，必须先写 `xhs_safety_state.json` 和当前报告，再立即熔断；旧状态下 `--resume` 必须拒绝，重试队列标为“人工完成平台处理后开启新会话”，不得自动重试。
10. 批次结束后在用户本轮授权的浏览器里重新抓取完整专辑成员并做数量核对；可访问前端运行时 API 时优先用 `U_` + `Ks` 做最终核验。post snapshot 必须同时证明：本轮开始时的全部专辑成员仍留在原有专辑，专辑外成功项已进入目标专辑，未成功项保持专辑外。**首次归档保护只在这次回读成功时生效**：成功项转为 `first_archive_confirmed`；失败、中止、未核验和仅 dry-run 条目继续保持 `first_archive_pending`，只能留在 `pending_not_archived`。只有回读成功后，才允许从 post snapshot 重建完整 inventory，并用 `build_archived_notes_registry.py` 写出一份新的、不可覆盖的版本化 registry。禁止原地修改旧 registry，也禁止把执行计划直接追加为 confirmed。
    - 当用户反馈“专辑里的笔记数量和笔记总量不一致”时，先做只读三方核对，不要立即执行移动：
      1. 在用户本轮授权的浏览器里从顶部全量滚动收藏页，得到可访问笔记集合 A。
      2. 通过 webpack runtime 中的 `yC` 严格分页列出完整专辑，通过 `Ks` 分页抓每个专辑的真实笔记集合 B；禁止把 `window.__INITIAL_STATE__` 当成完整专辑清单。
      3. 比较 `A - B`（收藏页可见但不在任何专辑）、`B - A`、专辑列表显示计数 vs `Ks` 实际返回计数、重复 noteId。
      4. 如果 `A == B` 但 UI 总数或专辑卡片计数更大，结论应是小红书缓存/失效/不可见笔记口径差异；不能声称有可移动的缺失笔记，也不要为了修计数执行 `note/move`。

11. 第 10 步完整核验通过后，若 `report_requested=true`，只对同批完整分类中已有的完整图文 OCR 运行 `scripts/analyze_image_ocr.py`，生成与来源哈希逐条绑定的 `image_analysis.json`；随后运行 `scripts/generate_collection_report.py --board-snapshot <post snapshot> --classification <同批完整 classification.json> --image-analysis <image_analysis.json> --output "$HOME/Desktop/我的小红书专辑整理报告.html"`。报告按专辑说明主题、内容类型、已保存主题和主要笔记内容，只展示整体概括，不展示原始 OCR。生成器必须验证快照成员与分类 ID/目标逐条一致，并验证图文摘要完整覆盖且来源哈希未变；任一缺页、重复、数量变化、分类目标不一致或摘要来源变化都停止。若 `report_requested=false`，不得生成 HTML。报告阶段不打开浏览器、不重新 OCR/转写、不执行任何小红书写入；图文概括只分析已经保存的 OCR 证据。

## 图文收藏整理与专辑规划流程

当用户目标是整理“所有收藏图文 / 所有点赞图文 / 图文笔记 / 收藏夹整体分类”时，归档排除必须早于内容读取：

1. 全量抓取列表级目标范围
   - 按用户选择抓取收藏、点赞或二者合并后的所有条目，区分图文笔记、视频笔记和不可识别条目。
   - 此阶段只保存列表卡片允许的元数据和 note id；不得补齐详情、下载图片、运行 OCR、转写或视频分析。
   - 不要只依赖当前可见卡片；需要滚动/翻页直到覆盖收藏列表，并把抓取覆盖情况写入 `visible_items.json`。

2. 建立首次归档确认后的保护基线
   - 先抓完整只读专辑成员快照并生成 `existing_boards_inventory.json`；当前属于任一专辑的全部 note id 视为首次归档已确认并自动保护，不询问是否重组。
   - 当前 inventory 是本轮唯一成员关系事实源；历史 registry 只用于审计，不得覆盖本轮快照。
   - 保护集合建立后才允许进入下一步；pending、dry-run、失败或未回读条目都不是当前专辑成员。

3. 只读取未归档内容并生成专辑建议
   - 仅对排除集合外的图文笔记补齐按原顺序排列的封面和全部内页图片，下载/保存可访问图片并逐图 OCR；已归档 note id 不得进入详情请求或 OCR provider。
   - 基于本轮未归档条目的标题、正文/描述、标签、作者，以及图文笔记封面和全部内页图片中的 OCR 文本，生成“可创建专辑”的建议清单。OCR 不提供无文字纯画面的视觉理解。
   - 专辑建议只能从用户本次真实收藏和真实已有专辑中归纳；Skill 不提供任何预设主题名称。不确定项留空待复核，禁止机械套用示例或默认“杂项”。
   - 输出时同时给出：建议专辑名、包含的代表笔记、为什么这样分、可能需要合并/拆分的边界。

4. 专辑创建前必须询问用户
   - 在创建新专辑或批量归档专辑外笔记前，先把建议专辑体系展示给用户，询问是否要继续创建。
   - 明确询问用户是否有自己的分类想法，以及建议清单是否覆盖了他想到的所有层面。
   - 第 2 步的已有专辑保护基线不可修改；不得在完成 OCR 后才排除。
   - 如果用户认为没有覆盖完整，就继续追问/迭代分类体系，不执行专辑创建和批量移动。
   - 只有用户确认专辑外笔记的分类体系后，才根据用户需求创建所需专辑。

5. 按图文特性归档
   - 用户确认专辑体系后，仅根据每条本轮未归档图文笔记的标题、正文、标签、作者和全套图片 OCR 文本，将其归入对应专辑；无文字纯画面不能被当成 OCR 分类依据。
   - 允许一条笔记因主题跨界进入最合适的主专辑；如果平台支持多专辑再考虑多归档，否则记录次级标签到 `classification.json`。
   - 归档完成后必须核验目标专辑样本、数量变化和失败项；未成功归档的条目写入 `retry_queue.json`。

## 输入
- 当前已登录的小红书收藏页 / 点赞页 / 专辑页
- 用户给定或确认后的专辑体系 JSON
- 固定生命周期策略：`first_archive_pending -> first_archive_confirmed -> 永久保护`；不可关闭
- 用户确认的图文 OCR 开关：`classify_images_with_ocr=true/false`
- 用户确认的视频内容分类开关：`classify_video_by_content=true/false`
- 用户选择的快速启动档位：`quick` / `light` / `deep` / `custom`，以及其解析后的上述开关与 `visual_analysis=true/false`
- `existing_boards_inventory.json`
- 同账号、不可覆盖、只含回读确认成员的 `archived_notes_registry.json`
- 已抓取的 `visible_items.json`，每条建议包含 `source_lists` / `source_primary` 表示来自收藏、点赞或二者都有
- 用户确认“当前可访问 N 条”时的 `collection_scope.json`；它必须与 `visible_items.json` 和后续分类的完整 note id 顺序一致
- `image_items.json`：已补齐的图文封面及全部内页图片列表与完整性状态
- 已下载的图片素材与逐图 OCR 结果
- 开关开启时的 `video_transcripts.json` / `video_analysis.json`
- 历史 `run_report.json` / `retry_queue.json`
- 当前会话共享的 `xhs_safety_state.json`（不保存 cookie、token 或完整签名 URL）

## 输出
- `visible_items.json`
- `collection_scope.json`（仅用户确认的可访问范围）
- `image_items.json`（仅图文 OCR 开启时）
- `ocr_results.json`（仅图文 OCR 开启时）
- `video_transcripts.json`（仅视频内容分类开启时）
- `video_analysis.json`（仅视频内容分类开启时）
- `classification.json`
- `existing_boards_inventory.json`
- `archived_notes_registry.json`：同账号、不可覆盖；`confirmed_archived`（`first_archive_confirmed`）与 `pending_not_archived`（`first_archive_pending`）严格分开
- `board_snapshot.json`
- `created_boards.json`
- `run_report.json`
- `retry_queue.json`
- `xhs_safety_state.json`：可恢复的 `active` 或不可由 `--resume` 清除的 `security_halted` 状态

## 分类复核要求
- 图文 OCR 开关开启时，WorkBuddy 轻度整理只允许 `xhs_workbuddy_capture(organizing_depth=light)`；但用户要求已归档不再读取时，当前 WorkBuddy 路径必须按固定入口第 3 步停止。其他宿主运行 `enrich_note_images.py -> ocr_note_images.py`。允许执行的路径都必须只对本轮未归档图文，把封面和全部内页图片按原顺序逐张 OCR；关闭时不安装、不运行，预检结果也不得用于分类，并把对应条目标记为 `ocr_status=skipped` 或 `skipped_by_user`。视频视觉模块开启时，每个本轮未归档且明确选择的视频分段都跑完整时轴真实帧 + 逐帧 Vision OCR；所有本地分段完成前不得声称已覆盖本轮未归档的全部视频。未开启视频视觉模块时只能使用合格文字稿并标记 `transcript_only`。图文 OCR 与视频画面分析是两个独立开关；Vision OCR 不可用时，有视觉能力的 analysis provider 仍可直接看真实帧，但必须记录 `ocr_status=unavailable`。
- `scripts/ocr_note_images.py` 的后端按平台自动选择：macOS 优先 `scripts/ocr_image.swift.txt` + Vision；Windows 优先 Tesseract / EasyOCR。所有后端必须逐图回写同一份 `ocr_results.json`；OCR 成功但未发现文字与图片下载/OCR 失败必须明确区分。
- 如果用户关闭图文 OCR，分类流程继续走标题、desc、tags、作者等元数据，但必须在 `classification.json` 保留 `ocr_status=skipped` 或 `skipped_by_user`，并说明图片文字未参与分类、准确性可能下降。
- 复核顺序：标题/desc/tags/作者 -> OCR 文本 -> 人工判断。
- 复核后的结论必须回写 `classification.json`，不能只留 review 文件。
- 图片列表补齐失败、任一图片下载失败或 OCR 失败的条目，要显式保留真实 `ocr_status`，不得使用部分图片文字分类。
- `scripts/classify_items.py` 只有在已有 `ocr_results.json` 条目 `status=ok`、完整图片集合哈希与当前集合一致，且 `ocr_run_fingerprint` 与本次运行一致时才复用；如需全量重跑，使用 `--force-ocr`。
- 开启视频内容分类后，视频复核顺序为：合格文字稿 -> 视觉模块开启时的完整时轴真实帧/逐帧 OCR -> 所选 analysis provider 的内容 memo -> 人工确认；简介和封面 OCR 不参与视频结论。如果第二步未开启，必须明示 `transcript_only`。
- 视频链路失败不得进入元数据分类分支；在 `classification.json` 中保留 `classification_basis=video_content` 和真实失败状态，再机械进入固定专辑“无法确定”。

## 批量执行要求
- 每条必须有 `status`、`attempt`、`events`、`error`。
- 首个普通错误也必须先落盘再停止整批；安全验证/异常访问、登录页、执行页绑定失效和状态不确定必须写入共享 `xhs_safety_state.json` 后立即停写。
- 每完成一条立即写盘。
- 不模拟真人行为：不做鼠标抖动、随机等待、刷新、自动滚动、验证码处理、代理/IP 轮换或伪装浏览器指纹。
- 采集每段最多 200 条；详情、视频和移动都必须由用户明确给出本次上限。达到上限后只保存本地结果，不自动开始下一段。
- OCR 结果默认只在完整图片集合哈希 `image_set_sha256` 和 `ocr_run_fingerprint` 都一致时增量复用；图片数量、顺序、集合或 OCR 运行配置改变时必须重跑。`ocr_run_fingerprint` 绑定实际 provider、Tesseract 语言配置，以及 Swift OCR 脚本内容哈希所代表的脚本版本，避免旧结果跨配置复用。

## 单条失败处理
- 页面结构、状态桥或 API 解析失败：落盘错误，停止本段，由用户检查页面后再开启新的会话；不通过 UI 重试或额外点击补救。
- AppleEvent / JXA 超时：当前条失败，错误落盘后停止整批。
- 图片列表补齐失败、任一图片下载失败或 OCR 失败：当前条不使用部分 OCR 文本，最终目标专辑机械写为“无法确定”并等待用户自行调整，在 `classification.json` 保留真实错误状态；不得静默退回元数据分类。
- 目标专辑点击失败：记录实际 board 文本，退出当前条。
- 如能从 `window.__INITIAL_STATE__.board.boardListData` 读到目标专辑 `boardId`，优先记录后再继续，避免重复定位。

## 整体续跑机制
- 启动前读取旧 `run_report.json`；只有共享 `xhs_safety_state.json` 仍为 `active` 时，执行或 dry-run 才可使用 `--resume` 跳过已 `status=success` 且旧 `target_board` 与当前分类完全一致的条目。目标专辑改变时必须拒绝续跑，改用新报告处理差量。
- `status=success` 的条目直接跳过。
- `security_halted` 不得补跑；它在 `retry_queue.json` 中必须是 `retry_eligible=false`，下一步只能是用户完成平台处理后开启新的状态文件。普通失败也必须先人工复核再开新会话。
- `ocr_results.json` 中只有 `status=ok`、完整性计数一致、`image_set_sha256` 与当前完整图片集合一致，且 `ocr_run_fingerprint` 与本次运行一致的条目才可直接复用。指纹改变说明 provider、Tesseract 语言或 Swift OCR 脚本版本至少一项改变，必须重跑。
- 执行批处理时按单条提交给浏览器运行时；每条返回后立即 `merge_report_chunk` 并写回 `run_report.json`，避免长批次中断后丢失进度。
- 中途终止后只从未成功条目继续，不从头跑。

## 最低离线回归链路
- 修改脚本后先跑 `python3 -m compileall -q .`。
- 跑 `python3 -m unittest discover -s tests -p 'test_*.py'`，当前核心用例应覆盖 resume 过滤、报告 chunk 合并、抓取 manifest 写盘。
- 再跑无副作用 smoke：`python3 scripts/classify_items.py examples/visible_items.example.json /tmp/xhs-classification-smoke.json --skip-ocr` 和 `python3 scripts/run_reassign_batch.py /tmp/xhs-classification-smoke.json /tmp/xhs-run-report-smoke.json`；后者必须得到 `mode=classification_preview`、`ready_for_execute=false`，不能得到 `planned`。
- 不做真实网页登录态探针，不生成新的专辑快照，不创建专辑，不执行移动。发布验收只能验证这些入口在浏览器启动前安全停止。

## 核验方式
1. 首次归档保护核验：必须先建立本轮排除清单；`classification.json` 中已有成员只能是 `existing_board_member_protected + first_archive_confirmed`，`run_report.json` 不得出现这些笔记的移动事件；专辑外收藏必须是 `not_in_any_board + first_archive_pending` 才能处理。
2. OCR 核验：`ocr_results.json` 只覆盖每条本轮未归档的明确图文笔记，并覆盖其封面和全部内页图片；`image_set_complete=true`、声明/可用/已处理图片数一致，每张图片都有独立 `status`，并记录本次 `ocr_run_fingerprint`
3. 分类核验：`classification.json` 包含 `ocr_status` / `ocr_text` / `ocr_confidence` / `ocr_run_fingerprint`；成功图文透传与 OCR 结果相同的非空指纹，非图文、跳过或未成功 OCR 的行为空
4. 事件核验：专辑外条目检查 `board:FOUND:<目标专辑>`、`note_move:CALLED`、`verify:note_present`、`archive:first_confirmed`；只有最后两个回读事件出现后才进入永久保护。已有专辑成员只能出现 `skip:existing_board_excluded` 或 `skip:first_archive_not_eligible`，不得出现任何写入事件。
5. 页面核验：重新抓目标专辑，确认条目已出现
6. 数量核验：比较 `board_counts_before` / `board_counts_after`
7. 最终核验：运行 `scripts/verify_classification_membership.py`，只读抓取全部专辑成员；所有已放行视频必须全局恰好出现一次并位于目标专辑，未决视频单独列出且保持零移动。

## 明确禁止事项
- 不要把 `.collect-wrapper` 当成直接入专辑入口。
- 不要把 `#collect -> #collected` 图标变化当成“已加入目标专辑”；它只说明笔记被收藏/取消收藏。
- 禁止调用或恢复任何小红书私有专辑读写接口，包括网页内部的移动接口；不得以已知参数形状为理由重新启用。
- 不要在未完成全专辑成员关系核对时执行；任何已属于专辑、带 `source_board_id`、成员状态无法证明为 `not_in_any_board`，或生命周期状态不是 `first_archive_pending` 的条目都禁止调用 `d0`。
- 禁止 `--include-existing-boards`、跨专辑事务、取消收藏后重收、模型纠正用户已归档结果，以及删除/重命名/清理现有专辑。
- 不要把 `GET /api/sns/web/v2/note/collect/page` 的 `code=-9109 参数错误` 直接判定为未登录；它也可能是页面上下文或参数不完整。
- 不要把 UI 总数当成已完整抓取数。
- 不要只写文档不落盘 JSON。
- 不要把封面成功当成整条图文 OCR 成功；必须核对完整图片集合并写出真实 `ocr_status`。

## 环境前提与限制
- 支持 macOS + Arc/Chrome/Safari；支持 Windows + Chrome/Edge。
- macOS：Chrome 路径需要开启“允许 Apple 事件中的 JavaScript”；Safari 路径使用 AppleScript `do JavaScript`，复杂 JS 应写临时文件后执行，避免 shell 引号错误；OCR 默认用 `swift` + macOS Vision。
- Windows：浏览器抓取走 Playwright 或 CDP；推荐 Chrome/Edge 已登录小红书网页端；OCR 默认用 Tesseract（可选 EasyOCR）。
- Windows 安装建议：`python -m pip install playwright easyocr`、`python -m playwright install chromium`，或安装 Tesseract 并确保 `tesseract.exe` 在 PATH。
- 用户必须已登录小红书网页端；如果 Chrome 未登录但 Safari 已登录，按用户指示切 Safari 继续。
- 依赖按平台检测：`scripts/check_environment.py` 会输出 `browser_automation_ready`、`ocr_ready`、`windows_supported_path_ready`。
- 视频内容分类是可选功能，只在用户开启后检测：Video Transcript Extractor、`yt-dlp`、`ffmpeg`、`ffprobe`、本地 ASR，以及明确选择的 analysis provider（`codex-cli`、`mimo-vl-mlx` 或 `command`）。是否检测/安装 MiMo-VL 取决于用户是否选本地视觉 provider；macOS 全时轴画面证据复用系统自带 Swift + Vision OCR。
- 视频内容分类不需要 Qwen 或 LM Studio；不得为了这条链路要求用户安装它们。

## 关联资源
- 执行脚本：`scripts/`
- 输入输出契约：`references/io-contract.md`
- 收藏 / 点赞来源范围选择与“我全都要”合并：`references/source-scope-selection.md`
- 恢复与续跑：`references/recovery-and-resume.md`
- 环境检查：`references/environment-and-limitations.md`
- 图文 OCR 开关、体积与安装门禁：`references/image-ocr-classification.md`
- 视频内容分类开关、依赖与失败边界：`references/video-content-classification.md`
- Windows Playwright/CDP + OCR 支持：`references/windows-playwright-ocr-notes.md`
- WorkBuddy Plugin + 专用 Playwright 路径：`references/workbuddy-plugin.md`
- Safari 自动化补充：`references/safari-web-automation-notes.md`
- Safari 小红书前端模块/私有接口观察：`references/safari-xhs-private-api-notes.md`
- Safari 专辑移动前端运行时路径与已验证 payload：`references/safari-xhs-board-move-fallback.md`
- 分发可用性审计：`references/distribution-readiness-audit.md`
- 小红书发布标题/文案/标签/推送策略：`references/xiaohongshu-publishing-playbook.md`
- 示例文件：`examples/`

## 当用户要求“分享/发布这个 skill 到小红书”时

不要把内容包装成“程序员开源项目发布”；小红书首屏应先打泛用户痛点：收藏夹太乱、收藏了找不到、杂项灵感爆炸。输出时优先给用户可直接复制的标题、封面文案、正文、置顶评论和标签，并提醒第一版不要承诺“任何人下载就能直接用”或“一键整理所有收藏”。

发布策略要围绕收藏率、评论入口和系列化展开：封面表达“混乱收藏夹 → 自动分类整理”；正文自然引导“先收藏，后续发从 0 安装教程”；评论区引导“教程 / Windows”等关键词；建议拆成痛点展示、安装教程、真实整理案例三篇。详细模板见 `references/xiaohongshu-publishing-playbook.md`。

## 当用户要求“把 skill 变成可以公开发布状态”时

不要只给建议或只检查本机目录；要主动完成发布闭环：同步 GitHub、跑发布前验证、再用新下载版复核。推荐流程：

1. 临时 clone 公开仓库，而不是直接在本机安装目录里提交：`git clone https://github.com/<owner>/<repo>.git /tmp/<work>/repo`。
2. 从本机 skill 目录同步完整可发布内容到 clone，排除 `.git/`、`__pycache__/`、`*.pyc` 和运行产物 `visible_items.json` / `crawl_manifest.json` / `image_items.json` / `ocr_results.json` / `video_transcripts.json` / `video_analysis.json` / `classification.json` / `classification_preview.json` / `board_snapshot.json` / `created_boards.json` / `run_report.json` / `retry_queue.json`。
3. 检查 diff，尤其是 `SKILL.md`、`README.md`、`references/`、`scripts/`；如果新增 reference 被 `SKILL.md` 引用，必须确保文件也被同步。
4. 在 clone 中跑发布前验证：`python3 -m compileall -q .`、`python3 -m unittest discover -s tests -p 'test_*.py'`、`check_environment.py`、`classify_items.py --skip-ocr`、`run_reassign_batch.py` 分类预览、`build_retry_queue.py`、`summarize_run_report.py`，并用临时 `HERMES_HOME` 验证 `hermes skills list` 能识别；无浏览器证据的报告必须是 `classification_preview`、`ready_for_execute=false`。
5. 提交并推送 main 后，不要立即宣布完成；重新 `git clone --depth 1` 和下载 `main.zip`，再比较新下载版与本机 skill 目录是否无差异，并重复关键 smoke。
6. 最终回复必须给出 commit hash、验证项目和公开发布口径；明确说明专辑读取、创建和移动当前已停用，不得建议用户传 `--execute`。

详细审计标准见 `references/distribution-readiness-audit.md`。

## 当用户要求“测试下载后别人是否可用”时

不要只看本机安装目录，也不要只说“仓库存在”。必须模拟陌生用户路径，结论要分清“可下载/可安装/基础可运行/适合对外发布”四层：

1. 公开性检查：用 GitHub API 或匿名下载确认仓库可访问，避免只依赖已登录 `gh`。
2. 下载检查：至少覆盖 `git clone --depth 1` 和 GitHub `main.zip` 下载/解压两条路径。
3. 临时安装检查：创建临时 `HERMES_HOME`，把下载后的 skill 放到 `skills/social-media/xiaohongshu-web-collection-organizing/`，运行 `hermes skills list` 确认可识别。
4. 静态检查：运行 `python3 -m compileall -q <repo>`，确认脚本无语法错误。
5. 示例烟测：用 examples/templates 跑无副作用脚本，例如 `check_environment.py`、`classify_items.py --skip-ocr`、`run_reassign_batch.py` 分类预览、`build_retry_queue.py`、`summarize_run_report.py`；涉及 `capture_board_snapshot.py` 或真实收藏移动的脚本必须先获得当前浏览器授权，真实移动还必须显式传 `--execute`。
6. 本机最新版对比：如果本机 skill 目录和 GitHub 下载版本不同，必须明确提醒“GitHub 版本落后/不一致”，不能把本机已修复能力误报成外部用户可用能力。
7. 发布判断：README 必须包含安装命令、浏览器权限、登录态要求、可直接运行脚本、真实移动入口和限制；否则结论应是“可下载和基础可跑，但不建议直接对外宣传为任何人下载即用”。

详细审计清单见 `references/distribution-readiness-audit.md`。

## 当用户要求“补齐并打包 skill”时

如果用户不是只要整理收藏，而是明确要求把这个 skill 交付成可分发安装包，不能只改 `SKILL.md` 或 `README.md`。最小交付必须同时完成：

1. 补齐正式目录结构：
   - `SKILL.md`
   - `README.md`
   - `LICENSE.txt`
   - `references/`
   - `scripts/`
   - `templates/`
   - `examples/`
2. `SKILL.md` 的 frontmatter 只保留 `name` 和 `description`，并把触发条件、前提环境、适用请求写进 `description`。
3. 至少提供最小可复用执行资源，而不只是文档：
   - 收藏抓取脚本
   - OCR 落盘脚本
   - 分类落盘脚本
   - dry-run / execute 运行报告生成脚本
   - retry queue / resume 相关资源
4. 明确输入输出契约，并给出 JSON 示例：
   - `visible_items.json`
   - `image_items.json`（图文 OCR 开启时）
   - `ocr_results.json`
   - `video_transcripts.json` / `video_analysis.json`（视频开关开启时）
   - `classification.json`
   - `created_boards.json`
   - `run_report.json`
   - `retry_queue.json`
5. 最终要同时产出：
   - skill 安装版目录（放回 skill 路径）
   - 一份桌面复制版目录
   - 一个新的 zip 包，方便直接分发
6. 回复用户时必须说明：
   - 新增了哪些文件
   - 哪些能直接运行
   - 哪些是模板 / 示例
   - 当前还存在什么限制
