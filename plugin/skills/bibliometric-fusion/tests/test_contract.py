#!/usr/bin/env python3
"""Offline API, security, schema, and statistics contract tests."""

import csv
import io
import json
import os
import stat
import sys
import tempfile
import urllib.error
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, SCRIPTS)

from bibliometric import fusion  # noqa: E402
from bibliometric import openalex_core as core  # noqa: E402


def test_topic_and_filter_contract():
    calls = []

    def fake_get(path, params, mailto=None):
        calls.append((path, dict(params), mailto))
        return {"results": [{
            "id": "https://openalex.org/T10077",
            "display_name": "Artificial intelligence",
            "works_count": 100,
            "domain": {"display_name": "Sciences"},
            "field": {"display_name": "Computer science"},
        }]}

    old = core.http_get
    core.http_get = fake_get
    try:
        rows = core.resolve_topic("artificial intelligence")
    finally:
        core.http_get = old
    assert rows[0]["id"] == "T10077"
    assert calls[0][0] == "/topics"
    assert calls[0][1]["per_page"] == 5
    assert "per-page" not in calls[0][1]

    args = SimpleNamespace(field="ai", topic_id="https://openalex.org/T10077",
                           start=2020, end=2025)
    assert core.build_query(args) == {
        "filter": "topics.id:T10077,publication_year:2020-2025"
    }


def test_sampling_contract_and_h_index():
    calls = []

    def fake_get(path, params, mailto=None):
        calls.append((path, dict(params)))
        return {"results": [
            {"id": "W%s-%s" % (params["seed"], index), "cited_by_count": index}
            for index in range(params["sample"])
        ]}

    old = core.http_get
    core.http_get = fake_get
    try:
        rows = core.fetch_sample({"filter": "publication_year:2025"}, 230,
                                 "id,cited_by_count")
    finally:
        core.http_get = old
    assert len(rows) == 230
    assert len(calls) == 3
    for path, params in calls:
        assert path == "/works"
        assert 1 <= params["per_page"] <= 100
        assert params["sample"] == params["per_page"]
        assert "page" not in params and "cursor" not in params
    assert fusion._h_index([]) == 0
    assert fusion._h_index([6, 5, 3, 1, 0]) == 3


def test_no_retired_openalex_tokens():
    for filename in ("openalex_core.py", "fusion.py"):
        with open(os.path.join(SCRIPTS, "bibliometric", filename), encoding="utf-8") as handle:
            source = handle.read()
        assert '"/concepts"' not in source
        assert '"per-page"' not in source
        assert '"concepts.id:' not in source


def test_error_redaction_covers_network_exceptions():
    sentinel = "SENTINEL_SECRET_123"
    old_fetch = core._fetch_urllib
    old_load = core.load_api_key
    old_retries = core.MAX_RETRIES
    core.load_api_key = lambda: sentinel
    core.MAX_RETRIES = 1

    def fail(url):
        raise urllib.error.URLError("failed url=" + url)

    core._fetch_urllib = fail
    try:
        try:
            core.http_get("/works", {"per_page": 1})
        except RuntimeError as exc:
            assert sentinel not in str(exc)
            assert "api_key=***" in str(exc)
        else:
            raise AssertionError("network failure should propagate")
    finally:
        core._fetch_urllib = old_fetch
        core.load_api_key = old_load
        core.MAX_RETRIES = old_retries


def test_config_is_validated_atomic_and_private():
    sentinel = "SENTINEL_VALID_KEY"
    old_path = core.PKG_CONFIG_PATH
    old_get = core.http_get
    old_cli = core._CLI_API_KEY
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "nested", "openalex.json")
        core.PKG_CONFIG_PATH = target
        core.http_get = lambda *args, **kwargs: {"results": [{"id": "W1"}]}
        try:
            rc = core.cmd_config(SimpleNamespace(
                api_key=sentinel, show=False, clear=False
            ))
            assert rc == 0
            assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
            with open(target, encoding="utf-8") as handle:
                assert json.load(handle)["openalex_api_key"] == sentinel
            assert not [name for name in os.listdir(os.path.dirname(target))
                        if ".tmp-" in name]

            core.http_get = lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("invalid key")
            )
            rc = core.cmd_config(SimpleNamespace(
                api_key="BAD_KEY_VALUE", show=False, clear=False
            ))
            assert rc == 2
            with open(target, encoding="utf-8") as handle:
                assert json.load(handle)["openalex_api_key"] == sentinel
        finally:
            core.PKG_CONFIG_PATH = old_path
            core.http_get = old_get
            core._CLI_API_KEY = old_cli


def test_csv_formula_injection_guard():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "safe.csv")
        core.write_csv(path, ["title"], [["=HYPERLINK(\"bad\")"], ["+SUM(1,1)"],
                                         ["normal"]])
        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows[1][0].startswith("'=")
        assert rows[2][0].startswith("'+")
        assert rows[3][0] == "normal"


def test_config_rejects_control_characters():
    rc = fusion.cmd_config(SimpleNamespace(
        api_key="bad\nkey", stdin=False, show=False, clear=False
    ))
    assert rc == 2


def test_external_source_adapters_keep_sources_separate():
    calls = []

    def fake_external(url, headers=None, retries=3):
        calls.append((url, headers or {}))
        if "crossref.org" in url:
            return {"message": {"total-results": 123}}
        return {"total": 45}

    old = fusion._external_json
    fusion._external_json = fake_external
    try:
        crossref = fusion._crossref_counts("test field", [2024], None)
        s2 = fusion._s2_counts("test field", [2024], "S2_SENTINEL_KEY")
    finally:
        fusion._external_json = old
    assert crossref == {2024: 123}
    assert s2 == {2024: 45}
    assert "rows=0" in calls[0][0]
    assert "S2_SENTINEL_KEY" not in calls[1][0]
    assert calls[1][1]["x-api-key"] == "S2_SENTINEL_KEY"


def test_hong_kong_macao_taiwan_naming_policy():
    cases = [
        ({"key": "HK", "name": "Hong Kong"}, "中国香港", "Hong Kong, China"),
        ({"key": "https://example.test/countries/MO", "name": "Macau"},
         "中国澳门", "Macao, China"),
        ({"key": "TW", "name": "Taiwan"}, "中国台湾", "Taiwan, China"),
        ({"key": "unknown", "name": "中国香港特别行政区"},
         "中国香港", "Hong Kong, China"),
        ({"key": "unknown", "name": "Taiwan, Province of China"},
         "中国台湾", "Taiwan, China"),
    ]
    for row, expected_zh, expected_en in cases:
        assert fusion.country_display_name(row, True) == expected_zh
        assert fusion.country_display_name(row, False) == expected_en

    normalized = fusion.normalize_country_rows([row for row, _, _ in cases[:3]])
    assert [row["country_code"] for row in normalized] == ["HK", "MO", "TW"]
    assert [row["name"] for row in normalized] == [
        "Hong Kong, China", "Macao, China", "Taiwan, China"
    ]


def _bundle_args():
    return SimpleNamespace(
        field="test field", topic_id="T10077", start=2020, end=2021,
        mailto=None, sample_size=20, exact=False, max_exact_works=20000,
        force_exact=False, impact_sample=4, cooc_sample=4, h_index_limit=4,
        top_n=3, keyword_top=3, cooc_top=3, cooc_edges=4,
    )


def test_partial_semantics_include_sampling_failure():
    old = {
        "key": core.load_api_key,
        "annual": core.annual_publications,
        "citations": core.annual_citations,
        "group": core.group_top,
        "sample": core.fetch_sample,
        "export": core.export_works,
    }
    core.load_api_key = lambda: "fake-test-key"
    core.annual_publications = lambda *args, **kwargs: {2020: 10, 2021: 12}
    core.annual_citations = lambda *args, **kwargs: (100, False, None, 10, [], 10.0, 0)

    def group(base, group_by, n, mailto=None):
        if group_by == "authorships.institutions.id":
            raise RuntimeError("institution failed")
        return [{"name": "A", "key": "A1", "count": 5}]

    core.group_top = group
    core.fetch_sample = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("sample failed")
    )
    core.export_works = lambda *args, **kwargs: []
    try:
        bundle = fusion.collect_bundle(_bundle_args())
    finally:
        core.load_api_key = old["key"]
        core.annual_publications = old["annual"]
        core.annual_citations = old["citations"]
        core.group_top = old["group"]
        core.fetch_sample = old["sample"]
        core.export_works = old["export"]
    sections = {item["section"] for item in bundle["failed_sections"]}
    assert bundle["partial"] is True
    assert "ranking:institutions" in sections
    assert "random_analysis_sample" in sections
    assert bundle["impact"]["sample_size"] == 0


def test_endpoint_trust_levels():
    """端点信任分级正确：官方/子域/localhost 可信，其它公网域为 external-custom。"""
    assert core._endpoint_trust_level("https://api.openalex.org") == "default"
    assert core._endpoint_trust_level("http://api.openalex.org") == "default"
    assert core._endpoint_trust_level("https://api.openalex.org/") == "default"
    # 官方子域也视为可信
    assert core._endpoint_trust_level("https://sub.openalex.org") == "default"
    # 回环地址可信（自建 mock、测试、本地开发）
    assert core._endpoint_trust_level("http://127.0.0.1:8080") == "loopback"
    assert core._endpoint_trust_level("http://localhost:8080") == "loopback"
    assert core._endpoint_trust_level("http://[::1]:8080") == "loopback"
    # 其它公网域为不可信的自定义端点
    assert core._endpoint_trust_level("https://evil.example.com") == "external-custom"
    assert core._endpoint_trust_level("https://api.openalex.org.evil.com") == "external-custom"
    assert core._endpoint_trust_level("") == "default"  # 空值回退默认端点


def test_http_get_strips_key_on_unauthorized_custom_endpoint(monkeypatch=None):
    """未授权的自定义端点：请求仍发出，但 api_key 被剥离并打印警告。"""
    import contextlib
    import io as _io

    sent = {}

    def fake_fetch(url):
        sent["url"] = url
        return b"{}"

    old_key = core._CLI_API_KEY
    old_trust = core._ENDPOINT_TRUST
    old_allow = core._CUSTOM_ENDPOINT_OK
    old_fetch = core._fetch_urllib
    core._CLI_API_KEY = "SECRET-KEY-123"
    core._ENDPOINT_TRUST = "external-custom"
    core._CUSTOM_ENDPOINT_OK = False
    core._fetch_urllib = fake_fetch
    err_buf = _io.StringIO()
    try:
        with contextlib.redirect_stderr(err_buf):
            core.http_get("/works", {"per_page": 1})
    finally:
        core._CLI_API_KEY = old_key
        core._ENDPOINT_TRUST = old_trust
        core._CUSTOM_ENDPOINT_OK = old_allow
        core._fetch_urllib = old_fetch
    assert "api_key" not in sent["url"], "密钥不应出现在未授权端点的请求 URL 中"
    assert "SECRET-KEY-123" not in sent["url"]
    assert "WARNING" in err_buf.getvalue(), "应向 stderr 输出剥离密钥的警告"


def test_http_get_keeps_key_on_authorized_custom_endpoint():
    """已授权的自定义端点（OPENALEX_ALLOW_CUSTOM_ENDPOINT=1）：正常携带 key。"""
    sent = {}

    def fake_fetch(url):
        sent["url"] = url
        return b"{}"

    old_key = core._CLI_API_KEY
    old_trust = core._ENDPOINT_TRUST
    old_allow = core._CUSTOM_ENDPOINT_OK
    old_fetch = core._fetch_urllib
    core._CLI_API_KEY = "SECRET-KEY-456"
    core._ENDPOINT_TRUST = "external-custom"
    core._CUSTOM_ENDPOINT_OK = True
    core._fetch_urllib = fake_fetch
    try:
        core.http_get("/works", {"per_page": 1})
    finally:
        core._CLI_API_KEY = old_key
        core._ENDPOINT_TRUST = old_trust
        core._CUSTOM_ENDPOINT_OK = old_allow
        core._fetch_urllib = old_fetch
    assert "api_key=SECRET-KEY-456" in sent["url"], "授权端点应正常携带 key"


def test_http_get_keeps_key_on_default_endpoint():
    """官方默认端点：正常携带 key（主鉴权路径，不应受安全防线影响）。"""
    sent = {}

    def fake_fetch(url):
        sent["url"] = url
        return b"{}"

    old_key = core._CLI_API_KEY
    old_trust = core._ENDPOINT_TRUST
    old_allow = core._CUSTOM_ENDPOINT_OK
    old_fetch = core._fetch_urllib
    core._CLI_API_KEY = "SECRET-KEY-789"
    core._ENDPOINT_TRUST = "default"
    core._CUSTOM_ENDPOINT_OK = False
    core._fetch_urllib = fake_fetch
    try:
        core.http_get("/works", {"per_page": 1})
    finally:
        core._CLI_API_KEY = old_key
        core._ENDPOINT_TRUST = old_trust
        core._CUSTOM_ENDPOINT_OK = old_allow
        core._fetch_urllib = old_fetch
    assert "api_key=SECRET-KEY-789" in sent["url"], "官方端点应正常携带 key"


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("[OK]", test.__name__)
    print("\nFusion contract/security tests passed: %s" % len(tests))


if __name__ == "__main__":
    main()
