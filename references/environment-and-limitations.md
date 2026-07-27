# 环境前提与限制

## 支持平台

| 平台 | 可用浏览器路径 | 默认图文 OCR |
| --- | --- | --- |
| macOS | 用户当次授权的 Arc、Chrome 或 Safari；通过 AppleScript/JXA 访问已打开页面 | Swift + macOS Vision |
| Windows | 用户当次授权的 Chrome 或 Edge；Playwright/CDP adapter | Tesseract + `chi_sim` + `eng` |
| Linux | Playwright 与 Tesseract/EasyOCR 可用于抓取和 OCR；真实收藏移动目前主要按 macOS/Windows 验证 | 取决于用户安装的 OCR |

无论平台，都不抓取、不复制、不外传 cookie、xsec、token 或完整签名 URL。中间 JSON、图片缓存和报告只供本机当前用户使用；任务完成后可以由用户手动删除，不能为掩盖状态自动删除。

## v2.0 首次使用顺序

1. 欢迎页：选快速启动（按推荐设置）或完整启动（自己逐项设置）。
2. 选择整理范围：收藏、点赞或“我全都要”。
3. 运行本机能力只读检查：

   ```bash
   python3 scripts/check_environment.py --capability-preflight
   ```

   这一步只检查已配置路径、OCR、声音、视觉模型和宿主声明能力；不访问浏览器、不联网、不安装、不加载大模型，也不扫描整块硬盘。
4. 在读取前询问“图文 OCR（推荐开启，以提高识别率）”。已有 OCR 直接复用；用户选择开启但本机缺失时，才显示组件、实际大小与安装确认。
5. 取得当前回合对具体浏览器的授权，并确认：浏览器已打开、已登录小红书、已进入所选列表、且已启用必要的 JavaScript 能力。
6. 用户一次设置读取参数：每组条数默认 `200`、最大 `200`；每组暂停默认 `3` 分钟，可自定义。只有用户确认且当次任务执行器已验证支持受控续组，才允许本次读取按设置继续下一组。
7. 全部列表读取完成后，根据真实图文/视频比例决定视频声音、视频画面和模型；不提前安装视频模型。

默认浏览器、上一次登录态或过去的授权不能代替第 5 步。浏览器未获当前回合授权时，不得启动、连接、控制或改用任何外部浏览器。

## 图文 OCR（推荐，不强制）

OCR 会识别图文笔记封面和全部内页图片里的中英文可见文字。用户可以拒绝；拒绝后仍可按标题、正文和标签分类，但识别率可能下降。OCR 的详细说明和全图片契约见 [图文 OCR 分类开关](image-ocr-classification.md)。

检查与复验：

```bash
python3 scripts/check_environment.py --ocr
```

应看到 `ocr_checked=true`、`ocr_ready=true` 和实际 `ocr_provider`。Tesseract 路径还必须同时满足 `tesseract_chi_sim=true`、`tesseract_eng=true` 与 `tesseract_bilingual=true`；不能回退到单语。

| 平台 | 推荐组件 | 体积与位置提示 |
| --- | --- | --- |
| macOS | Swift + 系统 Vision | Vision 通常没有额外模型下载；缺少 Command Line Tools 时，由 macOS 安装窗口显示实际 GB 级大小。不要向 macOS 用户显示 Windows 安装路径。 |
| Windows | Tesseract + `chi_sim` + `eng` | 常见为几十到数百 MB，安装前以安装器实际显示为准；Windows 默认安装目录通常是 `C:\\Program Files\\Tesseract-OCR`，以用户实际选择为准。 |
| 可选 | EasyOCR | GB 级替代方案，只在用户明确选择时安装。 |

安装完成后必须重新检查。只有 `ocr_ready=true` 才可运行：

```bash
python3 scripts/enrich_note_images.py visible_items.json image_items.json \
  --allow-detail-requests --max-items <1–200>
python3 scripts/ocr_note_images.py image_items.json ocr_results.json
```

列表页图片只是 observed 线索；只有详情 `noteData.imageList` 提供完整有序图片集合时才可 OCR。任一图片失败时不能用部分 OCR 文本分类。

## 视频内容分类（按需）

视频能力只在列表读取完成、真实视频数量已知后询问。快速启动根据分布给出推荐；完整启动提供“耳朵和眼睛都要 / 先检查我是否已有类似功能的 AI 或模型 / 不开启”。

| 项目 | 用途 | 常见本地配置 |
| --- | --- | --- |
| 声音（耳朵） | 平台字幕或视频语音转成带时间戳文字 | MiMo-V2.5-ASR-MLX，约 6.6 GB |
| 画面（眼睛） | 完整时轴真实帧中的物体、场景、动作与画面文字 | MiMo-VL-7B-RL-2508 + MLX-VLM，约 16.6 GB；建议约 32 GB 统一内存的 Apple Silicon |
| 分类 provider | 用文字稿和/或真实帧生成短分类 memo | `mimo-vl-mlx`、`codex-cli` 或 `command`/其他 Agent 适配器 |

`codex-cli` 不是强制环境。Cloud Code、QQ Bot、Hermes 或其他 Agent/API 可以通过 `command` 接入；只有它实际接收图片帧时才可用作视觉 provider。MiMo-VL 只看画面，不听声音；声音来自字幕或 ASR。

用户确认要启用视频功能、并在当前回合授权浏览器后，才运行针对性的环境检查。例如：

```bash
python3 scripts/check_environment.py \
  --video-content --browser arc --check-login-state \
  --analysis-provider mimo-vl-mlx --visual-analysis
```

检查只显示缺失项。已有能力直接复用；缺失依赖需要逐项说明用途、安装位置、实际下载量和磁盘占用，并在用户同意后安装。不能因视频依赖缺失而把视频静默改按简介分类。

视频功能与质量边界见 [根据视频实际内容分类](video-content-classification.md)。

## 浏览器和分段限制

- 浏览器必须由用户在当前回合明确指定。`--browser auto` 不执行真实浏览器操作。
- 读取前确认浏览器已打开、登录正常、位于正确列表页，并已允许本 Skill 需要的 JavaScript。
- 每组最多 `200` 条；暂停分钟数由用户设置，默认 `3`。这不是平台公开安全阈值，也不应被说成规避验证或模仿真人。
- 用户已一次确认自动继续、且当次任务执行器已验证支持受控续组时，任务才能在当次页面接入持续可用且没有安全状态时请求下一组；每组先落盘。没有该确认或未验证续组能力时，使用单组被动读取。
- 安全验证、登录失效、页面绑定丢失或状态不确定时，保存结果后立即停止。用户处理并回复“继续整理”后，只检查当前状态，再恢复未完成队列；不自动重试旧请求或重读已完成分段。

## 真实移动与每周任务

真实移动前必须依次完成：分类方案预览、用户选择是否展开条目审阅、目标专辑确认、dry-run 和一次单独的执行确认。读取或本地分类不等于移动授权。

每周任务可保存范围、分段参数、OCR/视频选择、分类规则和审阅方式。到时间后只整理新内容、先生成分类方案；不自动创建专辑或移动笔记。每次周任务仍需要用户当次确认浏览器已登录、已打开且可用。

## 已知限制

- 小红书网页 DOM、前端模块和可见总数可能变化；页面实际状态优先于假设。
- `200` 不能保证不会遇到安全验证；遇到后必须停下而不是尝试规避。
- OCR 只读取文字，不理解无文字画面；视频画面识别只在用户开启眼睛、完整时轴证据齐全时才可声称已查看画面。
- 视频转写、provider 或画面证据失败时，保留空目标专辑与人工复核，不猜分类。
- Windows 浏览器 adapter 与 OCR 支持说明见 [Windows Playwright/CDP + OCR 支持说明](windows-playwright-ocr-notes.md)；Safari 说明见 [Safari 小红书网页端自动化补充](safari-web-automation-notes.md)。
