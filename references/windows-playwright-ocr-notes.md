# Windows Playwright/CDP + OCR 支持说明

## 使用顺序

Windows 用户同样遵守 v2.0 流程：先选快速启动或完整启动，再选收藏、点赞或“我全都要”；随后做本机能力检查、在读取前询问“图文 OCR（推荐开启）”、得到当前回合的浏览器授权、设置每组条数和暂停分钟数，最后才读取列表。

不要在 Windows 上因为可以使用 Playwright 就跳过浏览器授权。用户必须明确授权 Chrome 或 Edge，并确认该浏览器已经打开、已登录小红书、已经进入正确列表，且 JavaScript 接入可用。没有授权时，不创建或连接浏览器，也不改用其他 profile。

## 浏览器路径

优先使用用户当前授权且已打开的浏览器会话。若用户已经主动用远程调试端口启动 Chrome/Edge，才可连接 CDP：

```powershell
python scripts\extract_visible_items.py segment-001-visible.json --backend playwright --cdp-url http://127.0.0.1:9222 --capture-mode passive --segment-limit 200
```

持久化 profile 仅在用户明确要求一个独立 profile、并同意创建它时使用；它不是默认登录态替代品：

```powershell
python scripts\extract_visible_items.py segment-001-visible.json --backend playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --capture-mode passive --segment-limit 200
```

两条命令都只代表一组被动读取。只有用户一次确认自动继续、当次任务执行器已验证支持受控续组、当前页面接入持续可用且没有安全状态时，任务执行器才可以按保存的设置请求下一组。每组最大 `200` 条、默认暂停 `3` 分钟；这不是平台公开安全阈值，也不应描述为规避验证或模仿真人。

## Windows OCR 路径

读取前先运行：

```powershell
python scripts\check_environment.py --capability-preflight
```

显示“图文 OCR（推荐开启，以提高识别率）”。用户选择开启时，先复用已有 OCR；缺失时才展示组件和安装确认。Windows 默认推荐 Tesseract + 简体中文 `chi_sim` 与英文 `eng`，常见安装体积为几十到数百 MB，以安装器实际显示为准。默认安装目录通常是 `C:\Program Files\Tesseract-OCR`，以用户实际选择为准。

安装后必须复验：

```powershell
python scripts\check_environment.py --ocr
```

`tesseract --list-langs` 必须同时包含 `chi_sim` 与 `eng`；缺少任一项都不是 OCR 就绪，不能退回单语。EasyOCR 是 GB 级可选替代方案，只有用户明确选择时才安装。

OCR 环境就绪且用户已明确授权详情补齐范围后：

```powershell
python scripts\enrich_note_images.py visible_items.json image_items.json --allow-detail-requests --max-items <1–200>
python scripts\ocr_note_images.py image_items.json ocr_results.json --provider auto
```

列表页封面和图片只能算 observed，必须保持 `image_urls_complete=false`。只有详情 `noteData.type` 确认图文、`noteData.imageList` 提供完整有序图片集时，才可逐张 OCR。任一图片下载或 OCR 失败时，不得用部分文字分类。

## 视频与分类方案

读取全部列表并显示真实图文/视频比例后，快速启动给出是否开启视频内容分类的推荐；完整启动再逐项让用户选声音、画面和已有 AI/模型。Windows 的 OCR 不等于视频画面识别；视频画面需要真实帧和真正支持视觉输入的 provider。详见 [根据视频实际内容分类](video-content-classification.md)。

分类完成后先展示分类方案预览：默认只显示专辑和数量。用户要求时才展开专辑内条目。真实移动仍需分类、目标专辑、dry-run 和当次执行确认。

## 验证与恢复

安全验证、登录页、页面绑定丢失或状态不确定时，立即保存结果和 `xhs_safety_state.json`，停止所有浏览器读取、详情请求和写入。请用户在当前页面处理后回复“继续整理”；恢复时只检查当前授权页面，不重读已保存分段、不自动重试旧请求。

## 验证清单

```powershell
python -m compileall -q .
python scripts\check_environment.py --ocr
python scripts\extract_visible_items.py --help
python scripts\enrich_note_images.py --help
python scripts\ocr_note_images.py --help
python scripts\classify_items.py --skip-ocr examples\visible_items.example.json $env:TEMP\xhs_classification_skip.json
```

对外只能承诺“支持在 macOS / Windows 上读取授权列表、完整图文 OCR 和生成分类方案”。不要承诺任何人下载后即可无人值守地整理全部收藏；每次真实浏览器访问、恢复和移动都需要当次可验证的用户授权。
