# xiaohongshu-web-collection-organizing

小红书网页端收藏整理 skill。它在用户自己的电脑上运行，读取已登录浏览器里的收藏页，抓取收藏条目，识别封面文字，生成专辑分类建议，并在用户显式授权后把收藏移动到目标专辑。

适合：收藏夹很乱、想按主题整理到专辑的人。

不适合：未登录小红书网页端、想绕过浏览器授权、想无确认批量改账号数据的人。

## 当前能力

- 可被 Hermes Agent 安装和识别。
- 支持 macOS 默认 Python 3.9+，不要求额外 Python 包。
- 支持 macOS Chrome + AppleScript/JXA 抓取。
- 支持 macOS Swift + Vision OCR。
- 支持 Windows Chrome/Edge + Playwright/CDP 抓取。
- 支持 Tesseract / EasyOCR OCR。
- 支持分类计划、dry-run 报告、retry queue、报告汇总。
- 支持已有专辑排除清单，默认不移动用户决定保留的已有专辑内容。
- 支持本地轻量 WebUI，方便不熟悉命令行的用户做 dry-run。
- 支持真实批量移动收藏：默认不执行，必须显式传 `--execute`。
- 真实移动后会查询目标专辑笔记列表，确认 note id 已出现后才记为 `success`。

必须知道的限制：

- 用户必须先在浏览器里登录小红书网页端。
- 目标专辑必须已经存在；当前脚本只核对缺失专辑，不自动创建专辑。
- 小红书网页结构和前端模块可能变化；如果页面变更，需要重新验证。
- 分类建议是本地规则和 OCR 结果生成的，低置信度条目默认不会真实移动。

## 目录结构

```text
.
├── SKILL.md
├── README.md
├── LICENSE
├── requirements.txt
├── requirements-windows.txt
├── scripts/
│   ├── check_environment.py
│   ├── extract_visible_items.py
│   ├── ocr_cover_images.py
│   ├── classify_items.py
│   ├── build_existing_boards_inventory.py
│   ├── build_created_boards.py
│   ├── run_reassign_batch.py
│   ├── build_retry_queue.py
│   ├── xhs_skill_webui.py
│   └── summarize_run_report.py
├── templates/
├── examples/
├── references/
└── tests/
```

## 安装

### git clone

```bash
mkdir -p ~/.hermes/skills/social-media
git clone https://github.com/themrv1ck/xiaohongshu-web-collection-organizing.git ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
hermes skills list
```

### zip 下载

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

- Python 3.9+
- 已登录小红书网页端
- Chrome / Edge / Safari 至少一种浏览器

macOS：

- 默认可直接使用系统 `/usr/bin/python3`
- Chrome 需要开启“允许 Apple 事件中的 JavaScript”
- `osascript` 可用
- `swift` + macOS Vision OCR 可用

Windows：

- Chrome 或 Microsoft Edge
- Playwright Python：

```powershell
python -m pip install -r requirements-windows.txt
python -m playwright install chromium
```

- OCR 二选一：Tesseract，或 EasyOCR：

```powershell
python -m pip install easyocr
```

## 无副作用验证

这些命令不会修改小红书账号：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 -m compileall -q .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_environment.py
printf '{"boards":[{"name":"滑雪","notes":[{"id":"694d3390000000002203ae33","title":"固定器角度"}]}]}\n' > /tmp/xhs_existing_boards_source.json
python3 scripts/build_existing_boards_inventory.py /tmp/xhs_existing_boards_source.json /tmp/xhs_existing_boards_inventory.json
python3 scripts/classify_items.py --skip-ocr examples/visible_items.example.json /tmp/xhs_classification_skip.json
python3 scripts/run_reassign_batch.py /tmp/xhs_classification_skip.json /tmp/xhs_run_report_dry.json
python3 scripts/build_retry_queue.py /tmp/xhs_run_report_dry.json /tmp/xhs_retry_queue.json
python3 scripts/summarize_run_report.py /tmp/xhs_run_report_dry.json
```

如果要核对目标专辑和已有专辑：

```bash
printf '{"boards":["杂项灵感","滑雪"]}\n' > /tmp/xhs_existing_boards.json
python3 scripts/build_created_boards.py templates/board_taxonomy.template.json /tmp/xhs_existing_boards.json /tmp/xhs_created_boards.json
```

## 最短真实使用路径

先在 Chrome 打开并登录小红书收藏页，然后在本机终端执行：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing

python3 scripts/check_environment.py
python3 scripts/extract_visible_items.py visible_items.json
python3 scripts/ocr_cover_images.py visible_items.json ocr_results.json
python3 scripts/classify_items.py visible_items.json classification.json --ocr-results ocr_results.json
python3 scripts/run_reassign_batch.py classification.json run_report.json
python3 scripts/summarize_run_report.py run_report.json
```

上面最后一步是 dry-run，只生成计划，不改小红书账号。

确认 `classification.json` 里的目标专辑正确、目标专辑已经存在、低置信度条目已经人工复核后，再执行真实移动：

```bash
python3 scripts/run_reassign_batch.py classification.json run_report.json --execute --browser chrome
python3 scripts/build_retry_queue.py run_report.json retry_queue.json
python3 scripts/summarize_run_report.py run_report.json
```

如果你使用 Safari：

```bash
python3 scripts/run_reassign_batch.py classification.json run_report.json --execute --browser safari
```

Windows / Edge 示例：

```powershell
python scripts\extract_visible_items.py visible_items.json --backend playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore
python scripts\classify_items.py --skip-ocr visible_items.json classification.json
python scripts\run_reassign_batch.py classification.json run_report.json --browser playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore
python scripts\run_reassign_batch.py classification.json run_report.json --execute --browser playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore
```

## 本地 WebUI

不熟悉命令行时，可以在本机终端执行：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 scripts/xhs_skill_webui.py
```

然后打开 `http://127.0.0.1:8765`。WebUI 默认只做 dry-run，输出写到 `webui_runs/latest/`。真实执行必须勾选确认并输入 `EXECUTE`。

## 输出文件

- `visible_items.json`：抓取到的收藏条目
- `ocr_results.json`：每条封面 OCR 结果
- `existing_boards_inventory.json`：用户决定保留的已有专辑排除清单
- `classification.json`：分类建议和 OCR 证据
- `created_boards.json`：目标专辑确认/缺失结果
- `run_report.json`：dry-run 或真实移动报告
- `retry_queue.json`：失败项重试队列

这些文件包含个人收藏信息，默认被 `.gitignore` 忽略，不应提交到公开仓库。

## 脚本说明

- `scripts/check_environment.py`：检查 Python、浏览器自动化、OCR。
- `scripts/extract_visible_items.py`：抓取当前浏览器页面可见收藏条目。
- `scripts/ocr_cover_images.py`：下载封面并执行 OCR。
- `scripts/classify_items.py`：生成分类建议。
- `scripts/build_existing_boards_inventory.py`：从已有专辑 JSON 生成排除清单。
- `scripts/build_created_boards.py`：核对目标专辑是否已存在。
- `scripts/run_reassign_batch.py`：默认 dry-run；传 `--execute` 后真实移动收藏。
- `scripts/build_retry_queue.py`：从运行报告生成重试队列。
- `scripts/xhs_skill_webui.py`：本地轻量 WebUI，默认 dry-run。
- `scripts/summarize_run_report.py`：汇总运行报告。

## 安全边界

- 不保存、不打印 cookies、token、xsec、signed URL。
- 不自动创建、删除、重命名专辑。
- 不把 `.collect-wrapper` 图标变化当成“已加入目标专辑”。
- 不把 UI 总数当成完整抓取数。
- 不移动低置信度分类，除非显式传 `--allow-low-confidence`。
- 不传 `--execute` 时不会改账号。

## 给 Hermes 使用

安装后可以直接对 Hermes 说：

```text
用 xiaohongshu-web-collection-organizing 帮我整理小红书收藏夹。
```

如果当前浏览器未登录，先登录小红书网页端，再让 Hermes 继续。
