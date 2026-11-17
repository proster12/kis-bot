"""모멘텀 버스트 신호 백테스트 (~/kis-bot-data/history.json 사용).
기본 규칙: 신호(ACTIONABLE_DAY1) 다음 날 시가 진입, 신호일 저가 손절,
          하루 +10% 이상 오르면 그날 종가 익절, 최대 5거래일 보유 후 종가 청산.
변형(VARIANTS): 시장필터(SPY 50일선 위), 진입확인(신호일 고가 돌파 시 매수),
               본전손절(+1R 마감 후 손절가를 진입가로), 매집흔적(거래량 2배+고가 근처 마감).
  python backtest.py             결과 출력 + ~/kis-bot-data/backtest_trades_<변형>.csv
  python backtest.py --selftest  매매 시뮬레이션 규칙 자체 점검
"""
import csv
import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from kis import DATA_DIR

SCREENER = Path.home() / ".agents/skills/stockbee-momentum-burst-screener/scripts/screen_momentum_burst.py"
HOLD_DAYS = 5
PROTECT_GAIN = 0.10
FEE_ROUND_TRIP = 0.005  # 한투 기본 0.25% x 2
LOW_FEE_ROUND_TRIP = 0.002  # 0.1% x 2 (토스 기본·이벤트 요율 수준)
LOOKBACK = 80  # 스크리너 기본 history-days
MARKET_MA = 50


def accumulation(res):
    """큰손 매집 흔적: 20일 평균 대비 거래량 2배 이상 + 종가가 당일 범위 상단 30% 안."""
    return res["volume_ratio_20d"] >= 2 and res["close_location_pct"] >= 70


VARIANTS = {
    "기본": {},
    "시장필터": {"market": True},
    "진입확인": {"confirm": True},
    "본전손절": {"breakeven": True},
    "매집흔적": {"accept": accumulation},
    "결합(시장+확인+본전)": {"market": True, "confirm": True, "breakeven": True},
    "결합+매집": {"market": True, "confirm": True, "breakeven": True, "accept": accumulation},
}


def simulate(bars, stop, trigger=None, breakeven=False):
    """bars: 신호 다음 날부터 오래된→최신 순. (진입가, 청산가, 사유, 보유일), 진입 안 하면 None.
    trigger: 이 가격을 넘을 때만 매수(진입확인). breakeven: 종가가 +1R 이상이면 손절가를 진입가로."""
    first = bars[0]
    if trigger is not None:
        if first.high < trigger:
            return None  # 돌파 실패: 매수 안 함
        entry = max(first.open, trigger)
    else:
        entry = first.open
    if entry <= stop:
        return None  # 시가가 이미 손절선 아래
    risk = entry - stop
    prev_close = entry
    held = bars[:HOLD_DAYS]
    for n, b in enumerate(held, 1):
        if n > 1 and b.open <= stop:
            return entry, b.open, "gap_stop", n  # 갭 하락은 시가에 손절된다고 가정
        if b.low <= stop:
            return entry, stop, "stop", n  # 같은 날 손절·익절 둘 다 가능하면 손절로(보수적)
        if b.close >= prev_close * (1 + PROTECT_GAIN):
            return entry, b.close, "protect", n
        if breakeven and b.close >= entry + risk:
            stop = max(stop, entry)
        prev_close = b.close
    return entry, held[-1].close, "time", len(held)


def selftest():
    B = lambda o, h, l, c: SimpleNamespace(open=o, high=h, low=l, close=c)  # noqa: E731
    assert simulate([B(95, 99, 94, 98)], 96) is None
    assert simulate([B(100, 101, 95, 99)], 96) == (100, 96, "stop", 1)
    assert simulate([B(100, 112, 99, 111)], 90) == (100, 111, "protect", 1)
    assert simulate([B(100, 101, 99, 100), B(94, 95, 90, 92)], 96) == (100, 94, "gap_stop", 2)
    assert simulate([B(100, 102, 99, 101)] * 7, 90) == (100, 101, "time", 5)
    # 진입확인: 고가가 트리거 미달이면 진입 안 함, 넘으면 트리거 가격(또는 더 높은 시가)에 매수
    assert simulate([B(100, 101, 99, 100)], 96, trigger=102) is None
    assert simulate([B(100, 104, 99, 103)] * 5, 96, trigger=102)[:1] == (102,)
    # 본전손절: 1일차 종가 +1R 이상 → 이후 진입가 이탈 시 진입가에 청산
    assert simulate([B(100, 106, 99, 105), B(104, 104, 99, 100)], 96, breakeven=True) == (100, 100, "stop", 2)
    assert simulate([B(100, 106, 99, 105), B(104, 104, 99, 100)], 96)[2] == "time"
    print("selftest OK")


def load_screener():
    spec = importlib.util.spec_from_file_location("momentum_burst", SCREENER)
    mb = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mb  # dataclass가 자기 모듈을 sys.modules에서 찾음
    spec.loader.exec_module(mb)
    argv, sys.argv = sys.argv, ["screen"]
    args = mb.parse_arguments()  # 스크리너 기본 설정 그대로
    sys.argv = argv
    return mb, args


def market_ok_by_date(path=DATA_DIR / "spy.json"):
    """날짜 → 지수(기본 SPY) 종가가 50일 이동평균 위인지. 파일 없으면 None."""
    if not path.exists():
        return None
    bars = sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda b: b["date"])
    closes = [b["close"] for b in bars]
    return {b["date"]: b["close"] > sum(closes[i - MARKET_MA + 1:i + 1]) / MARKET_MA
            for i, b in enumerate(bars) if i >= MARKET_MA - 1}


def run(mb, args, history, market_ok, accept=lambda res: True, market=False, confirm=False, breakeven=False):
    trades = []
    for symbol, raw in history.items():
        chron = list(reversed(mb.normalize_bars(raw)))  # 오래된→최신
        busy_until = -1  # 같은 종목 보유 중엔 새 신호 무시
        for i in range(LOOKBACK - 1, len(chron) - HOLD_DAYS):
            if i <= busy_until or (market and not market_ok.get(chron[i].date, False)):
                continue
            res = mb.analyze_symbol(symbol, chron[i - LOOKBACK + 1:i + 1][::-1], args)
            if res["state"] != "ACTIONABLE_DAY1" or not accept(res):
                continue
            stop = res["stop_reference"]
            t = simulate(chron[i + 1:i + 1 + HOLD_DAYS], stop,
                         trigger=chron[i].high if confirm else None, breakeven=breakeven)
            if t is None:
                continue
            entry, exit_price, reason, days = t
            trades.append({"symbol": symbol, "signal_date": chron[i].date, "trigger": res["primary_trigger"],
                           "score": res["setup_score"], "vol_ratio_20d": res["volume_ratio_20d"],
                           "close_loc_pct": res["close_location_pct"], "entry": entry, "stop": stop,
                           "exit": exit_price, "reason": reason, "days": days,
                           "ret": exit_price / entry - 1 - FEE_ROUND_TRIP,
                           "r": (exit_price - entry) / (entry - stop)})
            busy_until = i + days
    return trades


def summary(trades):
    """비교표 한 줄용 핵심 수치."""
    rets = [t["ret"] for t in trades]
    wins, losses = [x for x in rets if x > 0], [x for x in rets if x <= 0]
    streak = worst = 0
    for t in sorted(trades, key=lambda t: t["signal_date"]):
        streak = streak + 1 if t["ret"] <= 0 else 0
        worst = max(worst, streak)
    return {"n": len(rets), "win": len(wins) / len(rets), "avg": statistics.mean(rets),
            "avg_low_fee": statistics.mean(rets) + FEE_ROUND_TRIP - LOW_FEE_ROUND_TRIP,
            "pf": sum(wins) / -sum(losses) if losses else float("inf"),
            "r": statistics.mean(t["r"] for t in trades), "streak": worst,
            "per_day": len(rets) / len({t["signal_date"] for t in trades})}


def report(trades):
    s = summary(trades)
    dates = sorted({t["signal_date"] for t in trades})
    print(f"기간 {dates[0]} ~ {dates[-1]}, 거래 {s['n']}건 (신호일 평균 {s['per_day']:.1f}건)")
    print(f"승률 {s['win']:.1%}, 평균 {s['avg']:+.2%} (저수수료 {s['avg_low_fee']:+.2%}), "
          f"PF {s['pf']:.2f}, 평균 R {s['r']:+.2f}, 최대 연속 손실 {s['streak']}회")
    print("청산 사유: " + ", ".join(f"{k} {v}" for k, v in Counter(t["reason"] for t in trades).most_common()))
    for trig in sorted({t["trigger"] for t in trades}):
        sub = [t["ret"] for t in trades if t["trigger"] == trig]
        print(f"  {trig:16} {len(sub):5}건  승률 {sum(x > 0 for x in sub) / len(sub):.0%}  평균 {statistics.mean(sub):+.2%}")


if __name__ == "__main__":
    selftest()
    if "--selftest" not in sys.argv:
        mb, args = load_screener()
        history = json.loads((DATA_DIR / "history.json").read_text(encoding="utf-8"))
        market_ok = market_ok_by_date()
        print(f"종목 {len(history)}개, 시장필터 데이터 {'있음' if market_ok else '없음(spy.json 필요)'}")
        table = []
        for name, opts in VARIANTS.items():
            if opts.get("market") and not market_ok:
                continue
            trades = run(mb, args, history, market_ok, **opts)
            print(f"\n===== {name} =====")
            if not trades:
                print("거래 없음")
                continue
            report(trades)
            table.append((name, summary(trades)))
            with (DATA_DIR / f"backtest_trades_{name}.csv").open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=trades[0].keys())
                w.writeheader()
                w.writerows(trades)
        print(f"\n{'변형':22}{'거래':>7}{'승률':>8}{'평균(0.5%)':>12}{'평균(0.2%)':>12}{'PF':>6}{'R':>7}{'연속손실':>8}")
        for name, s in table:
            print(f"{name:22}{s['n']:7}{s['win']:8.1%}{s['avg']:+12.2%}{s['avg_low_fee']:+12.2%}"
                  f"{s['pf']:6.2f}{s['r']:+7.2f}{s['streak']:8}")
