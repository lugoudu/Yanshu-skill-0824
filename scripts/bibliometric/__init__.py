"""bibliometric-fusion-v1 skill 作为子包。

从原 skill 的 scripts/ 目录移植,核心函数 import 即用:
- collect_bundle(args):采集 OpenAlex 数据,返回 bundle dict
- write_bundle(bundle, outdir):写 CSV + bundle.json
- render_all(bundle, outdir, lang, style):渲染全部图表 PNG
- render_chart(bundle, chart_type, outdir, lang, style):渲染单图
- resolve_topic(field, mailto, limit):topic 名称消歧

唯一改造:fusion.py 的 `import openalex_core` 改为包内相对 import。
"""
from .fusion import (
    collect_bundle,
    write_bundle,
    render_all,
    render_chart,
    load_style,
    CHART_TYPES,
)
from .openalex_core import resolve_topic, load_api_key

__all__ = [
    "collect_bundle",
    "write_bundle",
    "render_all",
    "render_chart",
    "load_style",
    "CHART_TYPES",
    "resolve_topic",
    "load_api_key",
]
