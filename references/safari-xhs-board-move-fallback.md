# Safari 小红书：专辑移动回退路径

## 触发条件
当 Safari 网页端满足以下现象时，直接切到此回退路径：
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

## webpack runtime 回退路径
先暴露运行时：
```js
let req
window.webpackChunkxhs_pc_web.push([[Math.random()], {}, function(__webpack_require__){ req = __webpack_require__ }])
```

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

## 推荐执行顺序
1. 从 `window.__INITIAL_STATE__.board.boardListData` 先确认目标专辑存在，并记录 `boardId`
2. 若详情页收藏态还没打开，先正常点一次收藏
3. 不再等 UI 弹层，直接复用前端真实 `d0({targetBoardId, notesId})`
4. 之后再用：
   - `Ks({params:{boardId,...}})` 或
   - 页面 state / 专辑页重抓
   去核验该笔记是否真的进入目标专辑

## 核验原则
- `note/move` 返回空对象 `{}` 不能直接当失败
- 也不能仅凭调用成功就宣布完成
- 必须复查目标专辑：条数变化、或目标 noteId 出现在专辑笔记列表里

## 常见误判
- 把 `.collect-wrapper` 的数字变化当成专辑条数变化
- 把 `#collected` 图标当成“已进 hermes 专辑”
- 因同步 `XMLHttpRequest` 直打接口拿到 `500 create invoker failed`，就误判接口不可用
  - 这通常说明没走前端真实异步调用链或上下文不对
  - 优先复用页面 webpack 模块导出的 API，而不是手搓裸请求
