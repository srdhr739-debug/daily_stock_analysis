#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 reports/*.md 生成为适合手机浏览的静态网站（用于 GitHub Pages）。

用法:
    python scripts/build_report_site.py --reports reports --site site [--keep-days 60]

- 新报告会复制到 <site>/reports/，已存在的历史报告会保留，实现按日期累积。
- 每次运行都会根据 <site>/reports/ 下的全部 Markdown 重新生成 index.html 和各报告页面。
- 超过 --keep-days 天的报告会被清理（0 表示不清理）。
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import markdown2

# report_20261114.md / report_20261114_600519.md / market_review_20261114.md / dashboard_20261114.md
FILENAME_RE = re.compile(r"^(?P<kind>report|market_review|dashboard)_(?P<date>\d{8})(?:_(?P<code>[^/]+))?\.md$")

KIND_LABELS = {
    "report": "Daily report",
    "stock": "Stock",
    "market_review": "Market review",
    "dashboard": "Dashboard",
}
KIND_ORDER = {"market_review": 0, "report": 1, "dashboard": 2, "stock": 3}

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--text:#1b1f24;--muted:#5d6670;--line:#e2e5e9;--accent:#0b7a75;--code:#f0f2f4}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#181c21;--text:#e6e8eb;--muted:#9aa3ad;--line:#2a3038;--accent:#3cc2bb;--code:#20262d}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Noto Sans CJK SC","Noto Sans Tamil",sans-serif}
header{position:sticky;top:0;z-index:1;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;align-items:center;gap:12px}
header h1{font-size:17px;margin:0;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
header a{color:var(--accent);text-decoration:none;font-weight:600;white-space:nowrap}
main{max-width:760px;margin:0 auto;padding:16px}
.day{margin:0 0 20px}
.day h2{font-size:14px;color:var(--muted);margin:0 0 8px;font-weight:600;letter-spacing:.02em}
.list{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.list a{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 16px;color:var(--text);text-decoration:none;border-top:1px solid var(--line);min-height:48px}
.list a:first-child{border-top:0}
.list a span.tag{font-size:12px;color:var(--muted);white-space:nowrap}
.list a:active{background:var(--code)}
.empty{color:var(--muted);text-align:center;padding:48px 16px}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px 16px 16px;overflow-wrap:anywhere}
article h1{font-size:21px}article h2{font-size:18px}article h3{font-size:16px}
article a{color:var(--accent)}
article table{display:block;overflow-x:auto;border-collapse:collapse;max-width:100%;font-size:14px}
article th,article td{border:1px solid var(--line);padding:6px 8px;white-space:nowrap}
article pre{overflow-x:auto;background:var(--code);padding:12px;border-radius:8px;font-size:13px}
article code{background:var(--code);padding:1px 4px;border-radius:4px;font-size:.9em}
article pre code{padding:0;background:none}
article blockquote{margin:0;padding:4px 12px;border-left:3px solid var(--accent);color:var(--muted)}
article img{max-width:100%;height:auto}
footer{color:var(--muted);font-size:12px;text-align:center;padding:24px 16px}
"""


def page(title: str, body: str, back: Optional[str] = None) -> str:
    back_link = f'<a href="{back}">&larr; Back</a>' if back else ""
    return (
        "<!DOCTYPE html>\n<html lang=\"zh\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="robots" content="noindex">\n'
        f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"<header>{back_link}<h1>{html.escape(title)}</h1></header>\n"
        f"<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


def parse_name(path: Path) -> Optional[Dict[str, str]]:
    m = FILENAME_RE.match(path.name)
    if not m:
        return None
    try:
        date = datetime.strptime(m.group("date"), "%Y%m%d")
    except ValueError:
        return None
    kind = m.group("kind")
    code = m.group("code") or ""
    if kind == "report" and code:
        kind = "stock"
    return {"kind": kind, "code": code, "date": date.strftime("%Y-%m-%d"), "stem": path.stem}


def first_heading(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:80]
    return ""


def render_markdown(text: str) -> str:
    # safe_mode="escape": 报告内容来自 LLM，转义其中的原始 HTML，避免注入脚本
    return markdown2.markdown(
        text,
        safe_mode="escape",
        extras=["tables", "fenced-code-blocks", "strike", "cuddled-lists", "break-on-newline"],
    )


def build(reports_dir: Path, site_dir: Path, keep_days: int) -> int:
    store = site_dir / "reports"
    store.mkdir(parents=True, exist_ok=True)

    copied = 0
    if reports_dir.is_dir():
        for src in sorted(reports_dir.rglob("*.md")):
            if parse_name(src):
                shutil.copy2(src, store / src.name)
                copied += 1

    cutoff = None
    if keep_days > 0:
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")

    entries: List[Dict[str, str]] = []
    for md in sorted(store.glob("*.md")):
        info = parse_name(md)
        if not info:
            continue
        if cutoff and info["date"] < cutoff:
            md.unlink()
            stale_html = store / f"{info['stem']}.html"
            if stale_html.exists():
                stale_html.unlink()
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        info["heading"] = first_heading(text)
        label = info["code"] or KIND_LABELS[info["kind"]]
        info["label"] = label
        title = f"{info['date']} · {label}"
        (store / f"{info['stem']}.html").write_text(
            page(title, f"<article>\n{render_markdown(text)}\n</article>", back="../index.html"),
            encoding="utf-8",
        )
        entries.append(info)

    by_day: Dict[str, List[Dict[str, str]]] = {}
    for e in entries:
        by_day.setdefault(e["date"], []).append(e)

    if by_day:
        parts = []
        for day in sorted(by_day, reverse=True):
            items = sorted(by_day[day], key=lambda e: (KIND_ORDER[e["kind"]], e["code"]))
            links = "".join(
                f'<a href="reports/{html.escape(e["stem"])}.html">'
                f"<span>{html.escape(e['label'])}</span>"
                f'<span class="tag">{"Stock" if e["kind"] == "stock" else ""}</span></a>'
                for e in items
            )
            parts.append(f'<section class="day"><h2>{day}</h2><div class="list">{links}</div></section>')
        body = "\n".join(parts)
    else:
        body = '<p class="empty">No reports yet. They appear here after the daily analysis runs.</p>'

    if (site_dir / "india" / "index.html").exists():
        body = ('<section class="day"><div class="list"><a href="india/index.html">'
                '<span>India &amp; Forex Daily</span><span class="tag">Dashboard</span></a></div></section>\n' + body)

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    body += f"\n<footer>Updated {updated} (UTC) · Not investment advice</footer>"
    (site_dir / "index.html").write_text(page("Daily Stock Reports", body), encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")

    print(f"copied {copied} new report(s); site now has {len(entries)} report(s) over {len(by_day)} day(s)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a mobile-friendly static site from Markdown reports")
    parser.add_argument("--reports", default="reports", help="directory containing new report .md files")
    parser.add_argument("--site", default="site", help="output site directory (existing history is kept)")
    parser.add_argument("--keep-days", type=int, default=60, help="drop reports older than N days (0 = keep all)")
    args = parser.parse_args(argv)
    return build(Path(args.reports), Path(args.site), args.keep_days)


if __name__ == "__main__":
    sys.exit(main())
