# 环境前提与限制

## 必备前提
- macOS
- Google Chrome
- 小红书网页登录态
- Chrome 已开启：查看 → 开发者 → 允许 Apple 事件中的 JavaScript
- 系统可用 `osascript`
- 系统可用 `swift`（用于调用 macOS Vision OCR）

## OCR 说明
- 当前 OCR 基于 macOS 原生 Vision，通过 `scripts/ocr_image.swift` 执行
- 默认对每个可见条目的封面图都跑一遍 OCR
- 分类脚本 `scripts/classify_items.py` 默认会复用已有 `ocr_results.json`，缺失时自动补跑

## 已知限制
- 网页端总数与可抓取总数可能不一致
- OCR 基于封面图，不是进入每条笔记后的全文识别
- 无封面图 URL 或图片下载失败的条目只能退回标题/desc/tags 规则分类
- 全量 OCR 比只复核模糊项更慢，收藏量大时会明显增加运行时间
- banner 是稳定路径，但不是所有失败都能自动恢复
