---
name: xiaohongshu-web-collection-organizing
description: Reorganize a logged-in Xiaohongshu web 收藏 / 专辑 library on macOS when the user asks to inspect favorites, create boards, classify saved notes, clean up a messy board like “杂项灵感”, or batch reassign notes through Chrome + AppleScript/JXA. Trigger only when Chrome is available, the user is already logged in on Xiaohongshu web, and Chrome has “允许 Apple 事件中的 JavaScript” enabled; this skill depends on macOS, osascript/JXA, Chrome DOM automation, and optionally the @lucasygu/redbook client for note metadata, collect/uncollect verification, and resume-safe batch execution.
---

# 小红书收藏整理 Skill

## 稳定工作流
1. 检查环境：macOS、Chrome、小红书网页登录态、Apple 事件中的 JavaScript。
2. 抓取收藏页当前实际可见条目，写入 `visible_items.json`。
3. 读取或创建专辑，写入 `created_boards.json`。
4. 生成 `classification.json`，对模糊项做 OCR / 视觉复核。
5. 批量执行 `uncollect -> collect -> banner 加入专辑 -> 选择目标专辑`。
6. 每条实时写入 `run_report.json`，失败项同步写入 `retry_queue.json`。
7. 批次结束后重新抓取目标专辑样本并做数量核对。

## 输入
- 当前已登录的小红书收藏页 / 专辑页
- 用户给定的专辑体系 JSON
- 已抓取的 `visible_items.json`
- 已完成的 `classification.json`
- 历史 `run_report.json` / `retry_queue.json`

## 输出
- `visible_items.json`
- `classification.json`
- `created_boards.json`
- `run_report.json`
- `retry_queue.json`

## 分类复核要求
- 弱标题、短标题、跨类内容必须复核。
- 复核顺序：标题/desc/tags/作者 -> 视觉识别 -> 人工判断。
- 复核后的结论必须回写 `classification.json`，不能只留 review 文件。

## 批量执行要求
- 每条必须有 `status`、`attempt`、`events`、`error`。
- 单条失败不能阻断整批。
- 每成功一条立即写盘。
- 第二次重试要延长 `.collect-wrapper` 等待时间。

## 单条失败处理
- `.collect-wrapper` 延迟挂载：首次等 45 秒，二次等 75 秒。
- banner 未出现：重发鼠标事件，再失败则进 `retry_queue.json`。
- AppleEvent / JXA 超时：当前条失败，整批继续。
- 目标专辑点击失败：记录实际 board 文本，退出当前条。

## 整体续跑机制
- 启动前读取旧 `run_report.json`。
- `status=success` 的条目直接跳过。
- `failed` 条目写入 `retry_queue.json` 并优先补跑。
- 中途终止后只从未成功条目继续，不从头跑。

## 核验方式
1. 事件核验：`board:CLICKED:<目标专辑>` 与 `toast:ok`
2. 页面核验：重新抓目标专辑，确认条目已出现
3. 数量核验：比较 `board_counts_before` / `board_counts_after`

## 明确禁止事项
- 不要把 `.collect-wrapper` 当成直接入专辑入口。
- 不要伪造未验证私有接口。
- 不要把 UI 总数当成已完整抓取数。
- 不要只写文档不落盘 JSON。
- 不要对全部条目做重型 OCR。

## 环境前提与限制
- 仅适用于 macOS + Google Chrome。
- 用户必须已登录小红书网页端。
- Chrome 必须开启“允许 Apple 事件中的 JavaScript”。
- 依赖：`osascript`、AppleScript/JXA、可选 Node.js 与 `@lucasygu/redbook`。

## 关联资源
- `scripts/`
- `references/io-contract.md`
- `references/recovery-and-resume.md`
- `references/environment-and-limitations.md`
- `examples/`
