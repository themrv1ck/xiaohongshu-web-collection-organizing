---
name: xiaohongshu-web-collection-organizing
description: Reorganize a logged-in Xiaohongshu web 收藏 / 专辑 library on macOS when the user asks to inspect favorites, create boards, classify saved notes, clean up a messy board like “杂项灵感”, or batch reassign notes through Chrome + AppleScript/JXA. Trigger only when Chrome is available, the user is already logged in on Xiaohongshu web, Chrome has “允许 Apple 事件中的 JavaScript” enabled, and macOS Vision OCR via swift is available for per-item cover-text review.
---

# 小红书收藏整理 Skill

## 稳定工作流
1. 检查环境：macOS、Chrome、小红书网页登录态、Apple 事件中的 JavaScript、`swift` Vision OCR。
2. 抓取收藏页当前实际可见条目，写入 `visible_items.json`。
3. 对全部条目封面图执行 OCR，写入 `ocr_results.json`。
4. 生成 `classification.json`，默认复用 OCR 结果；缺失时自动补跑 OCR。
5. 读取或创建专辑，写入 `created_boards.json`。
6. 批量执行 `uncollect -> collect -> banner 加入专辑 -> 选择目标专辑`。
7. 每条实时写入 `run_report.json`，失败项同步写入 `retry_queue.json`。
8. 批次结束后重新抓取目标专辑样本并做数量核对。

## 输入
- 当前已登录的小红书收藏页 / 专辑页
- 用户给定的专辑体系 JSON
- 已抓取的 `visible_items.json`
- 历史 `run_report.json` / `retry_queue.json`

## 输出
- `visible_items.json`
- `ocr_results.json`
- `classification.json`
- `created_boards.json`
- `run_report.json`
- `retry_queue.json`

## 分类复核要求
- 默认对每个条目先跑一遍封面 OCR。
- 复核顺序：标题/desc/tags/作者 -> OCR 文本 -> 人工判断。
- 复核后的结论必须回写 `classification.json`，不能只留 review 文件。
- OCR 下载失败或无图片 URL 的条目，要显式保留 `ocr_status`。

## 批量执行要求
- 每条必须有 `status`、`attempt`、`events`、`error`。
- 单条失败不能阻断整批。
- 每成功一条立即写盘。
- 第二次重试要延长 `.collect-wrapper` 等待时间。
- OCR 结果默认增量复用，避免无意义重跑。

## 单条失败处理
- `.collect-wrapper` 延迟挂载：首次等 45 秒，二次等 75 秒。
- banner 未出现：重发鼠标事件，再失败则进 `retry_queue.json`。
- AppleEvent / JXA 超时：当前条失败，整批继续。
- OCR 下载失败：当前条继续走元数据分类，但在 `classification.json` 保留 `ocr_status=error`。
- 目标专辑点击失败：记录实际 board 文本，退出当前条。

## 整体续跑机制
- 启动前读取旧 `run_report.json`。
- `status=success` 的条目直接跳过。
- `failed` 条目写入 `retry_queue.json` 并优先补跑。
- `ocr_results.json` 中 `status=ok` 的条目默认直接复用。
- 中途终止后只从未成功条目继续，不从头跑。

## 核验方式
1. OCR 核验：`ocr_results.json` 覆盖每个条目，至少能看到 `status`
2. 分类核验：`classification.json` 包含 `ocr_status` / `ocr_text` / `ocr_confidence`
3. 事件核验：`board:CLICKED:<目标专辑>` 与 `toast:ok`
4. 页面核验：重新抓目标专辑，确认条目已出现
5. 数量核验：比较 `board_counts_before` / `board_counts_after`

## 明确禁止事项
- 不要把 `.collect-wrapper` 当成直接入专辑入口。
- 不要伪造未验证私有接口。
- 不要把 UI 总数当成已完整抓取数。
- 不要只写文档不落盘 JSON。
- 不要假设 OCR 成功覆盖全部条目，必须写出真实 `ocr_status`。

## 环境前提与限制
- 仅适用于 macOS + Google Chrome。
- 用户必须已登录小红书网页端。
- Chrome 必须开启“允许 Apple 事件中的 JavaScript”。
- 依赖：`osascript`、`swift`、macOS Vision、可选 Node.js 与 `@lucasygu/redbook`。

## 关联资源
- 执行骨架：`scripts/`
- 输入输出契约：`references/io-contract.md`
- 恢复与续跑：`references/recovery-and-resume.md`
- 环境检查：`references/environment-and-limitations.md`
- 示例文件：`examples/`

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
   - 收藏抓取脚本或脚本骨架
   - OCR 落盘脚本
   - 分类落盘脚本
   - 运行报告生成脚本或脚本骨架
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
   - 哪些只是骨架 / 模板 / 示例
   - 当前还存在什么限制
