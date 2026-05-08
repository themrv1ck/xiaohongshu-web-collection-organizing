# 环境前提与限制

## 支持平台

- macOS：Chrome + AppleScript/JXA 或 Safari + AppleScript；OCR 默认使用 Swift + macOS Vision。
- Windows：Chrome / Edge + Playwright 或 CDP；OCR 使用 Tesseract 或 EasyOCR。
- Linux：脚本的 Playwright 抓取和 Tesseract/EasyOCR OCR 理论可用，但当前 skill 的真实收藏移动路径主要按 macOS/Windows 验证。

## 通用必备前提

- 已安装 Python 3.10+
- 已登录小红书网页端
- 不抓取、不复制、不外传 cookies / xsec / signed URL / token
- 真实移动收藏前必须先确认分类结果和目标专辑

## macOS 路径

必备：
- Google Chrome 或 Safari
- `osascript`
- Chrome 路径需要开启：查看 → 开发者 → 允许 Apple 事件中的 JavaScript
- `swift`
- macOS Vision OCR 可用

检查：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 scripts/check_environment.py
```

期望：
- `browser_automation_ready: true`
- `ocr_ready: true`

## Windows 路径

必备：
- Windows 10/11
- Google Chrome 或 Microsoft Edge
- Python 3.10+
- Playwright Python：

```powershell
python -m pip install playwright
python -m playwright install chromium
```

OCR 二选一：

```powershell
# 方案 A：Tesseract，建议同时安装中文语言包 chi_sim，并把 tesseract.exe 加入 PATH
# 安装方式可用系统包管理器或官方安装包

# 方案 B：EasyOCR
python -m pip install easyocr
```

检查：

```powershell
cd $env:USERPROFILE\.hermes\skills\social-media\xiaohongshu-web-collection-organizing
python scripts\check_environment.py
```

期望：
- `platform: "Windows"`
- `playwright_python: true`
- `chrome_or_edge_executable: true`
- `ocr_ready: true`
- `windows_supported_path_ready: true`

## Windows 抓取命令

推荐使用持久化 profile，让 Playwright 打开的 Chrome/Edge 保留小红书登录态：

```powershell
python scripts\extract_visible_items.py visible_items.json --backend playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore
```

如果已经手动用远程调试端口启动 Chrome/Edge，可以走 CDP：

```powershell
python scripts\extract_visible_items.py visible_items.json --backend playwright --cdp-url http://127.0.0.1:9222
```

## Windows OCR 命令

```powershell
# 自动选择可用 OCR 后端
python scripts\ocr_cover_images.py visible_items.json ocr_results.json --provider auto

# 强制 Tesseract
python scripts\ocr_cover_images.py visible_items.json ocr_results.json --provider tesseract --tesseract-lang chi_sim+eng

# 强制 EasyOCR
python scripts\ocr_cover_images.py visible_items.json ocr_results.json --provider easyocr
```

## 已知限制

- 小红书网页 DOM 和前端模块可能变化，需要按实际页面复核选择器。
- 网页端总数与可抓取总数可能不一致。
- OCR 基于封面图，不是进入每条笔记后的全文识别。
- 无封面图 URL 或图片下载失败的条目只能退回标题/desc/tags 规则分类。
- Windows 的 Playwright 路径优先覆盖“抓取 + OCR + 分类”；真实移动收藏仍必须在明确授权、目标专辑确认、且页面状态可核验时执行。
- Tesseract 中文识别依赖 `chi_sim` 语言包；缺失时脚本会退回英文，中文效果会变差。
