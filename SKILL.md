---
name: xiaohongshu-web-collection-organizing
description: Organize Xiaohongshu web favorites on macOS with Chrome + AppleScript + DOM injection, including album creation, cautious classification, and the verified re-collect -> banner -> add-to-album workflow.
---

# 小红书 Web 收藏整理（开源版）

## 什么时候用

当你要：
- 读取用户已登录的小红书网页端收藏
- 按主题给收藏分组
- 自动创建专辑
- 把收藏加入对应专辑

## 前提

- macOS
- Google Chrome
- 用户已登录小红书网页版
- Chrome 已开启：`查看 -> 开发者 -> 允许 Apple 事件中的 JavaScript`

## 结论先行

当前可复用的稳定路线是：

1. 先抓收藏页里当前实际可见的收藏条目
2. 先做分类
3. 在真正批量执行前人工复核分类
4. 创建/补齐专辑
5. 对每条笔记走：
   - `取消收藏`
   - 用收藏来源链接重新打开笔记
   - `重新收藏`
   - 等 `收藏成功 / 加入专辑` banner
   - 点 `加入专辑`
   - 点目标专辑
6. 跑完后核对数量与抽样验证

## 为什么不能只靠粗分类直接跑

因为实际执行中很容易出现这些问题：

- `杂项灵感` 变成垃圾桶，塞进大量其实能单独归类的内容
- 穿搭 / 摄影 / 审美 / 成长 / 训练 / 旅行之间会互相串类
- 一些内容从语义上看很像“杂项”，但从用户使用目标看更适合明确专辑

实战修正规则：

- `杂项灵感` 只能做临时缓冲区，不能当最终归宿
- “挑第一只手表”应归入 `穿搭发型与品味`
- 如果滑雪内容数量足够，应拆出独立 `滑雪` 专辑
- 如果用户反馈“你弄丢了两个收藏夹 / 数量不对”，必须先回到抓取和分类阶段重新核对，不要继续带错数据往下跑

## 已验证的关键实现细节

### 1. 不要只用 `/explore/<note_id>` 进入笔记
优先使用收藏页抓到的原始 `href`：

`/user/profile/<self_uid>/<note_id>?xsec_token=...&xsec_source=pc_collect`

原因：
- 这个入口更接近“从收藏场景进入”
- 某些笔记在这种入口下更容易正确挂载收藏控件

### 2. `.collect-wrapper` 不要只做 DOM `.click()`
部分笔记上，单纯 `.click()` 不稳定。
更稳的是派发一整组鼠标事件：

- `mouseover`
- `mousedown`
- `mouseup`
- `click`

### 3. 要接受页面很慢
即使 URL 已对：
- `.collect-wrapper` 也可能晚十几秒才出现
- 某些笔记需要更长等待甚至重试

建议：
- 单条等待至少 45 秒
- 失败时再重试一次

### 4. 批量任务必须可续跑
不要一条失败就整批报废。
建议报告至少包含：

- `visible_count`
- `group_counts`
- `processed`
- `errors`
- `board_counts_before`
- `board_counts_after`
- `sample_verify`

并支持：
- 读取旧报告
- 跳过已成功 ID
- 只补跑未完成条目

## 推荐的专辑层级思想

第一层按主主题分：
- 家居装修与收纳
- 穿搭发型与品味
- 运动训练与体态
- 效率系统与AI
- 日本旅行与机位
- 摄影审美与创作
- 思考与成长

第二层按实际内容再细化：
- 如果滑雪内容足够，拆出 `滑雪`
- 如果某一类下主题仍过杂，再拆更小专辑

## 不要做的事

- 不要把 `.collect-wrapper` 本身误当成“加入专辑按钮”
- 不要在没核对数量时宣称已完成全量整理
- 不要把 UI 显示总数直接当作已经抓全的条数
- 不要在 `杂项灵感` 明显过大时继续机械执行

## 开源交付建议

GitHub 里至少放：
- `README.md`
- `SKILL.md`
- `LICENSE`

如果后续继续完善，可补：
- `examples/`
- `scripts/`
- `reports/`
- `assets/`
