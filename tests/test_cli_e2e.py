#!/usr/bin/env python3
"""Real CLI E2E against a local fake OpenAlex HTTP server (no public network)."""

import csv
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.abspath(os.path.join(HERE, "..", "scripts", "fusion_run.py"))
SENTINEL = "E2E_SENTINEL_OPENALEX_KEY"
CALLS = []


def make_work(index, year, cited):
    keyword_a = "Neural networks" if index % 2 else "Deep learning"
    keyword_b = "Computer vision" if index % 3 else "Representation learning"
    return {
        "id": "https://openalex.org/W%s" % index,
        "display_name": "Fixture work %s" % index,
        "publication_year": year,
        "authorships": [{
            "author": {"display_name": "Author %s" % (index % 3 + 1)},
            "institutions": [{"display_name": "Institution %s" % (index % 3 + 1),
                              "country_code": ["US", "CN", "GB"][index % 3]}],
        }],
        "primary_location": {"source": {"display_name": "Journal %s" % (index % 2 + 1)}},
        "cited_by_count": cited,
        "type": "article",
        "doi": "https://doi.org/10.0000/fixture.%s" % index,
        "keywords": [
            {"id": "K%sA" % index, "display_name": keyword_a},
            {"id": "K%sB" % index, "display_name": keyword_b},
            {"id": "KCOMMON", "display_name": "Machine learning"},
        ],
        "primary_topic": {
            "id": "https://openalex.org/T%s" % (100 + index % 3),
            "display_name": "Fixture topic %s" % (index % 3 + 1),
        },
    }


WORKS = [
    make_work(1, 2020, 20), make_work(2, 2020, 15), make_work(3, 2020, 8),
    make_work(4, 2021, 4), make_work(5, 2021, 1), make_work(6, 2021, 0),
]


GROUPS = {
    "authorships.institutions.id": [
        {"key": "I1", "key_display_name": "Institution 1", "count": 4},
        {"key": "I2", "key_display_name": "Institution 2", "count": 3},
        {"key": "I3", "key_display_name": "Institution 3", "count": 2},
    ],
    "authorships.author.id": [
        {"key": "A1", "key_display_name": "Author 1", "count": 4},
        {"key": "A2", "key_display_name": "Author 2", "count": 3},
        {"key": "A3", "key_display_name": "Author 3", "count": 2},
    ],
    "primary_location.source.id": [
        {"key": "S1", "key_display_name": "Journal 1", "count": 4},
        {"key": "S2", "key_display_name": "Journal 2", "count": 2},
    ],
    "authorships.institutions.country_code": [
        {"key": "HK", "key_display_name": "Hong Kong", "count": 3},
        {"key": "MO", "key_display_name": "Macau", "count": 2},
        {"key": "TW", "key_display_name": "Taiwan", "count": 1},
    ],
    "keywords.id": [
        {"key": "K1", "key_display_name": "Machine learning", "count": 6},
        {"key": "K2", "key_display_name": "Deep learning", "count": 4},
        {"key": "K3", "key_display_name": "Computer vision", "count": 3},
    ],
    "primary_topic.id": [
        {"key": "T101", "key_display_name": "Fixture topic 1", "count": 3},
        {"key": "T102", "key_display_name": "Fixture topic 2", "count": 2},
        {"key": "T103", "key_display_name": "Fixture topic 3", "count": 1},
    ],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        CALLS.append((parsed.path, params))
        if parsed.path == "/topics":
            self.send_json({"results": [{
                "id": "https://openalex.org/T10077",
                "display_name": "Fixture topic",
                "works_count": 6,
                "domain": {"display_name": "Sciences"},
                "field": {"display_name": "Computer science"},
            }]})
            return
        if parsed.path != "/works":
            self.send_json({"error": "not found"}, 404)
            return

        group_by = params.get("group_by", [None])[0]
        if group_by == "publication_year":
            self.send_json({"group_by": [
                {"key": "2020", "count": 3}, {"key": "2021", "count": 3}
            ], "meta": {"count": 6}})
            return
        if group_by:
            self.send_json({"group_by": GROUPS.get(group_by, []), "meta": {"count": 6}})
            return

        if "sample" in params:
            size = int(params["sample"][0])
            self.send_json({"results": WORKS[:size], "meta": {"count": 6}})
            return

        filter_value = params.get("filter", [""])[0]
        if "publication_year:2020" in filter_value:
            selected = [work for work in WORKS if work["publication_year"] == 2020]
        elif "publication_year:2021" in filter_value:
            selected = [work for work in WORKS if work["publication_year"] == 2021]
        else:
            selected = sorted(WORKS, key=lambda work: work["cited_by_count"], reverse=True)
        self.send_json({"results": selected, "meta": {"count": len(selected),
                                                       "next_cursor": None}})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="融合 cli e2e ") as tmp:
            out = os.path.join(tmp, "报告 输出")
            env = os.environ.copy()
            env.update({
                "OPENALEX_API_BASE": "http://127.0.0.1:%s" % server.server_port,
                "OPENALEX_API_KEY": SENTINEL,
                "MPLCONFIGDIR": os.path.join(tmp, "mpl"),
                "PYTHONPYCACHEPREFIX": os.path.join(tmp, "pycache"),
            })
            command = [
                sys.executable, SCRIPT, "report", "--field", "fixture field",
                "--topic-id", "T10077", "--start", "2020", "--end", "2021",
                "--impact-sample", "4", "--cooc-sample", "4",
                "--h-index-limit", "4", "--sample-size", "3",
                "--top-n", "3", "--keyword-top", "3", "--cooc-top", "4",
                "--cooc-edges", "5", "--lang", "en", "--out", out,
            ]
            result = subprocess.run(command, cwd=tmp, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=60, check=False)
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert SENTINEL not in result.stdout + result.stderr
            payload = json.loads(result.stdout)
            assert payload["summary"]["partial"] is False
            assert os.path.isfile(os.path.join(out, "bundle.json"))
            with open(os.path.join(out, "bundle.json"), encoding="utf-8") as handle:
                bundle = json.load(handle)
            countries = {row["country_code"]: row
                         for row in bundle["rankings"]["countries"]}
            assert countries["HK"]["name_zh"] == "中国香港"
            assert countries["HK"]["name_en"] == "Hong Kong, China"
            assert countries["MO"]["name_zh"] == "中国澳门"
            assert countries["MO"]["name_en"] == "Macao, China"
            assert countries["TW"]["name_zh"] == "中国台湾"
            assert countries["TW"]["name_en"] == "Taiwan, China"
            with open(os.path.join(out, "ranking_countries.csv"),
                      encoding="utf-8-sig", newline="") as handle:
                country_csv = {row["country_code"]: row
                               for row in csv.DictReader(handle)}
            assert country_csv["HK"]["name_en"] == "Hong Kong, China"
            assert country_csv["MO"]["name_en"] == "Macao, China"
            assert country_csv["TW"]["name_en"] == "Taiwan, China"
            pngs = [name for name in os.listdir(out) if name.endswith(".png")]
            assert len(pngs) == 9, pngs
            assert all(os.path.getsize(os.path.join(out, name)) > 10000 for name in pngs)

        assert CALLS
        for path, params in CALLS:
            assert params.get("api_key") == [SENTINEL]
            assert "per-page" not in params
            if "per_page" in params:
                assert 1 <= int(params["per_page"][0]) <= 100
        print("[OK] real CLI E2E via local fake OpenAlex server")
        print("[OK] requests=%s charts=10 secret_scan=clean" % len(CALLS))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
