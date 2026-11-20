# -*- coding: utf-8 -*-
"""
India + Forex daily tracker.

Fetches Indian indices, NSE/BSE stocks and forex pairs from Yahoo Finance,
computes simple daily indicators, optionally asks the configured LLM (via the
project's ``GeminiAnalyzer``) for a short daily brief, and writes a
mobile-friendly static dashboard plus dated daily reports into ``<site>/india/``.

Usage:
    python -m src.india_fx_tracker --site site [--force] [--no-ai]

Configuration (environment variables, all optional; see docs/india-fx-tracker.md):
    INDIA_FX_INDICES          ^NSEI:NIFTY 50,^BSESN:SENSEX,^NSEBANK:NIFTY BANK
    INDIA_FX_STOCKS           RELIANCE.NS,TCS.NS,HDFCBANK.NS,INFY.NS,ICICIBANK.NS
    INDIA_FX_PAIRS            USDINR=X,EURINR=X,GBPINR=X,JPYINR=X,EURUSD=X
    INDIA_FX_AI_ENABLED       true
    INDIA_FX_REPORT_LANGUAGE  en   (en / ta / hi ...; only the AI brief is translated)
    INDIA_FX_KEEP_DAYS        90   (0 = keep all daily reports)
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import markdown2

logger = logging.getLogger("india_fx_tracker")

DEFAULT_INDICES = "^NSEI:NIFTY 50,^BSESN:SENSEX,^NSEBANK:NIFTY BANK"
DEFAULT_STOCKS = "RELIANCE.NS,TCS.NS,HDFCBANK.NS,INFY.NS,ICICIBANK.NS"
DEFAULT_PAIRS = "USDINR=X,EURINR=X,GBPINR=X,JPYINR=X,EURUSD=X"

GROUPS = (("index", "Indices"), ("stock", "Stocks"), ("forex", "Forex"))
LANGUAGE_NAMES = {"en": "English", "ta": "Tamil", "hi": "Hindi", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam"}
DISCLAIMER = "For information only. Not investment advice."

# A fetcher returns a list of (date "YYYY-MM-DD", close) sorted ascending, or [] when unavailable.
Fetcher = Callable[[str], List[Tuple[str, float]]]


# --------------------------------------------------------------------------- config


@dataclass
class Instrument:
    symbol: str
    name: str
    group: str  # index / stock / forex


def _parse_list(raw: str, group: str) -> List[Instrument]:
    items: List[Instrument] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        symbol, _, name = part.partition(":")
        symbol = symbol.strip()
        name = name.strip() or _default_name(symbol, group)
        if symbol:
            items.append(Instrument(symbol=symbol, name=name, group=group))
    return items


def _default_name(symbol: str, group: str) -> str:
    if group == "forex" and symbol.upper().endswith("=X"):
        pair = symbol[:-2].upper()
        return f"{pair[:3]}/{pair[3:]}" if len(pair) == 6 else pair
    if group == "stock":
        return symbol.split(".")[0].upper()
    return symbol


def load_instruments(env: Optional[Dict[str, str]] = None) -> List[Instrument]:
    env = os.environ if env is None else env
    return (
        _parse_list(env.get("INDIA_FX_INDICES") or DEFAULT_INDICES, "index")
        + _parse_list(env.get("INDIA_FX_STOCKS") or DEFAULT_STOCKS, "stock")
        + _parse_list(env.get("INDIA_FX_PAIRS") or DEFAULT_PAIRS, "forex")
    )


def _env_bool(value: Optional[str], default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- data


def yfinance_fetcher(symbol: str) -> List[Tuple[str, float]]:
    """Fetch ~6 months of daily closes from Yahoo Finance. Returns [] on failure."""
    try:
        import yfinance as yf

        df = yf.Ticker(symbol).history(period="6mo", interval="1d", auto_adjust=False)
    except Exception as exc:  # network / provider errors must not stop other symbols
        logger.warning("fetch failed for %s: %s", symbol, exc)
        return []
    if df is None or df.empty or "Close" not in df:
        logger.warning("no data for %s", symbol)
        return []
    rows: List[Tuple[str, float]] = []
    for idx, close in df["Close"].items():
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            rows.append((idx.strftime("%Y-%m-%d"), value))
    return rows


@dataclass
class Metrics:
    symbol: str
    name: str
    group: str
    ok: bool = False
    date: str = ""
    close: Optional[float] = None
    change_pct: Optional[float] = None
    change_5d_pct: Optional[float] = None
    change_1m_pct: Optional[float] = None
    ma20: Optional[float] = None
    ma50: Optional[float] = None
    rsi14: Optional[float] = None
    high_6m: Optional[float] = None
    low_6m: Optional[float] = None
    trend: str = ""
    notes: List[str] = field(default_factory=list)
    spark: List[float] = field(default_factory=list)


def _pct(new: float, old: float) -> Optional[float]:
    if old in (0, None):
        return None
    return (new / old - 1.0) * 100.0


def _rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):  # Wilder smoothing
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def compute_metrics(inst: Instrument, rows: List[Tuple[str, float]]) -> Metrics:
    m = Metrics(symbol=inst.symbol, name=inst.name, group=inst.group)
    if len(rows) < 2:
        m.notes.append("Data unavailable")
        return m
    closes = [c for _, c in rows]
    last = closes[-1]
    m.ok = True
    m.date = rows[-1][0]
    m.close = last
    m.change_pct = _pct(last, closes[-2])
    if len(closes) > 5:
        m.change_5d_pct = _pct(last, closes[-6])
    if len(closes) > 21:
        m.change_1m_pct = _pct(last, closes[-22])
    if len(closes) >= 20:
        m.ma20 = sum(closes[-20:]) / 20
    if len(closes) >= 50:
        m.ma50 = sum(closes[-50:]) / 50
    m.rsi14 = _rsi(closes)
    m.high_6m, m.low_6m = max(closes), min(closes)
    m.spark = closes[-30:]

    if m.ma20 is not None and m.ma50 is not None:
        if last > m.ma20 > m.ma50:
            m.trend = "Uptrend"
        elif last < m.ma20 < m.ma50:
            m.trend = "Downtrend"
        else:
            m.trend = "Sideways"
    elif m.ma20 is not None:
        m.trend = "Above MA20" if last >= m.ma20 else "Below MA20"

    if m.rsi14 is not None:
        if m.rsi14 >= 70:
            m.notes.append("RSI overbought (≥70)")
        elif m.rsi14 <= 30:
            m.notes.append("RSI oversold (≤30)")
    if m.ma20 is not None and len(closes) >= 21:
        prev_ma20 = sum(closes[-21:-1]) / 20
        if closes[-2] < prev_ma20 and last >= m.ma20:
            m.notes.append("Crossed above MA20")
        elif closes[-2] > prev_ma20 and last <= m.ma20:
            m.notes.append("Crossed below MA20")
    if last >= m.high_6m:
        m.notes.append("6-month high")
    elif last <= m.low_6m:
        m.notes.append("6-month low")
    if m.change_pct is not None and abs(m.change_pct) >= (1.0 if inst.group == "forex" else 3.0):
        m.notes.append("Big move today")
    return m


def collect(instruments: List[Instrument], fetcher: Fetcher) -> List[Metrics]:
    return [compute_metrics(inst, fetcher(inst.symbol)) for inst in instruments]


# --------------------------------------------------------------------------- summary


def _fmt_price(m: Metrics, value: Optional[float]) -> str:
    if value is None:
        return "—"
    if m.group == "forex":
        return f"{value:,.4f}"
    return f"{value:,.2f}"


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def rule_based_brief(metrics: List[Metrics]) -> str:
    ok = [m for m in metrics if m.ok]
    if not ok:
        return "No market data could be fetched today."
    lines: List[str] = []
    idx = [m for m in ok if m.group == "index"]
    if idx:
        avg = sum(m.change_pct or 0 for m in idx) / len(idx)
        mood = "positive" if avg > 0.3 else "negative" if avg < -0.3 else "flat"
        parts = ", ".join(f"{m.name} {_fmt_pct(m.change_pct)}" for m in idx)
        lines.append(f"- **Market mood: {mood}.** {parts}.")
    stocks = sorted((m for m in ok if m.group == "stock" and m.change_pct is not None), key=lambda m: m.change_pct)
    if stocks:
        lines.append(f"- **Top gainer:** {stocks[-1].name} {_fmt_pct(stocks[-1].change_pct)}; "
                     f"**top loser:** {stocks[0].name} {_fmt_pct(stocks[0].change_pct)}.")
    usdinr = next((m for m in ok if m.symbol.upper() == "USDINR=X"), None)
    if usdinr and usdinr.change_pct is not None:
        side = "weaker" if usdinr.change_pct > 0 else "stronger"
        lines.append(f"- **Rupee:** USD/INR {_fmt_price(usdinr, usdinr.close)} ({_fmt_pct(usdinr.change_pct)}), "
                     f"INR {side} vs USD.")
    flagged = [m for m in ok if m.notes]
    if flagged:
        lines.append("- **Watch:** " + "; ".join(f"{m.name}: {', '.join(m.notes)}" for m in flagged[:8]) + ".")
    return "\n".join(lines)


def build_ai_prompt(metrics: List[Metrics], language: str) -> str:
    lang_name = LANGUAGE_NAMES.get(language, language)
    data = [
        {k: v for k, v in asdict(m).items() if k not in {"spark"}}
        for m in metrics if m.ok
    ]
    return (
        "You are a careful market analyst writing a short daily brief for a retail investor in India.\n"
        f"Write in {lang_name}. Use Markdown with 4-6 bullet points and at most 180 words.\n"
        "Cover: overall Indian market mood (indices), notable stock moves, the rupee and other forex pairs, "
        "and 2-3 things to watch next session. Only use the numbers given; do not invent news or prices. "
        "No buy/sell recommendations. Do not include a title.\n\n"
        f"Data (JSON, percentages are daily % changes unless named otherwise):\n{json.dumps(data, ensure_ascii=False)}"
    )


def ai_brief(metrics: List[Metrics], language: str) -> Optional[str]:
    """Ask the project's configured LLM for a brief. Returns None when unavailable or on failure."""
    try:
        from src.analyzer import GeminiAnalyzer
        from src.config import get_config

        analyzer = GeminiAnalyzer(config=get_config())
        if not analyzer.is_available():
            logger.info("no LLM configured; using rule-based brief")
            return None
        text = analyzer.generate_text(build_ai_prompt(metrics, language), max_tokens=1024, temperature=0.3)
    except Exception as exc:  # LLM problems must not block the dashboard
        logger.warning("AI brief failed: %s", exc)
        return None
    text = (text or "").strip()
    return text or None


# --------------------------------------------------------------------------- rendering

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--text:#1b1f24;--muted:#5d6670;--line:#e2e5e9;--accent:#0b7a75;--up:#11803a;--down:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#181c21;--text:#e6e8eb;--muted:#9aa3ad;--line:#2a3038;--accent:#3cc2bb;--up:#4cc76f;--down:#ff6b6b}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Tamil","Noto Sans Devanagari",sans-serif}
header{position:sticky;top:0;z-index:1;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;align-items:center;gap:12px}
header h1{font-size:17px;margin:0;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
header a{color:var(--accent);text-decoration:none;font-weight:600;white-space:nowrap}
main{max-width:760px;margin:0 auto;padding:16px}
h2{font-size:14px;color:var(--muted);margin:22px 0 8px;font-weight:600;letter-spacing:.02em;text-transform:uppercase}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.brief p,.brief ul{margin:6px 0}.brief ul{padding-left:20px}
.meta{color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:1fr;gap:10px}
@media (min-width:560px){.grid{grid-template-columns:1fr 1fr}}
.q{display:grid;grid-template-columns:1fr auto;gap:2px 12px;align-items:center}
.q .n{font-weight:600;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.q .p{font-variant-numeric:tabular-nums;text-align:right;font-weight:600}
.q .c{font-variant-numeric:tabular-nums;text-align:right;font-size:14px}
.q .s{color:var(--muted);font-size:13px}
.q svg{grid-column:1/-1;width:100%;height:36px;margin-top:4px}
.up{color:var(--up)}.down{color:var(--down)}
.tags{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.tag{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted)}
.list{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.list a{display:flex;justify-content:space-between;padding:13px 16px;min-height:46px;color:var(--text);text-decoration:none;border-top:1px solid var(--line)}
.list a:first-child{border-top:0}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px 16px 16px;overflow-wrap:anywhere}
article table{display:block;overflow-x:auto;border-collapse:collapse;max-width:100%;font-size:14px}
article th,article td{border:1px solid var(--line);padding:6px 8px;white-space:nowrap;text-align:right}
article th:first-child,article td:first-child{text-align:left}
footer{color:var(--muted);font-size:12px;text-align:center;padding:24px 16px}
"""


def _page(title: str, body: str, back: Optional[str] = None) -> str:
    back_link = f'<a href="{back}">&larr; Back</a>' if back else ""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="robots" content="noindex">\n'
        f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"<header>{back_link}<h1>{html.escape(title)}</h1></header>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


def render_markdown(text: str) -> str:
    # LLM output is untrusted: escape raw HTML.
    return markdown2.markdown(text, safe_mode="escape", extras=["tables", "strike", "cuddled-lists"])


def _cls(value: Optional[float]) -> str:
    if value is None or value == 0:
        return ""
    return "up" if value > 0 else "down"


def _sparkline(values: List[float]) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    w, h = 100.0, 30.0
    pts = " ".join(
        f"{i * w / (len(values) - 1):.1f},{h - (v - lo) / span * (h - 2) - 1:.1f}" for i, v in enumerate(values)
    )
    cls = _cls(values[-1] - values[0])
    return (f'<svg class="{cls}" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" '
            f'points="{pts}"/></svg>')


def _quote_card(m: Metrics) -> str:
    e = html.escape
    if not m.ok:
        return (f'<div class="card q"><span class="n">{e(m.name)}</span><span class="p">—</span>'
                f'<span class="s">{e(m.symbol)}</span><span class="c">Data unavailable</span></div>')
    stats = [f"5D {_fmt_pct(m.change_5d_pct)}", f"1M {_fmt_pct(m.change_1m_pct)}"]
    if m.rsi14 is not None:
        stats.append(f"RSI {m.rsi14:.0f}")
    tags = ([m.trend] if m.trend else []) + m.notes
    tag_html = "".join(f'<span class="tag">{e(t)}</span>' for t in tags)
    return (
        f'<div class="card q"><span class="n">{e(m.name)}</span>'
        f'<span class="p">{_fmt_price(m, m.close)}</span>'
        f'<span class="s">{e(" · ".join(stats))}</span>'
        f'<span class="c {_cls(m.change_pct)}">{_fmt_pct(m.change_pct)}</span>'
        f"{_sparkline(m.spark)}"
        f'<div class="tags">{tag_html}</div></div>'
    )


def report_markdown(date: str, metrics: List[Metrics], brief: str, brief_source: str) -> str:
    lines = [f"# India & Forex Daily · {date}", "", f"## Brief ({brief_source})", "", brief, ""]
    for group, title in GROUPS:
        rows = [m for m in metrics if m.group == group]
        if not rows:
            continue
        lines += [f"## {title}", "", "| Name | Close | 1D | 5D | 1M | RSI | Trend |", "|---|---|---|---|---|---|---|"]
        for m in rows:
            rsi = f"{m.rsi14:.0f}" if m.rsi14 is not None else "—"
            name = m.name.replace("|", "/")
            lines.append(
                f"| {name} | {_fmt_price(m, m.close)} | {_fmt_pct(m.change_pct)} | {_fmt_pct(m.change_5d_pct)} | "
                f"{_fmt_pct(m.change_1m_pct)} | {rsi} | {m.trend or '—'} |"
            )
        lines.append("")
    lines.append(f"_{DISCLAIMER}_")
    return "\n".join(lines) + "\n"


def _data_date(metrics: List[Metrics]) -> Optional[str]:
    for group in ("index", "stock", "forex"):
        dates = [m.date for m in metrics if m.ok and m.group == group]
        if dates:
            return max(dates)
    return None


def write_site(site: Path, date: str, metrics: List[Metrics], brief: str, brief_source: str, keep_days: int) -> None:
    root = site / "india"
    reports = root / "reports"
    data_dir = root / "data"
    reports.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "date": date,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "brief": brief,
        "brief_source": brief_source,
        "instruments": [asdict(m) for m in metrics],
    }
    (data_dir / f"{date}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (data_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    md = report_markdown(date, metrics, brief, brief_source)
    (reports / f"{date}.md").write_text(md, encoding="utf-8")

    # prune old days
    if keep_days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        for folder, ext in ((reports, ".md"), (reports, ".html"), (data_dir, ".json")):
            for f in folder.glob(f"*{ext}"):
                if f.stem != "latest" and f.stem < cutoff:
                    f.unlink()

    # (re)render every daily report page
    days = sorted((f.stem for f in reports.glob("*.md")), reverse=True)
    for day in days:
        text = (reports / f"{day}.md").read_text(encoding="utf-8")
        (reports / f"{day}.html").write_text(
            _page(f"Daily report · {day}", f"<article>{render_markdown(text)}</article>", back="../index.html"),
            encoding="utf-8",
        )

    # dashboard
    parts = [
        f'<p class="meta">Market data as of {html.escape(date)} (Yahoo Finance, may be delayed) · '
        f'updated {html.escape(payload["generated_at"])}</p>',
        f'<h2>Today\'s brief · {html.escape(brief_source)}</h2><div class="card brief">{render_markdown(brief)}</div>',
    ]
    for group, title in GROUPS:
        cards = [_quote_card(m) for m in metrics if m.group == group]
        if cards:
            parts.append(f'<h2>{title}</h2><div class="grid">{"".join(cards)}</div>')
    if days:
        links = "".join(
            f'<a href="reports/{d}.html"><span>{d}</span><span class="meta">Daily report</span></a>' for d in days
        )
        parts.append(f'<h2>Daily activity</h2><div class="list">{links}</div>')
    parts.append(f"<footer>{DISCLAIMER}</footer>")
    (root / "index.html").write_text(_page("India & Forex Daily", "\n".join(parts)), encoding="utf-8")

    (site / ".nojekyll").write_text("", encoding="utf-8")
    if not (site / "index.html").exists():
        (site / "index.html").write_text(
            '<!DOCTYPE html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=india/">'
            '<title>India &amp; Forex Daily</title><a href="india/">India &amp; Forex Daily</a>\n',
            encoding="utf-8",
        )


# --------------------------------------------------------------------------- main


def run(site: Path, *, fetcher: Fetcher = yfinance_fetcher, use_ai: bool = True, force: bool = False,
        env: Optional[Dict[str, str]] = None, ai: Callable[[List[Metrics], str], Optional[str]] = ai_brief) -> int:
    env = os.environ if env is None else env
    instruments = load_instruments(env)
    metrics = collect(instruments, fetcher)
    date = _data_date(metrics)
    if date is None:
        logger.error("no market data fetched for any instrument; site left unchanged")
        return 1

    existing = site / "india" / "reports" / f"{date}.md"
    if existing.exists() and not force:
        logger.info("report for %s already exists (market closed or rerun); use --force to rebuild", date)
        return 0

    language = (env.get("INDIA_FX_REPORT_LANGUAGE") or "en").strip() or "en"
    brief, source = None, "rule-based"
    if use_ai and _env_bool(env.get("INDIA_FX_AI_ENABLED"), True):
        brief = ai(metrics, language)
        if brief:
            source = "AI"
    if not brief:
        brief = rule_based_brief(metrics)

    try:
        keep_days = int(env.get("INDIA_FX_KEEP_DAYS") or 90)
    except ValueError:
        keep_days = 90
    write_site(site, date, metrics, brief, source, keep_days)
    ok = sum(1 for m in metrics if m.ok)
    logger.info("wrote India & Forex report for %s (%d/%d instruments, brief: %s)", date, ok, len(metrics), source)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="India + Forex daily tracker (static mobile site)")
    parser.add_argument("--site", default="site", help="site output directory; pages go to <site>/india/")
    parser.add_argument("--force", action="store_true", help="rebuild even if today's report already exists")
    parser.add_argument("--no-ai", action="store_true", help="skip the LLM brief and use the rule-based summary")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    return run(Path(args.site), use_ai=not args.no_ai, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
