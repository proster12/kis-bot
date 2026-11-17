"""kr_trader.py 흐름 점검 (가짜 증권사).  python test_kr_trader.py"""
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import kr_trader as t


class FakeKIS:
    mode = "paper"

    def __init__(self, price):
        self.p, self.qty, self.orders = price, 0, []

    def kr_price(self, code):
        return self.p

    def kr_holdings(self):
        return {t.CODE: {"qty": self.qty, "avg": self.p}} if self.qty else {}

    def kr_order(self, side, code, qty, est):
        self.orders.append((side, qty))
        self.qty += qty if side == "buy" else -qty
        return "O1"


tmp = Path(tempfile.mkdtemp())
t.INDEX, t.STATE, t.DAILY, t.TRADES, t.LOG = (tmp / n for n in ("i.json", "s.json", "d.csv", "t.csv", "l.log"))
start = datetime(2026, 6, 1)


def write_index(closes):
    t.INDEX.write_text(json.dumps([{"date": (start + timedelta(days=i)).date().isoformat(), "close": c}
                                   for i, c in enumerate(closes)]))


at = lambda d: datetime(2026, 11, d, 9, 15, tzinfo=t.KST)  # noqa: E731
# 상승 추세(종가 > 100일선) → 30만 원 // (80,000 x 1.00015) = 3주 매수
write_index([70_000 + 100 * i for i in range(160)])  # 마지막 날 2026-11-07 이전
k = FakeKIS(80_000)
t.run(k, at(9))
assert k.orders == [("buy", 3)] and json.loads(t.STATE.read_text())["cash"] == round(300_000 - 3 * 80_000 * 1.00015)
t.run(k, at(9))  # 같은 날 두 번 안 함
assert len(k.orders) == 1
# 하락(종가 < 100일선) → 전량 매도, 거래 기록
write_index([70_000 + 100 * i for i in range(160)] + [60_000])
k.p = 60_000
t.run(k, at(10))
assert k.orders[-1] == ("sell", 3) and json.loads(t.STATE.read_text())["qty"] == 0
assert t.TRADES.read_text(encoding="utf-8-sig").count("\n") == 2
# 장 시간 밖이면 아무것도 안 함
t.run(k, datetime(2026, 11, 11, 16, 0, tzinfo=t.KST))
assert len(k.orders) == 2
print("kr trader flow OK")
