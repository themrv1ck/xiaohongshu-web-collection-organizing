# 执行工作流速查
1. `scripts/check_environment.py`
2. `scripts/extract_visible_items.py`
3. `scripts/ocr_cover_images.py`（对每个条目封面图全量 OCR，产出 `ocr_results.json`）
4. `scripts/classify_items.py`（默认会复用或自动补跑 OCR）
5. `scripts/build_created_boards.py`
6. `scripts/run_reassign_batch.py`
7. `scripts/build_retry_queue.py`
8. `scripts/summarize_run_report.py`
