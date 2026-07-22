# Spec: Quality Filtered API

## GET /api/daily-arxiv/quality-filtered

返回 `quality_filtered.json` 内容数组。

```json
{
  "success": true,
  "papers": [ ... ],
  "count": 2
}
```

## POST /api/daily-arxiv/quality-filtered/cleanup

请求体：
```json
{"arxiv_ids": ["2401.12345", "2401.67890"]}
{"all": true}
```

清理逻辑：
- 从 `quality_filtered.json` 查找每个 `arxiv_id` 对应的 `date` + `category`
- 删除 `{temp_dir}/{date}/{category}/{safe_id}.json`
- 删除 `{temp_dir}/{date}/{category}/{safe_id}.pdf`
- 删除 `{temp_dir}/{date}/{category}/{safe_id}_thumb.jpg`
- 从 `quality_filtered.json` 移除对应条目
- 如果论文也在阅读列表中，保留阅读列表条目（仅删除文件）

返回：
```json
{
  "success": true,
  "removed": 2,
  "remaining": 0
}
```
