---
name: bibliometric-fusion
description: 使用 OpenAlex 为任意研究领域生成可复核的文献计量数据包、CSV 与丰富可视化，包括年度发文和累计被引趋势、Top 作者/机构/来源/国家、Topic 甜甜圈、随机样本引用分布与语料 h-index、OpenAlex 主题词频次及共现网络；内置与研述 agent 1.0.0 同源的语料口径决策（Topic 门控 + 多级检索瀑布）与代表性文献提取（被引 Top + 两档标题相关性过滤）；支持 Crossref 和 Semantic Scholar 的独立年度命中数核验。用户提到文献计量、bibliometrics、发文量、被引趋势、科研产出、Topic、研究热点、关键词共现、h-index、Top 作者/机构/来源、学科态势、检索式决策、代表性文献或需要可溯源图表时使用。
metadata:
  version: 1.0.0
  author: Yize-Rd Team
  triggers:
    - 文献计量 / bibliometrics / scientometrics 分析请求
    - 年度发文量与累计被引趋势统计
    - Top 作者 / 机构 / 来源 / 国家排行
    - Topic 分布、关键词词频与共现网络
    - h-index、引用分布、多源命中数核验
    - 检索式/语料口径决策（Topic 还是短语、分块还是整串）
    - 代表性文献提取（高被引且标题相关，DOI 直链）
    - 需要可溯源图表或数据包的科研产出分析
---

# 文献计量融合（研述同源版）

以 OpenAlex 当前契约、统计方法、密钥安全和审计 CSV 为底座的文献计量分析技能，
与研述（Yize-Rd）agent 共用同一份核心代码（`scripts/bibliometric/` 包），
由仓库的同步脚本保持字节级一致。v1.0.0 起内置 agent 对齐层
（`scripts/agent_align.py`）：语料口径决策（Topic 门控 + 确定性瀑布）与
代表性文献提取，与 agent 1.0.0 同源（LLM 扩词层除外，见
`references/agent-alignment.md`）。只使用真实 API 数据；数据缺失时标记
`partial`，不得用零值或占位数据掩盖失败。

## 技能包结构

```text
bibliometric-fusion/
├── SKILL.md            # 技能入口：YAML 元数据 + 执行指令
├── scripts/
│   ├── fusion_run.py   # CLI 入口（所有命令都通过它调用）
│   ├── agent_align.py  # agent 1.0.0 对齐层：口径决策 + 代表性文献
│   ├── bibliometric/   # 核心包：与 agent 同源，勿直接修改
│   └── setup.sh        # 依赖检查
├── references/         # 参考文档（方法学、数据契约、绘图规则、多源边界、字段 schema、agent 对齐说明）
├── assets/examples/    # 离线样例图（OFFLINE FIXTURE 水印）
└── tests/              # 契约测试、离线测试、CLI 端到端测试、对齐层测试
```

## 入口与环境

始终先解析本 Skill 的绝对路径，不要依赖当前工作目录：

```bash
SKILL_DIR="<SKILL.md 所在目录的绝对路径>"
python3 "$SKILL_DIR/scripts/fusion_run.py" info
```

需要 Python 3 和 `matplotlib`。共现图使用内置确定性稀疏圆形布局，保证不同机器输出一致。运行 `bash "$SKILL_DIR/scripts/setup.sh"` 检查依赖。

## 安全配置 OpenAlex API key

> **声明**：本技能包不含、不内置、也不提供任何共享 OpenAlex API key。运行所需的 key 由使用者本人向 OpenAlex 申请并自行保管；skill 制作者的 key 不会被任何用户使用。请使用你自己的 key。

### 1. 申请你自己的 key

前往 OpenAlex 官方设置页申请 API key：**https://openalex.org/settings/api-key** 。登录后生成并复制 key，它绑定你的个人/机构账户，用于识别调用方并计入你的额度。请妥善保管，不要分享或提交进版本库。

### 2. 配置 key 到本机

日常使用交互配置，输入不回显且不进入 shell history：

```bash
python3 "$SKILL_DIR/scripts/fusion_run.py" config
python3 "$SKILL_DIR/scripts/fusion_run.py" config --show
```

配置流程先发起低成本验证请求，再以临时文件、`fsync`、权限 `600` 和 `os.replace` 原子写入用户配置目录（技能包之外）；坏 key 不覆盖旧配置。也可用环境变量 `OPENALEX_API_KEY`。`--api-key` 仅供受控 CI，日常不要使用，以免进入命令历史或进程列表。

## 标准工作流

### 1. agent 同款一步流程（推荐）

```bash
python3 "$SKILL_DIR/scripts/fusion_run.py" auto \
  --field "agentic reinforcement learning" --start 2023 --end 2025 \
  --charts --out ./fusion_output
```

`auto` 复刻 agent 1.0.0 的行为：先做语料口径决策（Topic 候选过整串短语
门控，命中健康走短语口径，低命中才用 Topic 兜底；复合低命中主题逐级
放宽到分块组合/词形/核心词），再按决策采集 bundle、输出代表性文献小节
（`representative_works.md`）。检查 stdout JSON 的 `decision`（口径与各层
命中数）与 `summary.representative_works`（小节为空＝宁缺毋滥，不是缺数据）。

只看口径不采集用 `decide`；从既有 bundle 提取小节用
`representative --data bundle.json`（不消耗 API）。

### 2. 手动控制口径（精细场景）

```bash
python3 "$SKILL_DIR/scripts/fusion_run.py" resolve --field "deep learning"
```

检查候选名称、Field、Domain 和作品量。不要静默采用首个候选——agent 的
教训是消歧粒度粗（复合词常被映到偏门大类）。确认后传 `--topic-id`；
不传时使用短语检索口径，并在交付中说明。

### 3. 一步生成完整报告

```bash
python3 "$SKILL_DIR/scripts/fusion_run.py" report \
  --field "deep learning" --topic-id T10320 \
  --start 2016 --end 2025 --out ./fusion_output
```

该命令生成 `bundle.json`、审计 CSV 和全部可用图表。检查 stdout JSON：

- `partial=false`：请求的核心维度完整。
- `partial=true` 或退出码 `3`：读取 `failed_sections` / `chart_failures`，不要把缺失项解释为零。
- 退出码 `2`：参数、认证或核心请求失败，不得继续解读旧输出。

### 4. 抓一次、离线反复调图

```bash
python3 "$SKILL_DIR/scripts/fusion_run.py" fetch \
  --field "quantum computing" --topic-id T10682 \
  --start 2016 --end 2025 --out ./fusion_output

python3 "$SKILL_DIR/scripts/fusion_run.py" chart \
  --data ./fusion_output/bundle.json --type all --out ./fusion_output
```

使用 `chart --type <类型>` 只重绘单图，不再次消耗 API。

## 图表路由

| 用户意图 | `--type` | 数据口径 |
| --- | --- | --- |
| 年度发文与累计被引 | `pub-citations` | 精确发文；小年份精确被引，大年份随机估计 + 95% CI（年度趋势已并入本图） |
| 机构排行 | `top-institutions` | 规范 `group_by` 精确计数 |
| 作者排行 | `top-authors` | 规范 `group_by` 精确计数 |
| OpenAlex 来源排行 | `top-sources` | primary-location 来源；可含期刊、会议、知识库或预印本平台 |
| 国家/地区分布 | `countries` | 作者机构国家 full counting；合作论文可计入多国 |
| 研究主题组成 | `topic-distribution` | Primary Topic Top 8 + 全语料余项 |
| 引用分布与 h-index | `citation-impact` | 分布/均值仅用随机样本；h-index 仅用被引降序集合 |
| 高频主题词 | `keyword-frequency` | OpenAlex 自动分配的多标签主题词；不是作者关键词 |
| 关键词关系 | `cooccurrence` | 固定 seed 随机作品样本 + 节点/边/文献 CSV |

图表样式集中在 `scripts/bibliometric/chart_style.json`（与核心包同目录），可用 `--style` 覆盖。保留图底的数据源、快照、检索模式和抽样口径脚注。

## 统计与审计红线

- 把“某发表年份论文截至快照日的累计被引”与“自然年度内收到的引用”严格区分。
- 仅用随机样本计算引用分布、均值、中位数和关键词共现。
- 仅用被引降序集合计算语料 h-index；若达到安全上限仍未观察到停止边界，显示 `≥N` 下界。
- 不把 Crossref、Semantic Scholar 与 OpenAlex 数值求和、平均或静默替换。
- 所有国家/地区发文量输出统一名称：中文使用“中国香港”“中国澳门”“中国台湾”；英文使用“Hong Kong, China”"Macao, China""Taiwan, China"。不得输出不带 `China` 的港澳台英文名称。
- 每个图表的底层值必须可由 `bundle.json` 和对应 CSV 复算。
- 必须转述 stdout `notes`；不得裁剪图表脚注。

详细口径见 `references/methodology.md`，数据字段见 `references/schema-v1.md`，绘图规则见 `references/chart-recipes.md`。

## 多源独立核验

```bash
python3 "$SKILL_DIR/scripts/fusion_run.py" crosscheck \
  --field "deep learning" --start 2020 --end 2025 \
  --providers crossref --out ./fusion_output
```

若使用 Semantic Scholar，优先设置 `SEMANTIC_SCHOLAR_API_KEY`，再传 `--providers crossref,semanticscholar`。输出只并列各源年度文本检索命中数，用于覆盖差异检查；不得称为同口径复现。使用前读 `references/data-sources.md`。

## 修改后的验证门槛

```bash
python3 "$SKILL_DIR/tests/test_contract.py"
python3 "$SKILL_DIR/tests/test_offline.py"
python3 "$SKILL_DIR/tests/test_agent_align.py"
python3 "$SKILL_DIR/tests/test_cli_e2e.py"  # 仅绑定本机 127.0.0.1，不访问公网
```

本技能的核心包与研述 agent（`app/bibliometric/`）同源：修改请落在仓库的
`app/bibliometric/`，然后运行仓库根的 `scripts/sync_plugin.sh` 重新同步，
不要直接编辑 `scripts/bibliometric/` 下的文件。

## 参考资源

- Agent 对齐层（口径决策与代表性文献规则）：`references/agent-alignment.md`
- OpenAlex 当前接口：`references/openalex-contract.md`
- 指标定义与限制：`references/methodology.md`
- 图表视觉与字段映射：`references/chart-recipes.md`
- 多源边界：`references/data-sources.md`
- Bundle 数据结构：`references/schema-v1.md`
- 离线样例图：`assets/examples/`，带 `OFFLINE FIXTURE` 水印，仅用于样式评审，不代表实时结果
