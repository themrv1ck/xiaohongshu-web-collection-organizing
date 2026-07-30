# 图文 OCR 分类开关

图文 OCR 是独立可选功能。它会按原顺序逐张处理图文笔记的封面和全部内页图片，把其中可见的中文或英文文字转成可分类文本，用来补足标题、正文和标签没有写出的信息。OCR 只做图片文字识别；没有文字的纯画面不属于 OCR，不能据此理解人物、物体、场景或动作，也不分析视频画面。

## 首次运行卡片

用户确认整理范围后，先运行 `python3 scripts/check_environment.py --capability-preflight` 并显示只读检查结果，再显示“快速整理 / 轻度整理 / 深度整理”卡片。快速整理关闭 OCR；轻度整理和深度整理都直接开启 OCR，因此不重复显示本卡片；只有用户回复“自定义”时，才显示本卡片并在得到回答后显示视频内容分类卡片。

三档与 OCR 的映射固定如下：

| 档位 | 图文 OCR | 后续处理 |
| --- | --- | --- |
| 快速整理 | 关闭 | 不安装、不运行 OCR，分类显式传 `--skip-ocr` |
| 轻度整理 | 开启 | 读取完整图片集合并运行 OCR |
| 深度整理 | 开启 | 读取完整图片集合并运行 OCR；视频另加语音和画面分析 |

> **已完成本地能力检查**
>
> - 图文 OCR：`<已找到：名称，可直接复用 / 未找到，推荐安装>`
> - 声音识别：`<已找到：名称，可直接复用 / 未找到，按需安装>`
> - 画面识别：`<已找到：名称，可直接复用 / 未找到，按需安装 / 当前 Agent 无法确认，按需检查>`
>
> “推荐安装”表示它是图文收藏整理的轻量基础能力；“按需安装”表示只有后续选择视频内容识别时才需要。

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
> OCR 只提取图片里的可见文字；没有文字的纯画面不属于 OCR，OCR 不能理解其中的人物、物体、场景或动作，也不负责分析视频画面。
>
> 回复“开启”：使用刚才检查到的中文 OCR；如果没有，则安装当前系统的推荐组件。安装前会显示具体组件、预计下载量和磁盘占用；系统权限窗口仍需你确认。
>
> - macOS：优先复用系统 Vision，OCR 模型本身通常是 **0 MB 额外下载**；如果缺少 Swift/Command Line Tools，需要安装 GB 级系统组件，实际大小以 macOS 安装窗口为准。
> - Windows：默认安装 Tesseract 和简体中文 `chi_sim` 语言包，通常为**几十到数百 MB**；安装前以当前安装器显示的大小为准。
> - EasyOCR：只在你明确选择时使用；它包含 PyTorch 和语言模型，通常是 **GB 级**，不会被自动当作默认方案。
>
> 回复“不开启”：不安装、不运行图文 OCR；刚才的只读检查结果不会被用于分类。仍可按标题、正文、标签和作者分类，但准确率可能下降。

自定义回复“开启”、或选择“轻度整理 / 深度整理”，都授权使用 OCR，并在缺失时按既有规则安装推荐 OCR 组件；不授权打开外部浏览器或移动小红书笔记。预检本身不构成任何使用或安装授权。

## 严格运行顺序

1. 先读取预检中的 `capabilities.ocr`。已有中文 OCR 时直接复用；缺失且用户选择“轻度整理”“深度整理”或自定义回复“开启”时安装推荐组件，随后运行：

   ```bash
   python3 scripts/check_environment.py --ocr
   ```

   Windows 使用：

   ```powershell
   python scripts\check_environment.py --ocr
   ```

2. 读取复验结果中的 `ocr_checked`、`ocr_status`、`ocr_ready`、`ocr_provider`、`tesseract_chi_sim` 和 `ocr_install_size`。
3. `ocr_ready=true`：报告复用的 provider，不安装任何内容。
4. `ocr_ready=false`：先展示 `ocr_install_size` 和当前系统安装器报告的实际大小，然后按推荐路径安装；用户的“轻度整理 / 深度整理”选择或自定义“开启”已经覆盖本次 OCR 安装同意，不重复询问，但操作系统自己的权限窗口仍由用户确认。
5. macOS 推荐路径是 Swift + 系统 Vision。缺少工具链时请求安装 Apple Command Line Tools，完成后必须重新验证 Swift 能导入 Vision。
6. Windows 推荐路径是 Tesseract + `chi_sim`。如果包管理器返回零个或多个同名候选，停止并展示候选，不猜包；安装完成后必须确认 `tesseract --list-langs` 包含 `chi_sim`。
7. 不得把 PaddleOCR 当成已支持 provider，不得在缺少 `chi_sim` 时静默回退英文，也不得自动改装 EasyOCR。
8. 安装后重新运行 `check_environment.py --ocr`；只有 `ocr_ready=true` 才能进入 OCR 分支。

## 全图片运行契约

环境就绪后，图文 OCR 的顺序固定为：

```bash
python3 scripts/enrich_note_images.py visible_items.json image_items.json \
  --allow-detail-requests --max-items <本次明确范围>
python3 scripts/ocr_note_images.py image_items.json ocr_results.json
```

Windows 使用：

```powershell
python scripts\enrich_note_images.py visible_items.json image_items.json --allow-detail-requests --max-items <本次明确范围>
python scripts\ocr_note_images.py image_items.json ocr_results.json
```

- `enrich_note_images.py` 默认低风险模式不访问详情；只有用户明确传 `--allow-detail-requests --max-items <1–200>` 才处理选定范围。它按笔记详情中的原始顺序写入封面和全部内页图片，并用 `image_urls_complete` 表示图片集合是否完整。
- 列表页卡片取得的封面或其它图片只能标为 observed，必须保持 `image_urls_complete=false`；只有详情 `LAUNCHER_SSR_STORE_PAGE_DATA.noteData.imageList` 可把 `image_list_source` 写为 `mobile_ssr_note_data.imageList` 并声明完整。
- 详情 `noteData.type` 是图文/视频类型的权威来源，必须覆盖列表页 observed 类型；详情确认是视频时写为 `not_applicable`，不得送入图文 OCR。
- 只有 `image_list_source=mobile_ssr_note_data.imageList`、`image_urls_complete=true` 且声明图片数与实际 URL 数一致时，`ocr_note_images.py` 才能执行；图片列表不完整必须写成 `incomplete_image_set`，不得只处理封面或部分内页。
- 详情请求触发 `security_blocked` 时，`enrich_note_images.py` 必须落盘当前状态，把后续未请求图文标为 `not_requested_after_security_block`，写入 `xhs_safety_state.json` 并立即停止；不得继续 OCR 或用 `--resume` 重发。
- `ocr_results.json` 必须逐图保存状态、文字、置信度、哈希和错误，同时提供按图片顺序聚合的 `ocr_text`。
- 任一图片下载或 OCR 失败时，整条笔记不得使用部分 OCR 文本分类。
- OCR 成功但没有识别出文字不是视觉理解成功；它只表示该图片没有可用于分类的 OCR 文本。
- 续跑只可复用 `status=ok`、完整性计数一致、`image_set_sha256` 与当前完整图片集合一致，且 `ocr_run_fingerprint` 与本次运行一致的结果。`ocr_run_fingerprint` 绑定实际 provider、Tesseract 语言配置，以及 Swift OCR 脚本内容哈希所代表的脚本版本；任一项改变都必须重跑。
- Tesseract 默认只使用已检测必备的 `chi_sim`。只有 `tesseract --list-langs` 明确包含 `eng` 时，才可显式传 `--tesseract-lang chi_sim+eng`；不得把未安装的 `eng` 写进默认命令。

## 关闭后的执行方式

用户选择“快速整理”或自定义回复“不开启”时：

- 不运行 `check_environment.py --ocr`；
- 不运行 `enrich_note_images.py` 或 `ocr_note_images.py`；
- 分类命令必须显式传 `--skip-ocr`；
- `classification.json` 对相关条目标记 `ocr_status=skipped` 或 `skipped_by_user`，不得伪造 OCR 文字。

## 体积口径

不要给所有系统写一个固定数字。macOS Vision 是系统框架；Apple Command Line Tools 会随 macOS 版本变化。Windows Tesseract 安装器是否携带额外语言也会改变体积。因此首次卡片提供可靠量级，实际安装前再显示本机安装器或包管理器报告的下载量和磁盘占用。

参考：[Apple Vision 文字识别](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)、[Apple Command Line Tools](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools)、[Tesseract 安装说明](https://tesseract-ocr.github.io/tessdoc/Installation.html)、[EasyOCR](https://pypi.org/project/easyocr/)。
