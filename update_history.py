"""매일 아침: history.json 종목과 SPY에 최근 일봉을 덧붙임 (신호 계산·백테스트용).
신호(200일선 위에서 이틀 급락)가 나오려면 전날 이미 200일선 위여야 하므로, 200일선 아래 종목은 건너뜀.
보유·주문 종목과 SPY는 항상, 30일 넘게 갱신 안 된 종목은 다시 받음(일봉 API가 최근 100일을 주므로 빈 날이 메워짐).
  python update_history.py         필요한 종목만
  python update_history.py --all   전체
"""
import json
import sys
from datetime import date

from kis import DATA_DIR, KIS, KISError, PRICE_EXCD

HIST, SPY, EXC = DATA_DIR / "history.json", DATA_DIR / "spy.json", DATA_DIR / "exchanges.json"
KEEP = 500  # 종목당 보관할 최근 거래일 수
MA_TOLERANCE = 0.99  # 200일선이 하루 사이 움직이는 몫만큼 여유
REFRESH_DAYS = 30


def needed(symbol, bars, must):
    """bars: 최신순. 오늘 갱신해야 하는 종목인지."""
    if "--all" in sys.argv or symbol in must or len(bars) < 200:
        return True
    if (date.today() - date.fromisoformat(bars[0]["date"])).days >= REFRESH_DAYS:
        return True
    closes = [b["close"] for b in bars[:200]]
    return closes[0] >= MA_TOLERANCE * sum(closes) / 200


k = KIS()
series = json.loads(HIST.read_text(encoding="utf-8"))
series["SPY"] = json.loads(SPY.read_text(encoding="utf-8")) if SPY.exists() else []
exc = json.loads(EXC.read_text(encoding="utf-8")) if EXC.exists() else {"SPY": "AMEX"}


def save():
    SPY.write_text(json.dumps(series["SPY"]), encoding="utf-8")
    HIST.write_text(json.dumps({s: v for s, v in series.items() if s != "SPY"}), encoding="utf-8")
    EXC.write_text(json.dumps(exc), encoding="utf-8")


state = json.loads((DATA_DIR / "paper_state.json").read_text(encoding="utf-8")) \
    if (DATA_DIR / "paper_state.json").exists() else {}
must = {"SPY"} | set(state.get("positions", {})) | set(state.get("pending", {}))
todo = [s for s, bars in series.items() if needed(s, bars, must)]
print(f"갱신 대상 {len(todo)}/{len(series)}개 (200일선 아래 종목 제외)", flush=True)

failed = 0
for n, symbol in enumerate(todo, 1):
    old = series[symbol]
    if n % 150 == 0:  # 중간에 끊겨도 받은 만큼은 남도록
        save()
        print(f"{n}/{len(todo)} 저장", flush=True)
    for e in [exc[symbol]] if symbol in exc else PRICE_EXCD:
        try:
            new = k.daily(symbol, e)
        except KISError as err:
            print(f"{symbol} {e}: {err}")
            continue
        if new:
            exc[symbol] = e
            break
    else:
        failed += 1
        continue
    merged = {b["date"]: b for b in old}
    merged.update({b["date"]: b for b in new})  # 새 값 우선: 미완성 봉·수정주가 덮어씀
    series[symbol] = sorted(merged.values(), key=lambda b: b["date"], reverse=True)[:KEEP]

save()
print(f"갱신 완료: {len(todo)}개 (전체 {len(series) - 1}개 종목 + SPY 중), 실패 {failed}개")
