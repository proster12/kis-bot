"""국내 모의투자: KODEX 200 100일선 추세 전략, 기준 자본 30만 원 (kr_backtest.py의 A 전략과 같은 규칙).
어제 종가가 100일선 위면 보유(없으면 시장가 매수), 아래면 전량 시장가 매도. ETF라 매도 증권거래세 없음.
  python kr_trader.py run   평일 장중(09:15 예약 작업): 판단 → 주문 → 기록
데이터는 같은 작업에서 먼저 도는 regime.py가 갱신하는 ~/kis-bot-data/kr_index.json(KODEX 200 일봉)을 씀.
"""
import csv
import json
import sys
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from kis import DATA_DIR, KIS

CODE, NAME = "069500", "KODEX 200"
MA = 100
CAPITAL = 300_000
FEE = 0.00015  # 위탁수수료 추정(한쪽). ETF 매도는 증권거래세 면제
KST = ZoneInfo("Asia/Seoul")
INDEX, STATE = DATA_DIR / "kr_index.json", DATA_DIR / "kr_paper_state.json"
DAILY, TRADES = DATA_DIR / "kr_paper_daily.csv", DATA_DIR / "kr_paper_trades.csv"
LOG = DATA_DIR / "logs" / "kr_trader.log"


def log(msg):
    line = f"{datetime.now(KST):%Y-%m-%d %H:%M} KST  {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def append_csv(path, row):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if new:
            w.writeheader()
        w.writerow(row)


def run(k=None, now=None):
    now = now or datetime.now(KST)
    today = now.date().isoformat()
    if now.weekday() >= 5:
        return
    if not ("0900" <= now.strftime("%H%M") < "1520"):
        log("장중(09:00~15:20)이 아님: 건너뜀")
        return
    k = k or KIS()
    if k.mode != "paper":
        raise SystemExit("kr_trader.py는 모의투자(KIS_MODE=paper) 전용입니다.")
    st = load_json(STATE, {"qty": 0, "cash": CAPITAL, "entry": None, "entry_date": None, "last_run": ""})
    if st["last_run"] == today:
        log("오늘은 이미 실행함: 건너뜀")
        return
    bars = [b for b in sorted(load_json(INDEX, []), key=lambda b: b["date"]) if b["date"] < today]
    if len(bars) < MA or (now.date() - datetime.fromisoformat(bars[-1]["date"]).date()).days > 5:
        log(f"{NAME} 일봉이 부족하거나 오래됨(마지막 {bars[-1]['date'] if bars else '-'}): 건너뜀")
        return
    closes = [b["close"] for b in bars]
    ma = sum(closes[-MA:]) / MA
    hold = closes[-1] > ma
    held = k.kr_holdings().get(CODE, {"qty": 0, "avg": 0})
    if held["qty"] != st["qty"]:
        log(f"기록 수량 {st['qty']}주와 잔고 {held['qty']}주가 다름 → 잔고 기준으로 맞춤")
        st["qty"] = held["qty"]
    # 어제 종가 기준 평가액 기록(오늘 매매 전)
    eq = st["cash"] + st["qty"] * closes[-1]
    rows = list(csv.DictReader(DAILY.open(encoding="utf-8-sig"))) if DAILY.exists() else []
    if not rows or rows[-1]["date"] < bars[-1]["date"]:
        prev = float(rows[-1]["equity"]) if rows else CAPITAL
        append_csv(DAILY, {"date": bars[-1]["date"], "close": closes[-1], "ma100": round(ma, 1),
                           "position": f"{st['qty']}주" if st["qty"] else "현금", "cash": round(st["cash"]),
                           "equity": round(eq), "daily_ret": f"{eq / prev - 1:.4f}", "cum_ret": f"{eq / CAPITAL - 1:.4f}"})
    log(f"{bars[-1]['date']} 종가 {closes[-1]:,.0f} / {MA}일선 {ma:,.0f} ({closes[-1] / ma - 1:+.1%}) → "
        f"{'보유' if hold else '현금'} 신호, 평가 {eq:,.0f}원 (누적 {eq / CAPITAL - 1:+.2%})")

    if hold and st["qty"] == 0:
        price = k.kr_price(CODE)
        qty = int(st["cash"] // (price * (1 + FEE)))
        if qty < 1:
            log(f"현금 {st['cash']:,.0f}원으로 1주({price:,.0f}원)를 못 삼")
        else:
            odno = k.kr_order("buy", CODE, qty, price)
            fill = k.kr_holdings().get(CODE, {}).get("avg") or price  # 시장가 체결가(평균단가), 없으면 주문 시점가
            st.update(qty=qty, entry=fill, entry_date=today, cash=round(st["cash"] - qty * fill * (1 + FEE)))
            log(f"시장가 매수 {NAME} {qty}주 @ {fill:,.0f} (주문 {odno}), 남은 현금 {st['cash']:,.0f}원")
    elif not hold and st["qty"] > 0:
        price = k.kr_price(CODE)
        odno = k.kr_order("sell", CODE, st["qty"], price)
        proceeds = st["qty"] * price * (1 - FEE)
        cost = st["qty"] * (st["entry"] or held["avg"]) * (1 + FEE)
        append_csv(TRADES, {"entry_date": st["entry_date"], "exit_date": today, "qty": st["qty"],
                            "entry": st["entry"], "exit": price, "pnl": round(proceeds - cost),
                            "ret": f"{proceeds / cost - 1:.4f}"})
        log(f"시장가 매도 {NAME} {st['qty']}주 @ {price:,.0f} (주문 {odno}), 손익 {proceeds - cost:+,.0f}원")
        st.update(qty=0, entry=None, entry_date=None, cash=round(st["cash"] + proceeds))
    st["last_run"] = today
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] != ["run"]:
        sys.exit(__doc__)
    try:
        run()
    except Exception:
        log("오류:\n" + traceback.format_exc())
        raise
