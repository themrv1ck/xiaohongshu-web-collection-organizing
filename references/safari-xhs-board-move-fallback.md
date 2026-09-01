# Safari 小红书：专辑移动历史取证（不可执行）

> 本文仅保留历史页面结构取证，不是回退路径，也不得直接运行其中的 JS、点击 UI 或调用接口。读取专辑成员、创建专辑和移动笔记当前全部安全停用。

## 历史观察
Safari 网页端曾出现以下现象：
- 详情页能点“收藏”，`.collect-wrapper` 图标会在 `#collect` / `#collected` 间切换
- 但“加入专辑 / 选择专辑 / hermes”弹层不稳定、不出现或抓不到
- 不能把“已收藏”误判成“已加入目标专辑”

## 关键观察
- 详情页收藏按钮 DOM：`#note-page-collect-board-guide.collect-wrapper`
- 该按钮只代表收藏状态，不代表目标专辑状态。
- 当前账号专辑列表可从页面状态直接读出：
  - `window.__INITIAL_STATE__.board.boardListData`
- 其中可直接拿到：
  - 当前账号 user key
  - 每个专辑的 `id`（boardId）
  - `name`
  - `total` / noteCount

## 本次会话已验证的专辑信息读取方式
```js
const state = window.__INITIAL_STATE__
const key = Object.keys((state.board && state.board.boardListData) || {})[0]
const boards = state.board.boardListData[key].boards
```

在该会话里，`hermes` 专辑已能从这里直接读到，且包含真实 `boardId` 与初始条数。

## 已废止的运行时探测

旧版本曾向网页打包运行时注册自定义模块。该方式与自动化会话进入安全验证错误页高度相关，已从可执行代码和本文示例中删除，禁止恢复。

本次会话再次验证到 API 模块是 `40122`，其中：
- `yC` → `GET /api/sns/web/v1/board/user`
- `Ks` → `GET /api/sns/web/v1/board/note`
- `d0` → `POST /api/sns/web/v1/note/move`
- `B1` → `POST /api/sns/web/v1/note/collect`
- `LN` → `POST /api/sns/web/v1/note/uncollect`

## 已验证的 move payload 形状
不要再盲猜；会话里已从前端调用点反推出：
```js
{ targetBoardId, notesId }
```

调用点来自前端 `BoardSelect` 逻辑，核心片段：
```js
ee({ targetBoardId: n, notesId: e.noteId })
```
其中 `ee` 是 `useMoveNoteToBoard()`，内部再调用 `d0(...)`。

## 当前执行规则

不要把这些历史观察当作直接执行说明。当前统一执行器会在浏览器启动前停止，不读取专辑、不创建专辑、不移动笔记。

## 核验原则
- `note/move` 返回空对象 `{}` 不能直接当失败
- 也不能仅凭调用成功就宣布完成
- 必须复查目标专辑：条数变化、或目标 noteId 出现在专辑笔记列表里

## 常见误判
- 把 `.collect-wrapper` 的数字变化当成专辑条数变化
- 把 `#collected` 图标当成“已进 hermes 专辑”
- 因同步 `XMLHttpRequest` 直打接口拿到 `500 create invoker failed`，就误判接口不可用
  - 这通常说明没走前端真实异步调用链或上下文不对
  - 这类内部调用已经停用，不得换一种方式继续尝试
