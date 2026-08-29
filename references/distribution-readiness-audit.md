# 分发可用性审计：小红书收藏整理 Skill

当用户问“我准备分享出去，别人下载后是否能使用”时，用这个清单模拟陌生用户，而不是只检查本机安装版。

## 审计层级

1. **可公开下载**
   - `https://api.github.com/repos/<owner>/<repo>` 返回 `200`。
   - `git clone --depth 1 https://github.com/<owner>/<repo>.git <tmp>/repo` 成功。
   - `curl -L -o <tmp>/main.zip https://github.com/<owner>/<repo>/archive/refs/heads/main.zip` 成功，zip 可解压。

2. **可被 Hermes 识别**
   - 新建临时目录作为 `HERMES_HOME`。
   - 把下载后的仓库复制到：`$HERMES_HOME/skills/social-media/xiaohongshu-web-collection-organizing/`。
   - 运行：`HERMES_HOME=<tmp> hermes skills list`。
   - 预期：显示 `xiaohongshu-web-collection-organizing`，category 为 `social-media`，status enabled。

3. **可被 WorkBuddy 作为 Plugin 识别**
   - `codebuddy plugin validate <repo>` 通过。
   - `codebuddy plugin validate <repo>/.codebuddy-plugin/marketplace.json` 通过。
   - 在临时 `CODEBUDDY_CONFIG_DIR` 中添加本地 marketplace 并安装 `xiaohongshu-organizer@xiaohongshu-skill-marketplace`。
   - `cd workbuddy-plugin-src && npm ci && npm run build && npm run test:mcp` 通过；六个 `xhs_workbuddy_*` 工具齐全，status 返回 `host=workbuddy` 与 `browser_backend=playwright`。

4. **SkillHub / RedSkill 发布包结构正确**
   - 分别运行：`python3 scripts/build_redskill_package.py --channel redskill --output-dir <tmp>/redskill` 和 `python3 scripts/build_redskill_package.py --channel skillhub --output-dir <tmp>/skillhub`。
   - ZIP 只能有一个顶层目录，且必须与 `SKILL.md` 的 `name` 一致。
   - ZIP 根 Skill 目录必须包含 `.codebuddy-plugin/plugin.json`、`.mcp.json` 和 `server/xhs-workbuddy-mcp.mjs`；不能把缺少 Plugin 的普通 Skill 包称为 WorkBuddy 2.1.0。
   - 上传包不得包含 `tests/` 或 `workbuddy-plugin-src/`，文件数不得超过 100；源码和测试继续保留在 GitHub。
   - `manifest.yaml`、Plugin、Marketplace、MCP 构建产物和 `SKILL.md` 的版本必须全部一致。
   - 用 `python3 scripts/build_redskill_package.py --output-dir <tmp>/noop --validate-only <zip>` 复验，结果必须为 `{"valid": true, "errors": []}`。

5. **脚本基础质量**
   - 运行：`python3 -m compileall -q <repo>`。
   - 无输出且 exit 0 才算语法检查通过。

6. **无副作用 smoke tests**
   在下载后的 repo 中运行：

   ```bash
   python3 scripts/check_environment.py
   python3 -m unittest discover -s tests -p 'test_*.py'
   python3 scripts/classify_items.py --skip-ocr examples/visible_items.example.json /tmp/xhs_classification_skip.json
   python3 scripts/run_reassign_batch.py /tmp/xhs_classification_skip.json /tmp/xhs_classification_preview.json
   python3 scripts/build_retry_queue.py examples/run_report.example.json /tmp/xhs_retry_queue.json
   python3 scripts/summarize_run_report.py examples/run_report.example.json /tmp/xhs_summary.json
   ```

   无浏览器证据的 `run_reassign_batch.py` 必须输出 `classification_preview`、`ready_for_execute=false`，不能输出可执行 dry-run。`build_created_boards.py` 需要三个参数；第二个参数是“现有专辑列表或 `board_snapshot.json`”，不是输出路径，例如：

   ```bash
   printf '{"boards":["杂项灵感","示例主题A"]}\n' > /tmp/xhs_existing_boards.json
   python3 scripts/build_created_boards.py templates/board_taxonomy.template.json /tmp/xhs_existing_boards.json /tmp/xhs_created_boards.json
   ```

7. **有副作用功能不应盲测**
   - `extract_visible_items.py` 依赖已登录浏览器和页面状态，可在用户授权浏览器环境中测。
   - `capture_board_snapshot.py` 依赖用户本轮授权的已登录浏览器，只读调用前端 `yC + U_ + Ks`。
   - `run_reassign_batch.py --execute` 会实际整理/移动收藏；没有 `board_snapshot.json` 和 `created_boards.json` 时必须在接触浏览器前拒绝。

## 必查差异

对比本机 skill 目录与 GitHub 下载版：

```bash
diff -qr ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing <downloaded-repo>
```

如果本机有新增 Safari 支持、私有 API notes、batch move verified 等而 GitHub 没有，结论必须写成：GitHub 版本落后，不能把本机能力承诺给外部下载者。

如果 GitHub API 匿名访问返回 `403 rate limit exceeded`，不要直接判定仓库不可公开访问；继续用两条匿名路径核验：`git clone --depth 1 https://github.com/<owner>/<repo>.git` 和 `curl -L --fail https://github.com/<owner>/<repo>/archive/refs/heads/main.zip`。clone 与 zip 均成功时，可判为“公开下载路径可用，但 API 检查受限”。

如果下载版与本机版仅 `SKILL.md` 存在差异，也不能自动忽略：`SKILL.md` 里常包含安全边界、workflow pitfall、回归链路和不泄露 token/xsec/cookie 的约束。发布前必须 diff 内容；若差异涉及安全/执行纪律/恢复流程，应先同步到 GitHub 后再宣布“达到公开发布最低标准”。

忽略差异时要排除：
- `.git/`
- `__pycache__/`
- `*.pyc`
- 本地运行产物：`visible_items.json`、`crawl_manifest.json`、`ocr_results.json`、`video_transcripts.json`、`video_analysis.json`、`.video-content-cache/`、`classification.json`、`classification_preview.json`、`board_snapshot.json`、`created_boards.json`、`run_report.json`、`retry_queue.json`

## 发布结论标准

- **可以说“可下载”**：GitHub API、clone、zip 都成功。
- **可以说“Hermes 可安装”**：临时 `HERMES_HOME` 下 `hermes skills list` 能识别。
- **可以说“WorkBuddy Plugin 可安装”**：插件/市场清单、临时市场安装和 MCP smoke 全部通过。
- **可以说“基础可跑”**：compileall 和无副作用 smoke tests 通过。
- **可以说“适合发给 WorkBuddy 用户直接用”**：除以上外，README 还必须写清插件安装、专用 Playwright 首次下载、独立登录态、六个 MCP 工具顺序、真实移动风险和授权边界；不得再要求 WorkBuddy 用户修复 Safari Automation。

如果 README 不足或 GitHub 版本落后，最终结论应为：**可下载和基础可跑，但不建议宣传为任何人下载即用；先同步最新版并补 README。**
