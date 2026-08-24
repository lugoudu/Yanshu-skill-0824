# 多数据源策略

## 角色分工

| 数据源 | 融合版角色 | 允许用途 | 禁止用途 |
| --- | --- | --- | --- |
| OpenAlex | 唯一主分析源 | Topic、年度、排行、引用、关键词、审计文献 | 与其他源静默混算 |
| Crossref | 文本检索命中数核验 | 按年 `total-results` 并列比较 | 把截断结果当 OpenAlex 等价总体 |
| Semantic Scholar | 可选文本检索命中数核验 | 有 key 时按年并列比较 | 用匿名限速结果替换主报告 |

`crosscheck` 每年单独查询，各源结果写到独立列。OpenAlex 可以使用 Topic 过滤；Crossref 与 Semantic Scholar 使用文本检索，因此差异既可能来自覆盖，也可能来自检索口径。

## 命令

```bash
python3 "$SKILL_DIR/scripts/fusion.py" crosscheck \
  --field "deep learning" --start 2020 --end 2025 \
  --providers crossref --out ./fusion_output
```

Semantic Scholar key 优先放入 `SEMANTIC_SCHOLAR_API_KEY`。输出 `source_crosscheck.json/csv`，`partial=true` 表示至少一个外部源失败。

## 解读模板

使用“不同数据库在相同文本词和年份条件下的检索命中数”表述，不使用“精确复现”“证明主数据正确”或“合并后的总文献量”。
