# Yanshu Skill — bibliometric-fusion（研述同源文献计量技能）

一个与研述（Yize-Rd）agent **1.0.0 正式版对齐**的文献计量分析技能：
给定任意研究领域，用 OpenAlex 真实数据生成可复核的数据包（bundle.json +
审计 CSV）、丰富图表，以及被引最高且标题相关的**代表性文献**小节。
核心检索与统计代码与研述 agent 字节级同源。

## 能力

- **本地运行，不受平台推理时长限制**：命令在用户本机/自己的 Agent 里
  执行，没有对话平台的网关超时（如 120 秒截断）；大语料慢一点也只是慢，
  不会被掐断。
- **在用户自己的 Agent 中运算**：技能包按宿主 Agent 执行习惯编写，
  也支持纯命令行独立使用。
- **统计窗口远超对话场景**：默认 10 年，`--start/--end` 任意指定
  （如 2000–2025 的 25 年窗口），年度发文/被引逐年全量统计。
- **语料口径决策**（与 agent 同源）：OpenAlex Topic 候选先过整串短语
  门控（命中 ≥500 走短语口径，低命中才用 Topic 兜底）；复合低命中主题
  逐级放宽——机械分块组合（块间 AND）、单词词形变体、首块核心词，
  门槛与决策顺序与 agent 完全一致。
- **代表性文献**：语料内被引 Top N + 两档标题相关性过滤（连续子串 /
  词元级词根覆盖，宁缺毋滥），标题可点击直达原文（真实 DOI，零 LLM）。
- **文献计量全家桶**：年度发文与累计被引趋势、Top 作者/机构/来源/国家、
  Topic 分布、引用分布与语料 h-index、关键词频次与共现网络、合作地图。
- **多源核验**：Crossref / Semantic Scholar 年度命中数并列对照。
- **审计友好**：每个数字可由 bundle.json 与 CSV 复算；数据缺失标记
  `partial`，不用零值掩盖失败。

## 安装与配置

### 作为 ZCode 等编程智能体的插件或技能（推荐）

以ZCode为例：ZCode 客户端 → 设置 → 插件管理 → 发现页 → 右上角 **`+`** → 粘贴本仓库
地址 `https://github.com/lugoudu/Yanshu-skill-0824` → 安装
**Bibliometric Fusion**。安装后自动获得技能 `bibliometric-fusion` 与
`/bib` 命令。

也可将压缩包发送给你偏好的编程智能体，赋予其必要权限并要求其完成安装。

### 独立命令行使用

只需要 Python 3 与 `matplotlib`：

```bash
git clone https://github.com/lugoudu/Yanshu-skill-0824 && cd Yanshu-skill-0824
SKILL=plugin/skills/bibliometric-fusion
bash $SKILL/scripts/setup.sh           # 依赖检查
python3 $SKILL/scripts/fusion_run.py config   # 配置你自己的 OpenAlex API key
```

> **密钥声明**：本技能包不含、不内置任何共享 API key。请到
> <https://openalex.org/settings/api-key> 申请你自己的 key 并自行保管。

## 快速开始

agent 同款一步流程（口径决策 → 采集 → 代表性文献 → 图表）：

```bash
python3 $SKILL/scripts/fusion_run.py auto \
  --field "agentic reinforcement learning" --start 2023 --end 2025 \
  --charts --out ./out
```

只看口径决策（不采集）：

```bash
python3 $SKILL/scripts/fusion_run.py decide \
  --field "post-training large language models" --start 2023 --end 2025
```

从既有 bundle 提取代表性文献（零 API 消耗）：

```bash
python3 $SKILL/scripts/fusion_run.py representative --data ./out/bundle.json
```

全部命令：`config` / `info` / `resolve` / `decide` / `auto` / `fetch` /
`report` / `chart` / `representative` / `crosscheck` / `list-charts`。
用法细节见 [SKILL.md](plugin/skills/bibliometric-fusion/SKILL.md)。

## 测试

```bash
SKILL=plugin/skills/bibliometric-fusion
python3 $SKILL/tests/test_contract.py     # 契约与安全
python3 $SKILL/tests/test_offline.py      # 离线采集-成图回归
python3 $SKILL/tests/test_agent_align.py  # 口径决策与代表性文献（agent 对齐层）
python3 $SKILL/tests/test_cli_e2e.py      # CLI 端到端（本机回环，不访问公网）
```

## 与研述 agent 的关系

| | 研述 agent（服务器版） | 本技能 |
|---|---|---|
| 核心采集/统计包 | `app/bibliometric/` | 同一份代码（`scripts/bibliometric/`） |
| 口径决策 | 完整版（含 DeepSeek 同义词层与块内穷举扩词） | 确定性子集（门槛与顺序一致，LLM 层缺席） |
| 代表性文献 | 相同两档过滤 | 相同 |
| 多轮对话 / PDF 报告 | 有 | 无（专注单次分析任务） |

LLM 层只影响召回宽度，不影响口径正确性；差异边界详见
[references/agent-alignment.md](plugin/skills/bibliometric-fusion/references/agent-alignment.md)。

## 许可

[MIT](LICENSE) © 2026 Wang Nan
