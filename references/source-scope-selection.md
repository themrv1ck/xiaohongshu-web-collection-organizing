# 收藏 / 点赞来源范围选择与合并

适用：用户要求整理小红书收藏、点赞，或说“我全都要 / 全部 / 都要”时。

## 交互口径

启动整理前先问：

> 这次按收藏整理、按点赞整理，还是我全都要？

- 用户回答“收藏”：只抓收藏页，来源标记 `collection` / `收藏`。
- 用户回答“点赞”：只抓点赞页，来源标记 `liked` / `点赞`。
- 用户回答“我全都要 / 全部 / 都要”：先抓收藏，再抓点赞，并按 note id 合并去重。

如果用户本轮已经明确给出范围，可以直接执行，不必重复问。

## 命令范式

只整理收藏：

```bash
python3 scripts/extract_visible_items.py visible_items.json --source collection
```

只整理点赞：

```bash
python3 scripts/extract_visible_items.py visible_items.json --source liked
```

收藏 + 点赞一起整理：

```bash
# 先打开收藏页
python3 scripts/extract_visible_items.py visible_items.json --source collection

# 再打开点赞页，追加合并
python3 scripts/extract_visible_items.py visible_items.json --source liked --append-existing
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

## 安全边界

- 选择点赞时不得取消点赞、删除互动记录或把点赞来源静默丢弃。
- “我全都要”不是执行两套独立移动；必须合并去重后再进入 OCR、分类、dry-run、确认、execute 链路。
- 真实移动仍需用户确认分类、目标专辑和风险后才可传 `--execute`。

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
