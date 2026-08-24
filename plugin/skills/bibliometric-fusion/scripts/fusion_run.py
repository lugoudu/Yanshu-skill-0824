#!/usr/bin/env python3
"""技能侧 CLI 入口。

核心代码在 bibliometric/ 包内，与服务器端 agent（app/bibliometric/）保持
字节级一致，由仓库根的 scripts/sync_plugin.sh 单向同步。不要直接修改
bibliometric/ 下的文件——改动请落在 app/bibliometric/ 后重新同步。

agent 对齐命令（v1.0.0，实现在 agent_align.py，同样与 agent 同源）：
  decide         语料口径决策（Topic 门控 + 确定性瀑布），只决策不采集
  auto           决策 + 采集 + 代表性文献一步完成（agent 同款行为）
  representative 从既有 bundle.json 提取代表性文献小节
其余命令原样委托给核心包 fusion.main()。
"""

import argparse
import json
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bibliometric import openalex_core as core  # noqa: E402
from bibliometric import fusion  # noqa: E402
import agent_align  # noqa: E402

AGENT_COMMANDS = ("decide", "auto", "representative")


def _last_full_year():
    return fusion.date.today().year - 1


def _apply_api_key(value):
    if value:
        core._CLI_API_KEY = value.strip()


def _decide_parser():
    parser = argparse.ArgumentParser(
        prog="fusion_run.py decide",
        description="语料口径决策：Topic 候选过 L1 短语门控，"
                    "无候选走确定性瀑布（与研述 agent 1.0.0 同源）")
    parser.add_argument("--field", required=True)
    parser.add_argument("--start", type=int, default=_last_full_year() - 9)
    parser.add_argument("--end", type=int, default=_last_full_year())
    parser.add_argument("--api-key", default=None,
                        help="OpenAlex key 单次覆盖；持久配置请用 config")
    return parser


def cmd_decide(argv):
    args = _decide_parser().parse_args(argv)
    _apply_api_key(args.api_key)
    fusion._required_key()
    shape = agent_align.resolve_corpus_scope(args.field, args.start, args.end)
    print(json.dumps({
        "decision": shape,
        "notes": [
            "mode=topic 为 OpenAlex Topic 聚合口径；phrase/chunks/variants 为短语口径变体。",
            "chunks 形态块间 AND、块内 OR；各层命中数见 counts（-1=探测失败，按原词继续）。",
            "agent 在此之上还有 LLM 同义词层与块内穷举扩词（需 DeepSeek），本 skill 为确定性子集。",
        ],
    }, ensure_ascii=False, indent=2))
    return 0


def _auto_parser():
    parser = argparse.ArgumentParser(
        prog="fusion_run.py auto",
        description="agent 同款流程：口径决策 → 采集 → 代表性文献"
                    "（--charts 同时渲染全部图表）")
    parser.add_argument("--field", required=True)
    parser.add_argument("--start", type=int, default=_last_full_year() - 9)
    parser.add_argument("--end", type=int, default=_last_full_year())
    parser.add_argument("--out", default="./bibliometric_fusion_output")
    parser.add_argument("--charts", action="store_true",
                        help="同时渲染全部图表（默认只出数据包）")
    parser.add_argument("--lang", default="auto", choices=["auto", "zh", "en"])
    parser.add_argument("--style", default=None)
    parser.add_argument("--api-key", default=None)
    return parser


def cmd_auto(argv):
    args = _auto_parser().parse_args(argv)
    _apply_api_key(args.api_key)
    fusion._required_key()
    if args.start > args.end:
        print(json.dumps({"error": "--start 不能晚于 --end。"},
                         ensure_ascii=False))
        return 2
    shape = agent_align.resolve_corpus_scope(args.field, args.start, args.end)
    collect_args = SimpleNamespace(
        field=shape["field"], topic_id=shape.get("topic_id"),
        search_scope="title_abstract",
        search_chunks=shape.get("chunks"),
        search_variants=shape.get("variants"),
        refine_note=shape.get("note"),
        start=args.start, end=args.end,
        sample_size=core.SAMPLE_SIZE,
        impact_sample=fusion.DEFAULT_IMPACT_SAMPLE,
        cooc_sample=fusion.DEFAULT_COOCCURRENCE_SAMPLE,
        h_index_limit=fusion.DEFAULT_H_INDEX_LIMIT,
        top_n=15, keyword_top=20, cooc_top=25, cooc_edges=60,
        exact=False, force_exact=False,
        max_exact_works=core.DEFAULT_MAX_EXACT_WORKS,
        mailto=None)
    bundle = fusion.collect_bundle(collect_args)
    files = fusion.write_bundle(bundle, args.out)
    repr_md = agent_align.representative_works(bundle)
    if repr_md:
        path = os.path.join(args.out, "representative_works.md")
        os.makedirs(args.out, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(repr_md)
        files = files + [path]
    chart_failures = []
    if args.charts:
        chart_files, chart_failures = fusion.render_all(
            bundle, args.out, args.lang, args.style)
        files = files + chart_files
    print(json.dumps({
        "decision": shape,
        "files": files,
        "summary": {
            "field": shape["field"], "mode": shape["mode"],
            "years": "%s-%s" % (args.start, args.end),
            "total_works": bundle["total_works"],
            "snapshot_date": bundle["snapshot_date"],
            "representative_works": bool(repr_md),
            "partial": bundle["partial"] or bool(chart_failures),
            "provenance": bundle.get("provenance", {}),
        },
        "warnings": bundle.get("warnings", []),
        "failed_sections": bundle["failed_sections"],
        "chart_failures": chart_failures,
        "notes": [
            "口径决策与研述 agent 1.0.0 的确定性子集同源（无 LLM 扩词层）。",
            "代表性文献：被引 Top5 + 两档标题相关性过滤，宁缺毋滥；"
            "小节为空表示语料头部无标题相关高被引文献。",
            "年度被引是各发表年份论文截至快照日的累计被引，不是自然年度引用。",
        ],
    }, ensure_ascii=False, indent=2))
    return 3 if (bundle["partial"] or chart_failures) else 0


def _representative_parser():
    parser = argparse.ArgumentParser(
        prog="fusion_run.py representative",
        description="从既有 bundle.json 提取代表性文献小节（不消耗 API）")
    parser.add_argument("--data", required=True,
                        help="fetch/auto 生成的 bundle.json")
    parser.add_argument("--limit", type=int, default=5)
    return parser


def cmd_representative(argv):
    args = _representative_parser().parse_args(argv)
    with open(args.data, encoding="utf-8") as handle:
        bundle = json.load(handle)
    markdown = agent_align.representative_works(bundle, args.limit)
    if not markdown:
        print(json.dumps({
            "representative_works": "",
            "notes": ["过滤后为空（宁缺毋滥）：语料头部无标题相关高被引文献。"],
        }, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"representative_works": markdown},
                     ensure_ascii=False, indent=2))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] in AGENT_COMMANDS:
        handlers = {"decide": cmd_decide, "auto": cmd_auto,
                    "representative": cmd_representative}
        return handlers[sys.argv[1]](sys.argv[2:])
    return fusion.main()


if __name__ == "__main__":
    sys.exit(main())
