"""시장필터용 SPY 약 2년 일봉 → ~/kis-bot-data/spy.json
  python fetch_spy.py
"""
import json
from datetime import date, timedelta

from kis import DATA_DIR, KIS

k = KIS()
bars, end = [], ""
for _ in range(6):  # 100거래일 x 6: 백테스트 시작 전 50일선 계산분까지
    new = [b for b in k.daily("SPY", "AMEX", end) if not bars or b["date"] < bars[-1]["date"]]
    if not new:
        break
    bars += new
    end = (date.fromisoformat(bars[-1]["date"]) - timedelta(days=1)).strftime("%Y%m%d")
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "spy.json").write_text(json.dumps(bars), encoding="utf-8")
print(f"SPY {len(bars)}일 저장 ({bars[-1]['date']} ~ {bars[0]['date']})")
