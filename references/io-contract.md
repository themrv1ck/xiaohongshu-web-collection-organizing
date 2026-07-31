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
    "image_urls": ["https://ci.xiaohongshu.com/cover-1.jpg"],
    "image_count": null,
    "image_urls_complete": false,
    "image_list_source": "collection_card_cover_only",
    "user": "穿搭研究所",
    "desc": "男士西装与香水搭配",
    "tags": ["穿搭", "老钱风"],
    "card_text": "到底为什么叫他贵妇？ 男士西装与香水搭配 #穿搭 #老钱风",
    "content_type": "image",
    "content_type_source": "xhs_initial_state_note_type",
    "source_lists": ["收藏", "点赞"],
    "source_primary": "收藏",
    "first_seen": 0
  }
]
```

`visible_items.json` 来自收藏/点赞列表页。列表页的 `cover_image_url` / `image_urls` 和 `content_type` 都只是 observed 线索：图片必须保持 `image_urls_complete=false`、`image_count=null`，不能证明已经取得图文笔记的全部图片，也不能直接作为“全图 OCR 已完成”的依据。图文 OCR 开启时，必须先生成下面的 `image_items.json`。

### `image_items.json`

由用户明确授权的 `scripts/enrich_note_images.py visible_items.json image_items.json --allow-detail-requests --max-items <1–200>` 生成。默认低风险模式不会访问详情。详情 `LAUNCHER_SSR_STORE_PAGE_DATA.noteData.type` 是图文/视频类型的权威来源，覆盖列表页 observed 类型；详情确认是图文时，`image_urls` 按 `noteData.imageList` 原始顺序排列，包含封面和全部内页图片。安全停机后必须使用新的 `xhs_safety_state.json`，不能用 `--resume` 重发。

```json
[
  {
    "id": "66d19b54000000001d03a93d",
    "title": "到底为什么叫他贵妇？",
    "href": "https://www.xiaohongshu.com/explore/66d19b54000000001d03a93d",
    "cover_image_url": "https://ci.xiaohongshu.com/cover-1.jpg",
    "image_urls": [
      "https://ci.xiaohongshu.com/image-1.jpg",
      "https://ci.xiaohongshu.com/image-2.jpg"
    ],
    "image_count": 2,
    "image_urls_complete": true,
    "image_list_source": "mobile_ssr_note_data.imageList",
    "image_enrichment_status": "ok",
    "image_enrichment_error": "",
    "content_type": "image",
    "content_type_source": "mobile_ssr_note_data.type",
    "source_lists": ["收藏", "点赞"],
    "source_primary": "收藏"
  }
]
```

- 只有 `image_list_source=mobile_ssr_note_data.imageList`、`image_urls_complete=true`、`image_enrichment_status=ok` 且 `image_count == image_urls.length` 的详情权威图文条目可进入 OCR。
- 图片集合不完整时必须保留 `image_enrichment_status=error|security_blocked|not_requested|not_requested_after_security_block` 和错误信息；不得只用 `cover_image_url` 或已取得的部分图片继续 OCR。
- 详情触发 `security_blocked` 时，脚本落盘当前条和后续未请求状态，立即停止详情请求并以非零退出码 `2` 结束；调用方必须停止，不得继续 OCR。
- `image_urls` 是 OCR 的权威输入顺序；`cover_image_url` 仅保留列表卡片元数据。

### `board_taxonomy.json`
```json
{"boards":[]}
```

### `command` / 宿主 Agent 适配器协议

Skill 直接执行用户提供的 argv，不经 shell。每次调用向 stdin 写入一行 JSON：

```json
{"protocol_version":1,"prompt":"分析要求","image_paths":["/absolute/frame-0001.jpg","/absolute/frame-0002.jpg"]}
```

- 纯文字分析时 `image_paths=[]`。
- 视觉分析时 `image_paths` 是按时间顺序排列的本地真实帧绝对路径。适配器只有真正把这些图像交给视觉模型，才能声明 `capabilities.visual_analysis.ready=true`。
- stdout 必须且只能输出一个 JSON 对象，包含 `main_topic`、`content_summary`、`target_board`、`confidence`、`reason`。不允许额外文字或 metadata 包装。
- 非零退出码、超时、非 JSON、多余 stdout 或 schema 不符都是硬失败，不自动换 provider。

### 已有专辑内容 JSON
最小格式：

```json
{"boards":["滑雪"]}
```

带专辑内容格式：

```json
{"boards":[{"name":"滑雪","notes":[{"id":"694d3390000000002203ae33","title":"固定器角度"}]}]}
```

## 输出
### `existing_boards_inventory.json`
```json
{
  "boards": ["滑雪"],
  "excluded_note_ids": ["694d3390000000002203ae33"],
  "note_to_board": {
    "694d3390000000002203ae33": "滑雪"
  },
  "generated_at": "2026-05-09T00:00:00Z"
}
```

### `ocr_results.json`

由 `scripts/ocr_note_images.py image_items.json ocr_results.json` 生成。每条图文笔记必须逐张保存证据，并按图片顺序聚合 `ocr_text`：

```json
[
  {
    "id": "66d19b54000000001d03a93d",
    "title": "到底为什么叫他贵妇？",
    "status": "ok",
    "ocr_text": "第1张：老钱风西装 第2张：香水推荐",
    "ocr_lines": [
      {"text": "老钱风西装", "confidence": 0.94, "image_index": 0},
      {"text": "香水推荐", "confidence": 0.92, "image_index": 1}
    ],
    "ocr_confidence": 0.93,
    "ocr_provider": "swift",
    "image_count_declared": 2,
    "image_count_available": 2,
    "image_count_processed": 2,
    "image_set_complete": true,
    "image_set_sha256": "0123456789abcdef",
    "ocr_run_fingerprint": "8888888888888888888888888888888888888888888888888888888888888888",
    "images": [
      {
        "image_index": 0,
        "status": "ok",
        "ocr_text": "老钱风西装",
        "ocr_lines": [{"text": "老钱风西装", "confidence": 0.94, "image_index": 0}],
        "ocr_confidence": 0.94,
        "ocr_provider": "swift",
        "source_url_sha256": "1111111111111111",
        "download_path": "ocr_cache/66d19b54-image-1.jpg",
        "image_sha256": "aaaaaaaaaaaaaaaa",
        "error": ""
      },
      {
        "image_index": 1,
        "status": "ok",
        "ocr_text": "香水推荐",
        "ocr_lines": [{"text": "香水推荐", "confidence": 0.92, "image_index": 1}],
        "ocr_confidence": 0.92,
        "ocr_provider": "swift",
        "source_url_sha256": "2222222222222222",
        "download_path": "ocr_cache/66d19b54-image-2.jpg",
        "image_sha256": "bbbbbbbbbbbbbbbb",
        "error": ""
      }
    ],
    "error": ""
  }
]
```

- 顶层 `status=ok` 只表示完整图片集合中的每张图片都已成功下载和 OCR。任一图片失败时顶层必须为 `error`，且分类不得使用部分 `ocr_text`。
- 图片集合不完整时顶层必须为 `incomplete_image_set`，不执行部分 OCR。
- 单张图片 `status=ok` 且 `ocr_text=""` 表示 OCR 成功但没有识别到可见文字；这不等于理解了无文字纯画面。OCR 不能识别人物、物体、场景或动作。
- 续跑只可复用 `status=ok`、完整性计数与逐图 URL 哈希一致、`image_set_sha256` 与当前有序完整图片集合一致，且 `ocr_run_fingerprint` 与本次运行一致的结果。
- `ocr_run_fingerprint` 绑定 OCR pipeline 版本和实际 provider；Tesseract 路径还绑定 `--tesseract-lang`，Swift 路径还绑定 `ocr_image.swift` 内容 SHA256 所代表的脚本版本。任一项改变都必须重跑。Tesseract 默认语言是 `chi_sim`；只有确认 `eng` 已安装时才显式选择 `chi_sim+eng`。

### `video_transcripts.json`

只在用户开启“根据视频实际内容分类”时生成。脚本只处理明确识别为 `video` 的条目；成功项必须通过覆盖率校验，失败项必须保留 `status`、`stage`、`reason_code` 和 `error`。

```json
[
  {
    "id": "694d3390000000002203ae33",
    "status": "success",
    "content_type": "video",
    "source_url": "https://www.xiaohongshu.com/explore/694d3390000000002203ae33",
    "source_kind": "mimo_audio",
    "transcript_sha256": "0123456789abcdef",
    "segment_count": 2,
    "char_count": 47,
    "segments": [
      {"start": 0.0, "end": 12.4, "text": "先说明固定器角度会怎样影响站姿。"},
      {"start": 12.4, "end": 28.0, "text": "再根据前后脚习惯调整角度，不要照搬别人的设置，并给出具体数值。"}
    ],
    "coverage": {
      "video_duration_seconds": 28.0,
      "transcript_first_start": 0.0,
      "transcript_last_end": 28.0,
      "transcript_covered_duration": 28.0,
      "transcript_coverage_ratio": 1.0,
      "coverage_threshold": 0.3,
      "transcript_segment_count": 2,
      "transcript_plain_text_char_count": 47,
      "transcript_source": "mimo_audio",
      "transcript_quality_reason": "coverage_ok",
      "transcript_quality_passed": true
    },
    "error": ""
  }
]
```

失败项示例：

```json
{
  "id": "694d3390000000002203ae35",
  "status": "failed",
  "stage": "transcript_quality",
  "reason_code": "transcript_coverage_too_low",
  "error": "转写覆盖率不足，不能用于内容分类"
}
```

### `video_analysis.json`

用户必须明确选择 analysis provider：`codex-cli`、`mimo-vl-mlx` 或 `command`/宿主 Agent 适配器。Provider 只根据合格文字稿和/或完整时轴真实帧生成最小分类 memo。`target_board` 只能来自当前专辑体系；失败或无准确匹配时必须为空。

每个成功行必须记录 `analysis_provider`、`analysis_model`、`analysis_provider_version`、`analysis_basis` 和 `visual_status`：

- 视觉模块未开启：`analysis_basis=transcript_only`、`visual_status=not_enabled`。没有 `evidence_manifest`，不得声称看过画面。
- 视觉模块开启：所有明确视频必须是 `analysis_basis=full_timeline_visual_with_transcript` 或 `full_timeline_visual`，`visual_status=analyzed`，并包含 `visual_evidence_sha256`、`analysis_input_sha256` 和 `evidence_manifest`。Manifest 内必须记录 `video_sha256`、视频时长、首尾覆盖、最大抽帧间隔、每帧时间戳/SHA256/Vision OCR；`video_sha256` 不是顶层字段。
- MiMo-VL 的视频入口只读画面，不读音轨。声音证据仍然来自 `video_transcripts.json` 中的平台字幕或 MiMo ASR。

```json
[
  {
    "id": "694d3390000000002203ae33",
    "status": "success",
    "main_topic": "单板滑雪固定器角度设置",
    "content_summary": "解释固定器角度对站姿的影响，并建议按个人前后脚习惯调整。",
    "target_board": "滑雪",
    "confidence": "high",
    "reason": ["核心内容是单板滑雪装备设置", "完整转写持续讨论固定器角度"],
    "transcript_sha256": "0123456789abcdef",
    "analysis_provider": "mimo-vl-mlx",
    "analysis_model": "/Users/example/Documents/MiMo-VL-7B-RL-2508/models/MiMo-VL-7B-RL-2508",
    "analysis_provider_version": "mlx-vlm-0.5.0",
    "analysis_basis": "transcript_only",
    "visual_status": "not_enabled",
    "error": ""
  }
]
```

上例只用了文字稿，因此不能当成画面分析成功。视觉模块开启后，对每个明确视频的最终行必须改为 `analysis_basis=full_timeline_visual_with_transcript|full_timeline_visual`、`visual_status=analyzed`，并带齐上述全时轴证据字段。

对应的 `classification.json` 失败行必须保持空目标专辑，不能补用简介：

```json
{
  "id": "694d3390000000002203ae35",
  "title": "转写失败的视频",
  "target_board": "",
  "confidence": "low",
  "reason": ["transcript_coverage_too_low"],
  "review_state": "video_content_unavailable",
  "content_type": "video",
  "classification_basis": "video_content",
  "video_analysis_status": "failed",
  "video_analysis_basis": "",
  "visual_status": "failed",
  "main_topic": "",
  "content_summary": "",
  "ocr_status": "skipped",
  "ocr_confidence": null,
  "ocr_text": "",
  "ocr_run_fingerprint": ""
}
```

### `classification.json`

`source_lists` / `source_primary` 从输入条目透传，用于区分收藏、点赞或二者都有。图文 OCR 关闭时，普通条目必须使用 `classification_basis=metadata_only` 和 `ocr_status=skipped`；开启且完整图片集合逐张 OCR 成功时为 `metadata_and_ocr`。图片集合不完整或任一图片失败时，图文行必须使用 `classification_basis=image_ocr_incomplete`、空目标专辑和 `review_state=image_ocr_incomplete`，不得使用部分 OCR 文本。成功图文行从 `ocr_results.json` 透传同一个非空 `ocr_run_fingerprint`；非图文、跳过或没有成功 OCR 的行该字段为空。视频开关开启后，视频行必须使用 `classification_basis=video_content`，并从 `video_analysis.json` 透传 `video_analysis_basis`、`visual_status` 和 provider identity。转写或所选 analysis provider 失败时不得根据简介/OCR 补分类。

真实 dry-run 和 execute 前必须先用 `capture_board_snapshot.py` 通过前端 `yC + U_ + Ks` 生成完整 `board_snapshot.json`，再生成 `created_boards.json`。两份证据必须同时传给 `run_reassign_batch.py`；否则只能得到 `classification_preview`、`ready_for_execute=false`、`missing_boards=null`。硬闸门通过后，执行清单字段必须满足：

- 已在目标专辑：从执行清单排除，或保留 `excluded=true`、`exclude_reason=already_in_target`，确保零写入。
- 不在任何专辑：`source_board`、`source_board_id` 都为空，执行器使用直接 `d0`。
- 已在另一个专辑：`source_board_id` 必须是核验得到的真实 board id；只有它显式存在且不同于目标 board id，执行器才启用跨专辑事务。
- 多来源、来源不明确或 `Ks` 分页不完整：目标留空或标为人工复核，不得进入 execute。

```json
[
  {
    "id": "66d19b54000000001d03a93d",
    "title": "到底为什么叫他贵妇？",
    "target_board": "穿搭发型与品味",
    "confidence": "high",
    "reason": ["西装", "ocr:老钱风"],
    "review_state": "ocr_reviewed",
    "content_type": "image",
    "classification_basis": "metadata_and_ocr",
    "video_analysis_status": "",
    "main_topic": "",
    "content_summary": "",
    "ocr_status": "ok",
    "ocr_confidence": 0.93,
    "ocr_text": "第1张：老钱风西装 第2张：香水推荐",
    "ocr_run_fingerprint": "8888888888888888888888888888888888888888888888888888888888888888",
    "ocr_image_count": 2,
    "ocr_image_set_complete": true,
    "ocr_image_evidence": [
      {
        "image_index": 0,
        "status": "ok",
        "ocr_text": "老钱风西装",
        "ocr_confidence": 0.94,
        "image_sha256": "aaaaaaaaaaaaaaaa",
        "source_url_sha256": "1111111111111111",
        "error": ""
      },
      {
        "image_index": 1,
        "status": "ok",
        "ocr_text": "香水推荐",
        "ocr_confidence": 0.92,
        "image_sha256": "bbbbbbbbbbbbbbbb",
        "source_url_sha256": "2222222222222222",
        "error": ""
      }
    ],
    "source_board": "",
    "source_board_id": "",
    "source_lists": ["收藏", "点赞"],
    "source_primary": "收藏"
  },
  {
    "id": "694d3390000000002203ae33",
    "title": "听CASI考官详细拆解什么固定器角度适合你？",
    "target_board": "滑雪",
    "confidence": "high",
    "reason": ["核心内容是单板滑雪装备设置", "完整转写持续讨论固定器角度"],
    "review_state": "video_content_classified",
    "content_type": "video",
    "classification_basis": "video_content",
    "video_analysis_status": "success",
    "video_analysis_basis": "full_timeline_visual_with_transcript",
    "visual_status": "analyzed",
    "analysis_provider": "mimo-vl-mlx",
    "main_topic": "单板滑雪固定器角度设置",
    "content_summary": "解释固定器角度对站姿的影响，并建议按个人前后脚习惯调整。",
    "ocr_status": "skipped",
    "ocr_confidence": null,
    "ocr_text": "",
    "ocr_run_fingerprint": "",
    "ocr_image_count": 0,
    "ocr_image_set_complete": false,
    "ocr_image_evidence": [],
    "source_board": "杂项灵感",
    "source_board_id": "board-source-001",
    "source_lists": ["点赞"],
    "source_primary": "点赞"
  },
  {
    "id": "694d3390000000002203ae34",
    "title": "已在用户保留专辑中的图文笔记",
    "target_board": "",
    "confidence": "high",
    "reason": ["滑雪", "固定器"],
    "review_state": "classified",
    "ocr_status": "skipped",
    "ocr_confidence": null,
    "ocr_text": "",
    "ocr_run_fingerprint": "",
    "ocr_image_count": 0,
    "ocr_image_set_complete": false,
    "ocr_image_evidence": [],
    "excluded": true,
    "exclude_reason": "user_kept_existing_boards",
    "source_board": "滑雪"
  }
]
```

不要把直接 `d0` 交给已在其他专辑但缺少 `source_board_id` 的条目。该调用可能静默 no-op；即使返回 `{}` 也不能算成功，必须以 `U_` + `Ks` 中确实出现 note id 为唯一成功依据。

### `created_boards.json`
```json
{"confirmed":["穿搭发型与品味","滑雪"],"created":[],"missing":["体态纠正与康复"],"failed":[],"action_required":"Create missing boards manually in Xiaohongshu before running --execute."}
```

### `board_snapshot.json`

由 `capture_board_snapshot.py` 通过当前授权的小红书前端只读生成。`mode` 必须是 `read_only`、`source.writes_performed=false`、`validation.full_membership_complete=true`；每个专辑必须包含真实 id、声明数量、完整分页数量和 `note_ids`。任一分页未完成、数量不一致或成员重复都会阻止 dry-run。

### `run_report.json`

没有两份专辑证据时，报告必须是 `mode=classification_preview`、`ready_for_execute=false`、`missing_boards=null`，所有可分类项只能是 `preview_only`，不能是 `planned`。真实 dry-run 只有同时满足 `mode=dry_run`、`ready_for_execute=true`、`blockers=[]` 才可提交用户确认。未归档成功项应出现 `note_move:CALLED`、`verify:note_present`。跨专辑成功项应出现 `transaction:uncollect`、`transaction:recollect`、`transaction:move`、`transaction:target_verified`。跨专辑非安全失败会严格回滚到真实 `source_board_id`；回滚成功仍是失败。Python 每次只提交一条，首个错误行先写入报告再停止整批。安全验证或页面绑定失效后立即停写，不追加回滚写操作。

```json
{"started_at":"2026-04-17T01:17:03Z","mode":"execute","visible_count":11,"processed":[{"id":"69538be3000000001e028205","title":"《技能练反脚》不用从头练！4个技能直接出活","target_board":"滑雪","status":"success","attempt":1,"events":["board:FOUND:滑雪","note_move:CALLED","verify:note_present"],"error":"","verified":true}],"errors":[],"missing_boards":[],"board_counts_before":{"滑雪":76},"board_counts_after":{"滑雪":77}}
```

### `retry_queue.json`
```json
[{"id":"684bde220000000022004e7d","title":"怀疑自己走姿不对？建议你别只想着纠正走姿","target_board":"体态纠正与康复","reason":"target board not found","next_action":"retry_after_fixing_browser_or_board_state"}]
```
