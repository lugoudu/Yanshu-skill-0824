# Bundle schema v1

`fetch` 和 `report` 写出 `bundle.json`。渲染器只消费该文件，因此同一数据可离线重复出图。

## 顶层字段

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `schema_version` | string | 当前为 `1.0` |
| `source` | string | 主报告固定 `openalex` |
| `snapshot_date` | ISO date | 数据快照/生成日期 |
| `provenance` | object | `kind`、`fixture`、provider、collector；离线夹具必须显式标记 |
| `warnings` | array | 对结果解释有影响、但不等同于抓取失败的警告 |
| `query` | object | field、topic_id、年份、Topic/全文模式 |
| `total_works` | integer | 当前年份窗口内作品总量 |
| `annual` | object | 年份到发文、累计被引、篇均、估计状态、n、CI |
| `rankings` | object | institutions/authors/sources/countries/keywords/topics |
| `impact` | object | 随机引用样本摘要与独立 h-index 结果 |
| `cooccurrence` | object | 样本文献数、节点、边、剔除泛词 |
| `audit` | object | trend、random analysis、high cited 三类文献明细 |
| `methodology` | object | 各指标计算方法的机器可读说明 |
| `partial` | boolean | 任一可选数据维度失败即为 true |
| `failed_sections` | array | section + 已脱敏错误信息 |

正式 API 抓取写入 `provenance.kind=live_api`、`fixture=false`。测试或演示数据必须写入
`kind=offline_fixture`、`fixture=true`，渲染器会在图面和脚注明示，不得把夹具图当作当前实证结果。

`rankings.countries` 每行包含 `country_code`、`name_zh`、`name_en`。`name` 使用合规英文默认值；
图表按语言读取 `name_zh` 或 `name_en`。港澳台对应值固定为：

| code | `name_zh` | `name_en` |
| --- | --- | --- |
| `HK` | 中国香港 | Hong Kong, China |
| `MO` | 中国澳门 | Macao, China |
| `TW` | 中国台湾 | Taiwan, China |

`ranking_countries.csv` 同时输出 `name_zh` 与 `name_en`，避免无语言上下文时产生歧义。

## `annual.<year>`

```json
{
  "publications": 1200,
  "cumulative_citations": 32000,
  "cumulative_citations_per_work": 26.67,
  "citations_estimated": true,
  "sample_size": 1000,
  "population_size": 1200,
  "cumulative_citations_ci95": 4100
}
```

请求失败时受影响的值为 `null`，同时出现在 `failed_sections`；绝不使用 0 冒充缺失。

## `impact`

- `citation_counts`、mean、median、P90、uncited share：只来自随机样本。
- `h_index_status`：`exact_for_filtered_corpus`、`lower_bound` 或 `unavailable`。
- `h_index_records_checked`：被引降序集合实际检查条数。

## 兼容规则

渲染器遇到未知 `schema_version` 必须拒绝运行。新增可选字段可以保持 1.x；删除字段、修改语义或改变类型时升级主版本。
