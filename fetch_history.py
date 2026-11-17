"""백테스트용 장기 일봉 수집: S&P 500(대형) + S&P 400(중형) x 약 2년 → ~/kis-bot-data/history.json
  python fetch_history.py   (중간에 멈춰도 다시 실행하면 이어서 받음)
"""
import json
import re
import urllib.request
from datetime import date, timedelta

from kis import DATA_DIR, KIS, KISError

OUT = DATA_DIR / "history.json"
PAGES = 5  # 한 번에 100거래일 → 약 2년
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
SP400_WIKI = ("https://en.wikipedia.org/w/api.php?action=parse&page=List_of_S%26P_400_companies"
              "&prop=wikitext&format=json&formatversion=2")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "kis-bot-research/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode()


def history(k, symbol, excgs=("NASD", "NYSE")):
    for excg in excgs:
        bars, end = [], ""
        for _ in range(PAGES):
            new = [b for b in k.daily(symbol, excg, end) if not bars or b["date"] < bars[-1]["date"]]
            if not new:
                break
            bars += new
            end = (date.fromisoformat(bars[-1]["date"]) - timedelta(days=1)).strftime("%Y%m%d")
        if bars:
            return bars
    return []


# shortcut: 현재 구성 종목만 사용(생존 편향), 정확한 검증이 필요하면 과거 구성 종목 데이터로 교체
symbols = {line.split(",")[0]: ("NASD", "NYSE") for line in fetch(SP500_CSV).splitlines()[1:] if line}
wiki = json.loads(fetch(SP400_WIKI))["parse"]["wikitext"]
for ex, sym in re.findall(r"\{\{(Nyse|Nasdaq)Symbol\|([A-Z.\-]+)", wiki):
    symbols.setdefault(sym, ("NYSE",) if ex == "Nyse" else ("NASD",))

k = KIS()
DATA_DIR.mkdir(exist_ok=True)
data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
for i, (symbol, excgs) in enumerate(symbols.items(), 1):
    if symbol in data:
        continue
    try:
        data[symbol] = history(k, symbol, excgs)
    except KISError as e:
        print(f"{symbol}: {e}")
        continue
    print(f"[{i}/{len(symbols)}] {symbol}: {len(data[symbol])}일", flush=True)
    if i % 25 == 0:
        OUT.write_text(json.dumps(data), encoding="utf-8")
OUT.write_text(json.dumps(data), encoding="utf-8")
print(f"\n완료: {sum(1 for v in data.values() if v)}개 종목 저장 → {OUT}")
