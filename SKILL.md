---
name: xiaohongshu-web-collection-organizing
description: Reorganize a logged-in Xiaohongshu web 收藏 / 专辑 library on macOS or Windows when the user asks to inspect favorites, classify saved notes, clean up a messy board like “杂项灵感”, or batch reassign notes through browser automation. macOS supports Chrome/Safari AppleScript plus Vision OCR; Windows supports Chrome/Edge via Playwright or CDP plus Tesseract/EasyOCR OCR. Requires the user to be logged in on Xiaohongshu web, defaults to dry-run before account changes, and keeps JSON outputs/retry reports for safe resume.
---

# 小红书工作流 Skill

This is the umbrella for Xiaohongshu web workflows. Use the collection-organizing sections for logged-in favorites/board cleanup, and use the single-note research section below for one shared note URL.

## 单篇笔记研究 / note research

Use this subsection when the user gives one Xiaohongshu note URL and asks to “研究一下”, summarize, extract value, inspect comments, or evaluate product/market implications. Do not run account-changing collection scripts for this task.

Workflow:
1. Try browser navigation first; if it shows login/IP-risk/security, continue with mobile HTML extraction instead of stopping.
2. Fetch the note page with an iPhone/mobile User-Agent and parse `window.__SETUP_SERVER_STATE__` / `LAUNCHER_SSR_STORE_PAGE_DATA.noteData` when present.
3. Extract title, desc, tags, user, interaction counts, image list, comments/subComments, and author context.
4. Download images only when needed for visual/OCR analysis, using normal browser headers and no credential leakage.
5. Synthesize value: what the post says, evidence from comments/engagement, implementation details, user pain points, competitor mentions, and actionable recommendations.
6. Answer in Chinese unless asked otherwise, with conclusion first and evidence compactly.

See `references/xiaohongshu-note-research.md` for the archived narrow workflow.

## 用户目标口径

当用户要求整理“小红书收藏 / 我的收藏 / 专辑分类”时，默认目标是：进入当前已登录账号的“我的页面/收藏”，完整阅览并抓取收藏笔记，重点收集笔记标题、作者、链接、现有专辑、封面/OCR 文本等可分类信息；整理或重建专辑体系；把所有收藏笔记合理归档进对应主题专辑。

允许的动作：
- 可核对目标专辑是否已存在，并把缺失专辑写入 `created_boards.json`；创建、删除、重命名专辑必须另行取得用户明确授权。
- 可通过前端真实 API `note/move` 路径完成重新归档；真实执行必须显式传 `--execute`。
- 只要下一步明确，就持续推进到全量完成、失败项入队、最终核验，不要在中间阶段只做总结就停止。

硬性边界：
- 所有收藏笔记不允许删除；“取消收藏”只能作为重新加入目标专辑的中间动作，必须确保最终仍被收藏并归入目标专辑。
- 不得把未分类、抓取失败或移动失败的笔记静默丢弃；必须写入 `retry_queue.json` / `run_report.json`。
- 删除或清理专辑前必须先核验该专辑内笔记已迁移或无需保留在该专辑；不得因专辑分类重构导致笔记丢失。
- 不得把完整小红书 URL query、`xsec_token`、cookie、signed media URL、`sign` 参数或任何疑似凭据写入模型上下文、正式报告、Telegram/Discord 回复或日志摘要；只保留标准 `/explore/<note_id>`、标题、作者、公开计数和分类所需普通文本。历史上完整 `xsec_token` 链接曾触发 GPT `cyber_policy` 误判。

## 稳定工作流
1. 检查环境：操作系统、浏览器网页登录态、浏览器自动化后端、OCR 后端。
   - **启动本 skill 后必须先运行 OCR 检测**：执行 `python3 scripts/check_environment.py`（Windows 可用 `python scripts/check_environment.py`），读取 `ocr_ready` / `image_text_recognition_ready`。
   - **只有 OCR 可用时，才开启图片文字识别参与分类**。如果 `ocr_ready=false`，不得假装已识别图片文字，也不得把图片文字识别结果写成成功；应先询问用户是否需要安装 OCR 功能，并解释：安装 OCR 的目的是识别收藏封面/图片中的中文文字，从而提高小红书笔记分类和专辑归档准确性。
   - 如果用户同意安装，再按当前系统选择 OCR 后端：macOS 优先 `swift` + Vision（通常系统自带，只需确认可用），Windows 优先 Tesseract（建议带中文 `chi_sim`）或 EasyOCR；安装涉及系统/配置文件变更时先请示。
   - macOS 默认优先 Chrome + AppleScript/JXA：检查小红书网页登录态、Chrome “允许 Apple 事件中的 JavaScript”、`swift` Vision OCR。
   - Windows 默认走 Chrome/Edge + Playwright 或已启动浏览器 CDP；OCR 走 Tesseract 或 EasyOCR，必须使用用户自己的网页登录态，不抓取或复制敏感 token。
   - 如果 Chrome 未登录但用户说“用 Safari”，立即切换 Safari，打开 `https://www.xiaohongshu.com/explore` 并验证 Safari 登录态，不要继续卡在 Chrome。
   - 如果目标浏览器未登录：明确告诉用户需要扫码登录；同时启动后台登录态轮询（建议每 5 分钟一次），检测到登录成功后自动续跑，不要把“等待登录”当成任务完成。
   - 登录态判断优先读取页面文本：出现“手机号登录 / 登录后推荐 / 马上登录即可 / 扫码”等视为未登录；未出现这些且页面已显示用户态内容再继续。
   - Safari 自动化细节见 `references/safari-web-automation-notes.md`。
2. 抓取收藏页当前实际可见条目，写入 `visible_items.json`，并同时写出 `crawl_manifest.json` 记录 `item_count`、`stopped_reason`、页面元信息和滚动快照；如果 `stopped_reason=max_scrolls_reached`，不得声称已经全量完成，只能说完成了当前滚动预算内的只读抓取。
   - macOS Chrome 执行复杂/长 JS 时，不要把整段 JS 直接插入 AppleScript `execute javascript "..."` 字符串；这容易触发 `预期是“\"”，却找到未知的记号 (-2741)`。应把 JS 写入临时 `.js` 文件，用 AppleScript `read POSIX file ... as «class utf8»` 读入变量后 `execute javascript jsSource`，执行后删除临时文件。
   - 每次修浏览器抓取器后，至少跑一次真实登录态的只读探针：`python3 scripts/extract_visible_items.py /tmp/xhs-visible-probe.json --backend macos-chrome --manifest /tmp/xhs-crawl-manifest-probe.json --max-scrolls 2 --scroll-pause 1`；Windows 用 `--backend playwright --channel chrome|msedge` 或 `--cdp-url`。
3. 对全部条目封面图执行 OCR，写入 `ocr_results.json`。
4. 如果用户选择不动已有专辑，先用 `scripts/build_existing_boards_inventory.py` 建立 `existing_boards_inventory.json`。
5. 生成 `classification.json`，默认复用 OCR 结果；缺失时自动补跑 OCR。传入 `--existing-boards-inventory` 时，默认排除已有专辑里的笔记，只有显式传 `--include-existing-boards` 才纳入。
6. 读取或核对专辑，写入 `created_boards.json`。缺失专辑只记录为 `missing`，不自动创建。
7. 先运行 `scripts/run_reassign_batch.py classification.json run_report.json` 做 dry-run；dry-run 不改账号。
8. 用户确认分类、目标专辑和风险后，才允许运行 `scripts/run_reassign_batch.py classification.json run_report.json --execute`。执行时使用前端运行时路径：
   - 检查 `#note-page-collect-board-guide.collect-wrapper` 状态，确认只是“已收藏/未收藏”切换，不把它误判成已入专辑。
   - 从 `window.__INITIAL_STATE__.board.boardListData` 读取当前账号专辑列表、`boardId`、现有计数，先定位目标专辑。
   - 通过 webpack runtime 暴露模块，优先复用真实前端 API：`yC`(专辑列表)、`Ks`(专辑笔记)、`d0`(移动到专辑)、`U_`(专辑详情)。
   - 已验证 `note/move` 的真实 payload 形状为 `{targetBoardId, notesId}`；不要再枚举猜参。
   - 当目标 `boardId` 与候选 `noteId` 列表已明确时，直接批量执行候选，再统一做核验和回复。
   - `d0(...)` 返回空对象 `{}` 不能直接判失败；必须继续调用 `U_` + `Ks`，用 `detail.total` 与返回的 `notes` 列表确认是否真的入专辑。
   - 已验证细节见 `references/safari-xhs-board-batch-move-verified.md`。
9. 每条实时写入 `run_report.json`，失败项同步写入 `retry_queue.json`。
10. 批次结束后重新抓取目标专辑样本并做数量核对，Safari 回退路径优先用 `U_` + `Ks` 做最终核验。

## 图文收藏整理与专辑规划流程

当用户目标是整理“所有收藏图文 / 图文笔记 / 收藏夹整体分类”时，采用先规划、再执行的两阶段流程：

1. 全量抓取收藏条目
   - 先抓取一遍所有收藏条目，区分图文笔记、视频笔记和不可识别条目。
   - 图文笔记优先下载/保存可访问的图片、标题、正文摘要、作者、标签、链接、现有专辑等信息；这些素材可以作为大模型分类输入。
   - 不要只依赖当前可见卡片；需要滚动/翻页直到覆盖收藏列表，并把抓取覆盖情况写入 `visible_items.json`。

2. 生成专辑分类建议
   - 基于全部收藏条目的标题、正文/描述、标签、作者、图片 OCR/视觉信息，先生成“可创建专辑”的建议清单。
   - 专辑建议应面向用户真实收藏主题，例如：装潢、穿搭、攀岩、滑雪、潜水、自我成长、灵感、旅行、健康、效率、审美参考等；不要机械照搬示例，必须从实际收藏中归纳。
   - 输出时同时给出：建议专辑名、包含的代表笔记、为什么这样分、可能需要合并/拆分的边界。

3. 专辑创建前必须询问用户
   - 在创建、删除、重命名专辑或批量移动笔记前，先把建议专辑体系展示给用户，询问是否要继续创建。
   - 明确询问用户是否有自己的分类想法，以及建议清单是否覆盖了他想到的所有层面。
   - **如果检测到用户已经创建过专辑，必须额外询问：是否需要一并移动/重组这些已创建专辑里的内容。**
   - 如果用户回答“不需要 / 不要动已有专辑 / 只整理未归档收藏”，必须先建立已有专辑排除清单；随后只移动“客户已创建专辑以外”的收藏内容，不移动、不取消收藏、不重新归档已在客户专辑中的笔记。
   - 如果用户同意重组已有专辑内容，才允许把已在客户专辑中的笔记纳入新的分类/移动计划；移动前仍需展示计划并确认。
   - 如果用户认为没有覆盖完整，就继续追问/迭代分类体系，不执行专辑创建和批量移动。
   - 只有用户确认分类体系和已有专辑处理策略后，才根据用户需求创建所需专辑。

4. 按图文特性归档
   - 用户确认专辑体系后，根据每条图文笔记的主题、场景、用途、视觉内容和文本信息，将其归入对应专辑。
   - 允许一条笔记因主题跨界进入最合适的主专辑；如果平台支持多专辑再考虑多归档，否则记录次级标签到 `classification.json`。
   - 归档完成后必须核验目标专辑样本、数量变化和失败项；未成功归档的条目写入 `retry_queue.json`。

## 输入
- 当前已登录的小红书收藏页 / 专辑页
- 用户给定或确认后的专辑体系 JSON
- 用户确认后的已有专辑处理策略：`include_existing_boards=true/false`
- `existing_boards_inventory.json`
- 已抓取的 `visible_items.json`
- 已下载/提取的图文素材与 OCR/视觉结果
- 历史 `run_report.json` / `retry_queue.json`

## 输出
- `visible_items.json`
- `ocr_results.json`
- `classification.json`
- `existing_boards_inventory.json`
- `created_boards.json`
- `run_report.json`
- `retry_queue.json`

## 分类复核要求
- 默认对每个条目先跑一遍封面 OCR；但必须以启动时环境检测为准，`ocr_ready=false` 时先询问用户是否安装 OCR，不得直接开启图片文字识别。
- OCR 走 `scripts/ocr_cover_images.py`，后端按平台自动选择：macOS 优先 `scripts/ocr_image.swift` + Vision；Windows 优先 Tesseract / EasyOCR。所有后端必须回写同一份 `ocr_results.json`。
- 如果用户未安装/不同意安装 OCR，分类流程可以继续走标题、desc、tags、作者等元数据，但必须在 `classification.json` 保留 `ocr_status=unavailable` 或 `ocr_status=skipped`，并说明图片文字未参与分类、准确性会下降。
- 复核顺序：标题/desc/tags/作者 -> OCR 文本 -> 人工判断。
- 复核后的结论必须回写 `classification.json`，不能只留 review 文件。
- OCR 下载失败或无图片 URL 的条目，要显式保留 `ocr_status`。
- `scripts/classify_items.py` 默认复用已有 `ocr_results.json`；如需全量重跑，使用 `--force-ocr`，不要无意义重复 OCR。

## 批量执行要求
- 每条必须有 `status`、`attempt`、`events`、`error`。
- 单条失败不能阻断整批。
- 每成功一条立即写盘。
- 第二次重试要延长 `.collect-wrapper` 等待时间。
- OCR 结果默认增量复用，避免无意义重跑。

## 单条失败处理
- `.collect-wrapper` 延迟挂载：首次等 45 秒，二次等 75 秒。
- `.collect-wrapper` 图标在 `#collect/#collected` 间切换，只代表收藏状态变化；如果没看到专辑选择流程，不算入专辑成功。
- banner 未出现：重发鼠标事件，再失败则切换到 webpack/runtime 回退路径，不要无限重试 UI。
- AppleEvent / JXA 超时：当前条失败，整批继续。
- OCR 下载失败：当前条继续走元数据分类，但在 `classification.json` 保留 `ocr_status=error`。
- 目标专辑点击失败：记录实际 board 文本，退出当前条。
- 如能从 `window.__INITIAL_STATE__.board.boardListData` 读到目标专辑 `boardId`，优先记录后再继续，避免重复定位。

## 整体续跑机制
- 启动前读取旧 `run_report.json`；执行或 dry-run 时使用 `--resume` 跳过已 `status=success` 的条目，并在新报告中保留已成功行。
- `status=success` 的条目直接跳过。
- `failed` 条目写入 `retry_queue.json` 并优先补跑。
- `ocr_results.json` 中 `status=ok` 的条目默认直接复用。
- 执行批处理时按单条提交给浏览器运行时；每条返回后立即 `merge_report_chunk` 并写回 `run_report.json`，避免长批次中断后丢失进度。
- 中途终止后只从未成功条目继续，不从头跑。

## 最低回归链路
- 修改脚本后先跑 `python3 -m compileall -q .`。
- 跑 `python3 -m unittest discover -s tests -p 'test_*.py'`，当前核心用例应覆盖 resume 过滤、报告 chunk 合并、抓取 manifest 写盘。
- 再跑无副作用 smoke：`python3 scripts/classify_items.py examples/visible_items.example.json /tmp/xhs-classification-smoke.json --skip-ocr` 和 `python3 scripts/run_reassign_batch.py /tmp/xhs-classification-smoke.json /tmp/xhs-run-report-smoke.json`。
- 最后做真实网页登录态只读探针与 dry-run：抓取到 `/tmp/xhs-visible-probe.json`，分类到 `/tmp/xhs-classification-probe.json`，再生成 `/tmp/xhs-run-report-probe.json`；没有用户明确授权前不得加 `--execute`。

## 核验方式
1. 已有专辑策略核验：如果用户选择不移动已有专辑内容，必须先建立排除清单，`classification.json` / `run_report.json` 中不得出现这些专辑内笔记的移动事件，只能处理专辑外收藏。
2. OCR 核验：`ocr_results.json` 覆盖每个条目，至少能看到 `status`
3. 分类核验：`classification.json` 包含 `ocr_status` / `ocr_text` / `ocr_confidence`
4. 事件核验：`board:FOUND:<目标专辑>`、`note_move:CALLED`、`verify:note_present`
5. 页面核验：重新抓目标专辑，确认条目已出现
6. 数量核验：比较 `board_counts_before` / `board_counts_after`

## 明确禁止事项
- 不要把 `.collect-wrapper` 当成直接入专辑入口。
- 不要把 `#collect -> #collected` 图标变化当成“已加入目标专辑”；它只说明笔记被收藏/取消收藏。
- 不要再笼统禁止 `POST /api/sns/web/v1/note/move`；禁止的是“盲猜 payload”。在已追到真实前端调用后，应复用真实调用形状 `{targetBoardId, notesId}`。
- 不要把 `GET /api/sns/web/v2/note/collect/page` 的 `code=-9109 参数错误` 直接判定为未登录；它也可能是页面上下文或参数不完整。
- 不要把 UI 总数当成已完整抓取数。
- 不要只写文档不落盘 JSON。
- 不要假设 OCR 成功覆盖全部条目，必须写出真实 `ocr_status`。

## 环境前提与限制
- 支持 macOS + Chrome/Safari；支持 Windows + Chrome/Edge。
- macOS：Chrome 路径需要开启“允许 Apple 事件中的 JavaScript”；Safari 路径使用 AppleScript `do JavaScript`，复杂 JS 应写临时文件后执行，避免 shell 引号错误；OCR 默认用 `swift` + macOS Vision。
- Windows：浏览器抓取走 Playwright 或 CDP；推荐 Chrome/Edge 已登录小红书网页端；OCR 默认用 Tesseract（可选 EasyOCR）。
- Windows 安装建议：`python -m pip install playwright easyocr`、`python -m playwright install chromium`，或安装 Tesseract 并确保 `tesseract.exe` 在 PATH。
- 用户必须已登录小红书网页端；如果 Chrome 未登录但 Safari 已登录，按用户指示切 Safari 继续。
- 依赖按平台检测：`scripts/check_environment.py` 会输出 `browser_automation_ready`、`ocr_ready`、`windows_supported_path_ready`。

## 关联资源
- 执行脚本：`scripts/`
- 输入输出契约：`references/io-contract.md`
- 恢复与续跑：`references/recovery-and-resume.md`
- 环境检查：`references/environment-and-limitations.md`
- Windows Playwright/CDP + OCR 支持：`references/windows-playwright-ocr-notes.md`
- Safari 自动化补充：`references/safari-web-automation-notes.md`
- Safari 小红书前端模块/私有接口观察：`references/safari-xhs-private-api-notes.md`
- Safari 专辑移动前端运行时路径与已验证 payload：`references/safari-xhs-board-move-fallback.md`
- 分发可用性审计：`references/distribution-readiness-audit.md`
- 小红书发布标题/文案/标签/推送策略：`references/xiaohongshu-publishing-playbook.md`
- 示例文件：`examples/`

## 当用户要求“分享/发布这个 skill 到小红书”时

不要把内容包装成“程序员开源项目发布”；小红书首屏应先打泛用户痛点：收藏夹太乱、收藏了找不到、杂项灵感爆炸。输出时优先给用户可直接复制的标题、封面文案、正文、置顶评论和标签，并提醒第一版不要承诺“任何人下载就能直接用”或“一键整理所有收藏”。

发布策略要围绕收藏率、评论入口和系列化展开：封面表达“混乱收藏夹 → 自动分类整理”；正文自然引导“先收藏，后续发从 0 安装教程”；评论区引导“教程 / Windows”等关键词；建议拆成痛点展示、安装教程、真实整理案例三篇。详细模板见 `references/xiaohongshu-publishing-playbook.md`。

## 当用户要求“测试下载后别人是否可用”时

不要只看本机安装目录，也不要只说“仓库存在”。必须模拟陌生用户路径，结论要分清“可下载/可安装/基础可运行/适合对外发布”四层：

1. 公开性检查：用 GitHub API 或匿名下载确认仓库可访问，避免只依赖已登录 `gh`。
2. 下载检查：至少覆盖 `git clone --depth 1` 和 GitHub `main.zip` 下载/解压两条路径。
3. 临时安装检查：创建临时 `HERMES_HOME`，把下载后的 skill 放到 `skills/social-media/xiaohongshu-web-collection-organizing/`，运行 `hermes skills list` 确认可识别。
4. 静态检查：运行 `python3 -m compileall -q <repo>`，确认脚本无语法错误。
5. 示例烟测：用 examples/templates 跑无副作用脚本，例如 `check_environment.py`、`classify_items.py --skip-ocr`、`run_reassign_batch.py` dry-run、`build_retry_queue.py`、`summarize_run_report.py`；涉及真实网页/收藏移动的脚本必须显式传 `--execute`，不要在未授权环境中执行。
6. 本机最新版对比：如果本机 skill 目录和 GitHub 下载版本不同，必须明确提醒“GitHub 版本落后/不一致”，不能把本机已修复能力误报成外部用户可用能力。
7. 发布判断：README 必须包含安装命令、浏览器权限、登录态要求、可直接运行脚本、真实移动入口和限制；否则结论应是“可下载和基础可跑，但不建议直接对外宣传为任何人下载即用”。

详细审计清单见 `references/distribution-readiness-audit.md`。

## 当用户要求“补齐并打包 skill”时

如果用户不是只要整理收藏，而是明确要求把这个 skill 交付成可分发安装包，不能只改 `SKILL.md` 或 `README.md`。最小交付必须同时完成：

1. 补齐正式目录结构：
   - `SKILL.md`
   - `README.md`
   - `LICENSE`
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
   - `ocr_results.json`
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
