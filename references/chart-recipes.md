# 图表配方

全部渲染器只读取 `bundle.json`。统一使用 200 DPI、白底、语义色、中文字体探测与英文回退，并在底部保留来源、快照、检索模式和方法学脚注。

## 视觉令牌

- 发文：蓝 `primary`
- 累计被引：橙 `accent`
- 洞察/篇均：紫 `secondary`
- 排行：蓝色主体，Top 1 橙、Top 2 紫、Top 3 绿
- 分类与网络节点：色盲友好 `palette`
- 网格：浅灰虚线；移除上/右边框

样式文件：`assets/chart_style.json`。局部覆盖时深度合并，不要求用户重复全部键。

## 图表与数据字段

| 图表 | Bundle 字段 | 强制说明 |
| --- | --- | --- |
| `annual-trends` | `annual.*.publications` | 精确年度聚合；末年可能有索引滞后 |
| `pub-citations` | `annual` 全部指标 | 被引截至快照日；估计年份显示 CI |
| Top 排行 | `rankings.*` | 计数口径；国家/机构为 full counting |
| `topic-distribution` | `rankings.topics`, `total_works` | Top 8 + 全语料余项 |
| `citation-impact` | `impact` | 分布/均值随机样本；h-index 精确或下界 |
| `keyword-frequency` | `rankings.keywords` | OpenAlex 自动分配的多标签主题词，不称作者关键词 |
| `cooccurrence` | `cooccurrence` | 随机样本 n、固定 seed、泛词过滤 |

## 布局

- 年度快图：柱状 + 3 年移动平均线，逐柱紧凑数字标签。
- 累计被引图：上方双轴发文/累计被引及 CI，下方累计篇均被引。
- 排行：动态高度横向条形，最大项在顶部，CSV 保留完整名称。
- 国家/地区排行：中文图强制使用“中国香港”“中国澳门”“中国台湾”；英文图强制使用“Hong Kong, China”“Macao, China”“Taiwan, China”。该规则由国家代码与名称别名双重归一化，禁止依赖数据源原始显示名。
- Topic：宽度 0.42 的甜甜圈，低于 4% 不在环内显示文字，外置图例。
- 引用画像：左侧对数式区间直方图，右侧五张指标卡。
- 共现：内置确定性 Top 12 节点/Top 18 边稀疏圆形布局；节点面积和边宽均显式说明，跨机器保持一致。

缺少必要字段时返回结构化失败；不得生成空白图。
