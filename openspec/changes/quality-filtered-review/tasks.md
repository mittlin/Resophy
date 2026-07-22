# Tasks: 质量过滤结果查看与人工清理

## Task 1: 后端 — 修改 Batch Summary 增加 affiliations 抽取 + 质量过滤

修改 `resophy/routes/basic_routes/daily_arxiv_route.py` 中 `api_generate_daily_arxiv_summary`。

- batch 循环中补摘要后，检查是否需要抽 affiliations
- 调用 `extract_affiliations_with_llm` 从 PDF 抽取机构
- 调用 `should_keep_paper_by_institution_tier` 判断质量
- 未通过则写入 `quality_filtered.json`
- 返回增加 `affiliations_extracted` / `affiliations_skipped` / `affiliations_failed` / `quality_filtered` 字段

## Task 2: 后端 — 新增 quality-filtered 端点

- `GET /api/daily-arxiv/quality-filtered` — 返回过滤列表
- `POST /api/daily-arxiv/quality-filtered/cleanup` — 接收 arxiv_ids / `{all: true}`，删除文件 + 更新 JSON

## Task 3: 前端 — 新增按钮 + 模态框 + 交互逻辑

- `templates/index.html:192` 旁新增 quality_filtered 按钮
- `static/js/app.js` 修改 `onDailyArxivBatchSummary` 显示过滤计数
- 新增 `onShowQualityFiltered` 展示模态框（checkbox + 清理按钮）
- 新增 `onCleanupQualityFiltered` 调用清理 API
