# Proposal: 质量过滤结果查看与人工清理

## 背景

无 LLM 抓取 arXiv 时，质量过滤因无法抽取机构信息而退化为透传（`allowUnknownInstitutions=true`）。等 LLM 可用后，用户可通过 Batch Summary 补摘要和机构信息。此时应同步执行质量过滤，将未通过论文记录到本地文件，并提供按钮查看和人工清理。

## 需求

1. Batch Summary 点击时一并抽取 affiliations 并执行质量过滤，将未通过论文持久化记录
2. 新增按钮「Filtered」展示被过滤论文列表（标题、机构、原因）
3. 支持人工选择清理被过滤论文（删除对应 JSON/PDF/缩略图）
