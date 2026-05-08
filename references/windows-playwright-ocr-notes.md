# Windows Playwright/CDP + OCR 支持说明

## 适用场景

当用户要求小红书收藏整理 skill 支持 Windows，或在 Windows 上抓取收藏、执行 OCR、生成分类建议时，使用本说明。

## 关键原则

- 不抓取、不复制、不外传 cookies / xsec / signed URL / token。
- Windows 路径应复用用户自己的网页登录态：优先 Playwright 持久化 profile 或连接用户主动启动的 Chrome/Edge CDP。
- macOS AppleScript/Safari 路径继续保留；Windows 不应模拟 AppleScript，而应走浏览器自动化 adapter。
- JSON 契约保持不变：`visible_items.json`、`ocr_results.json`、`classification.json`、`run_report.json`、`retry_queue.json`。

## Windows 浏览器抓取路径

推荐 Playwright 持久化 profile：

```powershell
python scripts\extract_visible_items.py visible_items.json --backend playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore
```

如果用户已经主动用远程调试端口启动 Chrome/Edge，可连接 CDP：

```powershell
python scripts\extract_visible_items.py visible_items.json --backend playwright --cdp-url http://127.0.0.1:9222
```

## Windows OCR 路径

自动选择可用 OCR：

```powershell
python scripts\ocr_cover_images.py visible_items.json ocr_results.json --provider auto
```

强制 Tesseract：

```powershell
python scripts\ocr_cover_images.py visible_items.json ocr_results.json --provider tesseract --tesseract-lang chi_sim+eng
```

强制 EasyOCR：

```powershell
python scripts\ocr_cover_images.py visible_items.json ocr_results.json --provider easyocr
```

## 验证清单

- `python -m compileall -q .`
- `python scripts\check_environment.py`
- `python scripts\extract_visible_items.py --help`
- `python scripts\ocr_cover_images.py --help`
- `python scripts\classify_items.py --skip-ocr examples\visible_items.example.json %TEMP%\xhs_classification_skip.json`

## 发布/分发注意

- 对外宣传可以说“macOS / Windows 支持抓取 + OCR + 分类”。
- 不要承诺“任何人下载就能一键整理全部收藏”。
- 真实移动收藏仍必须在明确授权、分类体系确认、目标专辑确认、并可核验页面状态后执行。
- Windows OCR 质量取决于 Tesseract 中文语言包或 EasyOCR 安装情况。