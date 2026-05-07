# Safari 小红书收藏整理：前端模块与接口笔记

## 背景
在 Safari 已登录小红书网页端、用户要求使用 Safari 的场景中，AppleScript `do JavaScript` 可稳定驱动页面；复杂 JS 应写入临时文件后执行，避免 shell/AppleScript 引号问题。

## 已验证的稳定执行方式
- 使用 Safari 当前标签执行 JS：`tell application "Safari" to do JavaScript ... in current tab of front window`
- 对复杂逻辑：先把 JS 写入文件，再由 Python/AppleScript 包装器读取文件内容注入 Safari。
- 可通过 webpack chunk 暴露运行时：
  ```js
  window.__wreq = null;
  window.webpackChunkxhs_pc_web.push([[Math.floor(Math.random()*1e9)], {}, function(r){ window.__wreq = r; }]);
  ```

## 小红书前端模块观察
前端 API 模块曾定位到 webpack module `40122`，其中导出含义包括：
- `xh` → 创建专辑：`POST /api/sns/web/v1/board`
- `yC` → 查询用户专辑：`GET /api/sns/web/v1/board/user`
- `Ks` → 查询专辑笔记列表：`GET /api/sns/web/v1/board/note`
- `d0` → 专辑间移动笔记：`POST /api/sns/web/v1/note/move`
- `Vn` → 个人页收藏列表：`GET /api/sns/web/v2/note/collect/page`

前端模块 `35804` 中观察到收藏/专辑选择器逻辑：
- 收藏页请求形态：`Vn({params:{cursor:e.cursor,num:30}})`
- 专辑笔记请求形态：`Ks({params:{boardId:e,num:30,cursor:t.cursor}})`
- 专辑列表请求形态：`yC({params:{userId:r,num:15,page:e.page}})`

移动笔记 hook 曾定位到 module `71946`：
- `useMoveNoteToBoard()` 内部调用 `d0(M)`，但真实 payload 仍需从调用 UI 上下文继续追踪，不能猜。

## 重要坑点
- 不要伪造或硬猜 `note/move` payload。多种常见参数组合会返回业务失败：`HTTPBizError`, `code: -1`, `success:false`。
- 直接调用个人收藏列表 `Vn` 时，如果页面/上下文不匹配或参数不完整，可能返回：`code:-9109`, `msg:"参数错误"`。不要把这误判为未登录。
- 私有接口调用需要复用页面前端 HTTP 客户端生成的请求头/sign；普通 `fetch` 可能返回 `406` 或业务失败。
- 页面上 UI 数量（如 `笔记・266`、`专辑・13`）只能作为状态信号，不能当作已完整抓取结果。
- 如果已成功创建目标专辑，应先记录目标专辑名称与 boardId，再继续整理，不要丢失状态。

## 后续推荐路径
1. 先用 `yC` 确认目标专辑存在并记录 `boardId`。
2. 用前端真实调用链继续追踪 `d0` 的调用位置/参数，而不是枚举 payload。
3. 若接口路径仍不稳定，回到 UI 交互：定位“管理/选择/移动到专辑”入口，通过真实页面操作触发移动。
4. 每次成功移动后立即写入 `run_report.json`，并复查目标专辑样本。