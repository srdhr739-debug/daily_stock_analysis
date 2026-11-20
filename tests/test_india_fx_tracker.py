# -*- coding: utf-8 -*-
"""Offline tests for src.india_fx_tracker (no network, no LLM)."""

from datetime import date, timedelta

from src import india_fx_tracker as t


def _series(values, start=date(2026, 6, 1)):
    return [((start + timedelta(days=i)).isoformat(), float(v)) for i, v in enumerate(values)]


def _fetcher(data):
    return lambda symbol: data.get(symbol, [])


ENV = {
    "INDIA_FX_INDICES": "^NSEI:NIFTY 50",
    "INDIA_FX_STOCKS": "RELIANCE.NS,TCS.NS",
    "INDIA_FX_PAIRS": "USDINR=X",
    "INDIA_FX_KEEP_DAYS": "0",
}


def test_load_instruments_defaults_and_names():
    items = t.load_instruments({})
    groups = {i.group for i in items}
    assert groups == {"index", "stock", "forex"}
    by_symbol = {i.symbol: i for i in items}
    assert by_symbol["^NSEI"].name == "NIFTY 50"
    assert by_symbol["RELIANCE.NS"].name == "RELIANCE"
    assert by_symbol["USDINR=X"].name == "USD/INR"


def test_compute_metrics_uptrend_and_changes():
    values = [100 + i for i in range(60)]  # steady rise
    m = t.compute_metrics(t.Instrument("X.NS", "X", "stock"), _series(values))
    assert m.ok and m.close == 159
    assert round(m.change_pct, 4) == round((159 / 158 - 1) * 100, 4)
    assert m.trend == "Uptrend"
    assert m.rsi14 == 100.0
    assert "RSI overbought (≥70)" in m.notes
    assert "6-month high" in m.notes
    assert len(m.spark) == 30


def test_compute_metrics_handles_missing_data():
    m = t.compute_metrics(t.Instrument("BAD", "Bad", "stock"), [])
    assert not m.ok and m.notes == ["Data unavailable"]


def test_rsi_mixed_series_in_range():
    values = [100, 101, 100, 102, 101, 103, 102, 104, 103, 105, 104, 106, 105, 107, 106, 108, 107]
    rsi = t._rsi(values)
    assert rsi is not None and 50 < rsi < 100


def test_run_writes_dashboard_reports_and_history(tmp_path):
    data = {
        "^NSEI": _series([20000 + 10 * i for i in range(60)]),
        "RELIANCE.NS": _series([2500 - i for i in range(60)]),
        "TCS.NS": _series([3500 + (i % 3) for i in range(60)]),
        "USDINR=X": _series([83 + 0.01 * i for i in range(60)]),
    }
    assert t.run(tmp_path, fetcher=_fetcher(data), env=ENV, ai=lambda m, lang: None) == 0
    day = data["^NSEI"][-1][0]
    root = tmp_path / "india"
    assert (root / "reports" / f"{day}.md").exists()
    assert (root / "reports" / f"{day}.html").exists()
    assert (root / "data" / "latest.json").exists()
    index = (root / "index.html").read_text(encoding="utf-8")
    assert "NIFTY 50" in index and "USD/INR" in index and "rule-based" in index
    assert f"reports/{day}.html" in index
    assert (tmp_path / "index.html").exists()  # root redirect created when missing

    # same market date again -> skipped unless forced
    calls = []
    assert t.run(tmp_path, fetcher=_fetcher(data), env=ENV, ai=lambda m, lang: calls.append(1)) == 0
    assert calls == []

    # next day is appended to history
    def next_day(rows):
        last_day, last_close = rows[-1]
        return rows + [((date.fromisoformat(last_day) + timedelta(days=1)).isoformat(), last_close * 1.01)]

    data2 = {k: next_day(v) for k, v in data.items()}
    assert t.run(tmp_path, fetcher=_fetcher(data2), env=ENV, ai=lambda m, lang: "- AI says **hi**") == 0
    index = (root / "index.html").read_text(encoding="utf-8")
    assert index.count('<a href="reports/') == 2
    assert "AI says <strong>hi</strong>" in index


def test_ai_output_is_html_escaped(tmp_path):
    data = {"^NSEI": _series([1, 2, 3])}
    env = dict(ENV, INDIA_FX_STOCKS="", INDIA_FX_PAIRS="")
    env["INDIA_FX_STOCKS"] = "NONE.NS"
    t.run(tmp_path, fetcher=_fetcher(data), env=env, ai=lambda m, lang: "<script>alert(1)</script>")
    index = (tmp_path / "india" / "index.html").read_text(encoding="utf-8")
    assert "<script>alert" not in index
    assert "Data unavailable" in index


def test_run_fails_without_any_data(tmp_path):
    assert t.run(tmp_path, fetcher=_fetcher({}), env=ENV, ai=lambda m, lang: None) == 1
    assert not (tmp_path / "india").exists()


def test_ai_prompt_language_and_no_sparkline():
    m = t.compute_metrics(t.Instrument("^NSEI", "NIFTY 50", "index"), _series(range(1, 40)))
    prompt = t.build_ai_prompt([m], "ta")
    assert "Write in Tamil" in prompt
    assert "spark" not in prompt


def test_rule_based_brief_mentions_rupee_and_movers():
    data = {
        "^NSEI": _series([100, 101]),
        "A.NS": _series([100, 110]),
        "B.NS": _series([100, 90]),
        "USDINR=X": _series([83, 83.5]),
    }
    insts = [
        t.Instrument("^NSEI", "NIFTY 50", "index"),
        t.Instrument("A.NS", "A", "stock"),
        t.Instrument("B.NS", "B", "stock"),
        t.Instrument("USDINR=X", "USD/INR", "forex"),
    ]
    brief = t.rule_based_brief(t.collect(insts, _fetcher(data)))
    assert "Market mood: positive" in brief
    assert "Top gainer:** A" in brief and "top loser:** B" in brief
    assert "INR weaker vs USD" in brief
