#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auditable bibliometric data collection and visualization fusion layer.

OpenAlex is the authoritative source for the primary report. Crossref and
Semantic Scholar are optional, separately labelled publication-count checks;
their values are never mixed into OpenAlex statistics.
"""

import argparse
import csv
import getpass
import itertools
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date
from types import SimpleNamespace

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from . import openalex_core as core  # noqa: E402


SCHEMA_VERSION = "1.0"
USER_AGENT = "bibliometric-fusion-v1/1.0 (competition entry)"
DEFAULT_IMPACT_SAMPLE = 600
DEFAULT_COOCCURRENCE_SAMPLE = 600
DEFAULT_H_INDEX_LIMIT = 500
MAX_ANALYSIS_SAMPLE = 10000

CHART_TYPES = (
    # annual-trends 已并入 pub-citations 组合图:检索窗口常只有 3 年,
    # 单独的年度柱状图只有 3 根柱,信息量不足;组合图里发文柱扩到 10 年趋势。
    "pub-citations",
    "top-institutions",
    "top-authors",
    "top-sources",
    "countries",
    "topic-distribution",
    "citation-impact",
    "keyword-frequency",
    "cooccurrence",
)

COUNTRY_NAMES = {
    "US": ("美国", "United States"), "CN": ("中国", "China"),
    "GB": ("英国", "United Kingdom"), "DE": ("德国", "Germany"),
    "JP": ("日本", "Japan"), "IN": ("印度", "India"),
    "KR": ("韩国", "South Korea"), "FR": ("法国", "France"),
    "CA": ("加拿大", "Canada"), "AU": ("澳大利亚", "Australia"),
    "IT": ("意大利", "Italy"), "ES": ("西班牙", "Spain"),
    "BR": ("巴西", "Brazil"), "NL": ("荷兰", "Netherlands"),
    "CH": ("瑞士", "Switzerland"), "SE": ("瑞典", "Sweden"),
    "SG": ("新加坡", "Singapore"),
    # Mandatory country/region naming policy for all bibliometric outputs.
    "HK": ("中国香港", "Hong Kong, China"),
    "MO": ("中国澳门", "Macao, China"),
    "TW": ("中国台湾", "Taiwan, China"),
}

SPECIAL_REGION_ALIASES = {
    "hong kong": "HK", "hong kong, china": "HK",
    "hong kong sar": "HK", "hong kong sar, china": "HK",
    "香港": "HK", "中国香港": "HK", "中国香港特别行政区": "HK",
    "macao": "MO", "macau": "MO", "macao, china": "MO",
    "macau, china": "MO", "macao sar": "MO", "macau sar": "MO",
    "澳门": "MO", "中国澳门": "MO", "中国澳门特别行政区": "MO",
    "taiwan": "TW", "taiwan, china": "TW",
    "taiwan, province of china": "TW", "台湾": "TW",
    "台湾省": "TW", "中国台湾": "TW",
}


def _country_code(row):
    """Resolve a country code, including defensive aliases for HK/MO/TW."""
    raw_key = str(row.get("country_code") or row.get("key") or "").strip()
    candidate = raw_key.rsplit("/", 1)[-1].upper()
    if candidate in COUNTRY_NAMES:
        return candidate
    raw_name = str(row.get("name") or row.get("name_en") or "").strip()
    alias = " ".join(raw_name.casefold().replace("，", ",").replace(".", "").split())
    return SPECIAL_REGION_ALIASES.get(alias)


def country_display_name(row, zh_ok):
    """Return the mandatory localized display name for a country/region row."""
    code = _country_code(row)
    if code in COUNTRY_NAMES:
        return COUNTRY_NAMES[code][0 if zh_ok else 1]
    localized = row.get("name_zh" if zh_ok else "name_en")
    return localized or row.get("name") or code or ""


def normalize_country_rows(rows):
    """Store explicit zh/en names and a compliant neutral English default."""
    normalized = []
    for row in rows:
        copy = dict(row)
        code = _country_code(copy)
        if code:
            copy["country_code"] = code
        copy["name_zh"] = country_display_name(copy, True)
        copy["name_en"] = country_display_name(copy, False)
        copy["name"] = copy["name_en"]
        normalized.append(copy)
    return normalized

DEFAULT_STYLE = {
    "colors": {
        "primary": "#4C78A8",
        "secondary": "#7A5195",
        "accent": "#F28E2B",
        "positive": "#59A14F",
        "danger": "#E15759",
        "grid": "#D9DEE7",
        "text": "#25324B",
        "muted": "#6B7280",
        "background": "#FFFFFF",
    },
    "palette": [
        "#4C78A8", "#7A5195", "#F28E2B", "#59A14F", "#E15759",
        "#76B7B2", "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F",
    ],
    "figsize": {
        "trend": [11, 6], "trend_detail": [11, 8], "bar": [10, 7],
        "donut": [10, 7], "impact": [13, 6], "network": [12, 8],
    },
    "dpi": 200,
}


def _deep_merge(base, override):
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_style(path=None):
    default_path = os.path.join(SCRIPT_DIR, "chart_style.json")
    target = path or default_path
    try:
        with open(target, encoding="utf-8") as handle:
            return _deep_merge(DEFAULT_STYLE, json.load(handle))
    except OSError:
        return DEFAULT_STYLE


def atomic_write_json(path, payload, mode=0o644):
    """Write JSON atomically so interrupted runs never leave a half bundle."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp = os.path.join(directory, ".%s.tmp-%s" % (os.path.basename(path), os.getpid()))
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp, mode)
        except OSError:
            pass
        os.replace(temp, path)
    finally:
        try:
            os.remove(temp)
        except FileNotFoundError:
            pass


def _sanitize_error(exc):
    return core._redact_url(str(exc))


def _percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return float(ordered[low])
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _h_index(citations):
    h = 0
    for index, value in enumerate(sorted(citations, reverse=True), 1):
        if value >= index:
            h = index
        else:
            break
    return h


def _normalize_work(work):
    row = core.work_row(work)
    row["keywords"] = [
        item.get("display_name") for item in (work.get("keywords") or [])
        if item.get("display_name")
    ]
    topic = work.get("primary_topic") or {}
    row["primary_topic"] = topic.get("display_name") or ""
    return row


def _cooccurrence(works, field, top=25, max_edges=60):
    docs = []
    for work in works:
        keywords = [
            item.get("display_name") for item in (work.get("keywords") or [])
            if item.get("display_name")
        ]
        if keywords:
            docs.append(sorted(set(keywords)))

    frequency, pairs = Counter(), Counter()
    stop = {field.casefold()}
    for keywords in docs:
        kept = [word for word in keywords if word.casefold() not in stop]
        frequency.update(kept)
        for left, right in itertools.combinations(sorted(set(kept)), 2):
            pairs[(left, right)] += 1

    generic = []
    cutoff = max(3, int(0.5 * len(docs)))
    candidates = [word for word, count in frequency.items() if count > cutoff]
    if candidates and len(frequency) - len(candidates) >= 5:
        generic = candidates
        for word in candidates:
            del frequency[word]
        pairs = Counter({
            (left, right): count for (left, right), count in pairs.items()
            if left in frequency and right in frequency
        })

    nodes = [
        {"name": word, "count": count}
        for word, count in frequency.most_common(top)
    ]
    node_names = {node["name"] for node in nodes}
    edges = [
        {"source": left, "target": right, "count": count}
        for (left, right), count in pairs.most_common()
        if left in node_names and right in node_names
    ][:max_edges]
    return {
        "documents": len(docs), "nodes": nodes, "edges": edges,
        "generic_keywords_removed": generic,
    }


def _country_pairs_from_works(works):
    """从已采样的作品列表统计国家两两合作共现对。

    直接复用 random_analysis_sample(~300 篇,WORK_SELECT 已含 authorships):
    此前合作图单独再采 80 篇,计数只有个位数——读者误以为领域总合作量就
    这么少,实际口径只是"80 篇样本内"。复用大样本既省一次采集,数字也更稳。
    """
    from collections import Counter
    from itertools import combinations

    pair_counter = Counter()
    for work in works:
        countries = set()
        for authorship in (work.get("authorships") or []):
            for institution in (authorship.get("institutions") or []):
                code = institution.get("country_code")
                if code:
                    countries.add(code.upper())
        if len(countries) >= 2:
            for a, b in combinations(sorted(countries), 2):
                pair_counter[(a, b)] += 1
    pairs = [{"country_a": a, "country_b": b, "count": c}
             for (a, b), c in pair_counter.most_common(60)]
    return {"sample_size": len(works), "pairs": pairs}


def _required_key():
    if not core.load_api_key():
        raise RuntimeError(
            "未配置 OpenAlex API key。请先运行 fusion.py config，"
            "或设置 OPENALEX_API_KEY。融合版不承诺已被官方取消的匿名额度。"
        )


def collect_bundle(args):
    """Collect one normalized OpenAlex bundle for all downstream charts.

    采集分两阶段:annual_publications 必须先跑(后续被引采样用它的 count_hint
    决定精确/抽样策略);其余 11 个任务(逐年被引 × N 年、6 个维度排行、随机
    样本、h-index 高被引集)彼此独立,用线程池并行执行——串行时它们要排队
    15+ 次 HTTP 往返,是清小搭 120s 网关超时的主要压力来源。OpenAlex 持 key
    限速 10 req/s,并发 6 路配合内核已有的 429 退避是安全的。
    """
    from concurrent.futures import ThreadPoolExecutor

    _required_key()
    base = core.build_query(args)
    snapshot = date.today().isoformat()
    failures = []

    # 发文趋势扩展到 10 年:group_by 聚合只占 1 次请求(成本≈0),而 3 年窗口的
    # 年度柱状图只有 3 根柱、毫无趋势可言。被引采样(贵,逐批抽样)仍只跑统计
    # 窗口内的年份,速度不受影响。
    trend_start = min(args.start, date.today().year - 9)
    # build_query 总会设置 filter(topic 模式为 topics.id+年份,全文模式为年份);
    # 这里只把其中的年份段替换成扩展区间,其余过滤条件原样保留。
    base_trend = dict(base)
    base_trend["filter"] = ",".join(
        "publication_year:%s-%s" % (trend_start, args.end)
        if part.startswith("publication_year:") else part
        for part in base["filter"].split(","))
    publications_all = core.annual_publications(base_trend, trend_start, args.end, args.mailto)
    # 被引分析、排行、抽样仍用统计窗口内的精确口径
    publications = {y: publications_all.get(y, 0)
                   for y in range(args.start, args.end + 1)}
    years = list(range(args.start, args.end + 1))
    annual = {}
    trend_audit = []

    group_specs = {
        "institutions": ("authorships.institutions.id", args.top_n),
        "authors": ("authorships.author.id", args.top_n),
        "sources": ("primary_location.source.id", args.top_n),
        "countries": ("authorships.institutions.country_code", args.top_n),
        "keywords": ("keywords.id", max(args.keyword_top, args.top_n)),
        "topics": ("primary_topic.id", 50),
    }

    def _annual_task(year):
        try:
            total, estimated, sample_n, count, works, mean, ci95 = core.annual_citations(
                base, year, args.mailto, count_hint=publications.get(year),
                sample_size=args.sample_size, exact=args.exact,
                max_exact_works=args.max_exact_works,
                force_exact=args.force_exact,
            )
            audit = []
            for work in works:
                normalized = _normalize_work(work)
                normalized["statistics_year"] = year
                normalized["is_sample"] = bool(estimated)
                audit.append(normalized)
            return ("annual:%s" % year, None, {
                "publications": publications.get(year, 0),
                "cumulative_citations": total,
                "cumulative_citations_per_work": round(mean, 4),
                "citations_estimated": bool(estimated),
                "sample_size": sample_n,
                "population_size": count,
                "cumulative_citations_ci95": ci95,
            }, audit)
        except Exception as exc:
            return ("annual:%s" % year, _sanitize_error(exc), {
                "publications": publications.get(year, 0),
                "cumulative_citations": None,
                "cumulative_citations_per_work": None,
                "citations_estimated": None,
                "sample_size": None,
                "population_size": publications.get(year, 0),
                "cumulative_citations_ci95": None,
            }, [])

    total_works = sum(publications.values())
    analysis_n = min(max(args.impact_sample, args.cooc_sample), total_works)
    h_limit = min(args.h_index_limit, total_works)

    def _ranking_task(name):
        group_by, limit = group_specs[name]
        try:
            return ("ranking:%s" % name, None,
                    core.group_top(base, group_by, limit, args.mailto))
        except Exception as exc:
            return ("ranking:%s" % name, _sanitize_error(exc), [])

    def _sample_task():
        if not analysis_n:
            return ("random_analysis_sample", None, [])
        try:
            return ("random_analysis_sample", None, core.fetch_sample(
                base, analysis_n,
                core.WORK_SELECT + ",keywords,primary_topic",
                args.mailto,
            ))
        except Exception as exc:
            return ("random_analysis_sample", _sanitize_error(exc), [])

    def _hindex_task():
        if not h_limit:
            return ("h_index_high_cited_set", None, [])
        try:
            return ("h_index_high_cited_set", None,
                    core.export_works(base, h_limit, "cited_by_count:desc", args.mailto))
        except Exception as exc:
            return ("h_index_high_cited_set", _sanitize_error(exc), [])

    rankings = {}
    analysis_works = []
    high_cited = []
    audit_by_year = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_annual_task, year) for year in years]
        futures += [pool.submit(_ranking_task, name) for name in group_specs]
        futures.append(pool.submit(_sample_task))
        futures.append(pool.submit(_hindex_task))
        for future in futures:
            key, error, payload, *extra = future.result()
            if error is not None:
                failures.append({"section": key, "error": error})
            if key.startswith("annual:"):
                year = key.split(":", 1)[1]
                annual[year] = payload
                audit_by_year[year] = extra[0] if extra else []
            elif key.startswith("ranking:"):
                rankings[key.split(":", 1)[1]] = payload
            elif key == "random_analysis_sample":
                analysis_works = payload
            elif key == "h_index_high_cited_set":
                high_cited = payload
    for year in sorted(audit_by_year):
        trend_audit.extend(audit_by_year[year])
    rankings["countries"] = normalize_country_rows(rankings.get("countries", []))

    # 趋势窗口早于统计窗口的年份:只有发文量,被引字段为 None(图表只画有值年份)
    for year in range(trend_start, args.start):
        annual[str(year)] = {
            "publications": publications_all.get(year, 0),
            "cumulative_citations": None,
            "cumulative_citations_per_work": None,
            "citations_estimated": None,
            "sample_size": None,
            "population_size": publications_all.get(year, 0),
            "cumulative_citations_ci95": None,
        }
    total_works_trend = sum(publications_all.values())

    impact_works = analysis_works[:min(args.impact_sample, len(analysis_works))]
    citations = [work.get("cited_by_count") or 0 for work in impact_works]
    impact = {
        "sampling": "uniform OpenAlex sample with deterministic multi-seed batches",
        "sample_size": len(citations),
        "citation_counts": citations,
        "mean_citations": round(statistics.mean(citations), 4) if citations else 0.0,
        "median_citations": round(statistics.median(citations), 4) if citations else 0.0,
        "p90_citations": round(_percentile(citations, 0.90), 4),
        "uncited_share": round(sum(value == 0 for value in citations) / len(citations), 4)
        if citations else 0.0,
    }

    h_values = [work.get("cited_by_count") or 0 for work in high_cited]
    h_value = _h_index(h_values)
    h_status = "unavailable"
    if high_cited:
        if total_works <= len(high_cited) or h_value < len(high_cited):
            h_status = "exact_for_filtered_corpus"
        else:
            h_status = "lower_bound"
    impact["h_index"] = h_value
    impact["h_index_status"] = h_status
    impact["h_index_records_checked"] = len(high_cited)

    cooc_works = analysis_works[:min(args.cooc_sample, len(analysis_works))]
    cooccurrence = _cooccurrence(
        cooc_works, args.field, top=args.cooc_top, max_edges=args.cooc_edges
    ) if cooc_works else {"documents": 0, "nodes": [], "edges": [],
                          "generic_keywords_removed": []}

    topic_id = core.normalize_topic_id(args.topic_id) if args.topic_id else None
    search_scope = None if topic_id else _search_scope(args)
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "source": "openalex",
        "snapshot_date": snapshot,
        "provenance": {
            "kind": "live_api",
            "fixture": False,
            "provider": "OpenAlex",
            "collector": "bibliometric-fusion-v1",
        },
        "warnings": [],
        "query": {
            "field": args.field, "topic_id": topic_id,
            "search_scope": search_scope,
            # 检索形态明细（分块 AND / 词形 OR / 放宽说明），供检索式
            # 展示与 JSONL 审计复核；phrase 形态时均为 None。
            "search_chunks": getattr(args, "search_chunks", None),
            "search_variants": getattr(args, "search_variants", None),
            "refine_note": getattr(args, "refine_note", None),
            "start": args.start, "end": args.end,
            # 发文趋势实际覆盖的年份区间(早于 start 的年份只有发文量、无被引)
            "trend_start": trend_start,
            "mode": "topic" if topic_id else _SCOPE_MODES[search_scope],
        },
        "total_works": total_works,
        "total_works_trend": total_works_trend,
        "annual": annual,
        "rankings": rankings,
        "impact": impact,
        "cooccurrence": cooccurrence,
        "collaboration": _country_pairs_from_works(analysis_works),
        "audit": {
            "trend_works": trend_audit,
            "random_analysis_works": [_normalize_work(work) for work in analysis_works],
            "high_cited_works": [_normalize_work(work) for work in high_cited],
        },
        "methodology": {
            "retrieval": (
                "topic filter, or quoted phrase search scoped to title+abstract "
                "by default; OpenAlex top-level search matches tokens AND-wise "
                "across title, abstract and fulltext, so fulltext scope "
                "(--search-scope fulltext) over-recruits hot terms"
            ),
            "annual_publications": "exact OpenAlex group_by publication_year",
            "annual_citations": (
                "exact for small years; otherwise random-sample mean times population, "
                "with approximate 95% confidence interval"
            ),
            "citation_distribution": "random sample only; includes zero-citation works",
            "h_index": (
                "computed only from a citation-descending set; exact when the stopping "
                "condition is observed, otherwise explicitly reported as a lower bound"
            ),
            "rankings": "exact OpenAlex group_by counts",
            "cooccurrence": "random work sample; deterministic seeds; generic terms filtered",
        },
        "partial": bool(failures),
        "failed_sections": failures,
    }
    return bundle


def _write_dict_rows(path, rows):
    if not rows:
        core.write_csv(path, ["no_data"], [])
        return
    keys = list(rows[0].keys())
    serial = []
    for row in rows:
        serial.append([
            "; ".join(value) if isinstance(value, list) else value
            for value in (row.get(key, "") for key in keys)
        ])
    core.write_csv(path, keys, serial)


def write_bundle(bundle, outdir, filename="bundle.json"):
    os.makedirs(outdir, exist_ok=True)
    bundle_path = os.path.join(outdir, filename)
    atomic_write_json(bundle_path, bundle)

    annual_csv = os.path.join(outdir, "annual_metrics.csv")
    annual_rows = []
    for year, row in sorted(bundle["annual"].items()):
        annual_rows.append([bundle["snapshot_date"], int(year)] + [
            row.get("publications"), row.get("cumulative_citations"),
            row.get("cumulative_citations_per_work"),
            row.get("citations_estimated"), row.get("sample_size"),
            row.get("cumulative_citations_ci95"),
        ])
    core.write_csv(
        annual_csv,
        ["snapshot_date", "publication_year", "publications",
         "cumulative_citations", "cumulative_citations_per_work",
         "citations_estimated", "sample_size", "cumulative_citations_ci95"],
        annual_rows,
    )

    audit_files = []
    for key, filename in (
        ("trend_works", "trend_works.csv"),
        ("random_analysis_works", "random_analysis_works.csv"),
        ("high_cited_works", "high_cited_works.csv"),
    ):
        path = os.path.join(outdir, filename)
        _write_dict_rows(path, bundle["audit"].get(key, []))
        audit_files.append(path)

    for dimension, rows in bundle["rankings"].items():
        path = os.path.join(outdir, "ranking_%s.csv" % dimension)
        if dimension == "countries":
            core.write_csv(
                path,
                ["rank", "country_code", "name_zh", "name_en",
                 "openalex_key", "works_count"],
                [[index, _country_code(row), country_display_name(row, True),
                  country_display_name(row, False), row.get("key"), row.get("count")]
                 for index, row in enumerate(rows, 1)],
            )
        else:
            core.write_csv(
                path, ["rank", "name", "openalex_key", "works_count"],
                [[index, row.get("name"), row.get("key"), row.get("count")]
                 for index, row in enumerate(rows, 1)],
            )
        audit_files.append(path)

    topic_rows = bundle["rankings"].get("topics", [])[:8]
    topic_other = max(
        bundle.get("total_works", 0) - sum(row.get("count", 0) for row in topic_rows),
        0,
    )
    topic_chart_csv = os.path.join(outdir, "topic_distribution.csv")
    topic_chart_rows = [
        [index, row.get("name"), row.get("key"), row.get("count"),
         round(row.get("count", 0) / bundle["total_works"], 6)
         if bundle.get("total_works") else 0]
        for index, row in enumerate(topic_rows, 1)
    ]
    if topic_other:
        topic_chart_rows.append([
            "other", "其他 Other", "", topic_other,
            round(topic_other / bundle["total_works"], 6)
            if bundle.get("total_works") else 0,
        ])
    core.write_csv(topic_chart_csv,
                   ["slice", "topic", "openalex_key", "works_count", "share"],
                   topic_chart_rows)
    audit_files.append(topic_chart_csv)

    impact = bundle.get("impact", {})
    impact_csv = os.path.join(outdir, "citation_impact_summary.csv")
    core.write_csv(impact_csv, ["metric", "value", "status_or_method"], [
        ["random_sample_size", impact.get("sample_size", 0), impact.get("sampling", "")],
        ["mean_citations", impact.get("mean_citations", 0), "random_sample"],
        ["median_citations", impact.get("median_citations", 0), "random_sample"],
        ["p90_citations", impact.get("p90_citations", 0), "random_sample"],
        ["uncited_share", impact.get("uncited_share", 0), "random_sample"],
        ["corpus_h_index", impact.get("h_index", 0), impact.get("h_index_status", "")],
        ["h_index_records_checked", impact.get("h_index_records_checked", 0),
         "citation_descending_set"],
    ])
    audit_files.append(impact_csv)

    nodes_path = os.path.join(outdir, "cooccurrence_nodes.csv")
    edges_path = os.path.join(outdir, "cooccurrence_edges.csv")
    core.write_csv(nodes_path, ["keyword", "frequency"], [
        [node["name"], node["count"]] for node in bundle["cooccurrence"]["nodes"]
    ])
    core.write_csv(edges_path, ["keyword_a", "keyword_b", "cooccurrence"], [
        [edge["source"], edge["target"], edge["count"]]
        for edge in bundle["cooccurrence"]["edges"]
    ])
    return [bundle_path, annual_csv] + audit_files + [nodes_path, edges_path]


def _setup_plot(lang, style):
    plt, zh_ok = core.setup_plot(lang, style)
    colors = style["colors"]
    plt.rcParams.update({
        "figure.facecolor": colors["background"],
        "axes.facecolor": colors["background"],
        "axes.titleweight": "semibold",
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    return plt, zh_ok


def _chart_title(bundle, zh_suffix, en_suffix, zh_ok):
    query = bundle["query"]
    field = query["field"]
    years = "%s–%s" % (query["start"], query["end"])
    if zh_ok:
        return "“%s” %s（%s）" % (field, zh_suffix, years)
    return '"%s" %s (%s)' % (field, en_suffix, years)


def _format_number(value, zh_ok):
    """Format large values without leaking Chinese units into English charts."""
    value = float(value)
    if zh_ok:
        if abs(value) >= 100000000:
            return ("%.1f亿" % (value / 100000000)).replace(".0亿", "亿")
        if abs(value) >= 10000:
            return ("%.1f万" % (value / 10000)).replace(".0万", "万")
    else:
        if abs(value) >= 1000000000:
            return ("%.1fB" % (value / 1000000000)).replace(".0B", "B")
        if abs(value) >= 1000000:
            return ("%.1fM" % (value / 1000000)).replace(".0M", "M")
        if abs(value) >= 1000:
            return ("%.1fk" % (value / 1000)).replace(".0k", "k")
    return "%g" % value


# 检索范围 → bundle.query.mode 的稳定取值（schema 契约，勿随意改名）。
_SCOPE_MODES = {
    "title_abstract": "title_abstract_phrase",
    "title": "title_phrase",
    "fulltext": "full_text_search",
}


def _search_scope(args):
    """读取调用方的检索范围，缺省回退到默认口径。"""
    scope = getattr(args, "search_scope", None) or "title_abstract"
    if scope not in _SCOPE_MODES:
        raise RuntimeError(
            "未知检索范围：%s（可选 %s）" % (scope, "/".join(core.SEARCH_SCOPES)))
    return scope


# mode → 图表脚注文案（中文, 英文）。
_FOOTER_MODE_LABELS = {
    "topic": ("Topic", "Topic"),
    "title_abstract_phrase": ("标题+摘要短语", "title+abstract phrase"),
    "title_phrase": ("标题短语", "title phrase"),
    "full_text_search": ("全文", "full text"),
}


def _footer_mode_label(mode, zh_ok):
    """mode → 脚注文案；未知 mode（含旧 bundle 的历史值）原样显示。"""
    label = _FOOTER_MODE_LABELS.get(mode)
    if label is None:
        return str(mode or "search")
    return label[0] if zh_ok else label[1]


def _add_footer(fig, bundle, zh_ok, note=None):
    snapshot = bundle["snapshot_date"]
    mode = bundle["query"].get("mode")
    fixture = bool((bundle.get("provenance") or {}).get("fixture"))
    if fixture and zh_ok:
        parts = ["数据：合成离线夹具", "生成：%s" % snapshot,
                 "检索结构：%s" % _footer_mode_label(mode, True)]
    elif fixture:
        parts = ["Data: synthetic offline fixture", "Generated: %s" % snapshot,
                 "Query shape: %s" % _footer_mode_label(mode, False)]
    elif zh_ok:
        parts = ["数据来源：OpenAlex", "快照：%s" % snapshot,
                 "检索：%s" % _footer_mode_label(mode, True)]
    else:
        parts = ["Source: OpenAlex", "Snapshot: %s" % snapshot,
                 "Query: %s" % _footer_mode_label(mode, False)]
    if bundle.get("partial"):
        parts.append("PARTIAL / 部分数据缺失")
    if fixture:
        fig.text(
            0.985, 0.012,
            ("离线夹具 / OFFLINE FIXTURE · 禁止定量解读" if zh_ok else
             "OFFLINE FIXTURE · NOT FOR QUANTITATIVE CLAIMS"),
            fontsize=8.5, weight="bold", color="#B91C1C", ha="right", va="bottom",
            bbox={"boxstyle": "round,pad=0.28", "facecolor": "#FEF2F2",
                  "edgecolor": "#B91C1C", "linewidth": 0.8},
        )
    footer_lines = []
    if note:
        footer_lines.append(note)
    footer_lines.append("  |  ".join(parts))
    fig.text(0.01, 0.012, "\n".join(footer_lines), fontsize=8.3,
             linespacing=1.35, color="#6B7280", ha="left", va="bottom")


def _save(fig, path, style, rect=(0, 0.085, 1, 1)):
    fig.tight_layout(rect=rect)
    fig.savefig(path, dpi=style["dpi"], bbox_inches="tight", facecolor="white")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return path



def chart_pub_citations(bundle, outdir, lang, style):
    """年度发文(10 年趋势) + 统计窗口内累计被引 + 篇均被引组合图。

    原版独立的 annual-trends 已并入本图:3 年窗口的单柱图信息量不足,
    这里发文柱扩展到 trend_start 起(10 年),被引折线只画有采样数据的年份,
    趋势年份的柱子用更淡的颜色区分。
    """
    plt, zh_ok = _setup_plot(lang, style)
    from matplotlib import colors as mpl_colors
    from matplotlib.ticker import FuncFormatter
    years = sorted(int(year) for year in bundle["annual"])
    stat_start = int(bundle["query"]["start"])
    rows = [bundle["annual"][str(year)] for year in years]
    xs = list(range(len(years)))
    pubs = [row["publications"] for row in rows]
    means = [row["cumulative_citations_per_work"] for row in rows]
    if not pubs:
        raise RuntimeError("年度发文数据为空，无法绘图。")

    fig, (ax1, ax3) = plt.subplots(
        2, 1, figsize=style["figsize"]["trend_detail"], sharex=True,
        gridspec_kw={"height_ratios": [2.1, 1], "hspace": 0.16},
    )
    colors = style["colors"]
    # 统计窗口内的年份用主色,仅趋势年份淡色(无被引数据)
    bar_colors = [colors["primary"] if year >= stat_start else
                  mpl_colors.to_rgba(colors["primary"], alpha=0.38)
                  for year in years]
    bars = ax1.bar(xs, pubs, color=bar_colors,
                   label=("发文量" if zh_ok else "Publications"))
    # 篇均累计被引不画折线——"累计"口径天然随暴露期递减(2023 年论文比 2025 年
    # 多积累约 2 年被引),画成下行折线必然被误读成"领域凉了"。改为统计区间
    # 柱内的白色小字:信息保留,误导性的视觉形态消失。柱顶统一标发文量,
    # 避免同一排标签混两种含义。
    ax1.bar_label(bars, labels=[_format_number(value, zh_ok) for value in pubs],
                  padding=2, fontsize=8)
    inner_labels = []
    for year, row in zip(years, rows):
        per_work = row.get("cumulative_citations_per_work")
        if year >= stat_start and per_work is not None:
            inner_labels.append(("篇均%.1f" if zh_ok else "%.1f/work") % per_work)
        else:
            inner_labels.append("")
    ax1.bar_label(bars, labels=inner_labels, label_type="center",
                  fontsize=7.5, color="white", fontweight="bold")
    ax1.set_ylabel("发文量（篇）" if zh_ok else "Publications")
    ax1.yaxis.set_major_formatter(
        FuncFormatter(lambda value, pos: _format_number(value, zh_ok)))
    ax1.legend(frameon=False, loc="upper left")

    # 篇均年化被引速率:篇均累计被引 ÷ 暴露年数(快照日距该年 1 月 1 日),
    # 归一化后跨年可比、能升能降,是"哪年发表的论文更热"的可比指标。
    # 画成柱状(与上图形式统一),同样避免折线的"走势暗示"。
    def _exposure_years(year: int) -> float:
        try:
            snap = date(*map(int, str(bundle.get("snapshot_date", "2000-1-1")).split("-")))
        except ValueError:
            snap = date.today()
        months = (snap.year - year) * 12 + snap.month - 1
        return max(months / 12.0, 1.0 / 12.0)

    rate_xs = [index for index in range(len(means)) if means[index] is not None]
    if rate_xs:
        rate_ys = [means[index] / _exposure_years(years[index])
                   for index in rate_xs]
        rate_bars = ax3.bar(rate_xs, rate_ys, width=0.55,
                             color=colors["secondary"], alpha=0.85,
                             label=("篇均年化被引速率" if zh_ok else
                                    "Annualized citations per work"))
        ax3.bar_label(rate_bars, labels=["%.2f" % v for v in rate_ys],
                      padding=2, fontsize=8.5)
        ax3.set_ylabel("次/篇/年" if zh_ok else "Citations/work/year")
        ax3.set_ylim(0, max(rate_ys) * 1.25)
        ax3.legend(frameon=False, loc="upper left")
    else:
        ax3.text(0.5, 0.5, "无被引采样数据" if zh_ok else "No citation sample",
                 ha="center", va="center", transform=ax3.transAxes,
                 fontsize=10, color=style["colors"]["muted"])
    ax3.set_xticks(xs)
    ax3.set_xticklabels(years, rotation=35)
    ax3.grid(axis="y", linestyle="--", alpha=0.35)

    ax1.set_title(_chart_title(
        bundle, "发文趋势与被引速率", "publication trend and citation rate", zh_ok
    ), fontsize=14, pad=14)
    estimated = [row for row in rows if row.get("citations_estimated")]
    sample_sizes = sorted({row.get("sample_size") for row in estimated
                           if row.get("sample_size")})
    sample_text = ",".join(str(value) for value in sample_sizes) or "0"
    stat_years = len(rate_xs)
    cited_years = len([row for row in rows if row.get("citations_estimated") is not None])
    note = (
        "深色柱为统计区间（%s 年），柱内白字为该年篇均累计被引；浅色柱为更早年份发文趋势"
        "（仅计数）；下图年化速率已按暴露期归一化（快照距发表年的时长），跨年可比；"
        "被引估计年 %s/%s，n=%s"
        % (stat_years, len(estimated), cited_years, sample_text)
        if zh_ok else
        "Dark bars: statistics window (%s yrs), in-bar white text = cumulative citations "
        "per work; light bars: earlier trend, counts only; lower panel annualized by "
        "exposure time for cross-year comparability; estimated citation years %s/%s, n=%s"
        % (stat_years, len(estimated), cited_years, sample_text)
    )
    _footer_extra = (
        "柱内篇均累计被引随暴露期增长而升高（越早发表的论文积累时间越长），不代表影响力变化"
        if zh_ok else
        "In-bar values grow with exposure time (older works accumulate longer); "
        "not a quality signal"
    )
    _add_footer(fig, bundle, zh_ok, note + "\n" + _footer_extra)
    fig.subplots_adjust(left=0.10, right=0.88, bottom=0.13, top=0.90, hspace=0.22)
    path = os.path.join(outdir, "publication_citations.png")
    fig.savefig(path, dpi=style["dpi"], bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _ranking_chart(bundle, outdir, lang, style, dimension):
    plt, zh_ok = _setup_plot(lang, style)
    rows = bundle["rankings"].get(dimension) or []
    if not rows:
        raise RuntimeError("%s 排行数据为空。" % dimension)
    rows = rows[::-1]
    names = [row["name"] for row in rows]
    if dimension == "countries":
        names = [country_display_name(row, zh_ok) for row in rows]
    values = [row["count"] for row in rows]
    height = max(4.8, 0.42 * len(rows) + 1.8)
    fig, ax = plt.subplots(figsize=(style["figsize"]["bar"][0], height))
    # Ranking is ordinal rather than categorical: keep a coherent base color and
    # reserve semantic accents for the leading three entries.
    colors = [style["colors"]["primary"]] * len(rows)
    if colors:
        colors[-1] = style["colors"]["accent"]
    if len(colors) > 1:
        colors[-2] = style["colors"]["secondary"]
    if len(colors) > 2:
        colors[-3] = style["colors"]["positive"]
    bars = ax.barh(names, values, color=colors, alpha=0.88)
    ax.bar_label(bars, fmt="%.0f", padding=4, fontsize=8)
    labels_zh = {"institutions": "机构", "authors": "作者", "sources": "来源",
                 "countries": "国家/地区", "keywords": "OpenAlex 主题词"}
    suffix = "%s发文量排行" % labels_zh[dimension]
    ax.set_title(_chart_title(bundle, suffix, "top %s" % dimension, zh_ok),
                 fontsize=14, pad=14)
    ax.set_xlabel("发文量 Works" if zh_ok else "Works")
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    core.use_latin_font_for_latin_ticks(ax)
    note = None
    if dimension == "countries":
        note = ("按作者机构国家计数；跨国合作可计入多国" if zh_ok else
                "By affiliation country; collaborative works may count for multiple countries")
    elif dimension == "institutions":
        note = ("按作者机构完全计数；合作论文可计入多个机构" if zh_ok else
                "Full counting by author affiliation; one work may count for multiple institutions")
    elif dimension == "sources":
        note = ("OpenAlex primary-location 来源；可包含期刊、会议、知识库或预印本平台"
                if zh_ok else
                "OpenAlex primary-location sources; may include venues, repositories, or preprint hosts")
    elif dimension == "keywords":
        note = ("OpenAlex 自动分配的多标签主题词，并非作者关键词" if zh_ok else
                "OpenAlex-assigned multi-label terms; not author-supplied keywords")
    _add_footer(fig, bundle, zh_ok, note)
    filename = "countries.png" if dimension == "countries" else "top_%s.png" % dimension
    return _save(fig, os.path.join(outdir, filename), style)


def chart_topic_distribution(bundle, outdir, lang, style):
    """主要 Topic 水平条形图(Top 12)。

    原甜甜圈版把 Top 8 之外全部并入"其他"——学术主题高度分散,Others 常占
    60-80%,饼图沦为一大块灰色,Top 项挤成细条毫无信息量;且 OpenAlex 主题名
    很长,饼图图例放不下。水平条形图两个问题都解决:完整主题名 + 长尾排序,
    覆盖率写进副标注而不是画一个巨大的灰色扇区。
    """
    plt, zh_ok = _setup_plot(lang, style)
    rows = bundle["rankings"].get("topics") or []
    if not rows:
        raise RuntimeError("Topic 分布数据为空。")
    top = rows[:12]
    covered = sum(row["count"] for row in top)
    total = bundle.get("total_works", 0) or 1
    share = covered / total * 100

    names = [row["name"] for row in top][::-1]
    values = [row["count"] for row in top][::-1]
    fig, ax = plt.subplots(figsize=(9.6, 6.4))
    colors = [style["palette"][i % len(style["palette"])] for i in range(len(values))]
    bars = ax.barh(names, values, color=colors, alpha=0.88)
    ax.bar_label(bars, labels=[_format_number(v, zh_ok) for v in values],
                 padding=4, fontsize=8.5)
    ax.set_xlabel("作品数 Works" if zh_ok else "Works")
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    core.use_latin_font_for_latin_ticks(ax)
    ax.tick_params(axis="y", labelsize=9)
    ax.set_title(_chart_title(
        bundle, "主要研究主题（OpenAlex Topic）", "primary research topics", zh_ok
    ), fontsize=14, pad=14)
    note = ("Top 12 主题合计 %s 篇，约占统计区间总量的 %.0f%%（主题分散是常态，"
            "其余为长尾主题）" % (_format_number(covered, zh_ok), share)
            if zh_ok else
            "Top 12 topics cover %s works, ~%.0f%% of the corpus (long tail omitted)"
            % (_format_number(covered, zh_ok), share))
    _add_footer(fig, bundle, zh_ok, note)
    return _save(fig, os.path.join(outdir, "topic_distribution.png"), style,
                 rect=(0, 0.085, 1, 1))


def chart_citation_impact(bundle, outdir, lang, style):
    plt, zh_ok = _setup_plot(lang, style)
    impact = bundle.get("impact") or {}
    values = impact.get("citation_counts") or []
    if not values:
        raise RuntimeError("随机引用样本为空。")
    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=style["figsize"]["impact"],
        gridspec_kw={"width_ratios": [3, 2]},
    )
    max_value = max(values)
    fixed = [0, 1, 5, 10, 25, 50, 100, 250, 500, 1000]
    bins = [value for value in fixed if value <= max(max_value, 1)]
    if bins[-1] <= max_value:
        bins.append(max_value + 1)
    if len(bins) < 2:
        bins = [0, 1, 2]
    counts = []
    labels = []
    for index in range(len(bins) - 1):
        left, right = bins[index], bins[index + 1]
        counts.append(sum(left <= value < right for value in values))
        labels.append("%s+" % left if index == len(bins) - 2 else "%s–%s" % (left, right - 1))
    ax1.bar(range(len(counts)), counts, color=style["colors"]["primary"], alpha=0.84)
    ax1.set_xticks(range(len(labels)))
    ax1.set_xticklabels(labels, rotation=40, fontsize=8)
    ax1.set_xlabel("被引次数区间 / Citation bucket" if zh_ok else "Citation-count bucket")
    ax1.set_ylabel("随机样本论文数" if zh_ok else "Random-sample works")
    ax1.set_title("随机样本引用分布 / Random-sample distribution"
                  if zh_ok else "Random-sample citation distribution", fontsize=11)
    ax1.grid(axis="y", linestyle="--", alpha=0.4)

    ax2.axis("off")
    h_prefix = "≥" if impact.get("h_index_status") == "lower_bound" else ""
    labels_zh = ["随机样本", "篇均被引", "中位被引", "零被引占比", "语料 h-index"]
    labels_en = ["Random sample", "Mean citations", "Median citations",
                 "Uncited share", "Corpus h-index"]
    labels = [
        ("%s\n%s" % pair) if zh_ok else pair[1]
        for pair in zip(labels_zh, labels_en)
    ]
    metrics = [
        (labels[0], str(impact.get("sample_size", 0))),
        (labels[1], "%.1f" % impact.get("mean_citations", 0)),
        (labels[2], "%.1f" % impact.get("median_citations", 0)),
        (labels[3], "%.1f%%" % (impact.get("uncited_share", 0) * 100)),
        (labels[4], h_prefix + str(impact.get("h_index", 0))),
    ]
    metric_colors = [style["colors"]["primary"], style["colors"]["secondary"],
                     style["colors"]["secondary"], style["colors"]["danger"],
                     style["colors"]["accent"]]
    for index, (label, value) in enumerate(metrics):
        y = 0.86 - index * 0.18
        color = metric_colors[index]
        ax2.add_patch(plt.Rectangle((0.02, y - 0.065), 0.96, 0.13,
                                    facecolor=color, alpha=0.12,
                                    edgecolor=color, linewidth=1.1))
        ax2.text(0.07, y, label, va="center", fontsize=9,
                 color=style["colors"]["text"])
        ax2.text(0.92, y, value, va="center", ha="right", fontsize=14,
                 weight="bold", color=color)
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.set_title("影响力指标 / Impact metrics" if zh_ok else "Impact metrics", fontsize=11)
    fig.suptitle(_chart_title(bundle, "引用影响力画像", "citation impact profile", zh_ok),
                 fontsize=14, y=0.99)
    h_note = (
        "分布/均值为随机样本 n=%s/%s；h-index 用独立高被引集合，精确值或显式下界"
        % (impact.get("sample_size", 0), bundle.get("total_works", 0))
        if zh_ok else
        "Distribution/means: random n=%s/%s; h-index uses a separate descending set "
        "and is exact or an explicit lower bound"
        % (impact.get("sample_size", 0), bundle.get("total_works", 0))
    )
    _add_footer(fig, bundle, zh_ok, h_note)
    return _save(fig, os.path.join(outdir, "citation_impact.png"), style,
                 rect=(0, 0.085, 1, 0.96))


def chart_cooccurrence(bundle, outdir, lang, style):
    plt, zh_ok = _setup_plot(lang, style)
    data = bundle.get("cooccurrence") or {}
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    if not nodes:
        raise RuntimeError("关键词共现数据为空。")
    path = os.path.join(outdir, "cooccurrence.png")
    if not edges:
        rows = nodes[::-1]
        fig, ax = plt.subplots(figsize=style["figsize"]["bar"])
        bars = ax.barh([row["name"] for row in rows], [row["count"] for row in rows],
                       color=style["colors"]["secondary"], alpha=0.86)
        ax.bar_label(bars, fmt="%.0f", padding=3, fontsize=8)
        ax.set_title(_chart_title(bundle, "高频关键词（网络降级视图）",
                                  "top keywords (network fallback)", zh_ok), fontsize=14)
        _add_footer(fig, bundle, zh_ok, "基于随机样本" if zh_ok else "Random sample")
        return _save(fig, path, style)

    # Keep this chart byte-for-byte reproducible across environments: an internal
    # deterministic layout is used instead of changing appearance when an optional
    # graph package happens to be installed. Limit density before drawing.
    connected = {edge["source"] for edge in edges} | {edge["target"] for edge in edges}
    visible = [node for node in nodes if node["name"] in connected][:12]
    visible_names = {node["name"] for node in visible}
    visible_edges = sorted(
        [edge for edge in edges
         if edge["source"] in visible_names and edge["target"] in visible_names],
        key=lambda row: (-row["count"], row["source"], row["target"]),
    )[:18]
    if not visible_edges:
        visible = nodes[:12]
    positions = {}
    for index, node in enumerate(visible):
        angle = (2 * math.pi * index / max(len(visible), 1)) + math.pi / 2
        positions[node["name"]] = (math.cos(angle), math.sin(angle))

    fig, ax = plt.subplots(figsize=style["figsize"]["network"])
    max_edge = max((edge["count"] for edge in visible_edges), default=1)
    for edge in visible_edges:
        left, right = positions[edge["source"]], positions[edge["target"]]
        ax.plot([left[0], right[0]], [left[1], right[1]],
                color=style["colors"]["primary"],
                linewidth=0.5 + 3.0 * edge["count"] / max_edge,
                alpha=0.32, zorder=1)
    max_node = max((node["count"] for node in visible), default=1)
    for node in visible:
        x, y = positions[node["name"]]
        size = 360 + 2300 * node["count"] / max_node
        # 节点用主色(次色在白底上对比不足,小节点发虚)
        ax.scatter([x], [y], s=size, color=style["colors"]["primary"],
                   alpha=0.9, edgecolor="white", linewidth=1.1, zorder=2)
        ax.text(x * 1.18, y * 1.18, node["name"], fontsize=8.5,
                ha="center", va="center", color=style["colors"]["text"], zorder=3,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.76,
                      "pad": 0.8})
    ax.set_xlim(-1.42, 1.42)
    ax.set_ylim(-1.42, 1.42)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(_chart_title(bundle, "关键词共现网络", "keyword co-occurrence network", zh_ok),
                 fontsize=14, pad=12)
    note = (
        "随机样本 n=%s；节点面积=主题词频次，边宽=共现次数；确定性 Top 12/18 视图"
        % data.get("documents") if zh_ok else
        "Random n=%s; node area=term frequency, edge width=co-occurrence; "
        "deterministic Top 12/18 view" % data.get("documents")
    )
    _add_footer(fig, bundle, zh_ok, note)
    return _save(fig, path, style)


def render_chart(bundle, chart_type, outdir, lang="auto", style_path=None):
    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("不支持的 bundle schema_version：%s" % bundle.get("schema_version"))
    os.makedirs(outdir, exist_ok=True)
    style = load_style(style_path)
    if chart_type == "pub-citations":
        return chart_pub_citations(bundle, outdir, lang, style)
    if chart_type == "top-institutions":
        return _ranking_chart(bundle, outdir, lang, style, "institutions")
    if chart_type == "top-authors":
        return _ranking_chart(bundle, outdir, lang, style, "authors")
    if chart_type == "top-sources":
        return _ranking_chart(bundle, outdir, lang, style, "sources")
    if chart_type == "countries":
        return _ranking_chart(bundle, outdir, lang, style, "countries")
    if chart_type == "topic-distribution":
        return chart_topic_distribution(bundle, outdir, lang, style)
    if chart_type == "citation-impact":
        return chart_citation_impact(bundle, outdir, lang, style)
    if chart_type == "keyword-frequency":
        return _ranking_chart(bundle, outdir, lang, style, "keywords")
    if chart_type == "cooccurrence":
        return chart_cooccurrence(bundle, outdir, lang, style)
    raise RuntimeError("未知图表类型：%s" % chart_type)


def render_all(bundle, outdir, lang="auto", style_path=None):
    files, failures = [], []
    for chart_type in CHART_TYPES:
        try:
            files.append(render_chart(bundle, chart_type, outdir, lang, style_path))
        except Exception as exc:
            failures.append({"chart": chart_type, "error": _sanitize_error(exc)})
    return files, failures


def _external_json(url, headers=None, retries=3):
    request = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("外部数据源请求失败 HTTP %s" % exc.code) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("外部数据源网络失败：%s" % exc) from exc
    raise RuntimeError("外部数据源请求失败")


def _crossref_counts(field, years, mailto=None):
    counts = {}
    for year in years:
        params = {
            "query.bibliographic": field,
            "filter": "from-pub-date:%s-01-01,until-pub-date:%s-12-31" % (year, year),
            "rows": 0,
        }
        if mailto:
            params["mailto"] = mailto
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
        payload = _external_json(url)
        counts[year] = payload.get("message", {}).get("total-results", 0)
    return counts


def _s2_counts(field, years, api_key):
    if not api_key:
        raise RuntimeError("Semantic Scholar 交叉核验需要 API key。")
    counts = {}
    headers = {"User-Agent": USER_AGENT, "x-api-key": api_key}
    for year in years:
        params = {"query": field, "year": str(year), "limit": 1, "fields": "paperId"}
        url = ("https://api.semanticscholar.org/graph/v1/paper/search?" +
               urllib.parse.urlencode(params))
        payload = _external_json(url, headers=headers)
        counts[year] = payload.get("total", 0)
    return counts


def cmd_resolve(args):
    _required_key()
    candidates = core.resolve_topic(args.field, args.mailto)
    print(json.dumps({"field": args.field, "candidates": candidates},
                     ensure_ascii=False, indent=2))
    return 0 if candidates else 1


def cmd_config(args):
    if args.show or args.clear:
        return core.cmd_config(SimpleNamespace(
            api_key=None, show=args.show, clear=args.clear
        ))
    key = args.api_key
    if args.stdin:
        key = sys.stdin.readline().strip()
    if not key:
        try:
            key = getpass.getpass("OpenAlex API key（输入不回显）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(json.dumps({"error": "已取消配置"}, ensure_ascii=False))
            return 130
    if any(character in key for character in ("\r", "\n", "\x00")):
        print(json.dumps({"error": "API key 含非法控制字符，未保存。"}, ensure_ascii=False))
        return 2
    return core.cmd_config(SimpleNamespace(api_key=key, show=False, clear=False))


def cmd_fetch(args):
    bundle = collect_bundle(args)
    files = write_bundle(bundle, args.out)
    print(json.dumps({
        "files": files, "partial": bundle["partial"],
        "provenance": bundle.get("provenance", {}),
        "warnings": bundle.get("warnings", []),
        "failed_sections": bundle["failed_sections"],
        "notes": [
            "非 Topic 检索默认为标题+摘要短语口径；--search-scope fulltext 才是全文分词口径，命中量更大但噪声更多。",
            "所有主报告统计来自 OpenAlex；引用分布使用随机样本，h-index 使用独立高被引集合。",
            "读取 bundle.json 可在不重复消耗 API 的情况下重新绘图。",
        ],
    }, ensure_ascii=False, indent=2))
    return 3 if bundle["partial"] else 0


def cmd_chart(args):
    with open(args.data, encoding="utf-8") as handle:
        bundle = json.load(handle)
    if args.type == "all":
        files, failures = render_all(bundle, args.out, args.lang, args.style)
    else:
        files = [render_chart(bundle, args.type, args.out, args.lang, args.style)]
        failures = []
    print(json.dumps({"files": files, "chart_failures": failures,
                      "provenance": bundle.get("provenance", {}),
                      "warnings": bundle.get("warnings", [])},
                     ensure_ascii=False, indent=2))
    return 3 if failures else 0


def cmd_report(args):
    bundle = collect_bundle(args)
    data_files = write_bundle(bundle, args.out)
    chart_files, chart_failures = render_all(bundle, args.out, args.lang, args.style)
    partial = bundle["partial"] or bool(chart_failures)
    print(json.dumps({
        "files": data_files + chart_files,
        "summary": {
            "field": args.field, "years": "%s-%s" % (args.start, args.end),
            "total_works": bundle["total_works"],
            "snapshot_date": bundle["snapshot_date"], "partial": partial,
            "provenance": bundle.get("provenance", {}),
        },
        "warnings": bundle.get("warnings", []),
        "failed_sections": bundle["failed_sections"],
        "chart_failures": chart_failures,
        "notes": [
            "年度被引量是各发表年份论文截至快照日的累计被引，不是自然年度内收到的引用。",
            "引用均值和分布来自随机样本；h-index 来自独立的被引降序集合，并标注精确值或下界。",
            "非 Topic 检索默认为标题+摘要短语口径；--search-scope fulltext 才是全文分词口径，命中量更大但噪声更多。",
            "所有 CSV 均可用于逐项复核；partial=true 时不得把缺失图表解释为零。",
        ],
    }, ensure_ascii=False, indent=2))
    return 3 if partial else 0


def cmd_crosscheck(args):
    _required_key()
    years = list(range(args.start, args.end + 1))
    base = core.build_query(args)
    results = {"openalex": core.annual_publications(base, args.start, args.end, args.mailto)}
    failures = []
    providers = [item.strip() for item in args.providers.split(",") if item.strip()]
    unknown = sorted(set(providers) - {"crossref", "semanticscholar"})
    if unknown:
        raise RuntimeError("未知交叉核验数据源：%s" % ", ".join(unknown))
    if "crossref" in providers:
        try:
            results["crossref"] = _crossref_counts(args.field, years, args.mailto)
        except Exception as exc:
            failures.append({"provider": "crossref", "error": _sanitize_error(exc)})
    if "semanticscholar" in providers:
        try:
            key = args.semantic_scholar_api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
            results["semanticscholar"] = _s2_counts(args.field, years, key)
        except Exception as exc:
            failures.append({"provider": "semanticscholar", "error": _sanitize_error(exc)})

    os.makedirs(args.out, exist_ok=True)
    json_path = os.path.join(args.out, "source_crosscheck.json")
    payload = {
        "query": {"field": args.field, "start": args.start, "end": args.end},
        "counts": {name: {str(year): count for year, count in values.items()}
                   for name, values in results.items()},
        "partial": bool(failures), "failures": failures,
        "notes": [
            "各数据源覆盖范围和检索算法不同；这里只并列年度检索命中数，不合并、不互相替代。",
            "OpenAlex 可使用 Topic 过滤；Crossref/Semantic Scholar 使用文本检索，差异不代表错误。",
        ],
    }
    atomic_write_json(json_path, payload)
    csv_path = os.path.join(args.out, "source_crosscheck.csv")
    headers = ["publication_year"] + list(results)
    core.write_csv(csv_path, headers, [
        [year] + [results[name].get(year, "") for name in results] for year in years
    ])
    print(json.dumps({"files": [json_path, csv_path], "partial": bool(failures),
                      "failures": failures, "notes": payload["notes"]},
                     ensure_ascii=False, indent=2))
    return 3 if failures else 0


def cmd_info(args):
    print(json.dumps({
        "skill": "bibliometric-fusion-v1",
        "openalex_key_configured": bool(core.load_api_key()),
        "charts": list(CHART_TYPES),
        "workflow": ["resolve", "fetch", "chart", "report", "crosscheck"],
        "schema_version": SCHEMA_VERSION,
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    last_year = date.today().year - 1
    parser = argparse.ArgumentParser(
        description="融合严谨 OpenAlex 统计与丰富图表的文献计量技能"
    )
    parser.add_argument("--out", default="./bibliometric_fusion_output")
    parser.add_argument("--lang", default="auto", choices=["auto", "zh", "en"])
    parser.add_argument("--style", default=None)
    parser.add_argument("--api-key", default=None,
                        help="OpenAlex key 单次覆盖；持久配置请用 config 交互输入")
    parser.add_argument("--mailto", default=None,
                        help="仅供 Crossref User-Agent；OpenAlex 不再发送 mailto")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(subparser):
        subparser.add_argument("--out", default=argparse.SUPPRESS)
        subparser.add_argument("--lang", choices=["auto", "zh", "en"],
                               default=argparse.SUPPRESS)
        subparser.add_argument("--style", default=argparse.SUPPRESS)
        subparser.add_argument("--api-key", default=argparse.SUPPRESS)
        subparser.add_argument("--mailto", default=argparse.SUPPRESS)

    def field_args(subparser):
        common(subparser)
        subparser.add_argument("--field", required=True)
        subparser.add_argument("--topic-id", default=None)
        subparser.add_argument("--search-scope", default="title_abstract",
                               choices=list(core.SEARCH_SCOPES),
                               help="非 Topic 检索的范围：title_abstract=标题+摘要"
                                    "短语（默认）；title=仅标题短语；fulltext=顶层"
                                    "search 全文分词（宽口径，噪声多）")
        subparser.add_argument("--start", type=int, default=last_year - 9)
        subparser.add_argument("--end", type=int, default=last_year)

    def collection_args(subparser):
        field_args(subparser)
        subparser.add_argument("--sample-size", type=int, default=core.SAMPLE_SIZE,
                               help="每年累计被引估计的随机样本量")
        subparser.add_argument("--impact-sample", type=int, default=DEFAULT_IMPACT_SAMPLE)
        subparser.add_argument("--cooc-sample", type=int,
                               default=DEFAULT_COOCCURRENCE_SAMPLE)
        subparser.add_argument("--h-index-limit", type=int, default=DEFAULT_H_INDEX_LIMIT)
        subparser.add_argument("--top-n", type=int, default=15)
        subparser.add_argument("--keyword-top", type=int, default=20)
        subparser.add_argument("--cooc-top", type=int, default=25)
        subparser.add_argument("--cooc-edges", type=int, default=60)
        subparser.add_argument("--exact", action="store_true")
        subparser.add_argument("--max-exact-works", type=int,
                               default=core.DEFAULT_MAX_EXACT_WORKS)
        subparser.add_argument("--force-exact", action="store_true")

    command = subparsers.add_parser("config", help="安全配置 OpenAlex API key")
    command.add_argument("--api-key", default=None,
                         help="非交互/CI 使用；日常建议直接运行 config")
    command.add_argument("--stdin", action="store_true", help="从标准输入读取 key")
    command.add_argument("--show", action="store_true")
    command.add_argument("--clear", action="store_true")
    command.set_defaults(func=cmd_config)

    command = subparsers.add_parser("resolve", help="检索 OpenAlex Topic 候选")
    common(command)
    command.add_argument("--field", required=True)
    command.set_defaults(func=cmd_resolve)

    command = subparsers.add_parser("fetch", help="抓取一次并保存可复用 bundle/CSV")
    collection_args(command)
    command.set_defaults(func=cmd_fetch)

    command = subparsers.add_parser("chart", help="从既有 bundle 离线渲染图表")
    common(command)
    command.add_argument("--data", required=True, help="fetch 生成的 bundle.json")
    command.add_argument("--type", required=True, choices=list(CHART_TYPES) + ["all"])
    command.set_defaults(func=cmd_chart)

    command = subparsers.add_parser("report", help="一次抓取并生成完整图表与 CSV")
    collection_args(command)
    command.set_defaults(func=cmd_report)

    command = subparsers.add_parser("crosscheck", help="多源年度命中数并列核验")
    field_args(command)
    command.add_argument("--providers", default="crossref",
                         help="crossref,semanticscholar（逗号分隔）")
    command.add_argument("--semantic-scholar-api-key", default=None)
    command.set_defaults(func=cmd_crosscheck)

    command = subparsers.add_parser("list-charts", help="列出全部图表类型")
    command.set_defaults(func=cmd_info)
    command = subparsers.add_parser("info", help="显示配置与能力")
    command.set_defaults(func=cmd_info)

    args = parser.parse_args(argv)
    if getattr(args, "api_key", None):
        core._CLI_API_KEY = args.api_key.strip()
    try:
        if hasattr(args, "start") and args.start > args.end:
            raise RuntimeError("--start 不能晚于 --end。")
        for name in ("sample_size", "impact_sample", "cooc_sample"):
            if hasattr(args, name) and not 1 <= getattr(args, name) <= MAX_ANALYSIS_SAMPLE:
                raise RuntimeError("--%s 必须在 1—%s 之间。" %
                                   (name.replace("_", "-"), MAX_ANALYSIS_SAMPLE))
        for name in ("h_index_limit", "top_n", "keyword_top", "cooc_top", "cooc_edges"):
            if hasattr(args, name) and getattr(args, name) < 1:
                raise RuntimeError("--%s 必须为正整数。" % name.replace("_", "-"))
        return args.func(args)
    except RuntimeError as exc:
        print(json.dumps({"error": _sanitize_error(exc)}, ensure_ascii=False))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
