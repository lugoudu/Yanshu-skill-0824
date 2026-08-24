"""研述 agent（1.0.0）检索能力的确定性移植层。

与服务器端 agent 的编排层（app/biblio.py）同源的语料口径决策与代表性
文献过滤，剥除 LLM 依赖后的确定性子集：

- **Topic 采纳门控**：OpenAlex Topic 候选先过整串短语命中门控——短语
  命中健康（≥500）时放弃候选、走短语口径，低命中才用 Topic 聚合兜底。
  agent v1.0.0 实测教训："agentic reinforcement learning" 被消歧到
  「机器人强化学习」大类，语料整体跑偏。
- **多级瀑布（确定性层）**：L1 整串短语 → 机械分块组合（L2，块间
  AND）→ 单词词形变体 → 首块核心词放宽（L3）。门槛常量与 agent
  完全一致。agent 另有 LLM 同义词层（S）与块内穷举扩词（C 层宽档），
  需要 DeepSeek，不进本 skill；两者只影响召回宽度，块间 AND 语义下
  不影响口径正确性。
- **代表性文献**：被引 Top N + 两档标题相关性过滤（连续子串 /
  词元级词根覆盖），宁缺毋滥；链接全部来自真实 DOI 字段，零 LLM。

核心包 bibliometric/ 保持与 agent 字节级同源；本模块只调用其
http_get / build_query / resolve_topic，不改动核心包。
"""

import re
import sys
from types import SimpleNamespace

from bibliometric import openalex_core as core

# ---- 门槛常量（与 agent app/biblio.py 完全一致）----
WATERFALL_MIN_HITS = 500     # 整串/分块命中的"充足"下限
WATERFALL_GAIN = 10          # 放宽形态的最低增益倍数
WATERFALL_CORE_FLOOR = 1000  # 核心词放宽的最低绝对命中

# 机械切块的白名单连接词（of/to 受保护，固定搭配不拆）
_SPLIT_WORDS = {
    "in", "for", "with", "on", "upon", "at", "via", "within", "across",
    "based", "driven",
}


def probe_hits(field, start, end, chunks=None, variants=None):
    """单次 OpenAlex count 探测（per_page=1 只取 meta.count）。

    瀑布各层与 Topic 采纳门控共用；异常返回 -1，调用方按
    「探测失败维持原行为」处理，绝不阻塞检索。
    """
    probe = SimpleNamespace(
        field=field, topic_id=None, start=start, end=end,
        search_scope="title_abstract",
        search_chunks=chunks, search_variants=variants)
    try:
        data = core.http_get(
            "/works", {**core.build_query(probe), "per_page": 1})
        return int(data.get("meta", {}).get("count") or 0)
    except Exception as exc:
        print("检索探测失败(按原词继续): %s" % exc, file=sys.stderr)
        return -1


def split_chunks(field):
    """按白名单连接词切成语块；of/to 受保护（固定搭配不拆）。

    "diffusion model in image generation" → ["diffusion model",
    "image generation"]；连接词本身丢弃。
    """
    words = [w for w in re.split(r"[\s,]+", (field or "").strip()) if w]
    blocks, cur = [], []
    for w in words:
        if w.lower() in _SPLIT_WORDS and cur:
            blocks.append(" ".join(cur))
            cur = []
        else:
            cur.append(w)
    if cur:
        blocks.append(" ".join(cur))
    return [b for b in blocks
            if any(w.lower() not in _SPLIT_WORDS for w in b.split())]


def word_variants(term):
    """确定性词形变体（封闭后缀规则，无语义判断，不依赖 LLM）。

    单词主题：-ism → -ic/-istic、复数 +s/+es、英美拼写互变、
    连字符/空格互变。错误词形在 OR 组合里命中 0，无害；规则外
    变体不做（语义判断是 agent LLM 层的职责）。
    """
    t = " ".join((term or "").lower().split())
    if not t:
        return set()
    if " " in t:  # 多词短语不加词形（留给概念块的等价表述扩展）
        return {t}
    out = {t}
    if t.endswith("ism"):
        stem = t[:-3]
        out.update({stem + "ic", stem + "istic"})
    if not t.endswith("s"):
        out.add(t + "s")
    if t.endswith(("s", "x", "z", "ch", "sh")) and not t.endswith("ss"):
        out.add(t + "es")
    if t.endswith("ise"):
        out.add(t[:-3] + "ize")
    if t.endswith("ize"):
        out.add(t[:-3] + "ise")
    if t.endswith("isation"):
        out.add(t[:-7] + "ization")
    if t.endswith("ization"):
        out.add(t[:-7] + "isation")
    if t.endswith("our"):
        out.add(t[:-3] + "or")
    if t.endswith("or") and len(t) > 4:
        out.add(t[:-2] + "our")
    if t.endswith("yse"):
        out.add(t[:-3] + "yze")
    if "-" in t:
        out.add(t.replace("-", " "))
    return out


def _shape(field, mode, **extra):
    base = {"orig": field, "field": field, "mode": mode,
            "chunks": None, "variants": None, "note": None,
            "counts": {}, "topic_id": None, "topic_name": None,
            "topic_works": None}
    base.update(extra)
    return base


def resolve_corpus_scope(field, start, end, mailto=None):
    """决定语料口径：Topic 候选先过 L1 短语门控，无候选走确定性瀑布。

    返回 shape dict：field 可能被 L3 改写；topic_id 非 None 时为
    Topic 聚合口径；counts 记录各层命中数（审计用）。决策顺序与
    agent 一致；探测失败（-1）时维持对应层的原行为。
    """
    field = " ".join((field or "").split())
    try:
        candidates = core.resolve_topic(field, mailto, 5)
    except Exception as exc:
        print("Topic 候选解析失败(改走短语口径): %s" % exc,
              file=sys.stderr)
        candidates = []
    n_l1 = probe_hits(field, start, end)

    # ---- Topic 门控：短语命中健康即放弃候选（agent v1.0.0）----
    if candidates and n_l1 < WATERFALL_MIN_HITS:
        first = candidates[0]
        return _shape(field, "topic",
                      counts={"L1": n_l1},
                      topic_id=first.get("id") or first.get("topic_id"),
                      topic_name=first.get("name", field),
                      topic_works=first.get("works_count", "?"))

    shape = _shape(field, "phrase", counts={"L1": n_l1})
    if n_l1 < 0 or n_l1 >= WATERFALL_MIN_HITS:
        return shape  # 标准词零打扰 / 探测失败按原词继续

    chunks = split_chunks(field)

    # ---- 单词主题：词形变体（组合命中提升即并入）----
    if len(chunks) <= 1:
        vs = sorted(word_variants(field))
        if len(vs) > 1:
            n_var = probe_hits(field, start, end, None, vs)
            shape["counts"]["variants"] = n_var
            if n_var > max(n_l1, 0):
                shape.update(
                    mode="variants", variants=vs,
                    note=(f"检索词「{field}」短语口径命中 {n_l1} 篇,"
                          f"已并入同概念词形({n_var} 篇)"))
        return shape

    # ---- L2 机械分块：块间 AND，各语义块须同现 ----
    groups = [[chunk] for chunk in chunks]
    n_grp = probe_hits(field, start, end, groups, None)
    shape["counts"]["L2"] = n_grp
    if (n_grp >= WATERFALL_MIN_HITS
            and n_grp >= WATERFALL_GAIN * max(n_l1, 1)):
        shape.update(
            mode="chunks", chunks=groups,
            note=(f"检索词「{field}」整串短语仅命中 {n_l1} 篇,"
                  f"已放宽为分块组合检索({n_grp} 篇,各语义块须同现)"))
        return shape

    # ---- L3 首块核心词（明示领域级口径放宽）----
    core_term = chunks[0]
    n_core = probe_hits(core_term, start, end)
    shape["counts"]["L3"] = n_core
    if n_core >= max(WATERFALL_GAIN * max(n_l1, 1), WATERFALL_CORE_FLOOR):
        shape.update(
            field=core_term,
            note=(f"检索词「{field}」命中过少({n_l1} 篇),"
                  f"已放宽为核心领域词「{core_term}」"
                  f"({n_core} 篇,领域级口径)"))
    return shape


# ---- 代表性文献（agent v1.0.0 两档标题相关性过滤）----

_CONNECTIVE_TOKENS = frozenset(
    "a an the of in for and or with to on at by via from".split())


def _title_tokens(text):
    """文本实词序列（小写、按非字母数字切分、剔除连接词）。"""
    return [t for t in re.split(r"[^0-9a-z]+", (text or "").casefold())
            if len(t) >= 2 and t not in _CONNECTIVE_TOKENS]


def _shares_word(words_a, words_b):
    """两组实词是否有词根级共享（相等或一方是另一方的前缀）。

    agentic/agent、glass/glassy、model/models 这类同根词形视为共享；
    短词（<4）不参与。
    """
    for a in words_a:
        for b in words_b:
            if a == b or (len(a) >= 4 and len(b) >= 4
                          and (a.startswith(b) or b.startswith(a))):
                return True
    return False


def representative_works(bundle, limit=5):
    """从 bundle 的高被引集合拼「代表性文献」小节（Markdown）。

    两档相关性判定，任一命中即保留：
    1. 标题包含检索词/语块/词形的连续子串；
    2. 词元级词根覆盖——标题实词按词根规则逐一覆盖检索词的全部实词。
    OpenAlex 短语检索带词干归并（agentic↔agent），语料头部可能是
    multi-agent 类标题，只认连续子串则新兴主题必然整节滤空。
    过滤后为空返回空串（宁缺毋滥）；链接一律来自真实 DOI 字段。
    """
    works = (bundle.get("audit") or {}).get("high_cited_works") or []
    q = bundle.get("query") or {}
    terms = set()
    if (q.get("field") or "").strip():
        terms.add(q["field"].strip().casefold())
    # variants 平铺字符串；chunks 嵌套块组，须逐块展平
    for variant in (q.get("search_variants") or []):
        text = str(variant).strip()
        if text:
            terms.add(text.casefold())
    for group in (q.get("search_chunks") or []):
        items = group if isinstance(group, (list, tuple)) else [group]
        for chunk in items:
            text = str(chunk).strip()
            if text:
                terms.add(text.casefold())
    if terms:
        term_tokens = [(t, _title_tokens(t)) for t in terms]
        kept = []
        for w in works:
            hay = (w.get("title") or "").casefold()
            toks = _title_tokens(w.get("title"))
            if any(t in hay for t, _ in term_tokens) or \
               any(all(_shares_word({tk}, toks) for tk in ts)
                   for _, ts in term_tokens if ts):
                kept.append(w)
        works = kept
    rows = []
    for w in works:
        if len(rows) >= limit:
            break
        doi = (w.get("doi") or "").strip()
        if doi.startswith("http"):
            link = doi
        elif doi:
            link = "https://doi.org/" + doi
        else:
            continue  # 无 DOI 无法直达，跳过
        title = (w.get("title") or "未命名文献").strip()
        meta = []
        if w.get("year"):
            meta.append(str(w["year"]))
        if w.get("venue"):
            meta.append(str(w["venue"])[:40])
        meta.append(f"被引 {w.get('cited_by_count') or 0} 次")
        rows.append(f"- [{title}]({link})（{', '.join(meta)}）")
    if not rows:
        return ""
    head = ("## 代表性文献\n\n"
            f"当前语料中被引最高且标题直接相关主题的 {len(rows)} 篇"
            "(标题可点击直达原文页):\n\n")
    return head + "\n".join(rows) + "\n"
