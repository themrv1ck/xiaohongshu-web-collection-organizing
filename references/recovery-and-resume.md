# 失败恢复与续跑

- 永远先读旧 `run_report.json`
- `status=success` 直接跳过
- `status=failed` 进入 `retry_queue.json`
- 每成功一条立即写盘
- `.collect-wrapper` 首次等 45 秒，二次等 75 秒
- banner 未出现则重发鼠标事件
- AppleEvent / JXA 超时后当前条失败、整批继续
- `ocr_results.json` 已存在且条目 `status=ok` 时，默认直接复用，不重复 OCR
- 如需全量重跑 OCR，执行 `scripts/ocr_cover_images.py --force` 或 `scripts/classify_items.py --force-ocr`
- 中途终止后只补跑未成功条目
