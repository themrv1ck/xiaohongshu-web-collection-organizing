# xiaohongshu-web-collection-organizing

一个面向 **macOS / Windows + Hermes Agent** 的小红书网页端收藏整理 skill：抓取当前可见收藏、对封面执行 OCR、生成分类建议、创建/核对专辑，并支持可续跑的批量移动流程。

> 适合：已经在小红书网页端登录、收藏夹比较乱、想把收藏按主题整理到专辑的人。
>
> 不适合：未登录小红书；希望完全无授权地操作账号；希望不经过浏览器状态直接批量改账号数据。

## 当前能力状态

- ✅ 可被 Hermes 作为本地 skill 安装和识别
- ✅ 可运行无副作用检查、示例分类、报告汇总脚本
- ✅ 支持每个条目的封面 OCR：macOS Vision / Tesseract / EasyOCR
- ✅ 支持 macOS Chrome + AppleScript/JXA 路径
- ✅ 支持 Windows Chrome/Edge + Playwright/CDP 抓取路径
- ✅ 支持 Safari 自动化说明与前端运行时回退路径文档
- ⚠️ 真实批量移动收藏会改动你的小红书账号收藏/专辑，必须在明确授权和确认目标专辑后执行
- ⚠️ 小红书网页结构可能变化；如果页面 DOM 或前端模块变更，需要重新验证脚本

## 目录结构

```text
.
├── SKILL.md
├── README.md
├── LICENSE
├── scripts/
│   ├── check_environment.py
│   ├── extract_visible_items.py
│   ├── ocr_cover_images.py
│   ├── classify_items.py
│   ├── build_created_boards.py
│   ├── run_reassign_batch.py
│   ├── build_retry_queue.py
│   └── summarize_run_report.py
├── templates/
├── examples/
└── references/
```

## 安装

### 方式 A：git clone

```bash
mkdir -p ~/.hermes/skills/social-media
git clone https://github.com/themrv1ck/xiaohongshu-web-collection-organizing.git ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
hermes skills list
```

### 方式 B：下载 zip 后安装

```bash
curl -L -o /tmp/xiaohongshu-web-collection-organizing.zip https://github.com/themrv1ck/xiaohongshu-web-collection-organizing/archive/refs/heads/main.zip
unzip /tmp/xiaohongshu-web-collection-organizing.zip -d /tmp
mkdir -p ~/.hermes/skills/social-media
rm -rf ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
mv /tmp/xiaohongshu-web-collection-organizing-main ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
hermes skills list
```

如果 `hermes skills list` 里出现 `xiaohongshu-web-collection-organizing`，说明 Hermes 已识别。

## 前置条件

通用：

- 已安装 Hermes Agent
- 已安装 Python 3.10+
- 已登录小红书网页端
- 使用 Chrome / Edge / Safari 中至少一种浏览器

macOS：

- Chrome 路径需要开启：Chrome 的“允许 Apple 事件中的 JavaScript”
- `osascript` 可用
- `swift` + macOS Vision OCR 可用

Windows：

- Chrome 或 Microsoft Edge
- Playwright Python：`python -m pip install playwright`，然后 `python -m playwright install chromium`
- OCR 二选一：Tesseract（建议带 `chi_sim` 中文语言包并加入 PATH）或 EasyOCR（`python -m pip install easyocr`）

先运行：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 scripts/check_environment.py
```

预期至少看到：

```json
{
  "browser_automation_ready": true,
  "ocr_ready": true
}
```

Windows 上如果看到 `"windows_supported_path_ready": true`，表示抓取 + OCR 的 Windows 路径已就绪。

## 无副作用 smoke test

这些命令不会修改你的小红书账号，适合安装后先验证：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 -m compileall -q .
python3 scripts/check_environment.py
python3 scripts/classify_items.py --skip-ocr examples/visible_items.example.json /tmp/xhs_classification_skip.json
python3 scripts/build_retry_queue.py examples/run_report.example.json /tmp/xhs_retry_queue.json
python3 scripts/summarize_run_report.py examples/run_report.example.json /tmp/xhs_summary.json
```

`build_created_boards.py` 需要三个参数：分类体系、现有专辑列表、输出文件。例如：

```bash
printf '{"boards":["杂项灵感","滑雪"]}\n' > /tmp/xhs_existing_boards.json
python3 scripts/build_created_boards.py templates/board_taxonomy.template.json /tmp/xhs_existing_boards.json /tmp/xhs_created_boards.json
```

## 最短真实使用路径

> 以下流程会读取浏览器页面；最后一步 `run_reassign_batch.py` 可能实际移动收藏，请先确认目标专辑和分类结果。

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing

# 1. 检查环境
python3 scripts/check_environment.py

# 2. 打开并登录小红书收藏页，然后抓取当前可见条目
# macOS 默认使用 Chrome + AppleScript；Windows 使用 --backend playwright
python3 scripts/extract_visible_items.py visible_items.json

# Windows 示例：
# python scripts\extract_visible_items.py visible_items.json --backend playwright --channel msedge --user-data-dir "%USERPROFILE%\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore

# 3. 对封面图跑 OCR
# macOS 默认 swift Vision；Windows 可用 --provider tesseract 或 --provider easyocr
python3 scripts/ocr_cover_images.py visible_items.json ocr_results.json

# 4. 生成分类建议
python3 scripts/classify_items.py visible_items.json classification.json --ocr-results ocr_results.json

# 5. 根据目标专辑体系生成专辑核对结果
python3 scripts/build_created_boards.py templates/board_taxonomy.template.json examples/created_boards.example.json created_boards.json

# 6. 真实移动收藏前，请先人工检查 classification.json 和 created_boards.json
# python3 scripts/run_reassign_batch.py ...
```

## 哪些脚本能直接运行

- `scripts/check_environment.py`：环境检查
- `scripts/extract_visible_items.py`：抓取当前浏览器页面可见收藏条目
- `scripts/ocr_cover_images.py`：下载封面并执行 OCR
- `scripts/classify_items.py`：生成分类建议
- `scripts/build_created_boards.py`：根据目标分类和现有专辑生成核对结果
- `scripts/build_retry_queue.py`：从运行报告生成重试队列
- `scripts/summarize_run_report.py`：汇总运行报告

## 哪些是骨架/模板/示例

- `templates/*.json`：模板，使用前应复制成真实运行配置
- `examples/*.json`：示例数据，只用于理解格式和 smoke test
- `scripts/run_reassign_batch.py`：真实批量移动收藏入口，依赖已登录浏览器页面、收藏状态、目标专辑和分类结果；执行前必须确认风险

## 浏览器说明

### macOS Chrome

默认优先 Chrome。需要：

- 已登录小红书网页端
- Chrome 允许 AppleScript/JXA 执行 JavaScript
- 页面处在收藏页或相关专辑页

### Windows Chrome / Edge

Windows 走 Playwright 或 CDP：

```powershell
python scripts\extract_visible_items.py visible_items.json --backend playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore
```

如果浏览器已用远程调试端口启动：

```powershell
python scripts\extract_visible_items.py visible_items.json --backend playwright --cdp-url http://127.0.0.1:9222
```

### Safari

Safari 路径见：

- `references/safari-web-automation-notes.md`
- `references/safari-xhs-private-api-notes.md`
- `references/safari-xhs-board-move-fallback.md`
- `references/safari-xhs-board-batch-move-verified.md`

Safari 路径里记录了通过前端运行时模块读取专辑、移动笔记、再核验数量和列表的回退方案。

## 输出文件

- `visible_items.json`：抓取到的收藏条目
- `ocr_results.json`：每条封面 OCR 结果
- `classification.json`：分类建议和 OCR 证据
- `created_boards.json`：目标专辑确认/缺失结果
- `run_report.json`：批量执行报告
- `retry_queue.json`：失败项重试队列

这些运行产物默认被 `.gitignore` 忽略，不应提交到公开仓库。

## 风险边界

- 不要在未确认分类结果时直接批量移动收藏
- 不要把 `.collect-wrapper` 图标变化误判为“已加入目标专辑”
- 不要盲猜小红书私有接口 payload
- 不要把 UI 总数当作完整抓取数
- 不要承诺小红书页面结构永久稳定

## 给 Hermes 使用

安装后可以直接对 Hermes 说：

```text
用 xiaohongshu-web-collection-organizing 帮我整理小红书收藏夹。
```

如果当前浏览器未登录，先登录小红书网页端，再让 Hermes 继续。
