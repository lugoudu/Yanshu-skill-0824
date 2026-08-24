#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
openalex_core.py — 融合版的 OpenAlex 数据、安全与基础绘图内核

子命令：
  resolve    解析学术领域名称 → OpenAlex topic 候选
  trend      年度发文量 + 被引量组合趋势图（柱状 + 折线，双轴）
  top        Top 机构 / 作者 / 期刊 / 关键词排名条形图
  countries  国家/地区发文分布图
  cooc       关键词共现网络图（需 networkx，缺失时自动降级为 Top 关键词条形图）
  export     导出文献明细清单 CSV（标题/作者/年份/期刊/被引/DOI），供检索核对
  config     验证并原子写入用户配置目录中的 OpenAlex API key

通用约定：
  - 所有图表输出 PNG（高分辨率）+ 同名 CSV（utf-8-sig，Excel 可直接打开）
  - 标准输出打印 JSON：{"files": [...], "summary": {...}, "notes": [...]}
  - 失败时以非零退出码退出，stdout 打印 {"error": "..."} 便于调用方诊断
  - 图表脚注统一标注数据来源与统计口径，保证学术规范
  - OpenAlex API key（主分析必需；在 OpenAlex 设置页申请）：
      融合入口默认使用不回显的交互配置，并写入包外用户配置目录；
      也支持    环境变量 OPENALEX_API_KEY、--api-key 单次覆盖、
                全局文件 ~/.config/openalex/api_key
      报错信息中的 URL 一律脱敏；key 不写入 Skill 包
"""

import argparse
import csv
import itertools
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date

API_BASE = os.environ.get("OPENALEX_API_BASE", "https://api.openalex.org").rstrip("/")
USER_AGENT = "bibliometric-fusion-v1/1.0 (competition entry)"

# 端点信任分级（安全防线：防止密钥随被改写的端点发往非预期服务）
#   default         —— 官方默认端点，可信
#   loopback        —— 127.0.0.1/::1/localhost（自建 mock、测试、本地开发），可信
#   external-custom —— 其它公网自定义域名（自建镜像），默认不携带 key，除非用户显式授权
_CUSTOM_ENDPOINT_OK = os.environ.get("OPENALEX_ALLOW_CUSTOM_ENDPOINT", "").strip() in ("1", "true", "yes", "on")


def _endpoint_trust_level(base):
    """返回端点信任级别：'default' | 'loopback' | 'external-custom'。"""
    low = (base or "").lower().strip()
    if low in ("", "https://api.openalex.org", "http://api.openalex.org"):
        return "default"
    try:
        host = urllib.parse.urlparse(low).hostname or ""
    except ValueError:
        return "external-custom"
    if not host:
        return "external-custom"
    if host in ("127.0.0.1", "::1", "localhost") or host.endswith(".localhost"):
        return "loopback"
    # 形如 api.openalex.org 的官方域及子域，即使经环境变量显式写出也视为可信
    if host == "api.openalex.org" or host.endswith(".openalex.org"):
        return "default"
    return "external-custom"


_ENDPOINT_TRUST = _endpoint_trust_level(API_BASE)
RANDOM_SEED = 42          # 抽样随机种子，保证结果可复现
SAMPLE_SIZE = 1000        # 大领域被引量估计的每年抽样规模（OpenAlex 单次抽样上限 10000）
EXACT_CAP = 2000          # 某年作品数 ≤ 此值时改为全量精确统计被引
DEFAULT_MAX_EXACT_WORKS = 20000  # --exact 默认安全上限，避免意外抓取海量记录
MAX_SAMPLE_SIZE = 10000
API_PAGE_SIZE = 100       # OpenAlex 当前 per_page 上限
HTTP_TIMEOUT = 30
MAX_RETRIES = 3

# 导出文献明细时抓取的字段（select 参数）
WORK_SELECT = ("id,display_name,publication_year,authorships,"
               "primary_location,cited_by_count,type,doi")

# API key 配置文件路径（技能包之外，避免随 ZIP/git 泄露）
API_KEY_FILE = os.path.expanduser(os.environ.get("OPENALEX_API_KEY_FILE",
                                                 "~/.config/openalex/api_key"))
# 融合版将配置保存在 Skill 包之外，避免普通 ZIP 打包把 key 一并带走。
# 测试或受管环境可用 BIBLIOMETRIC_FUSION_CONFIG 显式覆盖。
_CONFIG_ROOT = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
PKG_CONFIG_PATH = os.environ.get(
    "BIBLIOMETRIC_FUSION_CONFIG",
    os.path.join(_CONFIG_ROOT, "yize-rd", "bibliometric-fusion", "openalex.json"),
)
_CLI_API_KEY = None           # --api-key 命令行参数（最高优先级）


def load_api_key():
    """安全加载 OpenAlex API key，返回第一个非空值。

    优先级（高 → 低）：
      1. --api-key 命令行参数（单次试用，最少摩擦）
      2. 环境变量 OPENALEX_API_KEY
      3. 用户配置目录中的 openalex.json（config 子命令安全写入）
      4. ~/.config/openalex/api_key（多工具共享的全局位置）
      5. 均无 → 未认证访问（额度和可用性以 OpenAlex 当前政策为准）

    密钥不写入 Skill 包，避免进入 Git 或普通 ZIP。
    """
    if _CLI_API_KEY:
        return _CLI_API_KEY
    key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(PKG_CONFIG_PATH, encoding="utf-8") as f:
            key = (json.load(f).get("openalex_api_key") or "").strip()
            if key:
                return key
    except (OSError, ValueError):
        pass
    try:
        with open(API_KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _redact_url(url):
    """脱敏：隐藏 URL 中的 api_key 值，防止密钥随报错信息或日志泄露。"""
    return re.sub(r"(api_key=)[^&\s]+", r"\1***", url)


# ---------------------------------------------------------------------------
# HTTP 访问层（仅依赖标准库，避免 requests 依赖问题）
# ---------------------------------------------------------------------------

def _fetch_urllib(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        time.sleep(0.1)  # 轻量限速，礼貌访问
        return resp.read().decode("utf-8")

def http_get(path, params, mailto=None):
    """GET OpenAlex API，带重试与限流退避。返回解析后的 JSON dict。

    429 优先遵循 Retry-After；5xx 与网络错误使用指数退避。不会尝试通过
    更换客户端绕过服务端额度或限流。
    """
    params = dict(params)
    # 保留 mailto 形参以兼容旧调用，但不再发送：OpenAlex 已弃用 polite pool。
    del mailto
    api_key = load_api_key()
    if api_key:
        if _ENDPOINT_TRUST == "external-custom" and not _CUSTOM_ENDPOINT_OK:
            # 安全防线：端点被改写为非官方公网域名，且用户未显式授权。
            # 继续请求（不破坏可用性），但剥离 key 并警告，避免凭证发往非预期服务。
            print(
                "WARNING: OPENALEX_API_BASE 指向非官方端点 %s，已剥离 api_key 以保护凭证。"
                "若确为自建镜像，请设置 OPENALEX_ALLOW_CUSTOM_ENDPOINT=1 后重试。"
                % API_BASE,
                file=sys.stderr,
            )
        else:
            params["api_key"] = api_key   # 官方认证方式：query 参数 api_key
    url = API_BASE + path + "?" + urllib.parse.urlencode(params)

    for attempt in range(MAX_RETRIES):
        try:
            return json.loads(_fetch_urllib(url))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(
                    f"OpenAlex API key 无效或已过期（HTTP {e.code}）。"
                    "请重新运行 config --api-key，或更新环境变量 "
                    "OPENALEX_API_KEY / ~/.config/openalex/api_key。"
                ) from e
            if e.code == 429:
                if attempt < MAX_RETRIES - 1:
                    try:
                        retry_after = float(e.headers.get("Retry-After", "2"))
                    except (TypeError, ValueError):
                        retry_after = 2.0
                    time.sleep(min(max(retry_after, 1.0), 30.0))
                    continue
                raise RuntimeError(
                    "OpenAlex 访问受限（HTTP 429）。可能为当前 API 额度不足"
                    "或请求频率过高；请检查账户额度、稍后重试，或先运行 "
                    "python3 tests/test_offline.py 离线验证图表链路。"
                ) from e
            if e.code in (500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                f"OpenAlex 请求失败 HTTP {e.code}: {_redact_url(url)}") from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                "网络访问失败（请检查网络连接）: %s" % _redact_url(str(e))
            ) from e
    raise RuntimeError("OpenAlex 请求失败")


# ---------------------------------------------------------------------------
# OpenAlex 查询封装
# ---------------------------------------------------------------------------

def normalize_topic_id(raw):
    """接受 'T10017' 或 'https://openalex.org/T10017'，统一返回短 id。"""
    return raw.rstrip("/").split("/")[-1]


def resolve_topic(field, mailto=None, limit=5):
    """按名称搜索 OpenAlex topic，返回候选列表（按相关度排序）。"""
    data = http_get("/topics", {"search": field, "per_page": limit}, mailto)
    out = []
    for topic in data.get("results", []):
        out.append({
            "id": normalize_topic_id(topic["id"]),
            "name": topic.get("display_name", ""),
            "works_count": topic.get("works_count", 0),
            "domain": (topic.get("domain") or {}).get("display_name"),
            "field": (topic.get("field") or {}).get("display_name"),
        })
    return out


# 非 Topic 检索的检索范围。默认只搜标题+摘要短语；OpenAlex 顶层 search
# 在标题+摘要+全文联合范围内分词后按“每个词都出现”匹配（非短语），
# “federated learning” 会命中全文里分别出现两个词的论文，热门领域
# 会有数倍噪声，因此全文口径必须显式选择。
SEARCH_SCOPES = ("title_abstract", "title", "fulltext")

_SCOPE_FILTER_KEY = {
    "title_abstract": "title_and_abstract.search",
    "title": "title.search",
}


def _phrase_value(field):
    """把检索词规范成 OpenAlex filter 的带引号短语值。

    引号是短语界定符、逗号是 filter 分隔符，检索词混入这两种字符会
    改变查询语义，一律替换为空格；连续空白折叠成单个空格。
    """
    cleaned = " ".join(str(field or "").split())
    cleaned = " ".join(cleaned.replace('"', " ").replace(",", " ").split())
    if not cleaned:
        raise RuntimeError("检索词为空：请提供非空的研究领域名称。")
    return '"%s"' % cleaned


def build_query(args):
    """构造 works 查询参数：优先 topics.id 过滤，否则按 search_scope 检索。

    默认 scope=title_abstract：filter 追加 title_and_abstract.search:"<field>"
    短语匹配，命中的是“标题或摘要中完整出现该短语”的作品；scope=title
    收紧到仅标题；scope=fulltext 退回顶层 search 的宽口径（分词 AND +
    标题/摘要/全文），仅供显式覆盖口径时使用。

    可选 args.search_chunks（语块列表）：同一 .search 键重复出现，官方
    语义为 AND——各语块短语须同时出现（“分块组合检索”形态）。语块元素
    可以是字符串，也可以是同义表述列表（WoS 式 TS=(A OR B)）：块内以
    | 连接为 OR，块间仍为 AND。
    可选 args.search_variants（词形/同义表述列表）：单键值内以 | 分隔，
    官方语义为 OR——同概念表述任一命中（“词形/同义并集”形态）。
    两者互斥，都未提供时按整串短语检索；各表述同样过 _phrase_value
    清洗（引号/逗号是 filter 语法字符）。
    """
    year_filter = f"publication_year:{args.start}-{args.end}"
    params = {}
    topic_id = getattr(args, "topic_id", None)
    if topic_id:
        params["filter"] = f"topics.id:{normalize_topic_id(topic_id)},{year_filter}"
        return params
    scope = getattr(args, "search_scope", "title_abstract") or "title_abstract"
    if scope == "fulltext":
        params["filter"] = year_filter
        params["search"] = args.field
        return params
    if scope not in _SCOPE_FILTER_KEY:
        raise RuntimeError(
            "未知检索范围：%s（可选 %s）" % (scope, "/".join(SEARCH_SCOPES)))
    key = _SCOPE_FILTER_KEY[scope]
    chunks = getattr(args, "search_chunks", None)
    variants = getattr(args, "search_variants", None)
    parts = [year_filter]
    if chunks:
        for group in chunks:
            # 语块归一为同义表述组: ["a"] 或 ["a", "b"] → "a" 或 "a"|"b"
            terms = [group] if isinstance(group, str) else list(group or [])
            terms = [t for t in terms if str(t).strip()]
            if not terms:
                continue
            parts.append(f"{key}:" +
                         "|".join(_phrase_value(t) for t in terms))
    elif variants:
        parts.append(f"{key}:" + "|".join(_phrase_value(v) for v in variants))
    else:
        parts.append(f"{key}:{_phrase_value(args.field)}")
    params["filter"] = ",".join(parts)
    return params


def annual_publications(base_params, start, end, mailto=None):
    """年度发文量（精确值，group_by 聚合）。

    注意：group_by 请求不要传 per_page，否则分组结果会被截断。
    """
    params = dict(base_params)
    params["group_by"] = "publication_year"
    data = http_get("/works", params, mailto)
    counts = {int(g["key"]): g["count"] for g in data.get("group_by", [])}
    return {y: counts.get(y, 0) for y in range(start, end + 1)}


def fetch_sample(params, n, select, mailto=None):
    """随机抽样 n 条作品记录。

    OpenAlex 不允许 sample 与 page/cursor 组合，且 per_page 上限为 100。
    因此按不同 seed 发起多次独立小批抽样并按作品 id 去重，直到达到目标量。
    """
    if not 1 <= n <= MAX_SAMPLE_SIZE:
        raise RuntimeError(f"抽样量必须在 1—{MAX_SAMPLE_SIZE} 之间。")
    out, seen, attempt = [], set(), 0
    max_attempts = max(10, math.ceil(n / API_PAGE_SIZE) * 3)
    while len(out) < n and attempt < max_attempts:
        batch = min(API_PAGE_SIZE, n - len(out))
        q = {**params, "sample": batch, "seed": RANDOM_SEED + attempt,
             "select": select, "per_page": batch}
        data = http_get("/works", q, mailto)
        results = data.get("results", [])
        if not results:
            break
        for work in results:
            work_id = work.get("id")
            if work_id and work_id not in seen:
                seen.add(work_id)
                out.append(work)
        attempt += 1
    if not out:
        raise RuntimeError("OpenAlex 抽样未取得任何记录。")
    if len(out) < n:
        # 小语料领域:总量接近甚至小于抽样目标时,不同随机 seed 的结果高度
        # 重叠,去重计数永远凑不满 n。接受部分样本远好于空手而归——后者会
        # 连带引用分布/共现网络/合作地图三张图全部消失。
        print(f"[openalex] 抽样取得 {len(out)}/{n} 条去重记录(小语料,接受部分样本)",
              file=sys.stderr)
    return out


def _fetch_all_works(filter_str, extra, select, mailto=None):
    """游标分页全量抓取作品记录（仅用于小年份的精确统计）。"""
    out, cursor = [], "*"
    while True:
        params = {
            "filter": filter_str,
            "select": select,
            "per_page": API_PAGE_SIZE,
            "cursor": cursor,
        }
        params.update(extra)
        data = http_get("/works", params, mailto)
        out.extend(data.get("results", []))
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or not data.get("results"):
            break
    return out


def annual_citations(base_params, year, mailto=None, count_hint=None,
                     sample_size=SAMPLE_SIZE, exact=False,
                     max_exact_works=DEFAULT_MAX_EXACT_WORKS,
                     force_exact=False):
    """单一年份的总被引量与篇均被引。

    默认双口径：作品数 ≤ EXACT_CAP 时全量精确求和；否则用 OpenAlex 的 sample
    分批随机抽样，以「样本平均被引 × 该年作品数」作近似估计。
    exact=True 时全量统计，但默认受 max_exact_works 保护；只有显式
    force_exact=True 才允许越过上限。
    sample_size 可调抽样规模（上限 10000），越大越准、请求越多。

    count_hint 可传入已知的该年作品数（如来自 group_by 聚合），避免重复请求。

    返回 (累计被引量, 是否估计值, 样本量或None, 该年作品数, 作品记录列表,
          累计篇均被引, 总量估计的95%置信区间半宽)
    作品记录列表供导出明细 CSV，方便用户检索核对统计口径。
    """
    params = dict(base_params)
    # 把年份范围替换为单年
    filt = params.get("filter", "")
    parts = [p for p in filt.split(",") if not p.startswith("publication_year:")]
    parts.append(f"publication_year:{year}")
    params["filter"] = ",".join(parts)

    if count_hint is None:
        meta = http_get("/works", {**params, "per_page": 1, "select": "id"}, mailto)
        count = meta.get("meta", {}).get("count", 0)
    else:
        count = count_hint
    if count == 0:
        return 0, False, None, 0, [], 0.0, 0.0

    if exact or count <= EXACT_CAP:
        if exact and count > max_exact_works and not force_exact:
            raise RuntimeError(
                f"{year} 年共有 {count:,} 条作品，超过 --exact 的安全上限 "
                f"{max_exact_works:,}。请调高 --max-exact-works，或确认资源充足后加 "
                "--force-exact。")
        extra = {k: v for k, v in params.items() if k not in ("filter",)}
        works = _fetch_all_works(params["filter"], extra, WORK_SELECT, mailto)
        total = sum(w.get("cited_by_count") or 0 for w in works)
        return total, False, None, count, works, total / count, 0.0

    n = min(sample_size, count)
    works = fetch_sample(params, n, WORK_SELECT, mailto)
    hits = [w.get("cited_by_count") or 0 for w in works]
    if not hits:
        return 0, True, 0, count, [], 0.0, 0.0
    mean = sum(hits) / len(hits)
    if len(hits) > 1:
        variance = sum((x - mean) ** 2 for x in hits) / (len(hits) - 1)
        ci95_total = 1.96 * math.sqrt(variance / len(hits)) * count
    else:
        ci95_total = 0.0
    return round(mean * count), True, len(hits), count, works, mean, round(ci95_total)


def group_top(base_params, group_by, n, mailto=None):
    """通用 group_by 聚合，返回 Top-n (名称, 计数, key)。"""
    params = dict(base_params)
    params["group_by"] = group_by   # 不要传 per_page，会截断分组
    data = http_get("/works", params, mailto)
    out = []
    for g in data.get("group_by", [])[:n]:
        out.append({
            "name": g.get("key_display_name") or g["key"],
            "key": g["key"],
            "count": g["count"],
        })
    return out


def sample_works_keywords(base_params, sample_size, mailto=None):
    """随机抽样作品：返回 (关键词列表, 作品记录列表)。

    作品记录一并返回，供导出明细 CSV 供用户核对共现分析的数据基础。
    """
    docs, works = [], []
    for w in fetch_sample(base_params, sample_size, WORK_SELECT + ",keywords", mailto):
        works.append(w)
        kws = [k["display_name"] for k in (w.get("keywords") or []) if k.get("display_name")]
        if kws:
            docs.append(kws)
    return docs, works


# ---------------------------------------------------------------------------
# 文献明细导出
# ---------------------------------------------------------------------------

def work_row(w):
    """把 OpenAlex work 记录压平为 CSV 行字段（dict）。"""
    auths = w.get("authorships") or []
    names = [a.get("author", {}).get("display_name", "") for a in auths]
    names = [n for n in names if n]
    authors = "; ".join(names[:3]) + (" et al." if len(names) > 3 else "")
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    return {
        "title": w.get("display_name") or "",
        "authors": authors,
        "year": w.get("publication_year") or "",
        "venue": src.get("display_name") or "",
        "type": w.get("type") or "",
        "cited_by_count": w.get("cited_by_count") or 0,
        "doi": w.get("doi") or "",
        "openalex_id": w.get("id") or "",
    }


def export_works(base_params, n, sort, mailto=None):
    """抓取 n 条作品记录用于明细导出。

    sort: "cited_by_count:desc"（默认，高被引优先）/
          "publication_date:desc"（最新优先）/ "random"（随机抽样核对用）
    """
    if sort == "random":
        return fetch_sample(base_params, n, WORK_SELECT, mailto)
    out, cursor = [], "*"
    while len(out) < n:
        q = {**base_params, "select": WORK_SELECT, "sort": sort,
             "per_page": API_PAGE_SIZE, "cursor": cursor}
        data = http_get("/works", q, mailto)
        results = data.get("results", [])
        if not results:
            break
        out.extend(results)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break
    return out[:n]


def write_works_csv(path, works, rank=False, extra_col=None):
    """把作品记录列表写成明细 CSV。

    extra_col: (列名, 取值函数 work->value)，用于 trend 明细中标注是否抽样年份。
    """
    header = (["rank"] if rank else []) + ["title", "authors", "year", "venue",
                                           "type", "cited_by_count", "doi",
                                           "openalex_id"]
    if extra_col:
        header.append(extra_col[0])
    rows = []
    for i, w in enumerate(works, 1):
        r = work_row(w)
        row = ([i] if rank else []) + [r["title"], r["authors"], r["year"],
                                       r["venue"], r["type"], r["cited_by_count"],
                                       r["doi"], r["openalex_id"]]
        if extra_col:
            row.append(extra_col[1](w))
        rows.append(row)
    write_csv(path, header, rows)


# ---------------------------------------------------------------------------
# 绘图基础设施：字体、样式、脚注
# ---------------------------------------------------------------------------

# 常见中文字体候选，按优先级探测
CJK_FONT_CANDIDATES = [
    # 内置思源黑体简体中文版优先(随仓库分发,setup_plot 里 addfont 显式注册):
    # 服务器 fonts-noto-cjk 的 .ttc 只被 matplotlib 注册为 JP 变体(日式字形),
    # 各系统默认字体又不一致——内置字体保证任何环境渲染统一且字形正确。
    "Source Han Sans CN",
    "PingFang SC", "PingFang HK", "Hiragino Sans GB",
    "Microsoft YaHei", "SimHei",
    "Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Serif CJK JP",
    "Source Han Sans SC", "WenQuanYi Micro Hei",
    "STHeiti", "Heiti TC", "Songti SC", "SimSong", "Arial Unicode MS",
]

# 随仓库分发的内置字体(app/bibliometric/fonts/)
_BUNDLED_FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "fonts", "SourceHanSansCN-Regular.otf")

STYLE_PATH_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "assets", "chart_style.json")

FALLBACK_STYLE = {
    "colors": {"primary": "#660874", "secondary": "#82318E",
               "line": "#E87722", "grid": "#DDDDDD", "text": "#333333"},
    "figsize": {"trend": [10, 6], "bar": [10, 7], "network": [11, 8]},
    "dpi": 200,
}


def load_style(path=None):
    p = path or STYLE_PATH_DEFAULT
    try:
        with open(p, "r", encoding="utf-8") as f:
            style = FALLBACK_STYLE.copy()
            style.update(json.load(f))
            return style
    except OSError:
        return FALLBACK_STYLE


def setup_plot(lang="auto", style=None):
    """初始化 matplotlib，探测中文字体。

    返回 (plt, zh_ok)。zh_ok=False 表示无中文字体，调用方应改用英文标签，
    以免出现方框乱码。
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib import font_manager

    # 内置字体先注册(幂等:重复 addfont 无害),再探测候选
    if os.path.exists(_BUNDLED_FONT):
        try:
            font_manager.fontManager.addfont(_BUNDLED_FONT)
        except Exception:
            pass
    installed = {f.name for f in font_manager.fontManager.ttflist}
    cjk = next((f for f in CJK_FONT_CANDIDATES if f in installed), None)
    if cjk:
        plt.rcParams["font.sans-serif"] = [cjk, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
    zh_ok = (lang == "zh" and cjk is not None) or (lang == "auto" and cjk is not None)
    if style:
        c = style["colors"]
        plt.rcParams.update({
            "axes.edgecolor": c["text"], "axes.labelcolor": c["text"],
            "xtick.color": c["text"], "ytick.color": c["text"],
            "axes.grid": True, "grid.color": c["grid"], "grid.alpha": 0.6,
        })
    return plt, zh_ok


def _fmt_wan(x, pos=None):
    """大数值以“万”为单位显示（如 145万），避免 matplotlib 默认的
    科学计数法角标（1e6）被误读为“数值不到 1”。"""
    if abs(x) >= 10000:
        s = f"{x / 10000:.1f}".rstrip("0").rstrip(".")
        return s + "万"
    return f"{x:g}"


def use_latin_font_for_latin_ticks(ax):
    """让纯拉丁名称使用 DejaVu Sans，避免部分中文字体缺少变音字符。"""
    for tick in ax.get_yticklabels():
        if not re.search(r"[\u3400-\u9fff]", tick.get_text()):
            tick.set_fontfamily("DejaVu Sans")


def add_footer(fig, zh_ok, extra=None):
    """统一脚注：数据来源 + 口径说明 + 生成日期。"""
    src = "数据来源：OpenAlex (https://openalex.org)" if zh_ok else \
          "Data source: OpenAlex (https://openalex.org)"
    stamp = (f"生成日期：{date.today().isoformat()}" if zh_ok
             else f"Generated: {date.today().isoformat()}")
    parts = [src] + ([extra] if extra else []) + [stamp]
    fig.text(0.01, 0.01, "  |  ".join(parts), fontsize=8, color="#777777")


def _csv_safe(value):
    """Protect Excel/WPS users from formula injection in API-provided text."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([_csv_safe(value) for value in header])
        w.writerows([[_csv_safe(value) for value in row] for row in rows])


def ensure_outdir(d):
    os.makedirs(d, exist_ok=True)
    return d


def emit(files, summary, notes):
    print(json.dumps({"files": files, "summary": summary, "notes": notes},
                     ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 各子命令实现
# ---------------------------------------------------------------------------

def cmd_resolve(args):
    candidates = resolve_topic(args.field, args.mailto)
    emit([], {"field": args.field, "candidates": candidates},
         ["取第一个候选即为默认匹配；若候选明显不符，可用 --topic-id 显式指定。"]
         if candidates else ["未找到匹配主题，建议改用英文领域名称重试，"
                             "或不指定 topic 直接使用全文检索模式。"])
    return 0 if candidates else 1


def cmd_trend(args):
    plt, zh_ok = setup_plot(args.lang)
    style = load_style(args.style)
    base = build_query(args)

    pubs = annual_publications(base, args.start, args.end, args.mailto)
    years = list(range(args.start, args.end + 1))
    citations, estimated_flags, sample_sizes, per_work, ci95_values = [], [], [], [], []
    audit_works = []   # 各年被引统计所依据的文献记录（抽样年份为样本，小年份为全量）
    for y in years:
        cit, est, n, _cnt, works, mean, ci95 = annual_citations(
            base, y, args.mailto, count_hint=pubs.get(y),
            sample_size=args.sample_size, exact=args.exact,
            max_exact_works=args.max_exact_works,
            force_exact=args.force_exact)
        citations.append(cit)
        estimated_flags.append(est)
        sample_sizes.append(n)
        per_work.append(mean)
        ci95_values.append(ci95)
        audit_works.extend((y, est, w) for w in works)

    outdir = ensure_outdir(args.out)
    png = os.path.join(outdir, "trend.png")
    csv_path = os.path.join(outdir, "trend.csv")

    # ---- 绘图：双面板 ----
    # 上面板：柱状 = 发文量（左轴），折线 = 这些作品截至快照日的累计被引（右轴）
    # 下面板：折线 = 累计篇均被引（抽样年份为样本均值）
    from matplotlib.ticker import FuncFormatter
    xs = list(range(len(years)))
    labels = [str(y) for y in years]
    fs = style["figsize"]["trend"]
    fig, (ax1, ax3) = plt.subplots(
        2, 1, figsize=(fs[0], fs[1] + 2.2), sharex=True,
        gridspec_kw={"height_ratios": [2, 1], "hspace": 0.12})
    c = style["colors"]

    bars = ax1.bar(xs, [pubs[y] for y in years], color=c["secondary"],
                   alpha=0.85, zorder=2,
                   label=("年度发文量（篇）" if zh_ok else "Publications"))
    ax1.set_ylabel("发文量（篇）" if zh_ok else "Publications", fontsize=12)
    ax1.yaxis.set_major_formatter(FuncFormatter(_fmt_wan))
    ax1.bar_label(bars, fmt=_fmt_wan, fontsize=8, padding=2)
    ax1.set_zorder(ax1.get_zorder() + 1)
    ax1.patch.set_visible(False)

    ax2 = ax1.twinx()
    est_mark = "（估计值）" if zh_ok else " (est.)"
    ax2.plot(xs, citations, color=c["line"], marker="o",
             linewidth=2.2, zorder=3,
             label=("按发表年累计被引量" + est_mark if any(estimated_flags)
                    else ("按发表年累计被引量（次）" if zh_ok
                          else "Cumulative citations by publication year")))
    if any(ci95_values):
        lower = [max(0, value - ci) for value, ci in zip(citations, ci95_values)]
        upper = [value + ci for value, ci in zip(citations, ci95_values)]
        ax2.fill_between(xs, lower, upper, color=c["line"], alpha=0.15,
                         label=("估计值 95% 置信区间" if zh_ok else "Estimate 95% CI"))
    ax2.set_ylabel("累计被引量（次）" if zh_ok else "Cumulative citations", fontsize=12)
    ax2.yaxis.set_major_formatter(FuncFormatter(_fmt_wan))
    ax2.grid(False)

    title = (f"“{args.field}”领域年度发文量与累计被引量（{args.start}—{args.end}）"
             if zh_ok else
             f'"{args.field}": publications & cumulative citations ({args.start}-{args.end})')
    ax1.set_title(title, fontsize=14, pad=14)

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=10, frameon=False)

    ax3.plot(xs, per_work, color=c["primary"], marker="s", linewidth=2.0,
             label=("累计篇均被引（次/篇）" + est_mark if any(estimated_flags)
                    else ("累计篇均被引（次/篇）" if zh_ok
                          else "Cumulative citations per work")))
    ax3.set_ylabel("累计篇均被引（次/篇）" if zh_ok else "Cumulative citations per work",
                   fontsize=11)
    ax3.set_xticks(xs)
    ax3.set_xticklabels(labels)
    ax3.legend(loc="upper left", fontsize=10, frameon=False)
    ax3.grid(axis="y", alpha=0.5)

    note = None
    if any(estimated_flags):
        note = ("累计被引量为随机抽样估计（每年样本 n≤%d，多 seed），阴影为 95%% CI"
                % args.sample_size
                if zh_ok else
                f"Cumulative citations estimated from random samples "
                f"(n≤{args.sample_size}/yr, multiple seeds); band is 95% CI")
    add_footer(fig, zh_ok, note)
    # twin axis 与 tight_layout 不兼容；显式留出右轴、标题和脚注空间。
    fig.subplots_adjust(left=0.10, right=0.88, bottom=0.10, top=0.90, hspace=0.20)
    fig.savefig(png, dpi=style["dpi"])
    plt.close(fig)

    write_csv(csv_path,
              ["snapshot_date", "publication_year", "publications",
               "cumulative_citations", "cumulative_citations_per_work",
               "citations_estimated", "sample_size", "cumulative_citations_ci95"],
              [[date.today().isoformat(), y, pubs[y], c_, round(m, 2), int(e),
                s or "", ci]
               for y, c_, e, s, m, ci in zip(
                   years, citations, estimated_flags, sample_sizes, per_work, ci95_values)])

    # 导出被引统计所依据的文献明细，方便用户检索核对
    works_csv = os.path.join(outdir, "trend_works.csv")
    rows = []
    for y, est, w in audit_works:
        r = work_row(w)
        rows.append([r["title"], r["authors"], y, r["venue"], r["type"],
                     r["cited_by_count"], r["doi"], r["openalex_id"], int(est)])
    write_csv(works_csv,
              ["title", "authors", "year", "venue", "type", "cited_by_count",
               "doi", "openalex_id", "is_sample"],
              rows)

    files = [png, csv_path, works_csv]
    total_pubs, total_cits = sum(pubs.values()), sum(citations)
    summary = {"field": args.field, "years": f"{args.start}-{args.end}",
               "snapshot_date": date.today().isoformat(),
               "total_publications": total_pubs,
               "total_cumulative_citations": total_cits,
               "overall_cumulative_citations_per_work": round(total_cits / total_pubs, 2)
               if total_pubs else 0,
               "citations_estimated": any(estimated_flags),
               "works_exported": len(rows)}
    if any(estimated_flags):
        notes = ["被引量指各发表年份作品截至快照日的累计被引量，并非当年获得的引用；"
                 "其中大样本年份为抽样估计，误差取决于该年被引分布，trend.csv 提供 95% 置信区间；"
                 "统计所依据的文献样本已导出至 trend_works.csv 供核对；"
                 "需要精确值可加 --exact 全量统计（消耗更多 API 额度）。"]
    else:
        notes = ["被引量指各发表年份作品截至快照日的累计被引量，并非当年获得的引用；"
                 "本次为全量精确统计；"
                 "统计所依据的文献清单已导出至 trend_works.csv 供核对。"]
    if args.exact:
        notes.append("本次运行使用 --exact 全量精确模式。")
    emit(files, summary, notes)
    return 0


def cmd_top(args):
    plt, zh_ok = setup_plot(args.lang)
    style = load_style(args.style)
    base = build_query(args)

    group_map = {
        "institutions": "authorships.institutions.id",
        "authors": "authorships.author.id",
        "sources": "primary_location.source.id",
        "keywords": "keywords.id",
    }
    dim_zh = {"institutions": "机构", "authors": "作者",
              "sources": "期刊/会议", "keywords": "关键词"}
    rows = group_top(base, group_map[args.dimension], args.n, args.mailto)

    outdir = ensure_outdir(args.out)
    png = os.path.join(outdir, f"top_{args.dimension}.png")
    csv_path = os.path.join(outdir, f"top_{args.dimension}.csv")

    # ---- 横向条形图 ----
    names = [r["name"] for r in rows][::-1]
    vals = [r["count"] for r in rows][::-1]
    fig, ax = plt.subplots(figsize=style["figsize"]["bar"])
    c = style["colors"]
    bars = ax.barh(names, vals, color=c["secondary"], alpha=0.9)
    use_latin_font_for_latin_ticks(ax)
    ax.bar_label(bars, fmt="%.0f", fontsize=9, padding=3)
    dim = dim_zh[args.dimension] if zh_ok else args.dimension
    title = (f"“{args.field}”领域 Top {args.n} {dim}（{args.start}—{args.end}）"
             if zh_ok else
             f'Top {args.n} {args.dimension} in "{args.field}" ({args.start}-{args.end})')
    ax.set_title(title, fontsize=14, pad=12)
    ax.set_xlabel("发文量（篇）" if zh_ok else "Works", fontsize=11)
    add_footer(fig, zh_ok)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(png, dpi=style["dpi"])
    plt.close(fig)

    write_csv(csv_path, ["rank", "name", "openalex_key", "works_count"],
              [[i + 1, r["name"], r["key"], r["count"]] for i, r in enumerate(rows)])

    emit([png, csv_path],
         {"dimension": args.dimension, "top1": rows[0] if rows else None},
         [])
    return 0


def cmd_countries(args):
    plt, zh_ok = setup_plot(args.lang)
    style = load_style(args.style)
    base = build_query(args)
    rows = group_top(base, "authorships.institutions.country_code", args.n, args.mailto)

    outdir = ensure_outdir(args.out)
    png = os.path.join(outdir, "countries.png")
    csv_path = os.path.join(outdir, "countries.csv")

    total_top = sum(r["count"] for r in rows) or 1
    names = [r["name"] for r in rows][::-1]
    vals = [r["count"] for r in rows][::-1]
    fig, ax = plt.subplots(figsize=style["figsize"]["bar"])
    c = style["colors"]
    bars = ax.barh(names, vals, color=c["secondary"], alpha=0.9)
    use_latin_font_for_latin_ticks(ax)
    labels = [f"{v:,}  ({v / total_top * 100:.1f}%)" for v in vals]
    ax.bar_label(bars, labels=labels, fontsize=9, padding=3)
    title = (f"“{args.field}”领域发文国家/地区分布（{args.start}—{args.end}）"
             if zh_ok else
             f'Country/region distribution in "{args.field}" ({args.start}-{args.end})')
    ax.set_title(title, fontsize=14, pad=12)
    ax.set_xlabel("发文量（篇）" if zh_ok else "Works", fontsize=11)
    ax.set_xlim(0, max(vals) * 1.18 if vals else 1)
    foot = ("按作者机构所属国家统计，跨国合作论文计入多国" if zh_ok
            else "By author affiliation country; co-authored works count for each country")
    add_footer(fig, zh_ok, foot)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(png, dpi=style["dpi"])
    plt.close(fig)

    write_csv(csv_path, ["rank", "country", "code", "works_count"],
              [[i + 1, r["name"], r["key"].split("/")[-1], r["count"]]
               for i, r in enumerate(rows)])

    emit([png, csv_path], {"countries_top1": rows[0] if rows else None}, [])
    return 0


def cmd_cooc(args):
    plt, zh_ok = setup_plot(args.lang)
    style = load_style(args.style)
    base = build_query(args)

    docs, sample_works = sample_works_keywords(base, args.sample, args.mailto)
    stop = {args.field.lower()}
    freq, pair = Counter(), Counter()
    for kws in docs:
        kws = sorted({k for k in kws if k.lower() not in stop})
        freq.update(kws)
        for a, b in itertools.combinations(kws, 2):
            pair[(a, b)] += 1

    # 文档频率（DF）过滤：在超过半数抽样作品中都出现的词是学科级泛词
    # （如 Computer science），会让网络中心被无信息量的节点占据，予以剔除。
    df_cut = max(3, int(0.5 * len(docs)))
    generic = [k for k, v in freq.items() if v > df_cut]
    if generic and len(freq) - len(generic) >= 5:
        for k in generic:
            del freq[k]
        pair = Counter({(a, b): w for (a, b), w in pair.items()
                        if a in freq and b in freq})

    top_nodes = [k for k, _ in freq.most_common(args.top)]
    node_set = set(top_nodes)
    edges = [((a, b), w) for (a, b), w in pair.most_common()
             if a in node_set and b in node_set][:args.edges]

    outdir = ensure_outdir(args.out)
    nodes_csv = os.path.join(outdir, "cooccurrence_nodes.csv")
    edges_csv = os.path.join(outdir, "cooccurrence_edges.csv")
    works_csv = os.path.join(outdir, "cooc_works.csv")
    write_csv(nodes_csv, ["keyword", "frequency"],
              [[k, freq[k]] for k in top_nodes])
    write_csv(edges_csv, ["keyword_a", "keyword_b", "cooccurrence"],
              [[a, b, w] for (a, b), w in edges])
    write_works_csv(works_csv, sample_works)   # 抽样文献明细，供核对

    notes = [f"共现分析基于 {len(docs)} 篇随机抽样作品（seed={RANDOM_SEED}）。"]
    if generic:
        notes.append("已按文档频率剔除学科级泛词：" + "、".join(generic[:8])
                     + (" 等。" if len(generic) > 8 else "。"))
    try:
        import networkx as nx
    except ImportError:
        nx = None

    if nx is None or not edges:
        # 降级：Top 关键词条形图
        if nx is None:
            notes.append("未检测到 networkx，已降级为 Top 关键词条形图；"
                         "pip install networkx 后可绘制共现网络。")
        png = os.path.join(outdir, "cooccurrence.png")
        names = top_nodes[::-1]
        vals = [freq[k] for k in top_nodes][::-1]
        fig, ax = plt.subplots(figsize=style["figsize"]["bar"])
        ax.barh(names, vals, color=style["colors"]["secondary"], alpha=0.9)
        use_latin_font_for_latin_ticks(ax)
        ax.set_title(f"“{args.field}”领域高频关键词（抽样 n={len(docs)}）"
                     if zh_ok else
                     f'Top keywords in "{args.field}" (sample n={len(docs)})',
                     fontsize=14, pad=12)
        add_footer(fig, zh_ok)
        fig.tight_layout(rect=[0, 0.03, 1, 1])
        fig.savefig(png, dpi=style["dpi"])
        plt.close(fig)
        emit([png, nodes_csv, edges_csv, works_csv], {"documents_sampled": len(docs)}, notes)
        return 0

    # ---- 共现网络图 ----
    g = nx.Graph()
    for k in top_nodes:
        g.add_node(k, weight=freq[k])
    for (a, b), w in edges:
        g.add_edge(a, b, weight=w)
    g.remove_nodes_from(list(nx.isolates(g)))

    pos = nx.spring_layout(g, k=1.1, seed=RANDOM_SEED)
    fig, ax = plt.subplots(figsize=style["figsize"]["network"])
    c = style["colors"]
    max_w = max((g.nodes[n]["weight"] for n in g.nodes), default=1)
    sizes = [300 + 2200 * g.nodes[n]["weight"] / max_w for n in g.nodes]
    widths = [0.5 + 3.0 * g.edges[e]["weight"] / max(g.edges[e]["weight"] for e in g.edges)
              for e in g.edges] if g.edges else []
    nx.draw_networkx_edges(g, pos, ax=ax, width=widths,
                           edge_color=c["primary"], alpha=0.35)
    nx.draw_networkx_nodes(g, pos, ax=ax, node_size=sizes,
                           node_color=c["secondary"], alpha=0.85)
    nx.draw_networkx_labels(g, pos, ax=ax, font_size=9)
    title = (f"“{args.field}”领域关键词共现网络（抽样 n={len(docs)}，{args.start}—{args.end}）"
             if zh_ok else
             f'Keyword co-occurrence in "{args.field}" (sample n={len(docs)})')
    ax.set_title(title, fontsize=14, pad=12)
    ax.axis("off")
    add_footer(fig, zh_ok)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    png = os.path.join(outdir, "cooccurrence.png")
    fig.savefig(png, dpi=style["dpi"])
    plt.close(fig)

    emit([png, nodes_csv, edges_csv, works_csv],
         {"documents_sampled": len(docs), "nodes": g.number_of_nodes(),
          "edges": g.number_of_edges()}, notes)
    return 0


def cmd_export(args):
    """导出文献明细清单 CSV，供用户检索核对统计所依据的原始数据。"""
    base = build_query(args)
    works = export_works(base, args.n, args.sort, args.mailto)

    outdir = ensure_outdir(args.out)
    csv_path = os.path.join(outdir, "works_export.csv")
    write_works_csv(csv_path, works, rank=True)

    sort_zh = {"cited_by_count:desc": "按被引量降序（高被引优先）",
               "publication_date:desc": "按发表日期降序（最新优先）",
               "random": f"随机抽样（起始 seed={RANDOM_SEED}）"}[args.sort]
    emit([csv_path],
         {"works_exported": len(works), "sort": args.sort},
         [f"清单共 {len(works)} 条，{sort_zh}。"
          "可用 Excel/WPS 直接打开（utf-8-sig 编码），"
          "通过 doi 或 openalex_id 可溯源原文。"])
    return 0


def cmd_config(args):
    """一键配置 / 查看 / 清除 OpenAlex API key。

    面向分发场景的最简配置方式：用户无需了解环境变量，
    一条命令写入包外用户配置目录（权限 600）。
    先实测再原子写入；无效新 key 不会覆盖现有配置。
    """
    global _CLI_API_KEY
    if args.clear:
        try:
            os.remove(PKG_CONFIG_PATH)
            emit([], {}, ["已清除融合版用户配置；后续将使用环境变量或全局 OpenAlex 配置。"])
        except FileNotFoundError:
            emit([], {}, ["融合版用户配置不存在，无需清除。"])
        return 0
    if args.show:
        key = load_api_key()
        if not key:
            emit([], {"configured": False},
                 ["未配置 API key；可运行 config --api-key 以获得稳定的认证额度。"])
        else:
            masked = (key[:4] + "***" + key[-2:]) if len(key) > 8 else "***"
            emit([], {"configured": True, "key_masked": masked},
                 ["已配置 API key（已脱敏显示）。"])
        return 0
    if not args.api_key:
        print(json.dumps({"error": "请提供 --api-key，或使用 --show / --clear"},
                         ensure_ascii=False))
        return 2

    key = args.api_key.strip()
    if not key:
        print(json.dumps({"error": "API key 不能为空"}, ensure_ascii=False))
        return 2
    # 先用一次最便宜的请求实测，避免坏 key 覆盖已有配置。
    previous_cli_key = _CLI_API_KEY
    _CLI_API_KEY = key
    try:
        http_get("/works", {"per_page": 1, "select": "id"})
    except RuntimeError as e:
        _CLI_API_KEY = previous_cli_key
        print(json.dumps({"error": f"key 实测未通过，未保存：{e}"},
                         ensure_ascii=False))
        return 2
    _CLI_API_KEY = previous_cli_key

    config_dir = os.path.dirname(os.path.abspath(PKG_CONFIG_PATH))
    os.makedirs(config_dir, exist_ok=True)
    temp_path = os.path.join(config_dir, f".openalex.json.tmp-{os.getpid()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump({"openalex_api_key": key}, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, PKG_CONFIG_PATH)
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
    emit([], {"configured": True},
         ["API key 已实测通过并原子写入包外用户配置，后续命令自动生效。"])
    return 0


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main(argv=None):
    last_full_year = date.today().year - 1
    p = argparse.ArgumentParser(
        description="文献计量学图表生成（数据源：OpenAlex）")
    p.add_argument("--out", default="./bibliometrics_output", help="输出目录")
    p.add_argument("--mailto", default=None,
                   help="兼容旧命令的弃用参数（OpenAlex 已停止 polite pool，不再发送）")
    p.add_argument("--lang", default="auto", choices=["auto", "zh", "en"],
                   help="图表标签语言，auto=按系统中文字体自动判断")
    p.add_argument("--style", default=None, help="样式模板 JSON 路径")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common_args(sp):
        # 全局参数同时挂到子命令上（default=SUPPRESS 保证未提供时不覆盖全局值），
        # 这样 --out 等选项写在子命令前后都合法，降低调用方出错概率。
        sp.add_argument("--out", default=argparse.SUPPRESS)
        sp.add_argument("--mailto", default=argparse.SUPPRESS)
        sp.add_argument("--lang", default=argparse.SUPPRESS,
                        choices=["auto", "zh", "en"])
        sp.add_argument("--style", default=argparse.SUPPRESS)
        sp.add_argument("--api-key", default=argparse.SUPPRESS,
                        help="OpenAlex API key（单次覆盖；持久化请用 config 子命令）")

    def add_field_args(sp, years=True):
        sp.add_argument("--field", required=True, help="学术领域名称（建议英文，如 'deep learning'）")
        sp.add_argument("--topic-id", "--concept-id", dest="topic_id", default=None,
                        help="显式指定 OpenAlex topic id；--concept-id 仅作旧命令兼容")
        if years:
            sp.add_argument("--start", type=int, default=last_full_year - 9)
            sp.add_argument("--end", type=int, default=last_full_year)

    sp = sub.add_parser("resolve", help="解析领域名称 → topic 候选")
    add_common_args(sp)
    sp.add_argument("--field", required=True)
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("trend", help="年度发文量+被引量组合趋势图")
    add_common_args(sp)
    add_field_args(sp)
    sp.add_argument("--exact", action="store_true",
                    help="被引量强制全量精确统计（大领域消耗大量 API 额度，慎用）")
    sp.add_argument("--sample-size", type=int, default=SAMPLE_SIZE,
                    help="抽样估计的每年样本量（默认 %(default)s，上限 10000）")
    sp.add_argument("--max-exact-works", type=int, default=DEFAULT_MAX_EXACT_WORKS,
                    help="--exact 每年允许抓取的作品数上限（默认 %(default)s）")
    sp.add_argument("--force-exact", action="store_true",
                    help="确认资源充足后越过精确模式安全上限")
    sp.set_defaults(func=cmd_trend)

    sp = sub.add_parser("top", help="Top 机构/作者/期刊/关键词排名条形图")
    add_common_args(sp)
    add_field_args(sp)
    sp.add_argument("--dimension", required=True,
                    choices=["institutions", "authors", "sources", "keywords"])
    sp.add_argument("--n", type=int, default=15)
    sp.set_defaults(func=cmd_top)

    sp = sub.add_parser("countries", help="国家/地区发文分布图")
    add_common_args(sp)
    add_field_args(sp)
    sp.add_argument("--n", type=int, default=15)
    sp.set_defaults(func=cmd_countries)

    sp = sub.add_parser("cooc", help="关键词共现网络图")
    add_common_args(sp)
    add_field_args(sp)
    sp.add_argument("--sample", type=int, default=400, help="抽样作品数")
    sp.add_argument("--top", type=int, default=25, help="参与组网的高频关键词数")
    sp.add_argument("--edges", type=int, default=60, help="绘制的共现边数上限")
    sp.set_defaults(func=cmd_cooc)

    sp = sub.add_parser("export", help="导出文献明细清单 CSV（供检索核对）")
    add_common_args(sp)
    add_field_args(sp)
    sp.add_argument("--n", type=int, default=500, help="导出条数上限")
    sp.add_argument("--sort", default="cited_by_count:desc",
                    choices=["cited_by_count:desc", "publication_date:desc", "random"],
                    help="排序方式：被引降序 / 日期降序 / 随机抽样")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("config", help="一键配置 / 查看 / 清除 OpenAlex API key")
    sp.add_argument("--api-key", default=None, help="写入并实测该 key")
    sp.add_argument("--show", action="store_true", help="查看当前是否已配置（脱敏显示）")
    sp.add_argument("--clear", action="store_true", help="清除融合版用户配置")
    sp.set_defaults(func=cmd_config)

    args = p.parse_args(argv)
    cli_key = getattr(args, "api_key", None)
    if cli_key:
        global _CLI_API_KEY
        _CLI_API_KEY = cli_key.strip()
    try:
        if hasattr(args, "start") and args.start > args.end:
            raise RuntimeError("--start 不能晚于 --end。")
        if hasattr(args, "sample_size") and not 1 <= args.sample_size <= MAX_SAMPLE_SIZE:
            raise RuntimeError(f"--sample-size 必须在 1—{MAX_SAMPLE_SIZE} 之间。")
        if hasattr(args, "sample") and not 1 <= args.sample <= MAX_SAMPLE_SIZE:
            raise RuntimeError(f"--sample 必须在 1—{MAX_SAMPLE_SIZE} 之间。")
        if hasattr(args, "max_exact_works") and args.max_exact_works < 1:
            raise RuntimeError("--max-exact-works 必须为正整数。")
        if hasattr(args, "n") and args.n < 1:
            raise RuntimeError("--n 必须为正整数。")
        return args.func(args)
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
