# Design: 质量过滤结果查看与人工清理

## 架构决策

- 过滤结果存储为 `{papers_dir}/.daily_arxiv_temp/quality_filtered.json`
- Batch Summary 后端循环中追加写，按 arxiv_id 去重
- 后端新增 2 个端点，前端新增 1 个按钮 + 模态框
- 清理仅删除文件系统上的 JSON/PDF/缩略图，不操作数据库或阅读列表

## API 设计

### 修改 `POST /api/daily-arxiv/generate-summary`

batch 模式下增加 affiliations 抽取 + 质量过滤步骤，返回增加字段。

### 新增 `GET /api/daily-arxiv/quality-filtered`

返回 quality_filtered.json 内容数组。

### 新增 `POST /api/daily-arxiv/quality-filtered/cleanup`

接收 `{arxiv_ids: ["id1", "id2"]}` 或 `{all: true}`，删除对应文件并从 quality_filtered.json 移除条目。

## 数据格式

```json
[
  {
    "arxiv_id": "2401.12345",
    "title": "Paper Title",
    "affiliations": ["Some Univ"],
    "countries": ["CN"],
    "category": "cs.CV",
    "date": "2026-07-22",
    "reason": "institution tier C below minimum B",
    "filtered_at": "2026-07-22T10:30:00"
  }
]
```

## 前端设计

Batch Summary 按钮 (`index.html:192`) 右侧新增 `quality_filtered` 按钮，初始隐藏。点击后打开模态框列出被过滤论文，每篇带 checkbox，底部有清理按钮。
