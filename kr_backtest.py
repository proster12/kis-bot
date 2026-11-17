"""국내 전략 후보 백테스트 (30만 원 소액 기준).
A. 지수 추세: KODEX 200을 종가가 N일선 위면 보유, 아래면 현금 (약 20년, kr_index_long.json).
   ETF는 매도 시 증권거래세 면제 → 비용은 수수료만(왕복 약 0.03%).
B. 종목 돌파: 코스피200+코스닥150에서 종가가 직전 20일 최고가를 넘고 200일선 위면 다음 날 시가 매수,
   종가가 직전 10일 최저가를 깨면 다음 날 시가 매도 (약 2년, kr_history.json). 비용 왕복 0.23%.
  python kr_backtest.py
"""
import json
import statistics
from types import SimpleNamespace

from kis import DATA_DIR
from pullback_backtest import portfolio, sma

ETF_COST = 0.0003
STOCK_COST = 0.0023
CAPITAL = 300_000


def load(name):
    return sorted(json.loads((DATA_DIR / name).read_text(encoding="utf-8")), key=lambda b: b["date"])


def index_trend(bars, n):
    """N일선 위 보유 전략: (연환산 수익률, 최대 낙폭, 매매 횟수, 보유 비율)."""
    closes = [b["close"] for b in bars]
    equity, peak, mdd, trades, held_days, holding = 1.0, 1.0, 0.0, 0, 0, False
    for i in range(n, len(bars) - 1):
        if holding:
            equity *= bars[i + 1]["open"] / bars[i]["open"]  # 보유 중 시가→시가 수익
            held_days += 1
        want = closes[i] > sma(closes, i, n)
        if want != holding:
            equity *= 1 - ETF_COST / 2
            trades += 1
            holding = want
        peak, mdd = max(peak, equity), max(mdd, 1 - equity / max(peak, equity))
    years = (len(bars) - n) / 250
    return equity ** (1 / years) - 1, mdd, trades, held_days / (len(bars) - n)


def buy_hold(bars, start):
    eq = [b["close"] / bars[start]["close"] for b in bars[start:]]
    peak, mdd = 0, 0
    for e in eq:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    return eq[-1] ** (250 / len(eq)) - 1, mdd


def breakout_trades(history, entry_n=20, exit_n=10):
    trades = []
    for code, raw in history.items():
        bars = [SimpleNamespace(**b) for b in sorted(raw, key=lambda b: b["date"])]
        closes = [b.close for b in bars]
        i = 200
        while i < len(bars) - 2:
            b = bars[i]
            if not (b.close > max(x.high for x in bars[i - entry_n:i]) and b.close > sma(closes, i, 200)
                    and sum(x.close * x.volume for x in bars[i - 19:i + 1]) / 20 >= 3e9):
                i += 1
                continue
            entry, j = bars[i + 1].open, i + 1
            while j < len(bars) - 1 and bars[j].close >= min(x.low for x in bars[j - exit_n:j]):
                j += 1
            if j >= len(bars) - 1:
                break  # 아직 보유 중인 거래는 제외
            exit_ = bars[j + 1].open
            trades.append({"symbol": code, "entry_date": bars[i + 1].date, "exit_date": bars[j + 1].date,
                           "entry": entry, "exit": exit_, "ret": exit_ / entry - 1 - STOCK_COST,
                           "rsi2": -(b.close / max(x.high for x in bars[i - entry_n:i]))})  # 정렬키: 돌파 강도
            i = j + 1
    return trades


def selftest():
    up = [{"date": f"d{i:04d}", "open": 100 + i, "close": 100 + i} for i in range(300)]
    cagr, mdd, trades, held = index_trend(up, 50)
    assert trades == 1 and mdd < 0.001 and held > 0.9 and cagr > 0  # 낙폭은 진입 수수료만큼
    print("selftest OK")


if __name__ == "__main__":
    selftest()
    idx = load("kr_index_long.json")
    print(f"A. 지수 추세 (KODEX 200, {idx[200]['date']} ~ {idx[-1]['date']}, 비용 왕복 {ETF_COST:.2%})")
    bh = buy_hold(idx, 200)
    print(f"  그냥 보유          연 {bh[0]:+.1%}  최대 낙폭 -{bh[1]:.0%}")
    for n in (20, 50, 100, 200):
        cagr, mdd, trades, held = index_trend(idx[200 - n:], n)
        print(f"  {n:3}일선 위 보유     연 {cagr:+.1%}  최대 낙폭 -{mdd:.0%}  매매 {trades}회  보유 비율 {held:.0%}")
    for since in ("2016-01-01", "2024-10-01"):
        sub = [b for b in idx if b["date"] >= since]
        pre = [b for b in idx if b["date"] < since][-50:]
        cagr, mdd, trades, _ = index_trend(pre + sub, 50)
        bh = buy_hold(pre + sub, 50)
        print(f"  ({since}~) 50일선 연 {cagr:+.1%} 낙폭 -{mdd:.0%} 매매 {trades}회 / 그냥 보유 연 {bh[0]:+.1%} 낙폭 -{bh[1]:.0%}")

    trades = breakout_trades(json.loads((DATA_DIR / "kr_history.json").read_text(encoding="utf-8")))
    rets = [t["ret"] for t in trades]
    print(f"\nB. 종목 돌파 (20일 신고가 매수 / 10일 신저가 매도, 비용 왕복 {STOCK_COST:.2%})")
    print(f"  거래 {len(rets)}건, 승률 {sum(r > 0 for r in rets) / len(rets):.0%}, 평균 {statistics.mean(rets):+.2%}, "
          f"중앙값 {statistics.median(rets):+.2%}, 평균 수익 {statistics.mean([r for r in rets if r > 0]):+.1%} / "
          f"평균 손실 {statistics.mean([r for r in rets if r <= 0]):+.1%}")
    for slots in (1, 2, 3):
        end, mdd, done, missed = portfolio(trades, CAPITAL, slots, STOCK_COST / 2)
        print(f"  30만 원, 동시 {slots}종목: 최종 {end:,.0f}원 ({end / CAPITAL - 1:+.1%}), 최대 낙폭 -{mdd:.0%}, 거래 {done}건")
