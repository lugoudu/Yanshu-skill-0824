# 更新日志

版本号与研述 agent 对齐。日志按时间倒序排列。

---

## [1.0.0] - 2026-08-24

首个公开发布版本，与研述 agent 1.0.0 正式版对齐。

### 核心

- **语料口径决策**（`scripts/agent_align.py`，与 agent 同源的确定性子集）：
  OpenAlex Topic 候选先过整串短语门控（≥500 走短语口径，低命中才用
  Topic 兜底——agent 实测教训："agentic reinforcement learning" 曾被
  消歧到「机器人强化学习」大类导致整份语料跑偏）；复合低命中主题
  逐级放宽：机械分块组合（块间 AND）/ 单词词形变体 / 首块核心词。
- **代表性文献提取**：被引 Top N + 两档标题相关性过滤（连续子串 +
  词元级词根覆盖），宁缺毋滥；链接来自真实 DOI 字段，零 LLM。

### CLI

- 新增 `decide`（只决策）、`auto`（决策+采集+代表性文献一步完成）、
  `representative`（从既有 bundle 提取，零 API 消耗）。
- 保留核心包全部命令：`config` / `info` / `resolve` / `fetch` /
  `report` / `chart` / `crosscheck` / `list-charts`。

### 与 agent 的差异边界

agent 另有需 DeepSeek 的同义词层（S）与块内穷举扩词（C 层宽档），
只影响召回宽度，不影响口径正确性。详见
`references/agent-alignment.md`。
