# xiaohongshu-web-collection-organizing

一个整理小红书收藏和点赞内容的 Skill。它读取用户已登录的小红书网页，根据笔记实际内容生成专辑分类建议；只有用户再次确认后，才会移动笔记。

当前版本：**2.0.0**。默认只生成预览和 dry-run，不会直接修改账号。

## 能做什么

- 整理收藏、点赞，或将两者合并去重。
- 图文笔记可读取封面和全部内页的文字。
- 视频可选用字幕、语音和完整时轴画面分类。
- 先按专辑汇总预览，再按需展开逐条检查。
- 默认保留已有专辑内容，低置信度条目进入人工复核。
- 真实移动前再次确认，移动后核验目标专辑。
- 遇到登录失效、安全验证或状态不确定时立即停止。
- 支持 WorkBuddy、Hermes，以及 macOS / Windows 浏览器后端。

## 快速开始

### WorkBuddy

在 WorkBuddy 对话中执行：

```text
/plugin marketplace add themrv1ck/xiaohongshu-web-collection-organizing
/plugin install xiaohongshu-organizer@xiaohongshu-skill-marketplace
/reload-plugins
```

插件使用独立的可见 Chromium。首次使用时登录一次小红书，之后直接对 WorkBuddy 说：

```text
帮我整理小红书收藏。
```

详细说明见 [WorkBuddy Plugin](references/workbuddy-plugin.md)。

### Hermes 或其他 Agent

```bash
mkdir -p ~/.hermes/skills/social-media
git clone https://github.com/themrv1ck/xiaohongshu-web-collection-organizing.git \
  ~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing
hermes skills list
```

安装后可以直接说：

```text
用 xiaohongshu-web-collection-organizing 帮我整理小红书收藏。
```

其他 Agent 可从 [`SKILL.md`](SKILL.md) 读取完整工作流。

## 使用方式

先选择范围：

- **收藏**：只整理收藏列表。
- **点赞**：只整理点赞列表。
- **我全都要**：合并收藏和点赞，并按笔记去重。

再选择整理深度：

| 模式 | 图文 OCR | 视频语音 | 视频画面 |
|---|---:|---:|---:|
| 快速整理 | 关闭 | 关闭 | 关闭 |
| 轻度整理 | 开启 | 关闭 | 关闭 |
| 深度整理 | 开启 | 开启 | 开启 |

完整流程只有五步：

1. 检查本机已有能力。
2. 读取用户选择的收藏或点赞范围。
3. 生成分类方案和待复核条目。
4. 用户确认分类、已有专辑策略和 dry-run。
5. 用户再次确认后逐条移动，并核验结果。

目标专辑需要提前存在；当前版本不会自动创建、删除或重命名专辑。

## 支持环境

| 使用方式 | 浏览器 | 主要要求 |
|---|---|---|
| WorkBuddy Plugin | 插件独立 Chromium | 安装 Plugin，在该浏览器中登录小红书 |
| macOS | Arc、Chrome 或 Safari | Python 3.9+；OCR 优先使用系统 Vision |
| Windows | Chrome 或 Edge | Python 3.9+；Playwright/CDP；OCR 可选 Tesseract |

非 WorkBuddy 路径必须由用户在当前操作中明确指定浏览器。环境和 Windows 安装细节见：

- [环境与限制](references/environment-and-limitations.md)
- [Windows 浏览器与 OCR](references/windows-playwright-ocr-notes.md)

## 安全机制

- 默认被动读取当前已显示内容，每段最多 200 条。
- 不自动滚动、刷新、切换浏览器或进入下一段。
- 不保存或输出 cookie、token、xsec 和签名 URL。
- 不传 `--execute` 就不会修改账号。
- 缺少专辑快照、目标专辑核验或用户确认时拒绝执行。
- 移动后必须在目标专辑中查到对应 note id，才能记为成功。
- 安全验证、登录失效或页面绑定异常会触发停机，不自动重试。

200 条只是程序的防误操作上限，不代表平台公开的安全阈值。

## 主要输出

| 文件 | 用途 |
|---|---|
| `visible_items.json` | 已读取的收藏或点赞条目 |
| `ocr_results.json` / `video_analysis.json` | 图文和视频内容证据 |
| `classification.json` | 分类建议 |
| `board_snapshot.json` | 真实专辑与成员快照 |
| `run_report.json` | 分类预览、dry-run 或执行报告 |
| `retry_queue.json` | 需要人工处理的失败项 |
| `xhs_safety_state.json` | 当前会话安全状态 |

这些文件可能包含个人收藏信息，默认不会提交到 GitHub。

## 详细文档

- [完整 Skill 工作流](SKILL.md)
- [2.0 相比 1.0 的更新](references/v2-vs-v1.md)
- [图文 OCR](references/image-ocr-classification.md)
- [视频内容分类](references/video-content-classification.md)
- [输入输出格式](references/io-contract.md)
- [失败恢复](references/recovery-and-resume.md)
- [完整流程说明](references/workflow.md)
- [单篇笔记研究](references/xiaohongshu-note-research.md)

## 本地验证

以下命令不会修改小红书账号：

```bash
python3 -m compileall -q .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_environment.py --capability-preflight
```

## License

[MIT](LICENSE)
