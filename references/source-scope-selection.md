# 收藏 / 点赞来源范围选择与合并

适用：用户要求整理小红书收藏、点赞，或说“我全都要 / 全部 / 都要”。

## 在什么时点询问范围

欢迎页先让用户选择启动方式：

1. 快速启动（按推荐设置）；
2. 完整启动（自己逐项设置）。

**启动方式之后**才询问本次范围；不要先问范围，也不要在读取前猜测列表总数或图文比例：

> 本次要整理哪个列表？
>
> 1. 收藏
> 2. 点赞
> 3. 我全都要

- **收藏**：只读取收藏列表，来源写为 `collection` / `收藏`。
- **点赞**：只读取点赞列表，来源写为 `liked` / `点赞`；不得取消点赞或改动互动记录。
- **我全都要**：分别读取收藏和点赞，随后仅在本机按 note id 合并去重；同一笔记不会被重复分类或重复移动。

范围确认后，两种启动方式都先完成本机能力检查与“图文 OCR（推荐开启）”询问，再请求当前回合的浏览器授权，最后设置分段读取参数。完整顺序见 [v2.0 执行工作流](workflow.md)。

## 浏览器与读取前提

开始读取前必须逐项确认：

1. 用户在当前回合明确授权具体浏览器；
2. 该浏览器已经打开、已登录小红书，并停在对应的收藏或点赞列表；
3. 浏览器已开启运行本 Skill 所需的 JavaScript 能力；
4. 用户一次设置每组条数（默认 `200`，最大 `200`）与组间暂停分钟数（默认 `3`），并确认：如果当次任务执行器已验证支持受控续组，本次读取可以按该设置继续下一组。

`200` 只是不让单组扩大失控的上限，不是平台公开的安全阈值。暂停用于让用户保留可见、可停止的任务节奏；不要把它描述为规避验证或模仿真人。

已授权页面接入无法被确认时，不能偷偷换一个浏览器、profile 或自动化方式。应保存已完成分段并请用户处理当前浏览器状态。

## 单组采集命令

下面命令只表示**一组**被动读取。它们需要完成上述当前回合浏览器授权后才可执行；旧的 `passive` 路径不会自行滚动或自行进入下一组。

只整理收藏：

```bash
python3 scripts/extract_visible_items.py segment-001-collection.json --backend macos-arc --source collection \
  --capture-mode passive --segment-limit 200 \
  --arc-window-id '<window-id>' --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' --arc-expected-url-substring '<expected-path>'
```

只整理点赞：

```bash
python3 scripts/extract_visible_items.py segment-001-liked.json --backend macos-arc --source liked \
  --capture-mode passive --segment-limit 200 \
  --arc-window-id '<window-id>' --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' --arc-expected-url-substring '<expected-path>'
```

“我全都要”先读取收藏、再读取点赞；每个分段先落盘。任务执行器只有在用户已一次确认自动继续、该执行器已验证支持受控续组、当前页面接入仍可确认且没有安全状态时，才可以按保存的参数请求下一组。否则保留手动分段，不得尝试未验证的浏览器操作。

## 数据契约

`visible_items.json`、`classification.json`、`run_report.json` 都应保留：

```json
{
  "source_lists": ["收藏", "点赞"],
  "source_primary": "收藏"
}
```

- `source_lists`：该笔记出现过的所有来源。
- `source_primary`：第一次读到该笔记的来源。
- 同一 note id 同时出现在两个列表时，只保留一条分类记录，并合并来源。
- 列表页的 `content_type`、封面和图片 URL 都只是 observed 线索；`image_urls_complete` 必须为 `false`。只有详情 `noteData.type` 与 `noteData.imageList` 才能确定图文/视频类型和完整图片集合。

## 完成读取后的下一步

全部分段合并后，先展示真实的图文、视频、未知条数。快速启动据此给出推荐；完整启动再逐项决定视频声音、画面和模型。随后生成分类方案预览：默认只显示专辑与数量，只有用户要求才展开条目。

任何安全验证、登录失效或页面绑定异常都立即停止读取并保留分段；恢复规则见 [失败恢复与续跑](recovery-and-resume.md)。
