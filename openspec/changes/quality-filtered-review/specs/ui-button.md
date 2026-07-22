# Spec: UI — quality_filtered 按钮

## HTML 改动

`templates/index.html:192` 附近：

```html
<button class="btn btn-outline" id="daily-arxiv-batch-summary" ...>Batch summary</button>
<button class="btn btn-outline" id="daily-arxiv-quality-filtered"
        style="display:none;" title="View quality-filtered papers">
  <i class="fas fa-shield-alt"></i> Filtered <span class="badge" id="daily-arxiv-filtered-count">0</span>
</button>
```

## JS 改动

### `onDailyArxivBatchSummary` 修改

```js
// 成功后
if (data.quality_filtered > 0) {
    showMessage(`${data.quality_filtered} papers filtered by quality, click 'Filtered' to review`, 'warning');
    showFilteredButton(data.quality_filtered);
}
```

### 新增函数

- `showFilteredButton(count)` — 显示按钮 + 设置 badge 计数
- `onShowQualityFiltered()` — fetch GET /api/daily-arxiv/quality-filtered → 弹出模态框
- `onCleanupQualityFiltered(arxivIds)` — fetch POST 清理 + 刷新

### 模态框

列表展示被过滤论文，列：标题、机构、分类、原因。每行带 checkbox，底部 "Delete selected" / "Delete all" 按钮。
