# xiaohongshu-web-collection-organizing

小红书网页端收藏 / 点赞整理 skill。它在用户自己的电脑上运行，读取已登录浏览器里的收藏页或点赞页，按用户选择抓取收藏、点赞或二者合并后的条目，可逐张识别图文笔记封面和全部内页图片中的可见文字，生成专辑分类建议，并在用户显式授权后把目标范围内笔记移动到目标专辑。没有文字的纯画面不属于 OCR。用户还可以开启“根据视频实际内容分类”：平台字幕或 MiMo ASR 提供声音文字，用户明确选择的 analysis provider 根据文字稿和完整时轴画面选择专辑。

适合：收藏夹或点赞列表很乱、想按主题整理到专辑的人。

不适合：未登录小红书网页端、想绕过浏览器授权、想无确认批量改账号数据的人。

## 当前能力

- 可被 Hermes Agent 安装和识别。
- 可作为 WorkBuddy Plugin 安装；Windows 固定使用插件独立 profile 的 Edge，macOS/Linux 使用插件独立 Chromium，不依赖 Safari Automation，也不接管用户日常浏览器目录。
- 核心非视频流程支持 macOS 默认 Python 3.9+，不要求额外 Python 包。
- 支持 macOS Arc/Chrome/Safari + AppleScript/JXA 抓取收藏页、点赞页或当前小红书列表页。
- 支持 macOS Swift + Vision OCR。
- 支持 Windows Chrome/Edge + Playwright/CDP 抓取。
- 支持 Tesseract / EasyOCR OCR。
- 支持分类计划、dry-run 报告、retry queue、报告汇总。
- 支持已有专辑排除清单，默认不移动用户决定保留的已有专辑内容。
- 支持 `--source collection|liked|custom` 标记来源；支持 `--append-existing` 合并收藏和点赞，按 note id 去重，并保留 `source_lists`。
- 默认低风险采集：一次只读取当前已显示卡片、每段最多 200 条；不自动滚动、刷新、点击、导航或进入下一段。
- 支持真实批量移动收藏：默认不执行，必须显式传 `--execute --max-moves-per-session <1–200>`。
- 执行清单生成前先做全部专辑成员关系核对：已在目标专辑的条目零写入排除；未归档条目直接加入；跨专辑条目必须携带真实 `source_board_id`。
- 真实移动后会用 `U_` + `Ks` 查询目标专辑，确认 note id 已出现后才记为 `success`；`d0` 的空返回或静默 no-op 都不算成功。
- 视频内容分类是可选开关：Video Transcript Extractor 优先获取平台字幕，无字幕时用 MiMo-V2.5-ASR-MLX 本地转写。
- 视觉模块开启后，用户明确选择的每段视频都用 ffmpeg + macOS Vision + 所选 provider 分析完整时轴真实帧；所有本地分段完成后才可宣称覆盖全部视频。未开启视觉模块时只能标为 `transcript_only`。
- analysis provider 三选一：`codex-cli`、本地 `mimo-vl-mlx`、`command`/宿主 Agent 适配器。这个 Skill 不强制使用 Codex CLI。
- 视频链路只输出文字稿、分类所需的极简内容 memo 和必要证据 JSON；不需要 Qwen 或 LM Studio。
- 开启后若视频转写或所选 analysis provider 失败，该条进入人工复核，绝不回退简介分类。
- 同一次整理使用同一份 `xhs_safety_state.json`：下游脚本会自动继承输入文件旁已有的状态；若把工件放在不同目录，必须对每个小红书访问命令传同一个 `--safety-state <路径>`。检测到安全验证、异常访问、登录页、页面绑定丢失或写入状态不确定后会永久停机；`--resume` 不能绕过，必须由用户完成平台处理后使用新会话。

必须知道的限制：

- 用户必须先在浏览器里登录小红书网页端。
- 目标专辑必须已经存在；当前脚本只核对缺失专辑，不自动创建专辑。
- `run_reassign_batch.py` 不会自动推断来源专辑；跨专辑条目缺少真实 `source_board_id` 时不得进入 execute 清单。
- 小红书网页结构和前端模块可能变化；如果页面变更，需要重新验证。
- 分类体系默认是空的，只能从本次真实内容和用户已有专辑生成；图文可使用元数据与 OCR，视频开关开启时使用合格文字稿、完整时轴真实帧（若启用视觉模块）和所选 provider。低置信度条目默认不会真实移动。
- 200 是程序的防误操作分段上限，不是平台公开的安全阈值，不能保证不会出现验证。

## 目录结构

```text
.
├── .codebuddy-plugin/
│   ├── plugin.json
│   └── marketplace.json
├── .mcp.json
├── SKILL.md
├── README.md
├── LICENSE
├── requirements.txt
├── requirements-windows.txt
├── requirements-workbuddy.txt
├── server/
│   └── xhs-workbuddy-mcp.mjs
├── workbuddy-plugin-src/
│   ├── package.json
│   ├── server.mjs
│   └── smoke.mjs
├── scripts/
│   ├── check_environment.py
│   ├── extract_visible_items.py
│   ├── enrich_note_images.py
│   ├── transcribe_video_items.py
│   ├── analyze_video_transcripts.py
│   ├── analyze_video_visuals.py
│   ├── ocr_note_images.py
│   ├── classify_items.py
│   ├── build_existing_boards_inventory.py
│   ├── capture_board_snapshot.py
│   ├── build_created_boards.py
│   ├── run_reassign_batch.py
│   ├── workbuddy_runtime.py
│   ├── workbuddy_bridge.py
│   ├── build_retry_queue.py
│   └── summarize_run_report.py
├── templates/
├── examples/
├── references/
└── tests/
```

## 安装

### WorkBuddy Plugin（WorkBuddy 用户使用这一条）

若从 SkillHub 安装 Skill，直接提出整理请求即可；检测到 Plugin 缺失或版本不是 `2.0.7` 时，Skill 只会让用户回复一次“启用”，随后通过 WorkBuddy 官方 CLI 安装或更新固定的 GitHub Plugin，并要求重开一次 WorkBuddy。用户无需寻找插件页、配置 MCP 或粘贴下面的命令。下面的命令只保留给开发者手动安装和排障。

在 WorkBuddy 对话中执行：

```text
/plugin marketplace add themrv1ck/xiaohongshu-web-collection-organizing
/plugin install xiaohongshu-organizer@xiaohongshu-skill-marketplace
/reload-plugins
```

加载成功后应出现六个 `xhs_workbuddy_*` 工具。先运行离线 status；只有用户同意后才安装 Playwright 依赖。Windows 复用系统 Edge 程序但使用插件独立 profile，不下载 Chromium；macOS/Linux 安装插件独立 Chromium。用户只需在这个专用窗口登录一次小红书。

正常使用时，用户不需要寻找 URL、复制地址、手动滚动或关闭浏览器。插件在同一个专用浏览器会话中自动完成列表和轻度 OCR 详情读取，固定每 200 条独立保存一组，非末组真实等待 3 分钟；只有声明总数、实际条数与全部位置严格一致才算完整，绝不会把首屏约 10 条当作全部。Cookie、原始 query、签名图片 URL 和 xsec 不写入 JSON。插件先只读取得真实已有专辑；没有合适专辑时，模型只能依据本次真实内容提议新名称，不附带任何固定类别。待创建专辑及公开/私密设置、逐条移动和上限会合并为一次确认；执行时在同一个受管 BrowserContext 中先创建并核验空专辑，再移动收藏。

`capture → prepare → prepare → execute` 之间的证据凭证由插件自动传递，用户不需要查看、复制或保存。凭证绑定账号、来源、页面 `tab`、整理档位、专辑创建方案、隐私、逐条移动、上限和实际文件哈希；最终 `COMMIT` 前会全部重算。直接运行抓取或 `--execute` 脚本会在 WorkBuddy 宿主中被拒绝，不能靠改 JSON 或重置安全状态绕过插件。

已安装旧版的用户可在 WorkBuddy 中执行 `/plugin update xiaohongshu-organizer`，然后重启 WorkBuddy；当前插件版本为 `2.0.7`。

如果 WorkBuddy 对话里暂时不能执行 `/plugin`，在本机 Terminal.app 运行：

```bash
export CODEBUDDY_CONFIG_DIR="$HOME/.workbuddy"
WB_CODEBUDDY="/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy"
"$WB_CODEBUDDY" plugin marketplace add themrv1ck/xiaohongshu-web-collection-organizing
"$WB_CODEBUDDY" plugin install xiaohongshu-organizer@xiaohongshu-skill-marketplace --scope user
```

然后完全退出并重新打开 WorkBuddy，或执行 `/reload-plugins`。详细工具顺序见 [`references/workbuddy-plugin.md`](references/workbuddy-plugin.md)。

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

### SkillHub / RedSkill 上传包

不要手工复制文件或把任意命名的外层目录直接压缩。ZIP 的唯一顶层目录必须与 `SKILL.md` 的 `name` 完全一致，即 `xiaohongshu-web-collection-organizing/`；WorkBuddy 运行包必须包含 `.codebuddy-plugin/`、`.mcp.json` 和已构建的 `server/`。

在仓库根目录运行：

```bash
python3 scripts/build_redskill_package.py --channel redskill --output-dir /tmp/xhs-redskill-release
python3 scripts/build_redskill_package.py --channel skillhub --output-dir /tmp/xhs-skillhub-release
```

两个渠道使用同一套运行文件。脚本排除不参与运行的 `tests/` 和 `workbuddy-plugin-src/` 构建源码，保留已构建 MCP 服务器与全部运行脚本，使上传包不超过 SkillHub 默认 100 文件上限。每次生成一个展开文件夹和一个对应渠道的 ZIP：

- `xiaohongshu-web-collection-organizing/`：平台允许选择文件夹时使用；
- `xiaohongshu-web-collection-organizing-<redskill|skillhub>-<版本>.zip`：平台允许 ZIP 时使用。

不要只上传 `SKILL.md`。单文件不包含 WorkBuddy Plugin、MCP 服务和安全校验代码，不能作为可运行的 WorkBuddy 2.0.7 发布包。

脚本会在输出前校验顶层目录、frontmatter、文件数、WorkBuddy 运行文件、四处版本一致性、单文件 10 MB 和总大小 30 MB 限制；任一条件不满足都会失败，不会生成可误传的“通过”结果。

## 前置条件

通用：

- Python 3.9+
- 已登录小红书网页端
- 非 WorkBuddy 直接使用时需要 Arc / Chrome / Edge / Safari 至少一种浏览器；使用哪个浏览器必须由用户在当前操作中明确指定

WorkBuddy：

- 必须安装 `xiaohongshu-organizer` Plugin，不能只复制 `SKILL.md`
- Windows 固定为插件独立 profile 的 Playwright Edge；macOS/Linux 为插件独立 Chromium
- 不需要给 WorkBuddy 开 Safari/Arc/Chrome 自动化权限
- 禁止使用用户日常 Edge/Chrome profile、CDP、headless 或其他登录目录

macOS：

- 默认可直接使用系统 `/usr/bin/python3`
- 支持 Arc、Chrome 或 Safari
- Chrome 需要开启“允许 Apple 事件中的 JavaScript”
- `osascript` 可用
- 开启图文 OCR 时，优先复用 `swift` + macOS Vision

Windows：

- Chrome 或 Microsoft Edge
- Playwright Python：

```powershell
python -m pip install -r requirements-windows.txt
python -m playwright install chromium
```

- 开启图文 OCR 时默认使用 Tesseract + 简体中文 `chi_sim`；EasyOCR 只作为用户明确选择的 GB 级替代方案：

```powershell
python -m pip install easyocr
```

## 首次运行：范围、只读预检与快速启动

首次运行不会立即打开浏览器。Skill 先显示欢迎和范围卡片：

> **欢迎使用小红书收藏整理 Skill**
>
> 这个 Skill 可以读取你选择的收藏/点赞内容，根据笔记实际内容生成专辑分类建议；图文可选择 OCR 逐张读取封面和全部内页图片中的可见文字，视频可选择结合语音和完整时轴画面。它会先提供分类结果和模拟执行报告，只有再次得到你的明确授权后才会移动笔记。
>
> **首次使用，请在下方回复本次整理范围：**
>
> 1. 回复“收藏”：只整理收藏列表；
> 2. 回复“点赞”：只整理点赞列表；
> 3. 回复“我全都要”：合并收藏和点赞，并按笔记去重。

用户回答范围后，Skill 自动运行一次本地只读预检：

```bash
python3 scripts/check_environment.py --capability-preflight
```

当前宿主 Agent 已明确知道自己具备视觉能力时，可加 `--host-visual-capability ready --host-visual-name "<名称>"`；无法证明时保持默认 `unknown`。预检只检查已配置路径和本地组件，不访问浏览器、不联网、不安装软件、不加载大型模型。它会分别报告中文 OCR、本地语音模型、本地视觉模型和宿主视觉能力；检查结果不等于启用授权。

预检结果会明确标注安装方式：

> **已完成本地能力检查**
>
> - 图文 OCR：`<已找到：名称，可直接复用 / 未找到，推荐安装>`
> - 声音识别：`<已找到：名称，可直接复用 / 未找到，按需安装>`
> - 画面识别：`<已找到：名称，可直接复用 / 未找到，按需安装 / 当前 Agent 无法确认，按需检查>`
>
> “推荐安装”表示它是图文收藏整理的轻量基础能力；“按需安装”表示只有后续选择视频内容识别时才需要。

显示预检结果后，先显示快速启动卡片：

> **选择整理深度**
>
> 1. **快速整理｜只按标题、正文、标签和作者；不做 OCR 或视频识别**
> 2. **轻度整理｜读取图文全部图片文字；不分析视频**
> 3. **深度整理｜图文 OCR + 视频语音 + 完整时轴画面**
>
> 回复“快速整理”“轻度整理”或“深度整理”。如果希望逐项决定，可以回复“自定义”。

使用按钮或选择题展示这三个档位时，按钮必须直接显示上面的完整差异文案，不得只写档位名，不得添加“推荐”，也不得默认选中。

三个档位会直接解析为既有功能开关，不会额外新增一套执行逻辑：

| 档位 | 图文 OCR | 视频语音 | 视频画面 |
| --- | --- | --- | --- |
| 快速整理 | 关闭 | 关闭 | 关闭 |
| 轻度整理 | 开启 | 关闭 | 关闭 |
| 深度整理 | 开启 | 开启 | 开启 |

快速整理跳过 OCR 和视频功能卡，不安装、不运行任何内容识别组件；轻度整理按 OCR 的既有安装与复验规则执行；深度整理按 OCR、语音和画面功能的既有规则执行。选择档位不授权打开浏览器或移动笔记；若需要安装，会先显示实际缺失组件和体积，系统权限窗口仍由用户确认。深度整理发现多个可用画面识别能力时，仍须由用户选择 analysis provider，不能默认绑定 Codex CLI。

只有用户回复“自定义”时，再显示图文 OCR 卡片：

> **图文 OCR 识别**
>
> 是否开启？
>
> OCR 就是“图片文字识别”：把图片中看得见的文字转成可用于分类的文字资料。它会逐张读取图文笔记的封面和全部内页图片，而不是只看封面。
>
> 封面本身就是一张图片；只要图片里有清晰可见的文字，OCR 就能识别。
>
> 例如：标题只写“建议收藏”，但内页图片写着“上海两天一夜路线、餐厅和交通方式”。OCR 读出这些内容后，就能判断它更适合归入“旅行 / 上海攻略”。它也能补足标题、正文或标签没有写出的型号、地点、清单、步骤、菜名和商品名。
>
> **为什么推荐开启？** 在常见的小红书收藏整理场景中，按图文笔记占比较高的情况估算，OCR 约可覆盖 **60%** 的内容识别需求，而且默认组件通常很轻量。这里的 60% 指可参与分类的场景覆盖估算，不是 OCR 的固定识别准确率；实际比例取决于收藏中图文笔记的占比。
>
> OCR 只提取图片里的可见文字；没有文字的纯画面不属于 OCR，OCR 不能理解其中的人物、物体、场景或动作，也不分析视频画面。
>
> 回复“开启”：使用预检找到的中文 OCR；如果没有，则安装当前系统的推荐组件。安装前显示具体组件、预计下载量和磁盘占用；系统权限窗口仍需用户确认。
>
> - macOS：优先复用系统 Vision，OCR 模型本身通常是 **0 MB 额外下载**；缺少 Swift/Command Line Tools 时需要 GB 级系统组件，实际大小以 macOS 安装窗口为准。
> - Windows：默认安装 Tesseract + `chi_sim`，通常为**几十到数百 MB**，实际大小以当前安装器为准。
> - EasyOCR：只在用户明确选择时使用，通常是 **GB 级**。
>
> 回复“不开启”：不安装、不运行图文 OCR；预检结果不会被用于分类。仍可按标题、正文、标签和作者分类，但准确率可能下降。

回复“开启”只授权使用 OCR，并在缺失时安装推荐 OCR 组件，不授权浏览器、视频分析或移动笔记。已有中文 OCR 就复用；缺失时按当前系统安装，再运行 `python3 scripts/check_environment.py --ocr` 复验。详细规则见 [图文 OCR 分类开关](references/image-ocr-classification.md)。

自定义路径在用户回答 OCR 后，才显示视频卡片：

> **是否开启视频内容识别？**
>
> 开启后，Skill 会根据视频的实际声音和画面判断主要内容，再进行分类，不再只依赖标题和简介。
>
> 例如：
>
> - 标题只写“太香了”，但视频里的讲解实际在教番茄炒蛋。声音分析可以判断它属于“做饭 / 菜谱”。
> - 视频里没人说话，只在演示怎样整理衣柜。只分析声音时无法判断；同时分析画面后，可以归入“收纳整理”。
>
> 这项功能包含两部分：
>
> - **语音识别（耳朵）**：把字幕或视频中的人声整理成文字。
> - **画面识别（眼睛）**：从视频开头到结尾分段查看真实画面，识别物品、场景、动作和画面文字。
>
> 预检已经查过现有能力：已有能力直接复用。只有所选能力缺失时才安装；本地语音模型 `MiMo-V2.5-ASR-MLX` 约 **6.6 GB**，本地视觉模型 `MiMo-VL-7B-RL-2508` 约 **16.6 GB**，两者都缺失时合计约 **23.2 GB**。运行本地视觉模型建议电脑具备 **32 GB 内存**；如果已有可用的视觉 AI，就不需要重复安装。
>
> 请选择一个选项：
>
> 1. **声音和画面都分析**：使用字幕、视频语音和完整时轴真实画面，结果最完整；复用已有能力，缺少哪一项才安装哪一项。
> 2. **只分析声音**：只使用字幕和视频语音，不查看画面；无声视频或声音与画面内容不同的视频可能无法准确分类。缺少本地语音能力时才安装语音模型。
> 3. **不开启**：不安装、不运行视频内容识别，仍按标题、简介和标签分类。
>
> 此处只选择功能，不会打开浏览器，也不会移动任何收藏。

回复“声音和画面都分析”（兼容“耳朵和眼睛都要”或裸回复“开启”）覆盖缺失时安装本地 ASR 与所选本地视觉模型的同意；已有视觉 Agent 或模型时优先复用，发现多个候选时让用户选择。回复“只分析声音”（兼容“只听声音”）只覆盖缺失 ASR 的安装，不安装 MiMo-VL。这些选择都不等于授权移动收藏。Arc 路径在本机终端运行：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 scripts/check_environment.py \
  --video-content --browser arc --check-login-state \
  --analysis-provider mimo-vl-mlx --visual-analysis
```

需要的项目：Video Transcript Extractor、`yt-dlp`、`ffmpeg`、`ffprobe`、用于 Arc 登录态只读检测的 `browser-cookie3`、本地 ASR、所选 analysis provider，以及 Arc 已运行并登录小红书。登录检测同时检查会话 cookie 和现有小红书标签页；页面停在 `/login` 时不会把旧 cookie 误报为已登录。检测结果必须分开读取 `capabilities.asr.ready`、`capabilities.text_analysis.ready` 和 `capabilities.visual_analysis.ready`。

如果 `missing` 非空，必须展示实际缺失项、用途、执行位置和安装命令。“声音和画面都分析”允许按需安装 ASR 与所选本地视觉模型；“只分析声音”只允许按需安装 ASR。其他缺失项仍须逐项询问。MiMo-VL 使用 `./scripts/install_mimo_vl_mlx.sh` 安装，用 `python3 scripts/verify_mimo_vl_install.py --run-inference` 做真实推理验收。用户拒绝必要依赖时停止视频分支，不得改用简介给视频分类。完整检测和安装流程见 [根据视频实际内容分类](references/video-content-classification.md)。

开启后的最短无移动链路：

```bash
# 用户手动停在当前段后，只读取当前已显示卡片；不会自动滚动或进入下一段
python3 scripts/extract_visible_items.py \
  segment-001-visible.json --backend macos-arc --source collection \
  --capture-mode passive --segment-limit 200 \
  --arc-window-id '<window-id>' --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' \
  --arc-expected-url-substring '<expected-path>'

# 本示例按“图文 OCR 已开启”执行；先补齐封面和全部内页图片，再逐张 OCR
python3 scripts/enrich_note_images.py \
  visible_items.json image_items.json \
  --allow-detail-requests --max-items <本次明确范围>

python3 scripts/ocr_note_images.py \
  image_items.json ocr_results.json

python3 scripts/transcribe_video_items.py \
  visible_items.json video_transcripts.json \
  --browser arc \
  --extractor-root "$HOME/video-transcript-extractor" \
  --allow-video-access --max-videos <本次明确范围>

python3 scripts/analyze_video_transcripts.py \
  video_transcripts.json video_analysis.json \
  --taxonomy board_taxonomy.json \
  --analysis-provider mimo-vl-mlx \
  --mimo-vl-root "$HOME/Documents/MiMo-VL-7B-RL-2508" \
  --resume

# 开启视觉模块时，显式选择当前一段视频
python3 scripts/analyze_video_visuals.py \
  visible_items.json video_transcripts.json \
  video_analysis.json video_analysis_visual.json \
  --all-videos --allow-video-access --max-videos <本次明确范围> \
  --analysis-provider mimo-vl-mlx \
  --mimo-vl-root "$HOME/Documents/MiMo-VL-7B-RL-2508" \
  --resume

python3 scripts/classify_items.py \
  image_items.json classification.json \
  --ocr-results ocr_results.json \
  --classify-video-by-content \
  --require-visual-analysis \
  --video-analysis video_analysis_visual.json

python3 scripts/run_reassign_batch.py classification.json classification_preview.json
```

最后一条只生成 `mode=classification_preview`，用于展示 `classification.json` 中的视频主要内容、目标专辑、置信度和失败项；它不是可执行 dry-run。必须再完成真实专辑与成员关系核验，报告出现 `ready_for_execute=true` 后，才能请求移动确认。

长批次中 ASR 与持久 provider worker 各自只加载一次模型；转写和分析都原子写盘。每个小红书访问段完成后都停下，不自动续段；只有本地合并全部已保存段后，正式分类才要求分析文件覆盖所有明确视频。视觉模块关闭时只能生成 `transcript_only`。MiMo-VL 只分析画面，声音仍然来自平台字幕或 ASR。

## 无副作用验证

这些命令不会修改小红书账号：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 -m compileall -q .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_environment.py
printf '{"boards":[{"name":"用户已有专辑A","notes":[{"id":"694d3390000000002203ae33","title":"示例笔记"}]}]}\n' > /tmp/xhs_existing_boards_source.json
python3 scripts/build_existing_boards_inventory.py /tmp/xhs_existing_boards_source.json /tmp/xhs_existing_boards_inventory.json
python3 scripts/classify_items.py --skip-ocr examples/visible_items.example.json /tmp/xhs_classification_skip.json
python3 scripts/run_reassign_batch.py /tmp/xhs_classification_skip.json /tmp/xhs_classification_preview.json
python3 scripts/build_retry_queue.py /tmp/xhs_classification_preview.json /tmp/xhs_retry_queue.json
python3 scripts/summarize_run_report.py /tmp/xhs_classification_preview.json
```

没有 `board_snapshot.json` 和 `created_boards.json` 时，上述命令必须输出 `mode=classification_preview`、`ready_for_execute=false`、`missing_boards=null`，不能输出 `planned`。如果要核对目标专辑：

```bash
printf '{"boards":["用户已有专辑A","用户已有专辑B"]}\n' > /tmp/xhs_existing_boards.json
python3 scripts/build_created_boards.py board_taxonomy.json /tmp/xhs_existing_boards.json /tmp/xhs_created_boards.json
```

## 最短真实使用路径

先确认整理范围，自动完成本地能力只读预检，再选择快速整理、轻度整理或深度整理；只有回复“自定义”才逐项询问图文 OCR 和视频内容分类。档位或两个开关确认后，才让用户明确本轮允许使用哪个已登录浏览器。只整理收藏时打开收藏页；只整理点赞时打开点赞页；二者都要时先抓收藏再抓点赞。

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing

# 范围确认后自动运行；不访问浏览器、不联网、不安装、不加载大模型
python3 scripts/check_environment.py --capability-preflight
# 只有用户选择“轻度整理”“深度整理”或自定义开启图文 OCR 才运行完整 OCR 复验；快速整理不安装、不使用 OCR
python3 scripts/check_environment.py --ocr
# 收藏：用户手动停在当前段后运行；每段单独保存，最多 200 条
python3 scripts/extract_visible_items.py segment-001-visible.json --backend macos-arc --source collection \
  --capture-mode passive --segment-limit 200 \
  --arc-window-id '<window-id>' --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' --arc-expected-url-substring '<expected-path>'
# 用户手动滚动到下一段后，明确开始下一次；所有段在本地合并后才得到 visible_items.json。
# 列表页图片只算 observed，image_urls_complete=false；图文 OCR 详情访问必须显式限定范围：
python3 scripts/enrich_note_images.py visible_items.json image_items.json \
  --allow-detail-requests --max-items <本次明确范围>
python3 scripts/ocr_note_images.py image_items.json ocr_results.json
python3 scripts/classify_items.py image_items.json classification.json --ocr-results ocr_results.json
# 图文 OCR 关闭：不要运行上面两条图片补齐/OCR 命令，改用：
# python3 scripts/classify_items.py --skip-ocr visible_items.json classification.json
# 下面只生成分类预览，不代表专辑已核验
python3 scripts/run_reassign_batch.py classification.json classification_preview.json
python3 scripts/summarize_run_report.py classification_preview.json
```

上面最后一步只是分类预览，不改小红书账号，也不具备执行资格。

确认 `classification.json` 的分类建议后，必须先通过用户本轮授权的浏览器读取全部专辑的完整 `yC + U_ + Ks` 成员关系，再生成目标专辑核验结果和可执行 dry-run：

Arc 执行还必须提供稳定的窗口、标签页、`window.name` 标记和预期 URL：

```bash
python3 scripts/capture_board_snapshot.py board_snapshot.json \
  --browser arc --user-id '<user-id>' \
  --expected-url-substring '<expected-path>' \
  --arc-window-id '<window-id>' \
  --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>'
python3 scripts/build_created_boards.py classification.json board_snapshot.json created_boards.json
python3 scripts/run_reassign_batch.py classification.json run_report.json \
  --board-snapshot board_snapshot.json \
  --created-boards created_boards.json
python3 scripts/summarize_run_report.py run_report.json
# 只有上一步显示 mode=dry_run、ready_for_execute=true、blockers=[]，并取得用户再次确认，才运行：
python3 scripts/run_reassign_batch.py classification.json run_report.json \
  --board-snapshot board_snapshot.json \
  --created-boards created_boards.json \
  --execute --browser arc --user-id '<user-id>' \
  --max-moves-per-session <本次明确范围> \
  --arc-window-id '<window-id>' \
  --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' \
  --arc-expected-url-substring '<expected-path>'
python3 scripts/build_retry_queue.py run_report.json retry_queue.json
python3 scripts/summarize_run_report.py run_report.json
```

真实执行不接受浏览器 `auto`；必须把 `arc`、`chrome`、`safari` 或 `playwright` 写清楚，并且该浏览器已由用户在当前回合明确授权。缺少 `board_snapshot.json` 或 `created_boards.json` 时，脚本会在接触浏览器前拒绝执行。Arc 执行器通过隐藏 DOM bridge 把任务注入页面 main world，再从 Rspack `req.m` 按精确 endpoint 唯一解析 `LN/B1/d0/Ks/yC/U_`；匹配不唯一就停止，不猜导出名。

未归档条目使用 `d0 -> U_/Ks`。只有输入显式包含且不同于目标专辑的真实 `source_board_id` 才走跨专辑事务：先预检来源存在、目标不存在，再执行紧邻的 `LN -> B1 -> d0(target) -> U_/Ks`；不会为首次写入失败追加一次写入。非安全错误执行 `LN -> B1 -> d0(source) -> U_/Ks` 严格回滚；安全验证、登录页、页面绑定失效或状态不确定会先写报告和 `xhs_safety_state.json`，立即停写且不追加回滚。每次只提交一条，达到本次上限后等待人工检查，不自动进入下一段。

如果你使用 Safari：

```bash
python3 scripts/capture_board_snapshot.py board_snapshot.json --browser safari --user-id '<user-id>' --expected-url-substring '<expected-path>'
python3 scripts/build_created_boards.py classification.json board_snapshot.json created_boards.json
python3 scripts/run_reassign_batch.py classification.json run_report.json --board-snapshot board_snapshot.json --created-boards created_boards.json
python3 scripts/run_reassign_batch.py classification.json run_report.json --board-snapshot board_snapshot.json --created-boards created_boards.json --execute --browser safari --user-id '<user-id>' --expected-url-substring '<expected-path>' --max-moves-per-session <本次明确范围>
```

Windows / Edge 示例：

```powershell
python scripts\extract_visible_items.py segment-001-visible.json --backend playwright --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --source collection --capture-mode passive --segment-limit 200
python scripts\classify_items.py --skip-ocr visible_items.json classification.json
python scripts\capture_board_snapshot.py board_snapshot.json --browser playwright --user-id "<user-id>" --expected-url-substring "/user/profile/" --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore
python scripts\build_created_boards.py classification.json board_snapshot.json created_boards.json
python scripts\run_reassign_batch.py classification.json run_report.json --board-snapshot board_snapshot.json --created-boards created_boards.json
python scripts\run_reassign_batch.py classification.json run_report.json --board-snapshot board_snapshot.json --created-boards created_boards.json --execute --browser playwright --user-id "<user-id>" --expected-url-substring "/user/profile/" --max-moves-per-session <本次明确范围> --channel msedge --user-data-dir "$env:USERPROFILE\.xhs-skill-browser-profile" --url https://www.xiaohongshu.com/explore
```

## 输出文件

- `visible_items.json`：抓取到的收藏 / 点赞条目；每条保留 `source_lists` / `source_primary` 来源标记
- `crawl_manifest.json`：抓取覆盖范围、停止原因和页面快照统计
- `image_items.json`：从详情 `noteData.imageList` 按原顺序补齐的封面和全部内页图片列表、详情权威类型及图片集合完整性状态；列表页图片只能标为 observed
- `ocr_results.json`：每条明确图文笔记的逐图 OCR 结果、聚合文字、图片集合哈希、覆盖计数和 `ocr_run_fingerprint`；缓存复用要求图片集合哈希和运行指纹同时一致
- `video_transcripts.json`：开关开启时的视频时间戳文字稿
- `video_analysis.json`：所选 analysis provider 根据合格文字稿和/或完整时轴真实帧生成的主要内容、短摘要、目标专辑、置信度和理由；视觉项额外带可验证证据清单，纯文字项必须标明 `transcript_only`
- `existing_boards_inventory.json`：用户决定保留的已有专辑排除清单
- `classification.json`：分类建议；图文 OCR 成功时包含逐图证据和同一 `ocr_run_fingerprint`，非图文、跳过或未成功 OCR 的行指纹为空
- `board_snapshot.json`：通过当前授权浏览器前端只读取得的全部专辑、完整分页成员关系及完整性检查
- `created_boards.json`：目标专辑确认/缺失结果
- `run_report.json`：分类预览、硬闸门 dry-run 或真实移动报告；`ready_for_execute` 和 `blockers` 是唯一执行资格依据
- `retry_queue.json`：失败项重试队列
- `xhs_safety_state.json`：共享会话状态；`security_halted` 不能由 `--resume` 清除，也不保存 cookie、token 或签名 URL

这些文件包含个人收藏信息，默认被 `.gitignore` 忽略，不应提交到公开仓库。`visible_items.json`、`image_items.json` 和 `ocr_cache/` 内文件都是仅供本机当前用户使用的私密中间工件，文件权限必须为 `0600`；正式报告不得复制原始 CDN query。任务结束后可以手动删除这些中间文件和缓存，但流程不得自动删除，以免破坏核验与续跑。

## 脚本说明

- `scripts/check_environment.py`：默认只检查基础运行环境；只有传 `--ocr` 才检测图文 OCR，只有传 `--video-content` 才检测视频依赖。
- `scripts/extract_visible_items.py`：默认被动读取当前浏览器页面已显示的收藏 / 点赞 / 专辑条目，单段最多 200 条；不会自动滚动、刷新或进入下一段。列表页 `content_type` 和图片都只是 observed 线索，图片集合必须保持 `image_urls_complete=false`。
- `scripts/enrich_note_images.py`：仅供非 WorkBuddy 直接运行路径使用；默认不访问详情，只有传 `--allow-detail-requests --max-items <1–200>` 才从笔记详情 `noteData` 读取权威类型并补齐图片。WorkBuddy 环境会拒绝该无浏览器登录态入口，轻度整理必须改由 `xhs_workbuddy_capture(organizing_depth=light)` 在同一登录态前端会话内完成。
- `scripts/transcribe_video_items.py`：只处理明确、用户选定范围的视频；需要 `--allow-video-access` 与明确的 `--video-id` 或 `--max-videos <1–200>`。
- `scripts/analyze_video_transcripts.py`：通过明确选择的 `codex-cli`、`mimo-vl-mlx` 或 `command` provider，只根据合格文字稿生成视频内容分类 memo。
- `scripts/analyze_video_visuals.py`：对显式列入的当前视频段按全时轴抽真实帧、逐帧 Vision OCR，再交给所选视觉 provider；`--all-videos` 也必须同时给 `--allow-video-access --max-videos <1–200>`。
- `scripts/install_mimo_vl_mlx.sh`：安装锁定版本的 MLX-VLM 与 MiMo-VL 官方 BF16 权重。
- `scripts/verify_mimo_vl_install.py`：校验模型分片并用 `--run-inference` 做一次真实推理验收。
- `scripts/ocr_note_images.py`：对完整图片集合逐张下载并执行 OCR；任一图片失败时不使用部分 OCR 文本分类。缓存复用还要求 `ocr_run_fingerprint` 一致；该指纹绑定实际 provider、Tesseract 语言和 Swift OCR 脚本版本。
- `scripts/classify_items.py`：生成分类建议。
- `scripts/build_existing_boards_inventory.py`：从已有专辑 JSON 生成排除清单。
- `scripts/capture_board_snapshot.py`：在用户本轮授权的浏览器中，通过前端 `yC + U_ + Ks` 只读生成全部专辑成员快照；分页或数量不完整会明确标记。
- `scripts/build_created_boards.py`：用 `classification.json` 和真实 `board_snapshot.json` 核对本次目标专辑是否存在。
- `scripts/run_reassign_batch.py`：没有两份专辑证据时只输出不可执行的分类预览；同时传入 `--board-snapshot` 和 `--created-boards` 且硬闸门通过后才输出 dry-run。真实执行还必须传 `--execute --max-moves-per-session <1–200>`，达到上限只落盘。
- `scripts/verify_board_membership.py`：只读抓取全部专辑成员，并核验一批已执行移动的来源、目标和全局唯一性。
- `scripts/verify_classification_membership.py`：移动完成后只读复抓全部专辑，核验完整分类中所有已放行视频都只出现一次且位于目标专辑；空目标、低置信度和待复核视频单独列出。
- `scripts/build_retry_queue.py`：从运行报告生成重试队列。
- `scripts/summarize_run_report.py`：汇总运行报告。

## 安全边界

- Cookie、卡片 query、签名图片地址与 xsec 只留在浏览器/进程内；轻度整理只把已下载的图片字节、相对本地路径和内容 SHA256 写入权限为 0600 的运行目录，不把源 URL 写入 JSON、模型、分类、正式报告或错误输出。
- 不自动创建、删除、重命名专辑。
- 不把 `.collect-wrapper` 图标变化当成“已加入目标专辑”。
- 不把 UI 总数当成完整抓取数。
- 不移动低置信度分类，除非显式传 `--allow-low-confidence`。
- 视频内容分类失败时不使用标题、简介、作者、封面 OCR 或猜测兜底。
- 视频链路只产出文字稿、分类 memo 和必要证据 JSON，不要求 Qwen 或 LM Studio。
- 不将仅文字稿的结果冒充为已检查画面；视觉模块开启后，不跳过任何明确视频的完整时轴画面证据。
- 不传 `--execute` 时不会改账号。
- 不把对已在其他专辑条目直接 `d0` 的静默 no-op 算成功；没有 `U_` + `Ks` 的 note id 核验就不记成功。

## 给 Hermes 使用

安装后可以直接对 Hermes 说：

```text
用 xiaohongshu-web-collection-organizing 帮我整理小红书收藏夹。
# 或
用 xiaohongshu-web-collection-organizing 帮我整理小红书点赞。
# 或
用 xiaohongshu-web-collection-organizing 帮我把收藏和点赞一起整理。
```

如果当前浏览器未登录，先登录小红书网页端，再让 Hermes 继续。
