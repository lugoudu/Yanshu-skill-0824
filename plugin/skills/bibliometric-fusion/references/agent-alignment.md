# Agent 对齐层（agent_align.py）

本 skill 的 `scripts/agent_align.py` 是研述 agent（1.0.0）检索能力的
**确定性移植层**：与 agent 编排层同源的语料口径决策与代表性文献过滤，
剥除 LLM 依赖。门槛常量、决策顺序、过滤规则与 agent 完全一致；本文
同时说明与 agent 的差异边界。

## 语料口径决策（`resolve_corpus_scope`）

决策顺序（前者语义最准，命中不足才逐级放宽）：

```
Topic 门控   OpenAlex Topic 候选先过整串短语命中门控：
             整串短语 ≥500 → 放弃候选，短语口径（标准词零打扰）
             整串短语 <500（含探测失败）→ 采纳首个 Topic 候选兜底
L1           整串短语（标题+摘要）≥500 → 采用
L2           机械分块组合：按连接词（in/for/with/based/driven 等，
             of/to 受保护）切块，块间 AND、块内 OR
             ≥500 且 ≥10× L1 → 采用
词形          单词主题并入确定性词形变体（-ism→-ic/-istic、复数 +s/+es、
             英美拼写、连字符/空格互变），组合命中提升即并入
L3           首块核心词：≥max(10×L1, 1000) → 放宽为核心领域词，
             说明中明示「领域级口径」
全不满足      → 原样整串短语（小语料提醒由展示层按 total_works 触发）
```

门槛常量：`WATERFALL_MIN_HITS=500`、`WATERFALL_GAIN=10`、
`WATERFALL_CORE_FLOOR=1000`。

### Topic 门控的由来（agent v1.0.0 实测教训）

OpenAlex Topic 消歧粒度粗，首个候选可能整体跑偏：实测
"agentic reinforcement learning" 被映到「Reinforcement Learning in
Robotics」（机器人强化学习大类），整份语料、综述与图表口径全部偏到
机器人 RL。因此短语命中健康（≥500）时放弃候选走短语口径；Topic 仅作
低命中兜底。探测失败（counts 记 -1）时维持旧行为（采纳首个候选），
不给门控引入新故障面。

### 与 agent 的差异（LLM 层缺席）

agent 在上述确定性层之上还有两层需要 DeepSeek 的能力：

- **S 层（LLM 同义词并集）**：低命中时由 LLM 生成同义表述，经词根
  零幻觉校验后并入全局 OR（≥2× 即采用）；
- **C 层宽档（块内穷举扩词）**：每个概念块由 LLM 生成等价表述
  （同义词、领域缩写、变体），逐一经 OpenAlex 实测命中校验后进块内
  OR，构成 WoS 式 `TS=(A OR B) AND (C OR D)`。

两者只影响**召回宽度**，不影响口径正确性（块间 AND 兜底）；本 skill
的确定性子集在绝大多数标准词与复合主题上与 agent 决策一致。需要
完整行为时请使用研述 agent 本体。

## 代表性文献（`representative_works`）

从 bundle 的 `audit.high_cited_works`（与语料同口径、被引降序 Top 200）
中选标题相关的前 N 篇（默认 5），拼成 Markdown 小节：

- **两档相关性判定，任一命中即保留**：
  1. 标题包含检索词/语块/词形的连续子串；
  2. 词元级词根覆盖——标题实词按词根规则（相等或一方是另一方前缀，
     双方 ≥4 字符：agentic/agent、alloy/alloys）逐一覆盖检索词的全部
     实词，连接词（of/in/for 等）不参与。
- OpenAlex 短语检索带词干归并（agentic↔agent），语料被引头部可能是
  multi-agent 类标题——只认连续子串则新兴主题必然整节滤空（实测该词
  被引 Top500 无一标题含字面连续短语），词根档因此必需。
- **宁缺毋滥**：过滤后为空返回空串，不输出噪声；无 DOI 条目跳过。
- 链接全部来自真实 DOI 字段（裸 DOI 自动补 `https://doi.org/` 前缀），
  零 LLM——模型生成的文献链接会凭空编造 DOI。

## 设计红线（与 agent 一致）

- OpenAlex 顶层 `search` 是全文分词 AND（非短语），热门词数倍过召；
  短语匹配一律用 `title_and_abstract.search:"..."`，多短语 AND 用重复
  filter 键，同义 OR 用值内 `|`。
- `group_by` 请求绝不带 per_page（分组会被截断）。
- 年度被引是「该年论文截至快照日的累计被引」，不是自然年度引用。
- 决策只依赖命中数（数量级差异），OpenAlex 日度快照漂移不改变决策。
