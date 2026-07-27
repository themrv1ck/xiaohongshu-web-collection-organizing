# 根据视频实际内容分类

这是可选功能。它让视频按实际声音、字幕和必要时的完整时轴画面分类，而不是只根据标题、简介或封面猜主题。

## 正确的询问时点

视频能力不能在还不知道列表内容时就要求用户安装。v2.0 的顺序是：

1. 欢迎页：选“快速启动（按推荐设置）”或“完整启动（自己逐项设置）”；
2. 选择收藏、点赞或“我全都要”；
3. 做本机只读能力检查，并在读取前询问“图文 OCR（推荐开启）”；
4. 用户在当前回合授权一个已打开、已登录小红书且具备 JavaScript 能力的浏览器；
5. 用户一次设置每组条数（默认 `200`，最大 `200`）和组间暂停分钟数（默认 `3`），并确认：只有当次任务执行器已验证支持受控续组时，本次读取才可按该设置继续；
6. 读取并合并全部列表分段；
7. 根据**真实**分布显示图文、视频和未确认数量，再决定是否启用视频内容分类。

不要在第 7 步之前预估视频比例、安装视频模型或宣称会分析画面。分段暂停不是规避验证的说法；出现验证时立即停止并按 [失败恢复与续跑](recovery-and-resume.md) 处理。

## 读取完成后的交互

先展示真实数据，例如“已读取 1,000 条：图文 720、视频 250、未确认 30”。

### 快速启动

快速启动只给与真实分布相符的推荐：

- 视频少：提示可先按图文 OCR 与元数据分类；
- 视频占比明显：提示“这些视频的标题可能不代表真实内容。建议开启视频内容分类，让 Skill 先听声音；需要时再看完整时轴画面。”

用户可接受推荐、保持基础分类，或切换到完整启动逐项设置。接受推荐后仍先检查可复用能力；缺失组件必须展示大小和安装确认，不能静默安装。

### 完整启动

完整启动按以下卡片逐项选择：

> **视频内容分类**
>
> 例如，标题写“太香了”，视频实际讲的是番茄炒蛋做法；听声音或字幕后，才能归到“做饭 / 菜谱”。又例如，视频没人说话，只是在演示整理衣柜；这时需要看画面，才知道它属于“收纳整理”。
>
> 1. **耳朵和眼睛都要**：用字幕和视频语音理解内容，并查看完整时轴画面；适合无声演示、画面和口述不一致的视频。
> 2. **先检查我是否已有类似功能的 AI 或模型**：只检查可复用能力，再由用户选择使用哪一个。
> 3. **不开启**：不安装、不运行视频内容分类，视频仍按基础信息进入分类或人工复核。

如用户选择“耳朵和眼睛都要”，先显示已有能力和缺失项。用户可复用本地模型或已接入的视觉 Agent；不能把 `codex-cli` 固定为唯一选择。

## “耳朵”和“眼睛”分别做什么

| 能力 | 作用 | 不能做什么 | 常见本地选择 |
| --- | --- | --- | --- |
| 耳朵（声音/字幕） | 读取平台字幕；字幕不足时把视频语音转成带时间戳的文字，用来理解讲话内容 | 不识别人、物体、动作或场景 | `MiMo-V2.5-ASR-MLX`，约 6.6 GB |
| 眼睛（画面） | 从开头到结尾按时间抽取真实帧，识别画面中的物体、场景、动作和文字 | 不读取视频音轨 | `MiMo-VL-7B-RL-2508` + MLX-VLM，约 16.6 GB |
| 分类 provider | 根据合格文字稿和/或真实帧生成简短分类 memo | 不能在没有视觉输入时声称看过画面 | `mimo-vl-mlx`、`codex-cli` 或 `command`/其他 Agent 适配器 |

MiMo-VL 只处理画面，不听声音；声音始终来自平台字幕或 MiMo ASR。若用户已有 Cloud Code、QQ Bot、Hermes 或其他 Agent/API，只要它实际支持图片输入，就可以通过 `command` 适配器作为视觉 provider；纯文本 Agent 不能冒充视觉能力。

本地“眼睛”建议使用 Apple Silicon 且具有约 32 GB 统一内存或以上；16.6 GB 是模型下载量，不等于运行峰值。安装前展示本机实际所需空间和依赖，不要只报一个固定总数。

## 能力检测与安装门

在用户确认要启用视频内容分类、且当前回合已经授权具体浏览器后，运行针对本次选择的检查。以本地 MiMo-VL 为例：

```bash
python3 scripts/check_environment.py \
  --video-content --browser arc --check-login-state \
  --analysis-provider mimo-vl-mlx --visual-analysis
```

- 只选声音时去掉 `--visual-analysis`；结果只能标记为 `transcript_only`，不能说已看画面。
- 选择其他 Agent 时使用相应的 `--analysis-provider command` 或可用 provider，并实测它能接收本地真实帧后才将视觉能力标为 ready。
- 检查只显示真正缺少的项目。已有能力直接复用；缺失 ASR、视觉模型或其它依赖时，先说明用途、下载量、安装位置和命令，得到安装同意后才安装。
- 安装不等于浏览器授权，也不等于移动笔记授权。

常见依赖包括 Python 3.9+、Video Transcript Extractor、`yt-dlp`、`ffmpeg`、`ffprobe`、选定的 ASR、视觉模型和 provider。无需为了本功能安装 Qwen 或 LM Studio。

本地 MiMo 安装路径和验证以 Skill 自带脚本为准：

```bash
./scripts/install_mimo_vl_mlx.sh
python3 scripts/verify_mimo_vl_install.py --run-inference
```

若模型目录已存在，只检查和补齐缺失依赖，不重复 clone 或覆盖已有文件。

## 处理链路与质量要求

1. 只处理已确认是 `video` 的条目；`unknown` 保留人工复核。
2. `transcribe_video_items.py` 必须带 `--allow-video-access` 和明确视频范围。优先使用平台字幕；无合格字幕时再用 ASR。每条文字稿做时轴覆盖率校验，覆盖不足即失败。
3. 对合格文字稿调用已选择的 provider，生成最小内容 memo：`main_topic`、`content_summary`、`target_board`、`confidence` 和 `reason`。
4. 如果开启眼睛，`analyze_video_visuals.py` 必须覆盖整条视频时间轴：至少 5 帧、包含首尾、任意相邻帧间隔不超过 10 秒；保存时间戳、帧哈希、视频哈希和视觉证据。声音看似足够也不能跳过画面检查。
5. 视觉成功项只能使用 `full_timeline_visual_with_transcript` 或 `full_timeline_visual`；只听声音的成功项使用 `transcript_only`，并且 `visual_status=not_enabled`。
6. 转写、provider 或必要画面证据失败时，`target_board` 留空并标为 `video_content_unavailable`；不退回标题、简介或封面 OCR 猜分类。
7. 所有视频结果准备好后再与图文结果一起生成分类方案预览。预览默认只显示专辑及数量；用户确认后才按需展开条目。

示例命令（具体 browser 参数必须替换为用户当次授权的浏览器）：

```bash
python3 scripts/transcribe_video_items.py \
  visible_items.json video_transcripts.json \
  --browser arc --extractor-root "$HOME/video-transcript-extractor" \
  --allow-video-access --max-videos <1–200>

python3 scripts/analyze_video_transcripts.py \
  video_transcripts.json video_analysis.json \
  --taxonomy board_taxonomy.json --analysis-provider mimo-vl-mlx

# 只有用户选择“耳朵和眼睛都要”才运行
python3 scripts/analyze_video_visuals.py \
  visible_items.json video_transcripts.json video_analysis.json \
  --allow-video-access --all-videos --max-videos <1–200> \
  --analysis-provider mimo-vl-mlx
```

## 浏览器、恢复与每周任务

本功能不会自行启动、连接或切换浏览器。浏览器只能在当前回合获授权、已打开、登录正常且页面接入可确认时使用；`--browser auto` 不用于真实浏览器操作。

出现安全验证或页面状态异常时，立即保存本地结果并停止。用户处理后回复“继续整理”时，只做一次当前页面状态检查，保留已完成分段，不自动重试旧请求；通过检查后才继续未完成队列。

每周任务只整理新出现的视频与图文，并先给出新的分类方案。它不会自动创建专辑或移动笔记；到时间仍需要用户当次确认浏览器可用与登录正常。
