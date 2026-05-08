# 输入输出契约

## 输入
### `visible_items.json`
```json
[
  {
    "id": "66d19b54000000001d03a93d",
    "title": "到底为什么叫他贵妇？",
    "href": "https://www.xiaohongshu.com/explore/66d19b54000000001d03a93d",
    "cover_image_url": "https://ci.xiaohongshu.com/cover-1.jpg",
    "user": "穿搭研究所",
    "desc": "男士西装与香水搭配",
    "tags": ["穿搭", "老钱风"],
    "card_text": "到底为什么叫他贵妇？ 男士西装与香水搭配 #穿搭 #老钱风",
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
    "id": "66d19b54000000001d03a93d",
    "title": "到底为什么叫他贵妇？",
    "image_url": "https://ci.xiaohongshu.com/cover-1.jpg",
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
    "id": "66d19b54000000001d03a93d",
    "title": "到底为什么叫他贵妇？",
    "target_board": "穿搭发型与品味",
    "confidence": "high",
    "reason": ["西装", "ocr:老钱风"],
    "review_state": "ocr_reviewed",
    "ocr_status": "ok",
    "ocr_confidence": 0.93,
    "ocr_text": "老钱风西装 香水推荐",
    "ocr_image_url": "https://ci.xiaohongshu.com/cover-1.jpg"
  }
]
```

### `created_boards.json`
```json
{"confirmed":["穿搭发型与品味","滑雪"],"created":[],"missing":["体态纠正与康复"],"failed":[],"action_required":"Create missing boards manually in Xiaohongshu before running --execute."}
```

### `run_report.json`
```json
{"started_at":"2026-04-17T01:17:03Z","mode":"execute","visible_count":11,"processed":[{"id":"69538be3000000001e028205","title":"《技能练反脚》不用从头练！4个技能直接出活","target_board":"滑雪","status":"success","attempt":1,"events":["board:FOUND:滑雪","note_move:CALLED","verify:note_present"],"error":"","verified":true}],"errors":[],"missing_boards":[],"board_counts_before":{"滑雪":76},"board_counts_after":{"滑雪":77}}
```

### `retry_queue.json`
```json
[{"id":"684bde220000000022004e7d","title":"怀疑自己走姿不对？建议你别只想着纠正走姿","target_board":"体态纠正与康复","reason":"target board not found","next_action":"retry_after_fixing_browser_or_board_state"}]
```
