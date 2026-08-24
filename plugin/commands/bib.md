---
description: 对指定研究领域执行研述同源文献计量分析，生成口径决策、代表性文献、图表与数据包。
argument-hint: “<研究主题> [起始年-结束年] [--data-pack] [--crosscheck]”
skills: bibliometric-fusion
---

使用 `bibliometric-fusion` 技能完成这次文献计量分析请求：

$ARGUMENTS

执行要点：

1. 年份未指定时默认近 10 年；优先用 `auto` 一步完成（口径决策 → 采集 →
   代表性文献），`decision` 字段如实转述口径与各层命中数。
2. 用户要求“数据包”时保留 CSV 与 `bundle.json`；要求“核验”时追加
   crosscheck；代表性文献小节为空时如实说明“宁缺毋滥”，不得编造。
3. 需要手动控制口径时才用 `resolve` + `fetch`/`report`；候选不唯一时
   向用户确认，不要静默采用首个 Topic 候选。
4. 图表数值解读由你（宿主模型）基于 bundle 真实数值完成，不得编造任何数字。
