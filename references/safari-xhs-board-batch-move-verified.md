# Safari 小红书专辑批量移动：已验证路径

## 场景
当网页端“收藏后加入专辑”弹层不稳定，且已经拿到目标 `boardId` 与候选 `noteId` 列表时，可直接在 Safari 当前页面的 webpack runtime 中调用真实前端 API 完成批量入专辑。

## 已验证 API
通过模块 `40122` 暴露的方法：

- `B1` → `POST /api/sns/web/v1/note/collect`
- `d0` → `POST /api/sns/web/v1/note/move`
- `Ks` → `GET /api/sns/web/v1/board/note`
- `U_` → `GET /api/sns/web/v1/board/{boardId}`
- `yC` → `GET /api/sns/web/v1/board/user`

## 已验证关键结论
1. 目标专辑 `boardId` 可直接从 `window.__INITIAL_STATE__.board.boardListData` 读取。
2. `note/move` 的真实 payload 形状已验证为：
   - `{ targetBoardId, notesId }`
3. 在本次会话中，连续对 7 条 Hermes Agent 相关笔记调用 `d0({targetBoardId, notesId})`，返回体均为 `{}`，但随后通过 `U_` + `Ks` 核验，目标专辑 `total=7`，且 7 条笔记全部出现在专辑中。
4. 因此：在这个路径里，`d0` 返回空对象 `{}` 不应直接判定为失败；必须继续做专辑详情与笔记列表核验。
5. UI 上的 `collect-wrapper` / 图标切换依旧不能作为“已入专辑”的证据，最终以 `U_` / `Ks` 结果为准。

## 推荐批量执行顺序
1. 从 `boardListData` 读取目标 `boardId`
2. 准备候选 `noteId[]`
3. 逐条调用 `d0({targetBoardId, notesId})`
4. 批次结束后调用：
   - `U_({resourceParams:{boardId}, params:{imageFormats:'jpg,webp,avif'}})`
   - `Ks({params:{boardId, num:30, cursor:''}})`
5. 用 `detail.total` 与 `notes.notes[].noteId` 做最终核验

## 风险与解释
- 直接用同步 XHR 打 `/api/sns/web/v1/note/move` 可能得到 `500 create invoker failed, service: jarvis-gateway-default`，这不代表前端真实 API 不能用；优先复用 webpack runtime 中现成的前端方法。
- 若 `d0` 空返回但核验成功，应记录为 success，不要误打成 failure。
- 若要给用户汇报，先批量完成并核验，再统一回复，不要停在“已尝试第一条”。
