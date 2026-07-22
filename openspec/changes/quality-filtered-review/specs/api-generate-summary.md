# Spec: POST /api/daily-arxiv/generate-summary 修改

## 改动

batch 模式 (`onlyMissing: true`) 循环中增加两步：

1. **Affiliations 抽取**：`paper_dict.get("affiliations_extracted")` 为 False 且 `local_pdf_path` 存在时，调用 `extract_affiliations_with_llm` 抽取并持久化
2. **质量过滤**：抽取成功后在内存中调用 `should_keep_paper_by_institution_tier(paper_dict, qualityConfig)`，未通过则追加到 `quality_filtered.json`

## 返回体增加字段

```json
{
  "affiliations_extracted": 8,
  "affiliations_skipped": 4,
  "affiliations_failed": 1,
  "quality_filtered": 2
}
```

## quality_filtered.json 位置

`{papers_dir}/.daily_arxiv_temp/quality_filtered.json`

写入规则：追加（不覆盖），同一 `arxiv_id` 去重。
