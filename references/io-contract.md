# 输入输出契约

## 输入
### `visible_items.json`
```json
[
  {
    "id": "000000000000000000000001",
    "title": "示例笔记A",
    "href": "https://www.xiaohongshu.com/explore/000000000000000000000001",
    "cover_image_url": "https://example.invalid/cover-1.jpg",
    "user": "示例账号A",
    "desc": "示例描述A",
    "tags": ["穿搭", "老钱风"],
    "card_text": "示例笔记A 示例描述A #穿搭 #老钱风",
    "first_seen": 0
  }
]
```

### `board_taxonomy.json`
```json
{"boards":["家居装修与收纳","穿搭发型与品味","运动训练与体态","体态纠正与康复","滑雪","效率系统与AI","摄影审美与创作","日本旅行与机位","思考与成长","杂项灵感"]}
```

## 输出
### `ocr_results.json`
```json
[
  {
    "id": "000000000000000000000001",
    "title": "示例笔记A",
    "image_url": "https://example.invalid/cover-1.jpg",
    "status": "ok",
    "ocr_text": "老钱风西装 香水推荐",
    "ocr_lines": ["老钱风西装", "香水推荐"],
    "ocr_confidence": 0.93,
    "error": ""
  }
]
```

### `classification.json`
```json
[
  {
    "id": "000000000000000000000001",
    "title": "示例笔记A",
    "target_board": "穿搭发型与品味",
    "confidence": "high",
    "reason": ["西装", "ocr:老钱风"],
    "review_state": "ocr_reviewed",
    "ocr_status": "ok",
    "ocr_confidence": 0.93,
    "ocr_text": "老钱风西装 香水推荐",
    "ocr_image_url": "https://example.invalid/cover-1.jpg"
  }
]
```

### `created_boards.json`
```json
{"confirmed":["穿搭发型与品味","滑雪"],"created":["体态纠正与康复"],"failed":[]}
```

### `run_report.json`
```json
{"started_at":"2026-04-17T01:17:03Z","visible_count":11,"processed":[{"id":"000000000000000000000004","title":"示例成功笔记","target_board":"滑雪","status":"success","attempt":1,"events":["collect:CLICKED","board:CLICKED:滑雪","toast:ok"]}],"errors":[],"board_counts_before":{"杂项灵感":76},"board_counts_after":{"杂项灵感":65}}
```

### `retry_queue.json`
```json
[{"id":"000000000000000000000003","title":"示例失败笔记","target_board":"体态纠正与康复","reason":"collect selector timeout","next_action":"retry_with_extended_wait"}]
```
