#!/usr/bin/env python3
"""Offline collection-to-bundle-to-ten-charts regression test."""

import csv
import json
import math
import os
import statistics
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, SCRIPTS)

from bibliometric import fusion  # noqa: E402
from bibliometric import openalex_core as core  # noqa: E402

with open(os.path.join(HERE, "sample_data.json"), encoding="utf-8") as handle:
    SAMPLE = json.load(handle)


TOPIC_NAMES = (
    "Medical Image Segmentation",
    "Vision Transformers",
    "Representation Learning",
    "Graph Neural Networks",
    "Natural Language Processing",
    "Generative Models",
    "Reinforcement Learning",
    "Multimodal Learning",
    "Time-Series Forecasting",
    "Trustworthy AI",
    "Remote Sensing Image Analysis",
    "Speech Recognition",
)

SYNTHETIC_AUTHORS = (
    "Amina Rao", "Bo Chen", "Clara Martin", "Diego Silva", "Elena Novak",
    "Farah Khan", "Gabriel Costa", "Hana Sato", "Ivan Petrov", "Julia Mensah",
)

SYNTHETIC_SOURCES = (
    ("Synthetic Journal of Vision Studies (journal)", "journal"),
    ("Synthetic Neural Systems Letters (journal)", "journal"),
    ("Synthetic Conference on Learning Systems (conference)", "conference"),
    ("Synthetic Symposium on Multimodal AI (conference)", "conference"),
    ("Synthetic Machine Intelligence Archive (repository)", "repository"),
    ("Synthetic Transactions on Trustworthy AI (journal)", "journal"),
    ("Synthetic Workshop on Graph Learning (conference)", "conference"),
    ("Synthetic Data Science Reports (journal)", "journal"),
    ("Synthetic Computing Preprints (repository)", "repository"),
    ("Synthetic Pattern Analysis Proceedings (conference)", "conference"),
)

TREND_SAMPLE_SIZE = 20
RANDOM_CITATIONS = [
    0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 5, 6, 8, 10, 12,
    15, 18, 22, 28, 35, 44, 55, 70, 90, 120, 160, 220, 300, 450, 700,
]
HIGH_CITATIONS = [1200 - 25 * index for index in range(30)]


def _slug(value):
    return value.casefold().replace(" ", "-")


def _synthetic_work(kind, index, year, cited_by_count, topic_index=None):
    topic_index = index % len(TOPIC_NAMES) if topic_index is None else topic_index
    topic_name = TOPIC_NAMES[topic_index % len(TOPIC_NAMES)]
    source_name, source_type = SYNTHETIC_SOURCES[index % len(SYNTHETIC_SOURCES)]
    author = SYNTHETIC_AUTHORS[index % len(SYNTHETIC_AUTHORS)]
    collaborator = SYNTHETIC_AUTHORS[(index + 3) % len(SYNTHETIC_AUTHORS)]
    work_type = {
        "journal": "article",
        "conference": "proceedings-article",
        "repository": "preprint",
    }[source_type]
    return {
        "id": "urn:offline-fixture:%s:%s:%03d" % (kind, year, index),
        "display_name": "Synthetic %s work %s-%03d" % (kind, year, index),
        "publication_year": year,
        "authorships": [
            {
                "author": {"display_name": "%s (synthetic author)" % author},
                "institutions": [{
                    "display_name": "Synthetic Institute for AI %s" % (index % 5 + 1),
                    "country_code": ("US", "CN", "GB", "DE", "IN")[index % 5],
                }],
            },
            {
                "author": {"display_name": "%s (synthetic author)" % collaborator},
                "institutions": [],
            },
        ],
        "primary_location": {"source": {"display_name": source_name}},
        "cited_by_count": int(cited_by_count),
        "type": work_type,
        "doi": "https://doi.org/10.0000/offline-fixture.%s.%s.%03d" % (
            kind, year, index
        ),
        "keywords": [
            {"id": "urn:offline-fixture:keyword:%s" % _slug(topic_name),
             "display_name": topic_name},
            {"id": "urn:offline-fixture:keyword:neural-networks",
             "display_name": "Neural Networks"},
            {"id": "urn:offline-fixture:keyword:%s" % (
                "self-supervised-learning" if index % 2 else "model-evaluation"
            ), "display_name": (
                "Self-Supervised Learning" if index % 2 else "Model Evaluation"
            )},
        ],
        "primary_topic": {
            "id": "urn:offline-fixture:topic:%s" % _slug(topic_name),
            "display_name": topic_name,
        },
    }


def _trend_sample(year):
    row = SAMPLE["citations"][str(year)]
    target_sum = round(row["citations"] / row["count"] * TREND_SAMPLE_SIZE)
    base, remainder = divmod(target_sum, TREND_SAMPLE_SIZE)
    values = [base + (index < remainder) for index in range(TREND_SAMPLE_SIZE)]
    return [
        _synthetic_work("trend", index, year, value,
                        topic_index=(year - 2016 + index) % len(TOPIC_NAMES))
        for index, value in enumerate(values)
    ]


RANDOM_WORKS = [
    _synthetic_work("random", index, 2016 + index % 10, cited_by_count)
    for index, cited_by_count in enumerate(RANDOM_CITATIONS)
]
HIGH_CITED_WORKS = [
    _synthetic_work("high-cited", index, 2016 + index % 10, cited_by_count)
    for index, cited_by_count in enumerate(HIGH_CITATIONS)
]


def _ranked_group(entity_type, names, counts):
    return [
        {
            "name": name,
            "key": "urn:offline-fixture:%s:%02d" % (entity_type, index),
            "count": count,
            "entity_type": entity_type,
        }
        for index, (name, count) in enumerate(zip(names, counts), 1)
    ]


TOTAL_WORKS = sum(SAMPLE["annual"].values())
TOPIC_SHARES = (0.18, 0.15, 0.13, 0.11, 0.09, 0.08,
                0.07, 0.06, 0.04, 0.03, 0.025, 0.015)
COUNTRY_FIXTURES = (
    ("US", "United States", 94000), ("CN", "China", 87000),
    ("IN", "India", 61000), ("GB", "United Kingdom", 26000),
    ("DE", "Germany", 22000), ("HK", "Hong Kong", 19000),
    ("TW", "Taiwan", 16000), ("MO", "Macau", 14500),
    ("AU", "Australia", 13200), ("BR", "Brazil", 11800),
)
SYNTHETIC_GROUPS = {
    "authorships.institutions.id": _ranked_group(
        "institution",
        ["Synthetic Institute for AI %s" % index for index in range(1, 11)],
        [9200, 7100, 5900, 4800, 4200, 3600, 3100, 2700, 2400, 2100],
    ),
    "authorships.author.id": _ranked_group(
        "author",
        ["%s (synthetic author)" % name for name in SYNTHETIC_AUTHORS],
        [420, 390, 355, 330, 305, 285, 265, 245, 230, 215],
    ),
    "primary_location.source.id": [
        {
            "name": name,
            "key": "urn:offline-fixture:source:%s:%02d" % (source_type, index),
            "count": count,
            "entity_type": source_type,
        }
        for index, ((name, source_type), count) in enumerate(zip(
            SYNTHETIC_SOURCES,
            [42000, 31000, 24500, 19800, 16200, 13100, 10800, 9200, 7800, 6500],
        ), 1)
    ],
    "authorships.institutions.country_code": [
        {"name": name, "key": code, "count": count, "entity_type": "country"}
        for code, name, count in COUNTRY_FIXTURES
    ],
    "keywords.id": _ranked_group(
        "keyword",
        ["Deep Learning", "Neural Networks", "Computer Vision",
         "Representation Learning", "Natural Language Processing",
         "Medical Imaging", "Generative Models", "Graph Neural Networks",
         "Multimodal Learning", "Model Evaluation", "Trustworthy AI",
         "Reinforcement Learning", "Time-Series Forecasting",
         "Speech Recognition", "Remote Sensing"],
        [390000, 310000, 180000, 150000, 132000, 99000, 86000, 72000,
         65000, 59000, 52000, 47000, 41000, 36000, 32000],
    ),
    "primary_topic.id": [
        {
            "name": name,
            "key": "urn:offline-fixture:topic:%s" % _slug(name),
            "count": round(TOTAL_WORKS * share),
            "entity_type": "topic",
        }
        for name, share in zip(TOPIC_NAMES, TOPIC_SHARES)
    ],
}

PROVENANCE = {
    "kind": "offline_fixture",
    "fixture": True,
    "network_access": False,
    "generator": "tests/test_offline.py",
    "source_file": "tests/sample_data.json",
    "source_note": SAMPLE["_comment"],
    "intended_use": "offline rendering and contract regression only",
}
WARNINGS = [
    "OFFLINE FIXTURE: synthetic IDs, samples, topics, and rankings; no live API data.",
    "Annual baselines come from the bundled historical sample_data fixture.",
    "Do not use generated statistics or charts as research findings or current OpenAlex results.",
]


def _args(out):
    return SimpleNamespace(
        field=SAMPLE["field"], topic_id="T10320", start=2016, end=2025,
        mailto=None, sample_size=100, exact=False, max_exact_works=20000,
        force_exact=False, impact_sample=30, cooc_sample=30, h_index_limit=30,
        top_n=10, keyword_top=15, cooc_top=18, cooc_edges=40,
        out=out, lang="en", style=None,
    )


def run_test(keep_dir=None):
    old = {
        "key": core.load_api_key,
        "annual": core.annual_publications,
        "citations": core.annual_citations,
        "group": core.group_top,
        "sample": core.fetch_sample,
        "export": core.export_works,
    }
    core.load_api_key = lambda: "offline-test-key"
    core.annual_publications = lambda *args, **kwargs: {
        year: SAMPLE["annual"].get(str(year), 0) for year in range(2016, 2026)
    }

    def citations(base, year, mailto=None, count_hint=None, **kwargs):
        row = SAMPLE["citations"][str(year)]
        works = _trend_sample(year)
        values = [work["cited_by_count"] for work in works]
        mean = statistics.mean(values) if values else 0
        estimated_total = round(mean * row["count"])
        variance = statistics.variance(values) if len(values) > 1 else 0
        ci95 = round(1.96 * math.sqrt(variance / len(values)) * row["count"])
        return estimated_total, True, len(works), row["count"], works, mean, ci95

    core.annual_citations = citations

    def group(base, group_by, n, mailto=None):
        return SYNTHETIC_GROUPS.get(group_by, [])[:n]

    core.group_top = group

    def fetch_sample(base, n, select, mailto=None):
        assert n <= len(RANDOM_WORKS), (n, len(RANDOM_WORKS))
        return RANDOM_WORKS[:n]

    core.fetch_sample = fetch_sample
    core.export_works = lambda base, n, sort, mailto=None: HIGH_CITED_WORKS[:n]

    owned_tmp = None
    if keep_dir is None:
        owned_tmp = tempfile.TemporaryDirectory(prefix="fusion-offline-")
        out = owned_tmp.name
    else:
        out = keep_dir
        os.makedirs(out, exist_ok=True)
    try:
        args = _args(out)
        bundle = fusion.collect_bundle(args)
        bundle["provenance"] = dict(PROVENANCE)
        bundle["warnings"] = list(WARNINGS)
        assert bundle["partial"] is False
        assert bundle["impact"]["sample_size"] == 30
        assert bundle["impact"]["citation_counts"] == RANDOM_CITATIONS
        assert bundle["impact"]["sampling"].startswith("uniform")
        assert bundle["impact"]["h_index"] == 30
        assert bundle["impact"]["h_index_status"] == "lower_bound"

        trend = bundle["audit"]["trend_works"]
        random = bundle["audit"]["random_analysis_works"]
        high_cited = bundle["audit"]["high_cited_works"]
        trend_ids = {row["openalex_id"] for row in trend}
        random_ids = {row["openalex_id"] for row in random}
        high_cited_ids = {row["openalex_id"] for row in high_cited}
        assert len(trend_ids) == len(trend) == TREND_SAMPLE_SIZE * 10
        assert len(random_ids) == len(random) == 30
        assert len(high_cited_ids) == len(high_cited) == 30
        assert trend_ids.isdisjoint(random_ids)
        assert trend_ids.isdisjoint(high_cited_ids)
        assert random_ids.isdisjoint(high_cited_ids)
        assert all(value.startswith("urn:offline-fixture:trend:")
                   for value in trend_ids)
        assert all(value.startswith("urn:offline-fixture:random:")
                   for value in random_ids)
        assert all(value.startswith("urn:offline-fixture:high-cited:")
                   for value in high_cited_ids)

        for year, row in bundle["annual"].items():
            records = [work for work in trend
                       if str(work["statistics_year"]) == year]
            assert len(records) == TREND_SAMPLE_SIZE
            assert all(str(work["year"]) == year for work in records)
            sample_mean = statistics.mean(
                work["cited_by_count"] for work in records
            )
            recalculated = round(sample_mean * row["population_size"])
            assert recalculated == row["cumulative_citations"]
            baseline = SAMPLE["citations"][year]["citations"]
            assert abs(recalculated - baseline) / baseline < 0.01

        topic_rows = bundle["rankings"]["topics"]
        assert [row["name"] for row in topic_rows] == list(TOPIC_NAMES)
        assert all(row["entity_type"] == "topic" for row in topic_rows)
        assert not any(row["name"].startswith("Topic ") for row in topic_rows)

        author_rows = bundle["rankings"]["authors"]
        assert all(row["entity_type"] == "author" for row in author_rows)
        assert all("synthetic author" in row["name"] for row in author_rows)
        assert not any("Journal" in row["name"] for row in author_rows)

        source_rows = bundle["rankings"]["sources"]
        assert {row["entity_type"] for row in source_rows} == {
            "journal", "conference", "repository"
        }
        assert all("(%s)" % row["entity_type"] in row["name"]
                   for row in source_rows)

        country_rows = {
            row["country_code"]: row for row in bundle["rankings"]["countries"]
        }
        required_regions = {
            "HK": ("中国香港", "Hong Kong, China"),
            "MO": ("中国澳门", "Macao, China"),
            "TW": ("中国台湾", "Taiwan, China"),
        }
        for code, (name_zh, name_en) in required_regions.items():
            assert country_rows[code]["name_zh"] == name_zh
            assert country_rows[code]["name_en"] == name_en
            assert country_rows[code]["name"] == name_en
        assert bundle["provenance"] == PROVENANCE
        assert bundle["warnings"] == WARNINGS

        data_files = fusion.write_bundle(bundle, out)
        chart_files, failures = fusion.render_all(bundle, out, "en", None)
        assert not failures, failures
        assert len(chart_files) == len(fusion.CHART_TYPES) == 9
        for path in data_files + chart_files:
            assert os.path.isfile(path), path
            floor = 10000 if path.endswith(".png") else 20
            assert os.path.getsize(path) > floor, (path, os.path.getsize(path))
        with open(os.path.join(out, "bundle.json"), encoding="utf-8") as handle:
            saved = json.load(handle)
        with open(os.path.join(out, "ranking_countries.csv"),
                  encoding="utf-8-sig", newline="") as handle:
            country_csv = {row["country_code"]: row for row in csv.DictReader(handle)}
        for code, (name_zh, name_en) in required_regions.items():
            assert country_csv[code]["name_zh"] == name_zh
            assert country_csv[code]["name_en"] == name_en
        assert saved["schema_version"] == fusion.SCHEMA_VERSION
        assert saved["methodology"]["citation_distribution"].startswith("random")
        assert saved["provenance"] == PROVENANCE
        assert saved["warnings"] == WARNINGS
        print("[OK] offline bundle and %s charts" % len(chart_files))
        print("[OK] audit/data files", len(data_files))
        return out, chart_files
    finally:
        core.load_api_key = old["key"]
        core.annual_publications = old["annual"]
        core.annual_citations = old["citations"]
        core.group_top = old["group"]
        core.fetch_sample = old["sample"]
        core.export_works = old["export"]
        if owned_tmp is not None:
            owned_tmp.cleanup()


if __name__ == "__main__":
    keep = sys.argv[1] if len(sys.argv) > 1 else None
    run_test(keep)
