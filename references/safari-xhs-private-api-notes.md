# Safari 小红书收藏整理：前端模块历史取证（不可执行）

> 本文保留前端结构证据，不能作为直接调用接口、模拟 UI 或回退执行的说明。所有读写必须走当前 Skill 的统一采集、执行和安全停机脚本。

## 背景
在 Safari 已登录小红书网页端、用户要求使用 Safari 的场景中，AppleScript `do JavaScript` 可稳定驱动页面；复杂 JS 应写入临时文件后执行，避免 shell/AppleScript 引号问题。

## 已废止的运行时探测

旧版本曾向网页打包运行时注册自定义模块，用来取得内部接口。该动作与自动化会话被重定向到安全验证错误页高度相关，现已从全部可执行代码删除。任何宿主、模型或浏览器都不得恢复、复制或改写这种探测方式。

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

## 当前规则

读取专辑成员、创建专辑和移动笔记当前全部安全停用，并在打开浏览器前报错。只有新的非注入式实现经过真实页面验证和回归测试后，才能重新开放这些功能。
