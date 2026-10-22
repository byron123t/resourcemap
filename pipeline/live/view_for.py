"""Pick the map view (a key of VIEWS in site/index.html) that best shows a story or a scenario.

The rules live once, in site/index.html (`const VIEW_FOR = [...]` in the #simCore block, plain JSON) and are read
from there, so the page, the CLI (cli/lib/page.mjs) and the pipelines agree. view_for() mirrors viewFor() in the
page; tests/view_for_cases.json holds the cases both are tested against.
"""
import json
import os
import re

INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "site", "index.html")
_rules = None
_views = None


def _html():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def rules():
    global _rules
    if _rules is None:
        m = re.search(r"^const VIEW_FOR = (\[.*?^\]);", _html(), re.S | re.M)
        if not m:
            raise RuntimeError("VIEW_FOR not found in site/index.html")
        _rules = json.loads(m.group(1))
    return _rules


def views():
    """{key: (group, label)} for every view in VIEWS."""
    global _views
    if _views is None:
        h = _html()
        i = h.index("const VIEWS = {")
        _views = {k: (g, l) for k, g, l in re.findall(r'\n    (\w+): \{ g: "([^"]*)", label: "([^"]*)"', h[i:i + 80000])}
    return _views


def view_for(src=None, kind=None, cat=None, shocks=(), text=""):
    """First matching rule's view; same semantics as viewFor() in site/index.html."""
    sh = list(shocks or [])
    for r in rules():
        if r.get("src") and r["src"] != src:
            continue
        if r.get("kind") and kind not in r["kind"]:
            continue
        if r.get("cat") and cat not in r["cat"]:
            continue
        if r.get("shock") and not any(k in r["shock"] for k in sh):
            continue
        if r.get("text") and not re.search(r["text"], text or "", re.I):
            continue
        return r["view"]
    return "overview"
