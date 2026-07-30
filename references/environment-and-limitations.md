# 环境前提与限制

## 支持平台

- macOS：Arc/Chrome + AppleScript/JXA 或 Safari + AppleScript；OCR 默认使用 Swift + macOS Vision。
- Windows：Chrome / Edge + Playwright 或 CDP；OCR 使用 Tesseract 或 EasyOCR。
- Linux：脚本的 Playwright 抓取和 Tesseract/EasyOCR OCR 理论可用，但当前 skill 的真实收藏移动路径主要按 macOS/Windows 验证。

## 通用必备前提

- 已安装 Python 3.9+
- 已登录小红书网页端
- 不抓取、不复制、不外传 cookies / xsec / signed URL / token
- 真实移动收藏前必须先确认分类结果和目标专辑
- `visible_items.json`、`image_items.json` 和 `ocr_cache/` 内文件都是仅供本机当前用户使用的私密中间工件，文件权限必须为 `0600`；正式报告不得复制原始 CDN query。任务结束后可以手动删除这些中间文件和缓存，但不得自动删除，以免破坏核验与续跑。

首次运行顺序固定为：欢迎与整理范围 → 本地能力只读预检 → 快速整理 / 轻度整理 / 深度整理 → 取得当前回合的具体浏览器授权 → 完整检查所选环境。只有用户回复“自定义”时，才改为逐项选择图文 OCR 和视频内容分类。快速=全部关闭；轻度=只开 OCR；深度=同时开启 OCR、视频语音和视频画面识别。预检只检查已配置路径和本地组件，不操作浏览器、不联网、不安装、不加载大模型；真正使用和安装仍需要用户选择。

## 首次本地能力预检

范围确认后运行：

```bash
python3 scripts/check_environment.py --capability-preflight
```

当前宿主 Agent 能证明自身具备视觉能力时，可加 `--host-visual-capability ready --host-visual-name "<名称>"`；无法证明时保持默认 `unknown`。该模式与 `--browser`、`--video-content`、`--analysis-provider`、`--check-login-state` 和 `--visual-analysis` 互斥，输出 `safety`、`capabilities.ocr`、`capabilities.video_audio`、`capabilities.local_visual` 与 `capabilities.host_visual`。它只检查 `XHS_MIMO_ASR_ROOT` / `XHS_MIMO_VL_ROOT`、显式参数和文档约定路径，不扫描整块硬盘寻找未知模型。

## macOS 路径

必备：
- Arc、Google Chrome 或 Safari
- `osascript`
- Chrome 路径需要开启：查看 → 开发者 → 允许 Apple 事件中的 JavaScript

## 可选图文 OCR

完整卡片、体积与安装门禁见 [图文 OCR 分类开关](image-ocr-classification.md)。首次预检已经报告 OCR 是否存在；用户选择“轻度整理”“深度整理”或自定义回复“开启”后才允许使用或安装，安装后运行：

检查：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 scripts/check_environment.py --ocr
```

期望：
- `ocr_checked: true`
- `ocr_ready: true`
- `ocr_provider: "swift-vision"` 或 `"tesseract-chi_sim"`（默认路径不会自动选择 EasyOCR）

macOS 优先复用系统 Vision，OCR 模型本身通常为 0 MB 额外下载。若缺少 Swift/Command Line Tools，则是随 macOS 版本变化的 GB 级系统组件，必须以系统安装窗口展示的实际下载量和磁盘占用为准。用户选择“轻度整理”“深度整理”或自定义回复“开启”已经授权缺失 OCR 组件安装，但系统权限窗口仍需用户确认。

用户选择“快速整理”或自定义回复“不开启”时不运行 `--ocr`、不安装或使用 OCR，也不运行 `enrich_note_images.py` 或 `ocr_note_images.py`；预检发现 OCR 也不能改变该选择，分类命令显式传 `--skip-ocr`。

## 可选视频内容分类

只有范围、只读预检和快速启动档位都已完成后，才进入视频分支：深度整理直接开启“根据视频实际内容分类”；快速/轻度整理不做完整视频环境检查，不安装或运行以下依赖。自定义路径仍在 OCR 回答后询问视频开关。完整对话卡片见 [video-content-classification.md](video-content-classification.md)。

Arc 检测命令：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 scripts/check_environment.py \
  --video-content --browser arc --check-login-state \
  --analysis-provider mimo-vl-mlx --visual-analysis
```

需要：

- Video Transcript Extractor
- `yt-dlp`、`ffmpeg`、`ffprobe`
- `browser-cookie3`（Arc 登录态只读检测）
- “耳朵”：MiMo-V2.5-ASR-MLX + Audio Tokenizer，约 6.6 GB
- “眼睛”：默认 MiMo-VL-7B-RL-2508 官方 BF16 + `mlx-vlm==0.5.0`，下载约 16.6 GB，本机实测推理峰值约 17.6 GB；Apple Silicon 建议 32 GB 统一内存或以上，24 GB 可能紧张
- 明确选择的 analysis provider：`codex-cli`、`mimo-vl-mlx` 或 `command`/宿主 Agent 适配器
- Arc 已安装、正在运行且已登录小红书

检测结果按能力解读：

- `capabilities.asr.ready=true`：字幕缺失时具备本地转写能力。
- `capabilities.text_analysis.ready=true`：所选 provider 可根据文字稿生成 memo。
- `capabilities.visual_analysis.ready=true` 且 `status=ready`：所选 provider 已实测能读取真实帧。这只会在显式传 `--visual-analysis` 时成为必需能力；一旦开启，所有明确视频都必须执行完整时轴画面分析。
- 不传 `--visual-analysis` 时，`capabilities.visual_analysis.status=not_enabled`；即使 provider 本身可以看图，当次结果也只允许生成 `transcript_only`，不得声称检查过画面。

`missing` 非空时先逐项展示用途和安装命令。用户选择“深度整理”或“声音和画面都分析”（兼容“耳朵和眼睛都要”或裸回复“开启”）已同意缺失时安装 ASR 与所选本地视觉模块；选择“只分析声音”（兼容“只听声音”）只同意安装缺失 ASR。其他缺失项仍须单独取得安装同意。必要依赖未安装时不得把视频改用简介分类。

MiMo-VL 的视频入口只看画面，不读取音轨；声音始终来自平台字幕或 MiMo ASR。`codex-cli` 是可选 provider，不是 Skill 的强制环境。`command` 可对接 Cloud Code、QQ Bot、Hermes 或其他宿主 Agent/API；仅文本适配器不能冒充视觉 provider。

不需要 Qwen 或 LM Studio。完整流程见 [video-content-classification.md](video-content-classification.md)。

## Windows 路径

必备：
- Windows 10/11
- Google Chrome 或 Microsoft Edge
- Python 3.9+
- Playwright Python：

```powershell
python -m pip install playwright
python -m playwright install chromium
```

用户开启图文 OCR 时，默认安装 Tesseract + 简体中文 `chi_sim`。其安装通常为几十到数百 MB，实际大小取决于当前安装器包含的语言，安装前必须显示安装器报告的下载量和磁盘占用：

```powershell
# 默认：Tesseract，同时安装中文语言包 chi_sim，并把 tesseract.exe 加入 PATH
# 安装方式可用系统包管理器或官方安装包

# 只有用户明确选择 GB 级替代方案时才安装 EasyOCR
python -m pip install easyocr
```

检查：

```powershell
cd $env:USERPROFILE\.hermes\skills\social-media\xiaohongshu-web-collection-organizing
python scripts\check_environment.py --ocr
```

期望：
- `platform: "Windows"`
- `ocr_checked: true`
- `playwright_python: true`
- `chrome_or_edge_executable: true`
- `ocr_ready: true`
- `tesseract_chi_sim: true`（使用 Tesseract 时）
- `windows_supported_path_ready: true`

## Windows 抓取命令

推荐使用持久化 profile，让 Playwright 打开的 Chrome/Edge 保留小红书登录态：

```powershell
python scripts\extract_visible_items.py segment-001-visible.json --backend playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --capture-mode passive --segment-limit 200
```

如果已经手动用远程调试端口启动 Chrome/Edge，可以走 CDP：

```powershell
python scripts\extract_visible_items.py segment-001-visible.json --backend playwright --cdp-url http://127.0.0.1:9222 --capture-mode passive --segment-limit 200
```

## Windows OCR 命令

```powershell
# 先补齐每条明确图文笔记的封面和全部内页图片
python scripts\enrich_note_images.py visible_items.json image_items.json --allow-detail-requests --max-items <本次明确范围>

# 自动选择可用 OCR 后端
python scripts\ocr_note_images.py image_items.json ocr_results.json --provider auto

# 强制 Tesseract
python scripts\ocr_note_images.py image_items.json ocr_results.json --provider tesseract --tesseract-lang chi_sim

# 强制 EasyOCR
python scripts\ocr_note_images.py image_items.json ocr_results.json --provider easyocr
```

`chi_sim` 是 Tesseract 默认语言配置。只有 `tesseract --list-langs` 明确包含 `eng` 时，才可显式改用 `--tesseract-lang chi_sim+eng`；未安装 `eng` 时不得把它写入默认命令。

## 已知限制

- 小红书网页 DOM 和前端模块可能变化，需要按实际页面复核选择器。
- 网页端总数与可抓取总数可能不一致。
- 列表页取得的封面或其它图片只能算 observed，必须保持 `image_urls_complete=false`；只有详情 `noteData.imageList` 才能声明完整。详情 `noteData.type` 是图文/视频类型的权威来源，覆盖列表页 observed 类型。
- 图文 OCR 必须基于笔记详情中按原顺序取得的封面和全部内页图片；图片列表不完整时标记 `incomplete_image_set`，不得只识别封面后声称完成。详情请求触发 `security_blocked` 时立即停止后续请求、落盘未请求状态并以非零退出码结束。
- 任一图文图片下载或 OCR 失败时，不使用部分 OCR 文本分类；目标专辑保持为空并进入人工复核，同时保留真实错误状态，不得静默退回元数据分类。
- OCR 只提取可见文字。没有文字的纯画面不属于 OCR，OCR 不能理解人物、物体、场景或动作。
- OCR 缓存只有在 `image_set_sha256` 和 `ocr_run_fingerprint` 同时一致时才可复用；运行指纹绑定实际 provider、Tesseract 语言及 Swift OCR 脚本版本。
- 上一条图文 OCR 边界不改变视频规则：视频内容分类开启后，视频转写/分析失败必须留给人工复核，不得回退简介或视频封面 OCR。视觉模块开启后，任意明确视频没有完整时轴证据都必须标为未完成。
- Windows 的 Playwright 路径优先覆盖“抓取 + OCR + 分类”；真实移动收藏仍必须在明确授权、目标专辑确认、且页面状态可核验时执行。
- Tesseract 中文识别依赖 `chi_sim` 语言包；缺失时必须判定中文 OCR 未就绪，不得静默改用英文。
