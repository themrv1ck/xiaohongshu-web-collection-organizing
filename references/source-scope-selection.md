# 收藏 / 点赞来源范围选择与合并

适用：用户要求整理小红书收藏、点赞，或说“我全都要 / 全部 / 都要”时。

## 交互口径

启动整理前先显示：

> **欢迎使用小红书收藏整理 Skill**
>
> 这个 Skill 可以读取你选择的收藏/点赞内容，根据笔记实际内容生成专辑分类建议；图文可选择 OCR 逐张读取封面和全部内页图片中的可见文字，视频可选择结合语音和完整时轴画面。没有文字的纯画面不属于 OCR。它会先提供分类结果和模拟执行报告，只有再次得到你的明确授权后才会移动笔记。
>
> **首次使用，请在下方回复本次整理范围：**
>
> 1. 回复“收藏”：只整理收藏列表；
> 2. 回复“点赞”：只整理点赞列表；
> 3. 回复“我全都要”：合并收藏和点赞，并按笔记去重。

- 用户回答“收藏”：只抓收藏页，来源标记 `collection` / `收藏`。
- 用户回答“点赞”：只抓点赞页，来源标记 `liked` / `点赞`。
- 用户回答“我全都要 / 全部 / 都要”：先抓收藏，再抓点赞，并按 note id 合并去重。

如果用户本轮已经明确给出范围，可以跳过范围卡片，但随后仍要自动运行本地能力只读预检，再显示“快速整理 / 轻度整理 / 深度整理”档位。只有用户回复“自定义”时，才依次取得图文 OCR 和视频内容分类两个开关的回答；档位或两项开关确定后，才能请求具体浏览器授权并开始完整环境检查或抓取。

## 命令范式

除不需要浏览器授权的 `check_environment.py --capability-preflight` 外，以下命令只能在范围和快速启动档位（或自定义的图文 OCR、视频内容分类两个开关）都已确认，并且用户在当前回合明确授权具体浏览器后运行。

只整理收藏：

```bash
python3 scripts/extract_visible_items.py segment-001-collection.json --backend macos-arc --source collection \
  --capture-mode passive --segment-limit 200 \
  --arc-window-id '<window-id>' --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' --arc-expected-url-substring '<expected-path>'
```

只整理点赞：

```bash
python3 scripts/extract_visible_items.py segment-001-liked.json --backend macos-arc --source liked \
  --capture-mode passive --segment-limit 200 \
  --arc-window-id '<window-id>' --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' --arc-expected-url-substring '<expected-path>'
```

收藏 + 点赞一起整理：

```bash
# 先打开收藏页；每段独立保存，用户手动滚动后再开始下一段
python3 scripts/extract_visible_items.py segment-001-collection.json --backend macos-arc --source collection \
  --capture-mode passive --segment-limit 200 \
  --arc-window-id '<window-id>' --arc-tab-id '<tab-id>' \
  --arc-tab-marker '<window-name-marker>' --arc-expected-url-substring '<expected-path>'

# 再打开点赞页，按同样的被动分段采集；所有段仅在本地按 note id 合并
```

## 数据契约

`visible_items.json`、`classification.json`、`run_report.json` 都应保留：

```json
{
  "source_lists": ["收藏", "点赞"],
  "source_primary": "收藏"
}
```

- `source_lists`：该笔记出现过的来源列表。
- `source_primary`：第一次抓到该笔记的来源。
- 同一 note id 在收藏和点赞中都出现时，不重复分类/移动，只合并来源。
- 列表页的 `content_type` 和 `image_urls` 都只是 observed 线索，图片必须保持 `image_urls_complete=false`；只有详情 `noteData.type` 可确定权威类型，只有详情 `noteData.imageList` 可形成完整图片集合。

## 安全边界

- 选择点赞时不得取消点赞、删除互动记录或把点赞来源静默丢弃。
- “我全都要”不是执行两套独立移动；必须合并去重后再进入 `enrich_note_images.py -> ocr_note_images.py`、分类、dry-run、确认、execute 链路。
- 详情补齐遇到 `security_blocked` 时必须落盘状态和 `xhs_safety_state.json`、停止后续请求并以非零退出码结束，不得继续 OCR 或 `--resume`。
- 真实移动仍需用户确认分类、目标专辑和风险后才可传 `--execute --max-moves-per-session <1–200>`；达到上限后不得自动续段。

## 回归验证

修改这类能力后至少跑：

```bash
python3 -m compileall -q .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/classify_items.py --skip-ocr examples/visible_items.example.json /tmp/xhs_classification_source_smoke.json
python3 scripts/run_reassign_batch.py /tmp/xhs_classification_source_smoke.json /tmp/xhs_run_report_source_smoke.json
```

检查点：

- 单元测试包含来源合并/去重覆盖。
- `classification.json` 透传 `source_lists` / `source_primary`。
- `run_report.json` 透传 `source_lists` / `source_primary`。
