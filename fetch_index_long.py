"""재진입 신호 검증용 장기 지수 데이터(최대 약 20년): SPY, VIX, KODEX 200 → ~/kis-bot-data/*_long.json
  python fetch_index_long.py
"""
import json
from datetime import date, timedelta

from fetch_history_kr import daily as kr_daily
from kis import DATA_DIR, KIS
from regime import fetch_vix

PAGES = 52  # 100거래일 x 52 ≈ 20년


def paged(fetch):
    bars, end = [], date.today().isoformat()
    for _ in range(PAGES):
        new = [b for b in fetch(end) if not bars or b["date"] < bars[-1]["date"]]
        if not new:
            break
        bars += new
        end = (date.fromisoformat(bars[-1]["date"]) - timedelta(days=1)).isoformat()
    return sorted(bars, key=lambda b: b["date"])


k = KIS()
for name, fetch in (("spy_long.json", lambda end: k.daily("SPY", "AMEX", end.replace("-", ""))),
                    ("kr_index_long.json", lambda end: kr_daily(k, "069500", end))):
    bars = paged(fetch)
    (DATA_DIR / name).write_text(json.dumps(bars), encoding="utf-8")
    print(f"{name}: {len(bars)}일 ({bars[0]['date']} ~ {bars[-1]['date']})")
vix = sorted(fetch_vix(k, pages=PAGES), key=lambda b: b["date"])
(DATA_DIR / "vix_long.json").write_text(json.dumps(vix), encoding="utf-8")
print(f"vix_long.json: {len(vix)}일 ({vix[0]['date']} ~ {vix[-1]['date']})")
