"""상승추세 눌림목(평균회귀) 전략 백테스트 (~/kis-bot-data/history.json 사용).
기본 규칙: 종가가 200일선 위(상승추세) + RSI(2) < 10(짧은 급락) + 유동성(가격 $10+, 20일 평균 거래대금 $20M+)
          → 다음 날 시가 매수. 종가가 5일선 위로 올라오면 그날 종가 매도,
          진입가 -8% 손절, 최대 5거래일 보유 후 종가 청산.
  python pullback_backtest.py             변형별 결과 + 기간 앞/뒤 절반 비교
  python pullback_backtest.py --kr        국내(코스피200+코스닥150, 시장필터 KODEX 200, 국내 비용)
  python pullback_backtest.py --selftest
"""
import json
import statistics
import sys
from types import SimpleNamespace

from backtest import FEE_ROUND_TRIP, market_ok_by_date, report, summary
from kis import DATA_DIR

HOLD_DAYS = 5
TREND_MA, EXIT_MA = 200, 5
MIN_PRICE, MIN_DOLLAR_VOL = 10, 20_000_000

VARIANTS = {
    "기본(RSI2<10, 손절8%)": {"rsi_max": 10, "stop_pct": 0.08},
    "깊은눌림(RSI2<5)": {"rsi_max": 5, "stop_pct": 0.08},
    "시장필터(지수 50일선 위)": {"rsi_max": 10, "stop_pct": 0.08, "market": True},
    "깊은눌림+시장필터": {"rsi_max": 5, "stop_pct": 0.08, "market": True},
    "손절없음(참고용)": {"rsi_max": 10, "stop_pct": None},
    # regime.py 판정이 매수중단(stop)인 날의 신호는 건너뜀
    "기본+브레이크": {"rsi_max": 10, "stop_pct": 0.08, "brake": True},
    "깊은눌림+시장필터+브레이크": {"rsi_max": 5, "stop_pct": 0.08, "market": True, "brake": True},
    "손절없음+브레이크": {"rsi_max": 10, "stop_pct": None, "brake": True},
    # 낮 실행(예약주문): 장중 감시 없이 종가로 판단 → 다음 날 시가에 청산, 매수는 전일 종가 +3% 지정가
    "기본(낮 실행)": {"rsi_max": 10, "stop_pct": 0.08, "daily": True},
    "깊은눌림+시장필터(낮 실행)": {"rsi_max": 5, "stop_pct": 0.08, "market": True, "daily": True},
    # 소액($200) 운용: 1주 가격이 종목당 예산 이하인 종목만
    "위와 같음, $100 이하 종목": {"rsi_max": 5, "stop_pct": 0.08, "market": True, "daily": True, "max_price": 100},
    "위와 같음, $66 이하 종목": {"rsi_max": 5, "stop_pct": 0.08, "market": True, "daily": True, "max_price": 66},
    "위와 같음, $40 이하 종목": {"rsi_max": 5, "stop_pct": 0.08, "market": True, "daily": True, "max_price": 40},
}
BUY_LIMIT_GAP = 0.03  # 낮 실행 매수 지정가 = 신호일 종가 x (1 + 3%). 시가가 이보다 높으면 미체결
BRAKE = {}  # 날짜 → (판정, 이유). __main__에서 regime.us_levels()/kr_levels()로 채움


def rsi(closes, n=2):
    """Wilder RSI. 앞 n개는 None."""
    out, gain, loss = [None] * len(closes), 0.0, 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            gain, loss = gain + g / n, loss + l / n
        else:
            gain, loss = (gain * (n - 1) + g) / n, (loss * (n - 1) + l) / n
        if i >= n:
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def sma(values, i, n):
    return sum(values[i - n + 1:i + 1]) / n


def entry_signal(bars, closes, rs, i, rsi_max):
    """i일 종가 기준 매수 신호. 백테스트와 모의투자(trader.py)가 같은 규칙을 쓰도록 공용."""
    b = bars[i]
    return (i >= TREND_MA - 1 and rs[i] is not None and rs[i] < rsi_max
            and b.close >= MIN_PRICE and b.close > sma(closes, i, TREND_MA)
            and sum(x.close * x.volume for x in bars[i - 19:i + 1]) / 20 >= MIN_DOLLAR_VOL)


def simulate(bars, closes, i, stop_pct):
    """신호일 i 다음 날 시가 진입. (진입가, 청산가, 사유, 보유일)."""
    entry = bars[i + 1].open
    stop = entry * (1 - stop_pct) if stop_pct else None
    for n in range(1, HOLD_DAYS + 1):
        b = bars[i + n]
        if stop and n > 1 and b.open <= stop:
            return entry, b.open, "gap_stop", n
        if stop and b.low <= stop:
            return entry, stop, "stop", n
        if b.close > sma(closes, i + n, EXIT_MA):
            return entry, b.close, "target", n
    return entry, bars[i + HOLD_DAYS].close, "time", HOLD_DAYS


def daily_exit(bars, closes, i, n, entry, stop_pct):
    """보유 n일째(i+n) 종가 기준 청산 사유. trader.py 낮 실행과 공용. 없으면 None."""
    c = closes[i + n]
    if stop_pct and c <= entry * (1 - stop_pct):
        return "stop"
    if c > sma(closes, i + n, EXIT_MA):
        return "target"
    return "time" if n >= HOLD_DAYS else None


def simulate_daily(bars, closes, i, stop_pct):
    """낮 실행: 다음 날 시가 진입(전일 종가 +3% 넘게 갭 상승하면 미체결), 종가로 판단해 다음 날 시가 청산."""
    entry = bars[i + 1].open
    if entry > closes[i] * (1 + BUY_LIMIT_GAP):
        return None
    for n in range(1, HOLD_DAYS + 1):
        reason = daily_exit(bars, closes, i, n, entry, stop_pct)
        if reason:
            return entry, bars[i + n + 1].open, reason, n + 1


def portfolio(trades, capital, slots, fee_side):
    """소액 계좌 시뮬레이션: 동시 보유 slots개, 정수 주식만, 남은 현금을 빈 자리 수로 나눠 매수(복리).
    같은 날 신호가 많으면 RSI2가 낮은 순. (최종 자산, 최대 낙폭, 실행 거래 수, 자리 없어 놓친 수)"""
    cash, open_, done, missed, peak, mdd = capital, [], 0, 0, capital, 0.0
    for t in sorted(trades, key=lambda t: (t["entry_date"], t["rsi2"])):
        for o in [o for o in open_ if o["exit_date"] <= t["entry_date"]]:  # 시가 매도 후 같은 날 시가 매수 가능
            cash += o["qty"] * o["exit"] * (1 - fee_side)
            open_.remove(o)
            equity = cash + sum(x["qty"] * x["entry"] for x in open_)
            peak, mdd = max(peak, equity), max(mdd, 1 - equity / peak)
        free = slots - len(open_)
        qty = int(cash / free // (t["entry"] * (1 + fee_side))) if free else 0
        if qty < 1:
            missed += 1
            continue
        cash -= qty * t["entry"] * (1 + fee_side)
        open_.append({**t, "qty": qty})
        done += 1
    cash += sum(o["qty"] * o["exit"] * (1 - fee_side) for o in open_)
    return cash, max(mdd, 1 - cash / max(peak, cash)), done, missed


def selftest():
    # 자리 1개, $100: 10주 매수(10달러) → 12달러 매도 = $120, 같은 날 겹친 두 번째 신호는 놓침
    t = [{"entry_date": "d1", "exit_date": "d3", "rsi2": 1, "entry": 10, "exit": 12},
         {"entry_date": "d1", "exit_date": "d2", "rsi2": 2, "entry": 5, "exit": 5}]
    assert portfolio(t, 100, 1, 0) == (120, 0.0, 1, 1)
    assert rsi([10, 9, 8])[2] == 0.0 and rsi([10, 11, 12])[2] == 100.0
    D = lambda o, c: SimpleNamespace(open=o, high=max(o, c), low=min(o, c), close=c)  # noqa: E731
    bars = [D(100, 100)] * 5 + [D(100, 91), D(90, 90)] + [D(90, 90)] * 5
    closes = [b.close for b in bars]
    assert simulate_daily(bars, closes, 4, 0.08) == (100, 90, "stop", 2)  # 91 ≤ 92 → 다음 날 시가 90
    bars = [D(100, 100)] * 5 + [D(104, 104)] + [D(104, 104)] * 6
    assert simulate_daily(bars, [b.close for b in bars], 4, 0.08) is None  # +4% 갭 → 미체결
    B = lambda o, h, l, c: SimpleNamespace(open=o, high=h, low=l, close=c)  # noqa: E731
    flat = [B(100, 100, 100, 100)] * 5
    # 다음 날 시가 100 진입, 1일차 종가 105 > 5일선 → 목표 청산
    bars = flat + [B(100, 106, 99, 105)] + [B(105, 105, 105, 105)] * 5
    closes = [b.close for b in bars]
    assert simulate(bars, closes, 4, 0.08) == (100, 105, "target", 1)
    # 1일차 저가가 -8% 아래 → 손절 92
    bars = flat + [B(100, 100, 91, 95)] + [B(95, 95, 95, 95)] * 5
    assert simulate(bars, [b.close for b in bars], 4, 0.08) == (100, 92.0, "stop", 1)
    print("selftest OK")


def run(history, market_ok, rsi_max, stop_pct, market=False, brake=False, daily=False, max_price=None):
    trades = []
    for symbol, raw in history.items():
        bars = [SimpleNamespace(**b) for b in sorted(raw, key=lambda b: b["date"])]
        closes = [b.close for b in bars]
        rs = rsi(closes)
        busy_until = -1
        for i in range(TREND_MA - 1, len(bars) - HOLD_DAYS - 2):
            b = bars[i]
            if i <= busy_until or not entry_signal(bars, closes, rs, i, rsi_max):
                continue
            if max_price and b.close * (1 + BUY_LIMIT_GAP) > max_price:  # 지정가로 1주도 못 사는 종목
                continue
            if market and not market_ok.get(b.date, False):
                continue
            if brake and BRAKE.get(b.date, ("normal",))[0] == "stop":
                continue
            t = (simulate_daily if daily else simulate)(bars, closes, i, stop_pct)
            if t is None:
                continue
            entry, exit_price, reason, days = t
            risk = entry * (stop_pct or 0.08)  # 손절 없을 때도 R은 8% 기준으로 표시
            trades.append({"symbol": symbol, "signal_date": b.date, "trigger": "rsi2_pullback",
                           "entry_date": bars[i + 1].date, "exit_date": bars[min(i + days, len(bars) - 1)].date,
                           "rsi2": round(rs[i], 1), "entry": entry, "exit": exit_price, "reason": reason,
                           "days": days, "ret": exit_price / entry - 1 - FEE_ROUND_TRIP,
                           "r": (exit_price - entry) / risk})
            busy_until = i + days
    return trades


if __name__ == "__main__":
    selftest()
    hist_file, index_file = "history.json", "spy.json"
    if "--kr" in sys.argv:
        import backtest
        hist_file, index_file = "kr_history.json", "kr_index.json"
        # 국내 왕복 비용: 증권거래세 약 0.15~0.20% + 수수료 약 0.03% (세율은 시기별로 다를 수 있음)
        FEE_ROUND_TRIP = backtest.FEE_ROUND_TRIP = 0.0023
        backtest.LOW_FEE_ROUND_TRIP = 0.0018
        MIN_PRICE, MIN_DOLLAR_VOL = 1_000, 3_000_000_000  # 1천원 이상, 하루 거래대금 30억원 이상
    if "--selftest" not in sys.argv:
        import backtest
        history = json.loads((DATA_DIR / hist_file).read_text(encoding="utf-8"))
        market_ok = market_ok_by_date(DATA_DIR / index_file)
        idx = sorted(json.loads((DATA_DIR / index_file).read_text(encoding="utf-8")), key=lambda b: b["date"])
        idx_ret = {b["date"]: b["close"] / a["close"] - 1 for a, b in zip(idx, idx[1:])}
        import regime
        BRAKE = regime.kr_levels() if "--kr" in sys.argv else regime.us_levels()
        if BRAKE:
            print(f"브레이크 판정 {min(BRAKE)} ~ {max(BRAKE)}: " + ", ".join(
                f"{lv} {sum(v[0] == lv for v in BRAKE.values())}일" for lv in regime.LEVELS))
        fee_hi, fee_lo = f"평균({backtest.FEE_ROUND_TRIP:.2%})", f"평균({backtest.LOW_FEE_ROUND_TRIP:.2%})"
        rows = []
        for name, opts in VARIANTS.items():
            trades = run(history, market_ok, **opts)
            print(f"\n===== {name} =====")
            if not trades:
                print("거래 없음")
                continue
            report(trades)
            # 오늘 같은 장세 점검: 지수 급락일에 나온 신호, 지수가 50일선 아래일 때 나온 신호
            for label, cond in (("지수 하루 -2% 이하 급락일", lambda t: idx_ret.get(t["signal_date"], 0) <= -0.02),
                                ("지수 50일선 아래", lambda t: not market_ok.get(t["signal_date"], True))):
                sub = [t["ret"] for t in trades if cond(t)]
                if sub:
                    print(f"  └ {label} 신호 {len(sub)}건: 승률 {sum(x > 0 for x in sub) / len(sub):.0%}, "
                          f"평균 {statistics.mean(sub):+.2%}")
            dates = sorted(t["signal_date"] for t in trades)
            mid = dates[len(dates) // 2]
            halves = [statistics.mean(t["ret"] for t in trades if (t["signal_date"] < mid) == first)
                      for first in (True, False)]
            rows.append((name, summary(trades), halves))
        print(f"\n{'변형':24}{'거래':>6}{'승률':>8}{fee_hi:>11}{fee_lo:>11}{'PF':>6}"
              f"{'연속손실':>7}{'앞절반':>9}{'뒤절반':>9}")
        for name, s, (h1, h2) in rows:
            print(f"{name:24}{s['n']:6}{s['win']:8.1%}{s['avg']:+11.2%}{s['avg_low_fee']:+11.2%}"
                  f"{s['pf']:6.2f}{s['streak']:7}{h1:+9.2%}{h2:+9.2%}")
        print(f"\n(앞/뒤 절반은 {fee_hi} 기준 평균 손익)")
        if "--kr" not in sys.argv:
            # $200 계좌로 실제 운용했다면: 동시 보유 종목 수별 (수수료 0.25%/회, 정수 주식)
            trades = run(history, market_ok, 5, 0.08, market=True, daily=True)
            print(f"\n$200 계좌 시뮬레이션 (깊은눌림+시장필터, 낮 실행, {min(t['entry_date'] for t in trades)} ~):")
            for slots in (1, 2, 3, 5):
                for fee in (0.0025, 0.001):
                    end, mdd, done, missed = portfolio(trades, 200, slots, fee)
                    print(f"  동시 {slots}종목, 수수료 {fee:.2%}/회: 최종 ${end:,.2f} ({end / 200 - 1:+.1%}), "
                          f"최대 낙폭 -{mdd:.1%}, 거래 {done}건 (자리·잔액 부족으로 놓친 신호 {missed}건)")
