# 执行工作流速查（历史合同，当前不可执行）

> 2.2.2 已安全停用专辑读取、创建和移动；相关入口会在打开浏览器前停止。本文只保留未来非注入式实现必须满足的合同，不得按本文启动浏览器或执行账号写入。
0. **先分流 WorkBuddy**：检查六个 `xhs_workbuddy_*` MCP 工具。若同时存在，必须改走 `workbuddy-plugin.md` 的固定顺序；禁止 Safari/Arc/Chrome Apple Events、CDP 和 `osascript`。工具不完整时停止并要求安装/重载插件，不回退外部浏览器。
1. **先显示欢迎与范围卡片**：说明 Skill 能读取选定的收藏/点赞内容、根据实际内容生成分类建议，并在再次授权后移动笔记；随后让用户回复“收藏”“点赞”或“我全都要”。
2. **范围回答后自动做本地能力只读预检**：运行 `scripts/check_environment.py --capability-preflight`。只检查文档约定路径、OCR、ASR、本地视觉模型和宿主声明能力；不访问浏览器、不联网、不安装、不加载大模型。先向用户显示找到与未找到的能力，并明确安装决策：缺失 OCR 标“推荐安装”，缺失声音与画面能力标“按需安装”；已找到的能力标“可直接复用”。检查不等于开启。
3. **预检后显示快速启动档位**：固定提供“快速整理｜只按标题、正文、标签和作者；不做 OCR 或视频识别”“轻度整理｜读取图文全部图片文字；不分析视频”“深度整理｜图文 OCR + 视频语音 + 完整时轴画面”。按钮或选择题必须直接显示完整差异，不得只显示档位名，不得添加“推荐”，也不得默认选中。三档只决定内容识别，不打开浏览器、不移动笔记；若用户回复“自定义”，才进入原来的逐项选择。
4. **选择档位后直接解析开关**：快速整理显式传 `--skip-ocr` 且不运行视频链路；轻度整理走分段图文 OCR 链路、不运行视频链路；深度整理走分段 OCR、转写、内容 memo 和完整时轴画面分析。轻度或深度必须在任何整理动作前询问是否要在最终核验后生成桌面专辑 HTML，并记录 `report_requested=true|false`；快速整理固定为 `false`。WorkBuddy capture 必须显式传 `organizing_depth=quick|light` 与对应 `generate_report`；轻度使用同一登录态详情补齐，禁止运行无 Cookie 的 `enrich_note_images.py`。WorkBuddy 深度在视频语音和完整时轴画面证据入口接入前必须于浏览器启动前停止；非 WorkBuddy 仍按完整深度链路执行。选择轻度/深度沿用相应组件的安装与复验规则，仍不构成浏览器或移动授权。
5. **只有自定义路径显示 OCR 与视频卡片**：OCR 卡片解释封面和全部内页图片文字、约 60% 场景覆盖估算及安装口径；随后视频卡片提供“声音和画面都分析”“只分析声音”“不开启”。
6. 视频开关开启后，明确选择 analysis provider：`codex-cli`、`mimo-vl-mlx` 或 `command`/宿主 Agent 适配器。预检发现多个可用视觉能力时让用户选择；`codex-cli` 不是必需前提。深度整理也遵守此项选择。
7. 档位确定，或自定义的两个开关回答后，才取得具体浏览器授权并完整检查所选环境。只有视频开关开启时才加 `--video-content --browser <用户授权浏览器> --analysis-provider <codex-cli|mimo-vl-mlx|command>`；深度整理或“声音和画面都分析”必须再加 `--visual-analysis`；Arc 加 `--check-login-state`。环境缺失时逐项展示用途、执行位置和安装命令；轻度/深度与对应自定义选择沿用既有 OCR / ASR / 视觉组件安装同意边界。
8. 为本次整理确定一份 `xhs_safety_state.json`。下游脚本默认继承输入文件旁已有状态；工件位于不同目录时，所有小红书访问命令必须显式传同一个 `--safety-state <路径>`。随后运行 `scripts/extract_visible_items.py` 默认 `--capture-mode passive`：只读当前已显示卡片、每段最多 200 条、写入独立分段文件和 manifest；不执行自动滚动、点击、刷新、导航或自动续段。用户手动滚到下一段后，才可开始下一次采集；本地合并后得到 `visible_items.json`。保留每条笔记的 observed `content_type` 和列表页 observed 图片；列表页图片必须保持 `image_urls_complete=false`，不能当作完整图片集合。
9. 在任何详情、OCR 或视频访问前，先运行 `capture_board_snapshot.py` 完整读取当前全部专辑成员，再生成 `existing_boards_inventory.json`。任何当前已属于专辑的 note id 都视为首次归档已确认并固定保护，不询问是否重组，也不能被历史分类或模型目标覆盖；只有处于 `first_archive_pending` 的专辑外 ID 进入后续内容链路。
10. 图文 OCR 开启时，`scripts/enrich_note_images.py` 只对专辑外 ID 补齐详情和完整图片集合，再运行 `scripts/ocr_note_images.py image_items.json ocr_results.json` 逐张 OCR；任一图片失败时整条不使用部分 OCR 文本分类。缓存复用要求 `image_set_sha256` 和 `ocr_run_fingerprint` 同时一致；指纹绑定实际 provider、Tesseract 语言和 Swift OCR 脚本版本。Tesseract 默认语言为 `chi_sim`，只有确认 `eng` 已安装时才显式传 `--tesseract-lang chi_sim+eng`。图文 OCR 关闭时显式传 `--skip-ocr`。
11. 视频开关开启时：`scripts/transcribe_video_items.py` 需要 `--allow-video-access` 加明确 `--video-id` 或 `--max-videos <1–200>`；每段落盘后等待用户明确开始下一段。随后用明确的 `--analysis-provider` 运行 `scripts/analyze_video_transcripts.py` -> `video_analysis.json`。
12. `capabilities.visual_analysis.ready=true` 时，`scripts/analyze_video_visuals.py --all-videos` 必须同时带 `--allow-video-access --max-videos <1–200>`；只处理这一段明确视频的完整时轴真实帧。完成所有本地保存段后再作完整分类。该能力关闭时只能使用合格文字稿，并标记 `transcript_only`，不声称检查过画面。MiMo-VL 视频入口不读音轨；声音来自平台字幕或 ASR。
13. `scripts/classify_items.py`；图文 OCR 开启时输入 `image_items.json` 和 `ocr_results.json`，关闭时输入 `visible_items.json` 并传 `--skip-ocr`。`classification.json` 必须包含 `ocr_run_fingerprint`：只有完整图文 OCR 成功行透传与 OCR 结果相同的非空指纹，非图文、跳过或未成功 OCR 的行必须为空。视频开关开启时同时传 `--classify-video-by-content --video-analysis <最终分析文件>`；视觉模块开启时还必须传 `--require-visual-analysis`。
14. 在用户本轮授权的浏览器中运行 `scripts/capture_board_snapshot.py`，通过前端 `yC + U_ + Ks` 完整分页生成 `board_snapshot.json`；只要 `full_membership_complete` 不是 `true` 就停止。
15. 运行 `scripts/build_created_boards.py classification.json board_snapshot.json created_boards.json`，再把两份证据交给 `run_reassign_batch.py`：任何已有专辑成员统一标记 `existing_board_member_protected + first_archive_confirmed` 并排除；只有 `not_in_any_board + first_archive_pending` 且空 `source_board_id` 的条目进入首次归档清单。空目标必须机械写成固定“无法确定”；专辑缺失时进入同一次待创建确认，不能猜入其他类别。其他目标缺失、分页不完整或无法证明专辑外状态时脚本必须阻止。
16. 运行带 `--board-snapshot board_snapshot.json --created-boards created_boards.json` 的 dry-run。只有 `mode=dry_run`、`ready_for_execute=true`、`blockers=[]` 才展示分类、成员关系分流、低置信度和失败项并取得用户确认。缺少证据时的 `classification_preview` 不是 dry-run。
17. 再次确认后才能在同一命令中加 `--execute --max-moves-per-session <1–200>`；仍必须传入两份证据。达到上限后保存报告、等待用户检查，不自动进入下一段。安全验证、登录页、绑定丢失或状态不确定会写 `xhs_safety_state.json`，旧状态下不得续跑；随后运行 `scripts/build_retry_queue.py` 和 `scripts/summarize_run_report.py`。
18. WorkBuddy 必须自动串联 capture receipt、inventory receipt 和单次 plan receipt；用户无需查看或复制。每一阶段都先校验 MCP 内存账本与实际文件哈希，Python 在任何文件删除、修改或浏览器启动前再次核验。只有 `xhs_workbuddy_prepare` 返回 `mode=dry_run`、`ready_for_execute=true`、`blockers=[]`、非空 `approval_digest` 和 plan receipt，才可描述为可执行；`approval_digest` 只是用户可见的方案编号，不能单独授权。WorkBuddy/MCP 重启后旧 receipt 失效，必须重新 capture 和两阶段 prepare；禁止从 `missing_boards` 空值、旧目录或本地 manifest 自行恢复信任。
19. 执行完成后运行 `scripts/verify_classification_membership.py`，只读复抓全部专辑；只有回读确认进入目标专辑的首次归档成功项才转为 `first_archive_confirmed` 并在以后永久保护。dry-run、失败、中止或未核验项继续保持 `first_archive_pending`。已放行视频必须全局恰好出现一次并位于目标专辑；无法确定的视频进入固定专辑并明确等待用户自行调整。
20. 只有第 19 步完整核验通过且 `report_requested=true` 时，运行 `scripts/generate_collection_report.py`，用最终完整成员快照与同批完整分类生成 `$HOME/Desktop/我的小红书专辑整理报告.html`。缺页、重复、数量变化、ID 范围或目标专辑不一致时硬停；不得为补报告重新读取、OCR、转写或分析笔记。`report_requested=false` 时不生成。

视频开关开启后，Video Transcript Extractor + MiMo ASR 生成文字稿，明确选择的 analysis provider 生成分类所需的极简内容 memo。视觉模块开启时，复用 ffmpeg + macOS Vision + provider 查看每一条明确视频的完整时轴真实帧。视频链路只产出文字稿、分类 memo 和必要证据 JSON；转写和所选分析路径都失败时不得回退简介分类。

真实移动收藏必须显式执行：

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

浏览器必须由用户在当前回合明确授权并显式传入；`--browser auto` 不执行。

Arc execute 通过隐藏 DOM 状态节点把脚本注入页面 main world；API 只从 Rspack `req.m` 按精确 endpoint 唯一解析 `d0/Ks/yC/U_`，0 个或多个匹配都停止。只有 `not_in_any_board + first_archive_pending` 的专辑外条目走 `d0 -> U_/Ks`；已有专辑成员、带 `source_board_id` 或未知成员/生命周期状态一律跳过。安全验证、登录页、页面绑定失效或状态不确定会先写报告和 `xhs_safety_state.json` 并立即停写；`--resume` 不能绕过。

执行前必须确认：
- `classification.json` 的目标专辑正确
- 目标专辑已经存在
- 全部专辑成员关系已完整读取；所有已有成员均为 `existing_board_member_protected + first_archive_confirmed`，移动清单只含 `not_in_any_board + first_archive_pending`
- 低置信度条目已经人工复核，或明确传入 `--allow-low-confidence`
