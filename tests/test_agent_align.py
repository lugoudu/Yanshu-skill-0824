#!/usr/bin/env python3
"""agent_align（v1.0.0 对齐层）离线回归测试。

mock openalex_core.http_get 返回预设 meta.count 与 /topics 候选，
不发真实网络请求。覆盖：Topic 门控、瀑布各确定性层、代表性文献
两档过滤、CLI 命令分发。
"""

import json
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, SCRIPTS)

import agent_align  # noqa: E402
from bibliometric import openalex_core as core  # noqa: E402
import fusion_run  # noqa: E402

_TOPICS = [
    {"id": "https://openalex.org/T10462",
     "display_name": "Reinforcement Learning in Robotics",
     "works_count": 61064},
    {"id": "https://openalex.org/T12794",
     "display_name": "Adaptive Dynamic Programming Control",
     "works_count": 11820},
]


class FakeTopicsCounts:
    """/topics 返回预设候选，/works 按 filter 短语序列返回预设命中数。"""

    def __init__(self, table, topics=None):
        self.table = table
        self.topics = topics
        self.works_calls = []
        self.topic_calls = 0

    def __call__(self, path, params, mailto=None):
        filter_str = params.get("filter", "")
        if path == "/topics":
            self.topic_calls += 1
            return {"results": self.topics or []}
        if "topics.id" in filter_str or path != "/works":
            return {"meta": {"count": 0}, "results": []}
        terms = [p.split(":", 1)[1] for p in filter_str.split(",")
                 if ".search:" in p]
        self.works_calls.append(filter_str)
        count = self.table.get(tuple(terms), self.table.get(None, 0))
        return {"meta": {"count": count}, "results": []}


def with_fake(fake, fn):
    old = core.http_get
    core.http_get = fake
    try:
        return fn()
    finally:
        core.http_get = old


def test_topic_gate_healthy_phrase_rejects_topic():
    fake = FakeTopicsCounts(
        {('"agentic reinforcement learning"',): 6463}, topics=_TOPICS)
    shape = with_fake(
        fake, lambda: agent_align.resolve_corpus_scope(
            "agentic reinforcement learning", 2023, 2025))
    assert shape["mode"] == "phrase" and shape["topic_id"] is None
    assert shape["counts"]["L1"] == 6463
    assert len(fake.works_calls) == 1  # 门控探测即终点，零多余请求


def test_topic_gate_low_hit_adopts_topic():
    fake = FakeTopicsCounts(
        {('"adaptive dynamic programming"',): 30}, topics=_TOPICS)
    shape = with_fake(
        fake, lambda: agent_align.resolve_corpus_scope(
            "adaptive dynamic programming", 2023, 2025))
    assert shape["mode"] == "topic"
    assert shape["topic_id"] == "T10462"
    assert shape["topic_name"] == "Reinforcement Learning in Robotics"
    assert shape["topic_works"] == 61064
    assert shape["counts"]["L1"] == 30


def test_no_candidates_l1_healthy_phrase():
    fake = FakeTopicsCounts({('"federated learning"',): 36984})
    shape = with_fake(
        fake, lambda: agent_align.resolve_corpus_scope(
            "federated learning", 2023, 2025))
    assert shape["mode"] == "phrase" and shape["field"] == "federated learning"
    assert len(fake.works_calls) == 1


def test_waterfall_l2_mechanical_chunks():
    table = {
        ('"diffusion model in image generation"',): 124,
        ('"diffusion model"', '"image generation"'): 4193,
    }
    fake = FakeTopicsCounts(table)
    shape = with_fake(
        fake, lambda: agent_align.resolve_corpus_scope(
            "diffusion model in image generation", 2023, 2025))
    assert shape["mode"] == "chunks"
    assert shape["chunks"] == [["diffusion model"], ["image generation"]]
    assert shape["counts"] == {"L1": 124, "L2": 4193}
    assert shape["note"] and "分块组合" in shape["note"]


def test_waterfall_single_word_variants():
    table = {
        ('"behavior"',): 120,
        ('"behavior"|"behaviors"|"behaviour"',): 260,
    }
    fake = FakeTopicsCounts(table)
    shape = with_fake(
        fake, lambda: agent_align.resolve_corpus_scope(
            "behavior", 2023, 2025))
    assert shape["mode"] == "variants"
    assert "behaviour" in shape["variants"] and "behaviors" in shape["variants"]
    assert shape["counts"]["variants"] == 260


def test_waterfall_l3_core_word():
    table = {
        ('"euhemerism in renaissance literature"',): 4,
        ('"euhemerism"', '"renaissance literature"'): 6,
        ('"euhemerism"',): 2100,
    }
    fake = FakeTopicsCounts(table)
    shape = with_fake(
        fake, lambda: agent_align.resolve_corpus_scope(
            "euhemerism in renaissance literature", 2015, 2025))
    assert shape["mode"] == "phrase" and shape["field"] == "euhemerism"
    assert shape["counts"]["L3"] == 2100
    assert shape["note"] and "核心领域词" in shape["note"]


def test_representative_two_tier_and_ningque():
    bundle = {"query": {"field": "agentic reinforcement learning"},
              "audit": {"high_cited_works": [
        {"title": "Deep Multi-Agent Reinforcement Learning for Highway Merging",
         "doi": "https://doi.org/10.1000/h", "cited_by_count": 225,
         "year": 2023, "venue": "IEEE TITS"},
        {"title": "Optimization of Image Transmission in Semantic Communication",
         "doi": "https://doi.org/10.1000/n", "cited_by_count": 100},
    ]}}
    md = agent_align.representative_works(bundle)
    assert "## 代表性文献" in md and "Highway Merging" in md
    assert "Semantic Communication" not in md  # 词根档不放过跨词根噪声

    # 连接词不参与匹配：介词/单复数差异由词元档兜住
    bundle2 = {"query": {"field": "diffusion model in image generation"},
               "audit": {"high_cited_works": [
        {"title": "Diffusion Models for Image Generation",
         "doi": "10.1000/p", "cited_by_count": 90}]}}
    assert "Diffusion Models for Image Generation" in \
        agent_align.representative_works(bundle2)

    # 宁缺毋滥：关键词根缺席整节滤空；无 DOI 跳过
    bundle3 = {"query": {"field": "federated learning"},
               "audit": {"high_cited_works": [
        {"title": "Machine learning at scale", "doi": "10.1000/q",
         "cited_by_count": 300}]}}
    assert agent_align.representative_works(bundle3) == ""
    assert agent_align.representative_works(
        {"query": {"field": "x"}, "audit": {"high_cited_works": []}}) == ""


def test_cli_dispatch():
    # agent 命令在 fusion_run 层拦截；其余委托 fusion.main
    saved_argv = sys.argv
    captured = SimpleNamespace(called=None)
    old_decide = fusion_run.cmd_decide
    old_fusion_main = fusion_run.fusion.main

    def fake_decide(argv):
        captured.called = "decide"
        return 0

    def fake_main(argv=None):
        captured.called = "fusion"
        return 0

    fusion_run.cmd_decide = fake_decide
    fusion_run.fusion.main = fake_main
    try:
        sys.argv = ["fusion_run.py", "decide", "--field", "x"]
        assert fusion_run.main() == 0 and captured.called == "decide"
        sys.argv = ["fusion_run.py", "info"]
        assert fusion_run.main() == 0 and captured.called == "fusion"
        sys.argv = ["fusion_run.py", "fetch", "--field", "x"]
        assert fusion_run.main() == 0 and captured.called == "fusion"
    finally:
        fusion_run.cmd_decide = old_decide
        fusion_run.fusion.main = old_fusion_main
        sys.argv = saved_argv


def run_test(keep=None):
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        if keep and keep not in name:
            continue
        try:
            fn()
            print("PASS %s" % name)
        except AssertionError as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_test(sys.argv[1] if len(sys.argv) > 1 else None))
