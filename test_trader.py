"""trader.py 낮 실행 흐름 점검 (가짜 증권사, 실제 주문 없음).  python test_trader.py"""
import json
import tempfile
from datetime import datetime
from pathlib import Path

import trader


class FakeKIS:
    max_order_usd = 500
    mode = "paper"

    def __init__(self, avg=100):
        self.prices, self.held, self.orders, self.avg = {}, {}, [], avg

    def price(self, sym, excg):
        return self.prices[sym]

    def balance(self, excg):
        rows = [{"ovrs_pdno": s, "ovrs_cblc_qty": str(q), "pchs_avg_pric": str(self.avg), "ovrs_excg_cd": "NASD"}
                for s, q in self.held.items()] if excg == "NASD" else []
        return rows, {}

    def resv_order(self, side, sym, qty, price, excg):
        self.orders.append((side, sym, qty, price))
        return f"R{len(self.orders)}"


def bars(closes, start=1):
    return [{"date": f"2026-11-{start + i:02d}", "open": c, "high": c, "low": c, "close": c, "volume": 1}
            for i, c in enumerate(closes)]


tmp = Path(tempfile.mkdtemp())
trader.STATE, trader.SIGNALS, trader.HISTORY = tmp / "state.json", tmp / "signals.json", tmp / "history.json"
trader.LOG, trader.TRADES, trader.DAILY = tmp / "trader.log", tmp / "trades.csv", tmp / "daily.csv"
trader.REGIME = tmp / "regime.json"  # 없음 → 판정 없이 진행
at = lambda d, h=17, m=50: datetime(2026, 11, d, h, m, tzinfo=trader.KST)  # noqa: E731

# 1일차 17:50: $200 / 2자리 = 종목당 $100. BIG(1주 $927)은 못 사서 건너뛰고 더 싼 AAA를
# 종가 +3% 지정가로 예약매수: 100 // (30.9 x 1.0025) = 3주
trader.SIGNALS.write_text(json.dumps({"asof": "2026-11-09", "candidates": [
    {"symbol": "BIG", "excg": "NASD", "rsi2": 0.5, "close": 900},
    {"symbol": "AAA", "excg": "NASD", "rsi2": 1.0, "close": 30}]}))
trader.HISTORY.write_text(json.dumps({"AAA": bars([30] * 9)}))
k = FakeKIS(avg=30)
trader.daily(k, at(10))
assert k.orders == [("buy", "AAA", 3, 30.9)], k.orders
trader.daily(k, at(10, 18))  # 같은 날 두 번 주문 안 함
assert len(k.orders) == 1
trader.daily(k, at(10, 9, 30))  # 다음 날이어도 접수시간 전이면 아무것도 안 함
assert len(k.orders) == 1

# 2일차: 체결(잔고에 있음) → 포지션. 종가 31 > 5일선 → 익절 MOO 예약매도
k.held = {"AAA": 3}
trader.HISTORY.write_text(json.dumps({"AAA": bars([30] * 9 + [31])}))
trader.SIGNALS.write_text(json.dumps({"asof": "2026-11-10", "candidates": []}))
trader.daily(k, at(11))
st = json.loads(trader.STATE.read_text(encoding="utf-8"))
assert st["positions"]["AAA"]["entry_date"] == "2026-11-10" and not st["pending"]
assert k.orders[-1] == ("sell", "AAA", 3, None) and st["positions"]["AAA"]["reason"] == "target"

# 3일차: 매도 체결(잔고에서 사라짐) → 거래 기록, 체결가 = 그날 시가 32
k.held = {}
trader.HISTORY.write_text(json.dumps({"AAA": bars([30] * 9 + [31, 32])}))
trader.daily(k, at(12))
row = trader.TRADES.read_text(encoding="utf-8-sig").splitlines()[-1]
assert row.startswith("AAA,2026-11-10,2026-11-11,3,30.0,32") and row.endswith("6.00"), row

# 손절: 진입 100, 종가 91 ≤ 92 → MOO 매도. 5일 보유 → time
for closes, reason in (([100] * 9 + [95, 91]), "stop"), (([100] * 9 + [99] * 5), "time"):
    trader.STATE.write_text(json.dumps({"positions": {"ZZZ": {"entry_date": "2026-11-10", "entry": 100,
                                                              "excg": "NASD"}}, "pending": {}}))
    trader.HISTORY.write_text(json.dumps({"ZZZ": bars(closes)}))
    k = FakeKIS()
    k.held = {"ZZZ": 2}
    trader.daily(k, at(20))
    assert json.loads(trader.STATE.read_text(encoding="utf-8"))["positions"]["ZZZ"]["reason"] == reason

# 주문했는데 그날 장이 끝난 뒤에도 잔고에 없음 → 미체결로 정리
trader.STATE.write_text(json.dumps({"positions": {}, "pending": {"QQQ": {"date": "2026-11-10", "qty": 1,
                                                                        "excg": "NASD"}}}))
trader.HISTORY.write_text(json.dumps({"QQQ": bars([100] * 10)}))
trader.daily(FakeKIS(), at(20))
assert not json.loads(trader.STATE.read_text(encoding="utf-8"))["pending"]

# 시장 판정: 뉴스 매수중단이면 매수 안 함, 주의면 최대 2종목
trader.SIGNALS.write_text(json.dumps({"asof": "2026-11-19", "candidates": [
    {"symbol": s, "excg": "NASD", "rsi2": 1.0, "close": 10} for s in ("E1", "E2", "E3")]}))
for lv, news, expect in (("normal", "stop", 0), ("caution", None, 2)):
    trader.REGIME.write_text(json.dumps({"us": {"date": "2026-11-19", "level": lv, "news_level": news or "normal"}}))
    trader.STATE.write_text(json.dumps({"positions": {}, "pending": {}}))
    k = FakeKIS()
    trader.daily(k, at(20))
    assert len(k.orders) == expect, (lv, k.orders)

# 모의 수익률: 기준 $200 + 실현 AAA +6.00 + 미실현 ZZZ +20(2주, 100→110) - 수수료 0.0025 x (30x3x2 + 200) = 225.05
trader.STATE.write_text(json.dumps({"positions": {"ZZZ": {"entry_date": "2026-11-10", "entry": 100,
                                                          "excg": "NASD"}}, "pending": {}}))
k = FakeKIS()
k.held, k.prices = {"ZZZ": 2}, {"ZZZ": 110}
trader.snapshot(k)
row = trader.DAILY.read_text(encoding="utf-8-sig").splitlines()[-1]
assert "225.05" in row, row
trader.snapshot(k)  # 같은 날 다시 기록하면 덮어씀
assert len(trader.DAILY.read_text(encoding="utf-8-sig").splitlines()) == 2

print("trader flow OK")
