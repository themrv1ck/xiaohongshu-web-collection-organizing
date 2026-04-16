# 小红书自动整理收藏夹skill

一个可分发、可安装、可续跑的小红书网页端收藏整理 skill 包，现已补上 **每个条目封面图全量 OCR**。

## 包含内容
- `SKILL.md`
- `scripts/`
- `references/`
- `examples/`
- `templates/`

## 新增 OCR 能力
- `scripts/ocr_cover_images.py`：对 `visible_items.json` 中每个条目跑封面 OCR，输出 `ocr_results.json`
- `scripts/ocr_image.swift`：调用 macOS Vision 执行单图 OCR
- `scripts/classify_items.py`：默认复用或自动补跑 OCR，再输出 `classification.json`

## 最短执行路径
```bash
python3 scripts/check_environment.py
python3 scripts/extract_visible_items.py visible_items.json
python3 scripts/ocr_cover_images.py visible_items.json ocr_results.json
python3 scripts/classify_items.py visible_items.json classification.json
```

## 安装位置
`~/.hermes/skills/social-media/xiaohongshu-web-collection-organizing/`
