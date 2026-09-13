# 执行工作流速查

1. 先让用户选择“收藏”“点赞”“我全都要”或“其他”。“其他”先复述来源、范围、结果、允许读写和本轮浏览器，再判断是否能严格映射到本流程；不可实现时说明原因并推荐手工步骤，不打开浏览器试探。
2. 运行本地能力只读预检，再选择快速、轻度或深度整理。轻度/深度必须先询问是否需要最终桌面 HTML 报告。
3. 取得当前回合对具体浏览器的授权后，完整读取所选来源；保存 note id 与 `source_lists/source_primary`。不得只保存标题后重新搜索。
4. 读取完整专辑卡片和成员卡片。声明数量、唯一 ID、专辑身份、成员数量只要缺页、重复或变化就停止。
5. 载入同账号最新的 `xhs-skill-archive-registry-v2`。只保护登记专辑在本轮快照中的实时成员；所有已收藏笔记或所有普通专辑成员都不是保护依据。首次运行使用显式空登记。
6. 只对未受保护条目做详情、OCR、视频或分类。无法确定的条目固定进入“无法确定”；不存在时和其他待创建专辑一起交给用户确认。
7. 用 `board_snapshot.json`、`created_boards.json` 和上一份 `archive_registry` 生成 dry-run。只有 `mode=dry_run`、`ready_for_execute=true`、`blockers=[]` 才能请求写入确认。
8. execute 时每次只处理一条：回到该条真实来源列表，按 note id 点击卡片。未收藏时点击一次收藏并立即使用“加入专辑”；已收藏的待归档条目只有明确同意取消后重收藏才能操作（直接脚本 `--allow-recollect`，WorkBuddy 绑定新审批合同）。详见 `visible-collection-entry.md`。禁止按标题搜索、访问内部模块、私有接口或操作已保护条目。
9. 写入后回读目标专辑必须精确增加该 ID；从未登记专辑迁移时，原专辑还必须精确减少该 ID。任一未知写入状态、300031、安全验证、登录页或绑定变化都持久化停机，不自动重试。
10. 只有整批完成且最终完整成员回读通过，才生成新的不可覆盖 v2 归档登记。失败、中止、仅 dry-run 或达到移动上限都不更新登记。
11. 若整理前选择生成报告，只使用同批已保存且校验通过的内容证据与最终成员快照生成桌面 HTML；不为补报告重新访问笔记。

直接执行示例（仅在本回合已明确授权 Arc 后，在仓库根目录运行）：

```bash
python3 scripts/run_reassign_batch.py classification.json run_report.json \
  --board-snapshot board_snapshot.json \
  --created-boards created_boards.json \
  --archive-registry previous-registry.json \
  --archive-output registry-NEW.json \
  --execute --browser arc --user-id '<user-id>' \
  --expected-url-substring '<exact-profile-binding>' \
  --max-moves-per-session '<1-200>' \
  --arc-window-id '<window-id>' \
  --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' \
  --arc-expected-url-substring '<exact-profile-binding>'
```

首次运行也必须传显式空的 v2 登记，防止把“未提供登记”和“登记读取失败”混为一谈。
