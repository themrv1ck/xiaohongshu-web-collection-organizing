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

3. **脚本基础质量**
   - 运行：`python3 -m compileall -q <repo>`。
   - 无输出且 exit 0 才算语法检查通过。

4. **无副作用 smoke tests**
   在下载后的 repo 中运行：

   ```bash
   python3 scripts/check_environment.py
   python3 -m unittest discover -s tests -p 'test_*.py'
   python3 scripts/classify_items.py --skip-ocr examples/visible_items.example.json /tmp/xhs_classification_skip.json
   python3 scripts/run_reassign_batch.py /tmp/xhs_classification_skip.json /tmp/xhs_run_report_dry.json
   python3 scripts/build_retry_queue.py examples/run_report.example.json /tmp/xhs_retry_queue.json
   python3 scripts/summarize_run_report.py examples/run_report.example.json /tmp/xhs_summary.json
   ```

   `build_created_boards.py` 需要三个参数；第二个参数是“现有专辑列表 JSON”，不是输出路径，例如：

   ```bash
   printf '{"boards":["杂项灵感","滑雪"]}\n' > /tmp/xhs_existing_boards.json
   python3 scripts/build_created_boards.py templates/board_taxonomy.template.json /tmp/xhs_existing_boards.json /tmp/xhs_created_boards.json
   ```

5. **有副作用功能不应盲测**
   - `extract_visible_items.py` 依赖已登录浏览器和页面状态，可在用户授权浏览器环境中测。
   - `run_reassign_batch.py` 会实际整理/移动收藏，只有在用户明确授权真实小红书账号操作时才执行。

## 必查差异

对比本机 skill 目录与 GitHub 下载版：

```bash
diff -qr ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing <downloaded-repo>
```

如果本机有新增 Safari 支持、私有 API notes、batch move verified 等而 GitHub 没有，结论必须写成：GitHub 版本落后，不能把本机能力承诺给外部下载者。

忽略差异时要排除：
- `.git/`
- `__pycache__/`
- `*.pyc`
- 本地运行产物：`visible_items.json`、`ocr_results.json`、`classification.json`、`created_boards.json`、`run_report.json`、`retry_queue.json`

## 发布结论标准

- **可以说“可下载”**：GitHub API、clone、zip 都成功。
- **可以说“可安装”**：临时 `HERMES_HOME` 下 `hermes skills list` 能识别。
- **可以说“基础可跑”**：compileall 和无副作用 smoke tests 通过。
- **可以说“适合发给别人直接用”**：除以上外，README 还必须写清：安装命令、macOS/浏览器前置、Chrome Apple Events JavaScript 或 Safari 路径、登录态要求、哪些脚本可直接运行、真实移动入口、真实移动收藏的风险和授权边界。

如果 README 不足或 GitHub 版本落后，最终结论应为：**可下载和基础可跑，但不建议宣传为任何人下载即用；先同步最新版并补 README。**
