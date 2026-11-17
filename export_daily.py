"""순위 API(거래량급증·상승률 상위) + universe.txt 종목의 일봉을 한투에서 받아 스크리너용 JSON으로 저장.
  python export_daily.py   →  ~/kis-bot-data/daily_ohlcv.json
"""
import json

from kis import DATA_DIR, KIS, KISError, PRICE_EXCD, RANK, ROOT

OUT = DATA_DIR / "daily_ohlcv.json"
TOP_N = 30  # 순위 목록·거래소별로 스캔에 넣을 상위 종목 수

k = KIS()
targets = {}  # 티커 -> 거래소 (None이면 자동 탐색)

for excg in PRICE_EXCD:
    for kind in RANK:
        try:
            rows = k.ranking(kind, excg)[:TOP_N]
        except KISError as e:
            print(f"순위 {kind} {excg}: {e}")
            continue
        for row in rows:
            if row.get("symb"):
                targets.setdefault(row["symb"].upper(), excg)
print(f"순위 API에서 {len(targets)}개 종목")

for line in (ROOT / "universe.txt").read_text(encoding="utf-8").splitlines():
    parts = line.split("#")[0].split()
    if parts:
        targets[parts[0].upper()] = parts[1].upper() if len(parts) > 1 else targets.get(parts[0].upper())
print(f"관심종목 포함 총 {len(targets)}개 종목 일봉 조회\n")

prices = {}
for symbol, excg in targets.items():
    # 거래소를 모르면 NASD → NYSE → AMEX 순으로 데이터가 나오는 곳을 찾음
    for e in [excg] if excg else PRICE_EXCD:
        try:
            bars = k.daily(symbol, e)
        except KISError as err:
            print(f"{symbol} {e}: {err}")
            continue
        if bars:
            prices[symbol] = bars
            break
    else:
        print(f"{symbol}: 데이터 없음 (티커/거래소 확인)")

DATA_DIR.mkdir(exist_ok=True)
OUT.write_text(json.dumps(prices), encoding="utf-8")
print(f"\n{len(prices)}개 종목 저장: {OUT}")
