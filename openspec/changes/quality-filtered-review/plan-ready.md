# 实现计划：质量过滤结果查看与人工清理

## 来源
- 提案：openspec/changes/quality-filtered-review/proposal.md
- 设计：openspec/changes/quality-filtered-review/design.md
- 规格：openspec/changes/quality-filtered-review/specs/
- 任务：openspec/changes/quality-filtered-review/tasks.md

## 实现步骤

### Task 1: 后端 — 修改 Batch Summary 增加 affiliations 抽取 + 质量过滤

**改动文件：**
- `resophy/routes/basic_routes/daily_arxiv_route.py:225-333`

**逻辑：**
在 `api_generate_daily_arxiv_summary` 的 batch 循环中，对每篇论文：
1. 已有逻辑：`_generate_for(paper_dict)` 补摘要
2. 新增逻辑：检查 `affiliations_extracted` 为 False 且 `local_pdf_path` 存在 → 调用 `extract_affiliations_with_llm` + `extract_pdf_first_page_text`
3. 抽取成功后：调用 `should_keep_paper_by_institution_tier(paper_dict, qualityConfig)` 判断
4. 未通过：追加写入 `quality_filtered.json`（以 `{papers_dir}/.daily_arxiv_temp/quality_filtered.json` 计算，通过 `manager.settings` 获取路径）

**quality_filtered.json 写操作：**
- 读取现有文件（如不存在则返回 `[]`）
- 检查新条目的 `arxiv_id` 是否已存在（去重）
- 追加后回写
- 每个条目的字段：`arxiv_id`, `title`, `affiliations`, `countries`, `category`, `date`, `reason`, `filtered_at`

**返回体增加字段：**
```python
"affiliations_extracted": int,
"affiliations_skipped": int,   # already had affiliations
"affiliations_failed": int,    # extraction error or no PDF
"quality_filtered": int        # count of papers failing quality gate
```

### Task 2: 后端 — 新增 quality-filtered 端点

**改动文件：**
- `resophy/routes/basic_routes/daily_arxiv_route.py`

**新增 GET `/api/daily-arxiv/quality-filtered`:**
```python
def api_get_quality_filtered():
    qf_path = os.path.join(temp_papers_dir, "quality_filtered.json")
    papers = []
    if os.path.exists(qf_path):
        with open(qf_path, "r") as f:
            papers = json.load(f)
    return jsonify({"success": True, "papers": papers, "count": len(papers)})
```

**新增 POST `/api/daily-arxiv/quality-filtered/cleanup`:**
```python
def api_cleanup_quality_filtered():
    data = request.json or {}
    arxiv_ids = data.get("arxiv_ids", [])
    all_flag = data.get("all", False)
    
    # 读取 quality_filtered.json
    qf_path = os.path.join(temp_papers_dir, "quality_filtered.json")
    if not os.path.exists(qf_path):
        return jsonify({"success": True, "removed": 0, "remaining": 0})
    
    with open(qf_path, "r") as f:
        papers = json.load(f)
    
    if all_flag:
        to_remove = papers[:]
    else:
        to_remove = [p for p in papers if p.get("arxiv_id") in arxiv_ids]
    
    # 删除文件
    for p in to_remove:
        safe_id = p["arxiv_id"].replace("/", "_").replace(":", "_")
        cat_dir = os.path.join(temp_papers_dir, p["date"], p["category"])
        for ext in [".json", ".pdf", "_thumb.jpg"]:
            fpath = os.path.join(cat_dir, f"{safe_id}{ext}")
            if os.path.exists(fpath):
                os.remove(fpath)
    
    # 移除条目
    removed_ids = {p["arxiv_id"] for p in to_remove}
    remaining = [p for p in papers if p["arxiv_id"] not in removed_ids]
    with open(qf_path, "w") as f:
        json.dump(remaining, f, ensure_ascii=False, indent=2)
    
    return jsonify({
        "success": True,
        "removed": len(to_remove),
        "remaining": len(remaining)
    })
```

**验证方式：**
```bash
curl -s http://127.0.0.1:7890/api/daily-arxiv/quality-filtered | python -m json.tool
```

### Task 3: 前端 — 新增按钮 + 模态框 + 交互逻辑

**改动文件：**
- `templates/index.html:192`
- `static/js/app.js`

**HTML (`index.html`):**
在 `#daily-arxiv-batch-summary` 按钮旁新增：
```html
<button class="btn btn-outline" id="daily-arxiv-quality-filtered"
        style="display:none;" title="View quality-filtered papers">
  <i class="fas fa-shield-alt"></i> Filtered <span class="badge" id="daily-arxiv-filtered-count">0</span>
</button>
```

**JS (`app.js`):**
- 修改 `onDailyArxivBatchSummary()`: 在成功后检查 `data.quality_filtered > 0` → 显示按钮 + 设置 badge
- 新增 `showFilteredButton(count)`: 显示按钮 + 更新计数
- 新增 `onShowQualityFiltered()`: fetch GET quality-filtered → 弹模态框展示论文列表（标题、机构、分类、原因、checkbox）
- 新增 `onCleanupQualityFiltered(arxivIds)`: fetch POST cleanup → 刷新页面

**模态框设计：**
```html
<div class="modal-header">
  <h5 class="modal-title">Quality Filtered Papers</h5>
  <button type="button" class="close" data-dismiss="modal">&times;</button>
</div>
<div class="modal-body">
  <table>
    <tr><th><input type="checkbox" id="select-all"></th><th>Title</th><th>Affiliations</th><th>Reason</th></tr>
    <!-- foreach papers -->
  </table>
</div>
<div class="modal-footer">
  <button class="btn btn-danger" onclick="onCleanupQualityFiltered()">Delete selected</button>
  <button class="btn btn-danger" onclick="onCleanupQualityFiltered('all')">Delete all</button>
</div>
```

**验证方式：** 打开浏览器，点击 Batch Summary → 如返回 quality_filtered > 0 → Filtered 按钮显示 → 点击查看列表
