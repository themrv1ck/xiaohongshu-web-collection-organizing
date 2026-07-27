# 图文 OCR 分类开关

OCR 是“图片文字识别”。它会按原顺序读取图文笔记的封面和全部内页图片，把其中可见的中文和英文文字转成可分类文本，补足标题、正文和标签没有写出的地点、型号、清单、步骤、菜名或商品名。

它不是画面理解：没有文字的纯画面不属于 OCR，OCR 不判断人物、物体、场景或动作，也不分析视频画面。

## 什么时候询问

欢迎页选择“快速启动”或“完整启动”后，用户选择收藏、点赞或“我全都要”。此时在**读取列表前**运行本机只读预检：

```bash
python3 scripts/check_environment.py --capability-preflight
```

预检不访问浏览器、不联网、不安装、不加载模型。它先显示本机是否已有中英文 OCR，再显示同一张卡片：

> **图文 OCR（推荐开启，以提高识别率）**
>
> OCR 会读取图文笔记封面和全部内页图片里的中英文文字，而不只是看标题或封面。
>
> 例如，标题只写“建议收藏”，内页却写着“上海两天一夜路线、餐厅和交通方式”；OCR 读出这些文字后，才能更准确地归到“旅行 / 上海攻略”。
>
> 为什么推荐？图文内容较多的收藏列表中，OCR 通常能覆盖约 60% 的内容识别场景。这里是场景覆盖的经验估计，不是固定准确率；不安装仍可使用 Skill，只是会更多依赖标题、正文和标签。
>
> 1. 开启 OCR
> 2. 不开启

两种启动方式都显示这张卡片。快速启动只是在用户选完后尽量沿用推荐设置；完整启动允许随后逐项设定分类规则。不要用旧版档位名代替 OCR 选择。

## 开启与安装规则

1. 用户选择“开启 OCR”后，先读取预检结果。
2. 已找到中英文 OCR：直接复用，显示 provider 和复验结果，不安装任何内容。
3. 未找到：先显示当前系统推荐组件、安装器报告的下载量和磁盘占用，再询问是否安装。未得到安装同意前，不安装、不运行 OCR。
4. 安装后重新运行 `check_environment.py --ocr`；只有 `ocr_ready=true` 才进入 OCR 分支。
5. 用户选择“不开启”：不安装、不运行 OCR，分类命令显式传 `--skip-ocr`，预检结果不得被偷偷用于分类。

推荐组件与体积口径：

| 系统 | 默认方案 | 安装提示 |
| --- | --- | --- |
| macOS | Swift + 系统 Vision | Vision 通常没有额外模型下载；若缺少 Swift / Command Line Tools，系统会显示实际的 GB 级组件大小，用户确认系统权限窗口。 |
| Windows | Tesseract + `chi_sim` + `eng` | 通常为几十到数百 MB，以安装器实际显示为准；只在 Windows 提示默认目录通常是 `C:\\Program Files\\Tesseract-OCR`，并以安装器最终位置为准。 |
| 用户明确选择的替代方案 | EasyOCR | 含 PyTorch 和语言模型，通常为 GB 级；不是默认自动安装项。 |

Tesseract 必须同时具备 `chi_sim` 与 `eng`；缺任一语言包都不是中英文 OCR 就绪状态，不能静默改成单语。

## 完整图片运行契约

OCR 环境就绪且用户已明确授权详情补齐范围后，顺序固定为：

```bash
python3 scripts/enrich_note_images.py visible_items.json image_items.json \
  --allow-detail-requests --max-items <1–200>
python3 scripts/ocr_note_images.py image_items.json ocr_results.json
```

- 列表页封面和图片只是 observed 线索，必须保持 `image_urls_complete=false`；不能把只读到封面说成“已 OCR 全部图片”。
- 只有详情 `noteData.type` 确认图文、`noteData.imageList` 提供完整有序图片集、`image_urls_complete=true` 且图片数一致时，才可以逐张 OCR。
- 详情确认视频时标为 `not_applicable`，不送入图文 OCR。
- 任一图片下载或 OCR 失败时，整条笔记不使用部分 OCR 文本分类；保留真实错误并进入人工复核。
- `ocr_results.json` 必须保留逐图状态、文字、置信度、哈希和聚合文本。只有完整性计数、`image_set_sha256` 与 `ocr_run_fingerprint` 全部一致时才可复用缓存。

详情读取出现 `security_blocked` 时，立即保存当前结果和 `xhs_safety_state.json`、停止后续请求；恢复方式见 [失败恢复与续跑](recovery-and-resume.md)。

## 关闭后的分类

关闭 OCR 后：

- 不运行 `enrich_note_images.py` 或 `ocr_note_images.py`；
- 分类显式传 `--skip-ocr`；
- `classification.json` 使用 `ocr_status=skipped` 或 `skipped_by_user`；
- 不伪造 OCR 文字，也不把封面图片当成画面识别结果。

视频的声音和画面识别在列表读取完成、确认真实视频比例后才另行询问，见 [根据视频实际内容分类](video-content-classification.md)。
