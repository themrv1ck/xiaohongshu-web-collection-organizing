# 根据视频实际内容分类

这是一个可选功能。它只解决一件事：视频笔记不再根据标题、简介或封面猜主题，而是根据实际语音和必要时的完整时轴真实画面选择目标专辑。

## 首次运行顺序

顺序固定为“欢迎与范围 → 本地能力只读预检 → 快速启动档位 →（仅自定义时）图文 OCR / 视频内容分类”。预检不需要用户额外选择，但不得访问浏览器、联网、安装软件或加载大型模型；快速启动档位直接决定 OCR、语音和画面功能是否使用。

Skill 先显示：

> **欢迎使用小红书收藏整理 Skill**
>
> 这个 Skill 可以帮助你读取选定范围内的小红书收藏/点赞内容，根据笔记实际内容生成专辑分类建议；图文可选择 OCR 逐张读取封面和全部内页图片中的可见文字，视频可选择结合语音和完整时轴画面。它会先提供分类结果和模拟执行报告，只有再次得到你的明确授权后才会移动笔记。
>
> **首次使用，请在下方回复本次整理范围：**
>
> 1. 回复“收藏”：只整理收藏列表；
> 2. 回复“点赞”：只整理点赞列表；
> 3. 回复“我全都要”：合并收藏和点赞，并按笔记去重。

用户回答范围后，先运行：

```bash
python3 scripts/check_environment.py --capability-preflight
```

宿主 Agent 已能证明自身具备视觉能力时，可传 `--host-visual-capability ready --host-visual-name "<名称>"`；无法证明时保持 `unknown`。先展示 OCR、本地 ASR、本地视觉模型与宿主视觉能力的检查结果，再显示下面的快速启动档位：

> **选择整理深度**
>
> 1. **快速整理｜只按标题、正文、标签和作者；不做 OCR 或视频识别**
> 2. **轻度整理｜读取图文全部图片文字；不分析视频**
> 3. **深度整理｜图文 OCR + 视频语音 + 完整时轴画面**
>
> 回复“快速整理”“轻度整理”或“深度整理”；想逐项选择时回复“自定义”。

- 使用按钮或选择题时，三个选项必须直接显示上面的完整差异文案；不得只显示档位名，不得添加“推荐”，也不得默认选中。
- 快速整理：不传 `--classify-video-by-content`，不运行视频环境检查或视频依赖。
- 轻度整理：只运行完整图文 OCR 链路，不运行视频环境检查或视频依赖。
- 深度整理：运行 OCR、视频转写、内容 memo 和所有明确视频的完整时轴画面分析；若有多个可用视觉能力，必须让用户选择 provider。
- 三档都不授权外部浏览器或移动收藏。选择深度整理不允许默认固定 Codex CLI。

只有用户选择“自定义”时，才按 [图文 OCR 分类开关](image-ocr-classification.md) 显示 OCR 卡片。

自定义路径中，Skill 显示：

> **图文 OCR 识别**
>
> 是否开启？
>
> OCR 会逐张读取图文笔记封面和全部内页图片中的可见文字，补足标题、正文和标签没有写出的型号、地点、清单、步骤等信息。没有文字的纯画面不属于 OCR，OCR 不能理解其中的人物、物体、场景或动作。回复“开启”会使用预检找到的中文 OCR；如果缺失，则按当前系统安装。macOS Vision 模型通常 0 MB 额外下载，缺少工具链时为 GB 级；Windows Tesseract + `chi_sim` 通常为几十到数百 MB。回复“不开启”则不安装、不运行 OCR，预检结果也不用于分类。

自定义路径得到 OCR 回答后，Skill 再显示：

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

- 选“声音和画面都分析”（兼容“耳朵和眼睛都要”或裸回复“开启”）：复用预检发现的视觉 Agent 或本地模型；多个候选必须让用户选择。所选能力缺失时，视为同意安装本地 ASR 和所选本地视觉模型。该同意不包含其他软件，也不等于授权移动收藏。
- 选“只分析声音”（兼容“只听声音”或“开启，仅文字稿”）：缺失时可安装本地 ASR，但不安装 MiMo-VL；只要转写和文字分析成功就可生成 memo，并必须标记 `analysis_basis=transcript_only` / `visual_status=not_enabled`，不得声称看过画面。
- 选“不开启”：不开启 `--classify-video-by-content`，不做完整视频环境检查，不安装或运行视频依赖；首次只读预检结果不参与分类。
- 用户未明确授权外部浏览器：不得启动、连接或控制浏览器，应继续询问。

## 环境检测门

Arc 路径在本机终端运行：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
python3 scripts/check_environment.py \
  --video-content --browser arc --check-login-state \
  --analysis-provider mimo-vl-mlx --visual-analysis
```

如果 Video Transcript Extractor 不在默认路径，可明确指定：

```bash
python3 scripts/check_environment.py \
  --video-content \
  --browser arc \
  --check-login-state \
  --analysis-provider mimo-vl-mlx \
  --visual-analysis \
  --extractor-root "$HOME/video-transcript-extractor"
```

上面是“开启视觉”的命令。如果用户明确选“开启，仅文字稿”，去掉 `--visual-analysis`；后续跳过 `analyze_video_visuals.py`，`classify_items.py` 也不加 `--require-visual-analysis`。

只有输出中的 `video_content_classification.video_content_ready` 为 `true` 才能继续。检测结果不得输出 cookie 内容；Arc 登录态同时要求存在会话 cookie，并确认现有小红书标签页没有停在 `/login` 或显示登录提示。仅有旧 cookie 不算已登录。

## 需要的环境和服务

| 项目 | 用途 | 检测结果中的缺失标识 |
|---|---|---|
| Python 3.9+ | 运行当前 skill | `python_version_supported=false` |
| Arc 已安装、正在运行且已登录小红书 | 读取用户自己的登录态 | `arc`、`arc-running`、`arc-xiaohongshu-login` |
| Video Transcript Extractor | 获取平台字幕、导出临时 Arc cookie、调用 MiMo | `video-transcript-extractor` |
| `yt-dlp`、`ffmpeg`、`ffprobe` | 获取字幕/音频、转码和读取时长 | 同名缺失项 |
| `browser-cookie3` | 只读确认 Arc 是否有小红书会话 cookie | `arc-xiaohongshu-login` 的错误详情 |
| MiMo-V2.5-ASR-MLX + Audio Tokenizer，约 6.6 GB | “耳朵”：没有平台字幕时把音轨中的语音转成带时间戳的文字 | `mimo-mlx-runtime-or-model` |
| MiMo-VL-7B-RL-2508 官方 BF16 + MLX-VLM，约 16.6 GB | “眼睛”：本地分析完整时轴真实帧；实测推理峰值约 17.6 GB，Apple Silicon 建议 32 GB 统一内存或以上，24 GB 可能紧张 | `mimo-vl-mlx` |
| analysis provider | 根据文字稿和/或真实帧生成分类 memo；三选一：`codex-cli`、`mimo-vl-mlx`、`command` | 见 `analysis_provider` 和 `capabilities.text_analysis.ready` / `capabilities.visual_analysis.ready/status` |
| macOS Swift + Vision | 对完整时轴抽帧逐帧 OCR；视觉 provider 仍直接看真实帧 | `ocr_ready=false` / `macos_swift_can_import_vision=false` |

不需要安装或启动：Qwen、LM Studio。

## 缺失时的确认流程

读取 `missing` 后，只展示实际缺失的项目。每项都要告诉用户用途、安装位置和可复制命令。

如果用户选择“深度整理”或“声音和画面都分析”（或兼容回复“耳朵和眼睛都要”/“开启”），缺失 ASR 与所选本地视觉模型的安装同意已经获得。展示各自用途、大小、位置和命令后直接安装，不重复询问。如果目录已存在，先检查并只补缺失文件或依赖，不得重复 clone 或覆盖。

如果用户选择已有视觉 Agent/API，不安装 `mimo-vl-mlx`；只检测所选 `codex-cli` 或 `command` 是否真的具备视觉输入能力。仅文本 Agent 不能将 `capabilities.visual_analysis.ready` 标为 `true`。

对 MiMo 以外的缺失项，原样询问：

> 检测到以下其他项目缺失。是否允许我安装这些项目？未获同意前不会安装，也不会把视频改用简介分类。

MiMo 以外的组件不得在同意前安装。用户同意哪些，只安装哪些；用户拒绝必要依赖或暂不安装时，将视频内容分类标为不可用并停止这条分支。

常用安装命令如下。

### Python 3.9+

在 macOS 本机终端执行；如果 Homebrew 不存在，先停止并另行询问是否安装 Homebrew：

```bash
brew install python@3.12
```

### `yt-dlp`、`ffmpeg`、`ffprobe`

在 macOS 本机终端执行；如果 Homebrew 也不存在，先停止并另行询问是否安装 Homebrew：

```bash
brew install yt-dlp ffmpeg
```

### Video Transcript Extractor

在本机终端执行：

```bash
git clone https://github.com/themrv1ck/video-transcript-extractor.git "$HOME/video-transcript-extractor"
```

安装后检查：

```bash
cd "$HOME/video-transcript-extractor"
python3 scripts/video_transcript_cli.py --check-env
```

Arc 登录态只读检测如果明确报缺少 `browser_cookie3`，在本机终端执行：

```bash
python3 -m pip install browser-cookie3
```

### MiMo ASR：耳朵

MiMo-V2.5-ASR-MLX 在这条链路中承担四项工作：

1. 在视频没有可用平台字幕时，把音频中的语音转成文字。
2. 输出带时间戳的片段，让程序核对开头、结尾和整体时长是否被文字稿覆盖。
3. 为所选 analysis provider 提供实际讲话内容，以便判断视频主要内容和目标专辑。
4. 长批次中只加载一次模型并持续处理后续视频，减少重复启动时间。

ASR 模块本身不识别画面、人物、物体或动作；这些内容由完整时轴真实帧和视觉 provider 处理。

用户选择“声音和画面都分析”或“只分析声音”后，在检测到 MiMo ASR 缺失时于 macOS 本机终端执行。这里复用已经验证的 `MiMo-V2.5-ASR-MLX` 开源实现，不另选 ASR 框架；模型和 tokenizer 合计约 6.6 GB：

```bash
git clone https://github.com/ailuntx/MiMo-V2.5-ASR-MLX.git "$HOME/MiMo-V2.5-ASR-MLX"
cd "$HOME/MiMo-V2.5-ASR-MLX"
python3 -m venv .venv
.venv/bin/pip install -r requirements-mlx.txt
.venv/bin/pip install huggingface-hub
.venv/bin/hf download mlx-community/MiMo-Audio-Tokenizer --local-dir models/MiMo-Audio-Tokenizer
.venv/bin/hf download mlx-community/MiMo-V2.5-ASR-MLX --local-dir models/MiMo-V2.5-ASR-MLX
```

如果该目录已经存在，不得重复 clone 或覆盖；直接进入目录检查并只补缺失依赖。

### MiMo-VL + MLX-VLM：眼睛

`mimo-vl-mlx` 使用 MiMo-VL-7B-RL-2508 官方 BF16 权重与独立 MLX-VLM runtime。官方 BF16 四个 safetensors 分片合计 16,612,526,808 bytes，约 16.6 GB；本机实测推理峰值约 17.6 GB。Apple Silicon 建议 32 GB 统一内存或以上，24 GB 可能紧张。默认位置是 `$HOME/Documents/MiMo-VL-7B-RL-2508`，也可通过 `XHS_MIMO_VL_ROOT` 覆盖。

用户选择“声音和画面都分析”（或兼容回复“耳朵和眼睛都要”/“开启”）且所选本地视觉模型缺失时，在 macOS 本机终端执行 Skill 自带的可复用安装器：

```bash
cd ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
./scripts/install_mimo_vl_mlx.sh
python3 scripts/verify_mimo_vl_install.py --run-inference
```

安装器使用 `requirements-mimo-vl-mlx.txt` 中的锁定版本，并固定官方模型 revision `4bfb270765825d2fa059011deb4c96fdd579be6f`。验收器必须同时确认 `config.json`、`model.safetensors.index.json`、四个 safetensors 分片与一次真实推理成功。MiMo-VL 的视频入口只看画面，不读取音轨；声音仍然必须来自平台字幕或 MiMo ASR。

### analysis provider：不绑定宿主 Agent

| provider | 适用情况 | 视觉能力的判定 |
|---|---|---|
| `mimo-vl-mlx` | 本地 MiMo-VL 官方 BF16 + MLX-VLM | 模型、runtime 和 worker 实测能处理 `image_paths` 后才是 `true` |
| `codex-cli` | 用户已有 Codex CLI，愿意由它分析文字和帧 | CLI 已登录且实测支持图像输入后才是 `true` |
| `command` | Cloud Code、QQ Bot、Hermes 或任何宿主 Agent/API 的本地适配器 | 适配器确实把 `image_paths` 传给有视觉能力的模型后才是 `true` |

`command` 适配器不经 shell 执行用户给定的 argv。每次调用向 stdin 写入一行 JSON：

```json
{"protocol_version":1,"prompt":"分析要求","image_paths":["/absolute/frame-0001.jpg"]}
```

stdout 必须且只能输出符合 `video_analysis.schema.json` 的一个 JSON 对象。非 JSON、多余文字、非零退出码、缺字段或超时都是硬失败，不切换到另一个 provider。

`codex-cli` 是可选适配器，不是运行这个 Skill 的必需前提。如果用户选它且本机缺失，要像其他非 MiMo 组件一样单独取得安装同意；不得因为它缺失就自动安装或改用它。

### Arc

Arc 不由本 skill 静默安装或启动。缺少 Arc 时先询问用户是否自行安装；Arc 未运行时请用户手动打开；未登录小红书时请用户在 Arc 中完成登录。完成后重新运行环境检测。

## 开启后的最短处理链路

1. 用同一个已授权 Arc 登录态按被动采集分段运行 `extract_visible_items.py --capture-mode passive --segment-limit 200`。每段只读取当前已显示卡片，不自动滚动；本地合并后才进入视频分支。只有明确为 `video` 的条目进入视频分支；`unknown` 必须留给人工复核。
2. Video Transcript Extractor 只处理用户明确选定的范围：必须传 `--allow-video-access` 加 `--video-id` 或 `--max-videos <1–200>`。它优先获取平台字幕；无可用字幕时才下载音频并用 MiMo 转写。每段落盘后不自动进入下一段。
3. 对文字稿执行确定性的覆盖率校验。覆盖不足就失败，不得补写或猜测。
4. `video_transcripts.json` 保存带时间戳的文字稿；所选 analysis provider 只使用这份文字稿和后续真实帧，不读取标题、简介、作者或热度来猜主题。
5. 如果视觉模块开启，`analyze_video_visuals.py` 必须处理用户明确选定的每段视频；`--all-videos` 也必须同时传 `--allow-video-access --max-videos <1–200>`。每条下载真实视频，至少均匀抽 5 帧、首尾都覆盖、任意相邻帧最大间隔不超过 10 秒。每帧保存时间戳、SHA256 和 Vision OCR，再交给有视觉能力的 provider。
6. 如果视觉模块未开启，不运行抽帧分析；文字分析成功项必须标记 `analysis_basis=transcript_only` 和 `visual_status=not_enabled`，不得把封面 OCR 当成画面分析，也不得声称已检查画面。
7. provider 输出最小内容 memo：`main_topic`、`content_summary`、`target_board`、`confidence`、`reason`。视觉成功项额外带 `evidence_manifest`、`visual_evidence_sha256`、`analysis_basis=full_timeline_visual_with_transcript|full_timeline_visual` 和 `visual_status=analyzed`；`video_sha256` 位于 `evidence_manifest` 内，不是顶层字段。
8. `classify_items.py` 将视频分析合入 `classification.json`。转写与所选分析路径都失败时，`target_board` 必须为空，`review_state=video_content_unavailable`，不得回退简介/封面 OCR。
9. 转写、文字分析和视觉分析都原子续跑；视觉结果只在真实帧、文字稿、provider identity 和专辑体系哈希都匹配时复用。
10. 所有本地保存分段完成后，正式分类才要求 `video_analysis.json` 覆盖所有明确视频；安全停机或未完成分段时，不能把未完成部分伪装成全量。`--allow-partial-video-analysis` 只允许用于显式抽样测试。
11. 先展示分类结果、不可用条目和目标专辑，再做 dry-run；只有用户再次明确确认后才能真实移动。

## 视觉模块的完整时轴逻辑

视觉模块一旦开启，用户明确选择的每一段视频都执行同一套完整时轴检查。语音是否看起来“已经足够”不能用来跳过画面，否则就无法严谨判断语音与画面是否一致。每段完成后保存本地结果，下一段必须由用户明确开始。

1. 用 `ffprobe` 读取真实视频时长；时长不可验证就停止该条。
2. 用 `ffmpeg` 覆盖整个时轴抽帧：至少 5 帧，必须包含首尾，任意相邻帧间隔不得超过 10 秒。长视频会按这一上限增加帧数，而不是只看开头几张图。
3. 每帧记录时间戳和 SHA256，并用 macOS Vision 识别画面文字；Vision OCR 不可用时仍让所选视觉 provider 直接看真实帧，但明确记录 OCR 不可用。
4. provider 同时查看按时间顺序排列的真实帧、帧内文字和可用文字稿，只输出视频主要内容、短摘要、目标专辑、置信度和理由。
5. 保存视频哈希、抽帧哈希、首尾覆盖和最大间隔证据；证据不完整就不得把结果标记为成功。
6. 语音与画面两条链路都失败时，保留 `video_content_unavailable` 和空目标专辑，不使用标题、简介或封面猜测。
7. `mimo-vl-mlx` 只接收真实帧与文字 prompt，不读取视频音轨；不能把它的画面结论写成“已听取原视频”。

Arc 示例命令：

```bash
python3 scripts/extract_visible_items.py \
  segment-001-visible.json --backend macos-arc --source collection \
  --capture-mode passive --segment-limit 200 \
  --arc-window-id '<window-id>' --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' --arc-expected-url-substring '<expected-path>'

# 本示例按“图文 OCR 已开启”执行；先补齐封面和全部内页图片，再逐张 OCR
python3 scripts/enrich_note_images.py \
  visible_items.json image_items.json --allow-detail-requests --max-items <本次明确范围>

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

# 视觉模块开启时，显式选择当前一段视频
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

最后一条命令只是分类预览，不会修改小红书账号，也不代表专辑和成员关系已经核验。真实 dry-run 前必须再生成 `board_snapshot.json`、`created_boards.json`，并确认报告为 `ready_for_execute=true`。

上面并行启用的图文 OCR 仍遵守完整图片契约：列表页图片只能标为 observed 且 `image_urls_complete=false`，详情默认不访问，只有明确传 `--allow-detail-requests --max-items <1–200>` 才请求选定范围。详情 `noteData.type` 才是权威类型，只有详情 `noteData.imageList` 可声明图片集合完整。详情补齐遇到 `security_blocked` 时必须停止、写入 `xhs_safety_state.json`，且不能 `--resume` 重发；OCR 缓存只有在 `image_set_sha256` 与 `ocr_run_fingerprint` 同时一致时才可复用。该图文规则不得被用于根据视频封面、标题或简介猜测视频内容。

如果用户选“仅文字稿”，跳过 `analyze_video_visuals.py`，并在分类前校验 `video_analysis.json` 中所有成功项都是 `analysis_basis=transcript_only` / `visual_status=not_enabled`。如果用户选 `codex-cli` 或 `command`，仅替换 `--analysis-provider` 及其对应参数，不改变全时轴证据门。

`transcribe_video_items.py` 只从 Arc 最新收藏缓存中读取匹配笔记的会话参数，在内存中构造访问链接；`xsec_token`、完整 query 和 signed media URL 不写入 `visible_items.json`、文字稿、memo 或日志。Cookie、音频和中间文件默认在单条完成后删除。缓存里找不到匹配笔记时直接返回 `arc_collection_context_missing`，不得改用无会话直链或简介分类。

## 执行前必须展示并确认

至少展示：笔记标题、内容类型、视频主要内容、目标专辑、置信度、失败原因。明确标出：

- `video_content_unavailable`
- `content_type_needs_review`
- `target_board` 为空
- 低置信度分类

用户没有确认分类和目标专辑时，不得传 `--execute`。确认一次“开启视频内容分类”不等于授权真实移动收藏。

必须在视频下载、转写和视觉分析前，用 `capture_board_snapshot.py` 通过前端 `yC + U_ + Ks` 完整分页生成 `board_snapshot.json`，先排除全部首次归档已确认的现有专辑成员。生成 execute 清单前再刷新快照并运行 `build_created_boards.py classification.json board_snapshot.json created_boards.json`。将两份证据传给 `run_reassign_batch.py` 后，任何已有专辑成员统一标记 `existing_board_member_protected + first_archive_confirmed` 并保持零写入；只有 `not_in_any_board + first_archive_pending` 且空 `source_board_id` 的条目可以执行首次归档。分页不完整或无法证明专辑外状态时硬闸门阻止执行。

确认后使用 Arc 真实移动时，还必须先为独立工作标签页取得稳定的 Arc `window id`、`tab id`、预先写入 `window.name` 的稳定 marker，以及预期 URL 片段。执行器通过 `--arc-tab-marker` 核验该 `window.name`；任一缺失或不唯一就中止：

```bash
python3 scripts/run_reassign_batch.py classification.json run_report.json \
  --board-snapshot board_snapshot.json \
  --created-boards created_boards.json \
  --execute --browser arc --user-id '<user-id>' \
  --expected-url-substring '<expected-path>' \
  --max-moves-per-session <本次明确范围> \
  --arc-window-id '<window-id>' \
  --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' \
  --arc-expected-url-substring '<expected-path>'
```

`--browser auto` 会拒绝真实执行，防止未经本轮授权自动控制其他浏览器。
Arc execute 通过隐藏 DOM 状态节点把任务注入页面 main world；前端 API 只从 Rspack `req.m` 按精确 endpoint 唯一解析 `d0/Ks/yC/U_`，匹配为 0 或多个都中止，禁止猜导出名。

只有 `first_archive_pending` 的未归档条目使用 `d0 -> U_/Ks`。任何已有专辑成员、带 `source_board_id`、成员状态不是 `not_in_any_board` 或生命周期状态不是 `first_archive_pending` 的条目都在 Python 和页面 JavaScript 两层跳过；回读确认后才转为 `first_archive_confirmed`。不提供跨专辑事务，也不调用取消收藏/重新收藏 endpoint。安全验证、异常访问、频繁访问或标签绑定变化时立即停写。

条目间默认固定等待 5 秒。Python 每次只提交一条；首个错误行先写入报告再停止整批。`d0` 返回 `{}` 或对已在其他专辑条目产生静默 no-op 都不能算成功，唯一成功依据是 `U_` + `Ks` 中确实存在 note id。
