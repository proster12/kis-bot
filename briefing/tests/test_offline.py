"""네트워크 없이 전체 흐름을 검증하는 테스트 (가짜 시세·뉴스·공시)."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import insider, signals  # noqa: E402

FORM4 = """<?xml version="1.0"?><ownershipDocument>
<reportingOwner><reportingOwnerId><rptOwnerName>Doe John</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionDate><value>2026-11-10</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares>
<transactionPricePerShare><value>50.5</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionDate><value>2026-11-11</value></transactionDate>
<transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>200</value></transactionShares>
<transactionPricePerShare><value>52</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def make_df(kind, n=120, end="2026-11-18"):
    idx = pd.bdate_range(end=end, periods=n)
    x = np.arange(n)
    if kind == "down":    # 계속 하락 → RSI 과매도
        close = 200 - x * 0.8 + np.sin(x) * 0.5
    elif kind == "up":    # 계속 상승 → RSI 과매수
        close = 100 + x * 0.8 + np.sin(x) * 0.5
    else:                 # 횡보
        close = 150 + np.sin(x / 3) * 3
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": 1e6}, index=idx)


def test_rsi_bounds():
    r = signals.rsi(make_df("flat")["Close"])
    assert r.dropna().between(0, 100).all()


def test_form4_parse():
    rows = insider.parse_form4(FORM4)
    assert rows[0]["code"] == "P" and rows[0]["role"] == "CEO" and rows[0]["value"] == 50500
    assert rows[1]["code"] == "S"


def test_drop_incomplete_bar():
    import datetime as dt

    from bot.prices import ET, drop_incomplete

    df = make_df("flat", end="2026-11-18")
    during = dt.datetime(2026, 11, 18, 12, 19, tzinfo=ET)   # 장중
    after = dt.datetime(2026, 11, 18, 17, 30, tzinfo=ET)    # 장 마감 후
    next_day = dt.datetime(2026, 11, 19, 9, 0, tzinfo=ET)   # 다음 날 개장 전
    assert drop_incomplete(df, during).index[-1].date() == dt.date(2026, 11, 17)
    assert drop_incomplete(df, after).index[-1].date() == dt.date(2026, 11, 18)
    assert drop_incomplete(df, next_day).index[-1].date() == dt.date(2026, 11, 18)


def test_watchlist_file(tmp_path):
    import main

    f = tmp_path / "u.txt"
    f.write_text("# 주석\nAAPL NASD\n\nspy amex  # ETF\n", encoding="utf-8")
    assert main.load_watchlist({"watchlist_file": str(f), "watchlist": ["X"]}) == ["AAPL", "SPY"]
    assert main.load_watchlist({"watchlist_file": str(tmp_path / "none.txt"), "watchlist": ["X"]}) == ["X"]


def test_full_run():
    import main
    from bot import paper

    tmp = tempfile.mkdtemp()
    shutil.copytree(ROOT / "config", Path(tmp) / "config")
    os.chdir(tmp)
    frames = {"AAPL": make_df("down"), "MSFT": make_df("up"), "NVDA": make_df("flat")}
    fake_prices = lambda ts: {t: frames[t] for t in ts if t in frames}  # noqa: E731
    fake_news = lambda t, n: [{"title": f"{t} news", "link": "https://example.com", "source": "x"}]  # noqa: E731
    fake_ins = lambda t: {"ticker": t, "buys": insider.parse_form4(FORM4)[:1] if t == "NVDA" else [],  # noqa: E731
                          "sell_value": 0.0, "sell_count": 0, "error": None}

    report = main.run(fake_prices, fake_news, fake_ins, send=False)
    assert "AAPL" in report and "매수후보" in report and "Doe John" in report
    assert "GOOGL" in report  # 시세 못 받은 종목 표시

    st = paper.load({"starting_cash_usd": 10000})
    assert "AAPL" in st["positions"]                 # 과매도 종목 가상 매수
    assert "MSFT" not in st["positions"]             # 과매수 종목은 안 삼
    assert Path("data/trades.csv").exists() and list(Path("reports").glob("*.md"))

    # 같은 날 재실행 → 중복 거래 없음
    main.run(fake_prices, fake_news, fake_ins, send=False)
    assert len(Path("data/trades.csv").read_text().strip().splitlines()) == 2  # 헤더 + 1건

    # 다음 날 AAPL 10% 더 급락 → 손절
    nxt = make_df("down", end="2026-11-19")
    nxt.iloc[-1, nxt.columns.get_loc("Close")] = frames["AAPL"]["Close"].iloc[-1] * 0.90
    frames["AAPL"] = nxt
    for t in ("MSFT", "NVDA"):
        frames[t] = make_df({"MSFT": "up", "NVDA": "flat"}[t], end="2026-11-19")
    main.run(fake_prices, fake_news, fake_ins, send=False)
    st = paper.load({"starting_cash_usd": 10000})
    assert "AAPL" not in st["positions"]
    assert "손절" in Path("data/trades.csv").read_text()
    eq = paper.equity(st, {})
    assert eq["total"] < 10000  # 손실 + 수수료 반영
