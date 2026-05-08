# 执行工作流速查
1. `scripts/check_environment.py`
2. `scripts/extract_visible_items.py`
3. `scripts/ocr_cover_images.py`（对每个条目封面图全量 OCR，产出 `ocr_results.json`）
4. `scripts/classify_items.py`（默认会复用或自动补跑 OCR）
5. `scripts/build_created_boards.py`
6. `scripts/run_reassign_batch.py`（默认 dry-run，不改账号）
7. `scripts/build_retry_queue.py`
8. `scripts/summarize_run_report.py`

真实移动收藏必须显式执行：

```bash
python3 scripts/run_reassign_batch.py classification.json run_report.json --execute --browser chrome
```

执行前必须确认：
- `classification.json` 的目标专辑正确
- 目标专辑已经存在
- 低置信度条目已经人工复核，或明确传入 `--allow-low-confidence`
