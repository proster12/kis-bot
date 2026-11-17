"""눌림목 전략(깊은눌림 RSI2<5 + 시장필터) 모의투자 자동매매 — 낮 실행(예약주문) 방식.
규칙은 pullback_backtest.py의 '깊은눌림+시장필터(낮 실행)'과 같음: 전날 미국 종가로 판단 → 다음 정규장 시가에 체결.
  python trader.py signals   매일 09:10(아침 작업): 전날 종가 기준 매수 후보 → ~/kis-bot-data/signals.json
  python trader.py snapshot  매일 09:10(아침 작업): 체결 반영 + 모의 수익률 기록 → ~/kis-bot-data/paper_daily.csv
  python trader.py daily     평일 17:50(한국): 체결 반영 → 청산 예약매도(MOO) → 신규 예약매수(지정가)
                             예약주문 접수는 한국시간 10:00~23:20(16:30~16:45 제외)만 가능
"""
import csv
import json
import sys
import traceback
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from backtest import market_ok_by_date
from kis import DATA_DIR, KIS, KISError
from pullback_backtest import BUY_LIMIT_GAP, daily_exit, entry_signal, rsi
from regime import LEVELS, REGIME

RSI_MAX = 5
STOP_PCT = 0.08
CAPITAL_USD = 200  # 모의 운용 기준 자본. 모의 계좌 잔고와 무관하게 이 돈만 쓰는 것처럼 운용
MAX_POSITIONS = 2  # 동시 보유 종목 수: 남은 현금 ÷ 빈 자리 = 종목당 예산
CAUTION_SLOTS = 2  # 시장 판정 '주의'일 때 하루 신규 매수 최대 종목 수
ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
STATE, SIGNALS = DATA_DIR / "paper_state.json", DATA_DIR / "signals.json"
LOG, TRADES = DATA_DIR / "logs" / "trader.log", DATA_DIR / "paper_trades.csv"
HISTORY = DATA_DIR / "history.json"
DAILY = DATA_DIR / "paper_daily.csv"
TRADE_FIELDS = ["symbol", "entry_date", "exit_date", "qty", "entry", "exit_order_price", "reason", "ret", "pnl"]
FEE_PER_SIDE = 0.0025  # 수익률 표시에 쓰는 추정 수수료(한투 기본). 모의 체결엔 수수료가 없음


def log(msg):
    line = f"{datetime.now(KST):%Y-%m-%d %H:%M} KST  {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def signals():
    hist, exc = load_json(HISTORY, {}), load_json(DATA_DIR / "exchanges.json", {})
    market_ok = market_ok_by_date()
    asof = max(market_ok)
    cands = []
    if market_ok[asof]:
        for symbol, raw in hist.items():
            bars = [SimpleNamespace(**b) for b in sorted(raw, key=lambda b: b["date"])]
            if not bars or bars[-1].date != asof:
                continue
            closes = [b.close for b in bars]
            rs = rsi(closes)
            if entry_signal(bars, closes, rs, len(bars) - 1, RSI_MAX):
                cands.append({"symbol": symbol, "excg": exc.get(symbol, "NASD"),
                              "rsi2": round(rs[-1], 1), "close": bars[-1].close})
    cands.sort(key=lambda c: c["rsi2"])  # 가장 깊게 눌린 순
    save_json(SIGNALS, {"asof": asof, "market_ok": market_ok[asof], "candidates": cands})
    log(f"신호({asof} 종가 기준): 시장필터 {'통과' if market_ok[asof] else 'SPY 50일선 아래 → 매수 안 함'}, "
        f"후보 {len(cands)}개 " + " ".join(c["symbol"] for c in cands[:10]))


def regime_level(asof):
    """market_regime.json의 미국 판정. 정량(level)과 Claude 뉴스(news_level) 중 더 엄격한 쪽."""
    us = load_json(REGIME, {}).get("us", {})
    if us.get("date") != asof:
        return "normal", f"판정 파일 날짜({us.get('date')})가 신호({asof})와 달라 판정 없이 진행"
    levels = [us.get("level", "normal"), us.get("news_level", "normal")]
    level = max(levels, key=LEVELS.index)
    return level, " / ".join(us.get("reasons", []) + ([us["news_summary"]] if us.get("news_summary") else []))


def holdings(k):
    out = {}
    for e in ("NASD", "NYSE", "AMEX"):
        for h in k.balance(e)[0]:
            qty = int(float(h.get("ovrs_cblc_qty") or 0))
            if qty > 0:
                out[h["ovrs_pdno"]] = {"qty": qty, "avg": float(h["pchs_avg_pric"]),
                                       "excg": h.get("ovrs_excg_cd") or e}
    return out


def bars_of(hist, sym):
    return sorted(hist.get(sym, []), key=lambda b: b["date"])


def read_trades():
    return list(csv.DictReader(TRADES.open(encoding="utf-8-sig"))) if TRADES.exists() else []


def cash_now(st, held, hist, tkey):
    """기준 자본 + 실현손익 - 추정 수수료 - 보유·주문 금액. 오늘 시가에 팔 종목은 마지막 종가로 회수된다고 봄."""
    trades = read_trades()
    cash = CAPITAL_USD + sum(float(t["pnl"] or 0) for t in trades) \
        - FEE_PER_SIDE * sum(float(t["entry"]) * float(t["qty"] or 0) * 2 for t in trades)
    for sym, p in st["positions"].items():
        h = held.get(sym)
        if not h:
            continue
        if p.get("sell_date") == tkey:
            last = (bars_of(hist, sym) or [{"close": h["avg"]}])[-1]["close"]
            cash += (last - h["avg"]) * h["qty"] - FEE_PER_SIDE * h["avg"] * h["qty"] * 2
        else:
            cash -= h["avg"] * h["qty"] * (1 + FEE_PER_SIDE)
    return cash - sum(p.get("cost", 0) for p in st["pending"].values())


def reconcile(st, held, hist):
    """잔고와 기록 맞추기: 예약매수 체결 → 포지션, 사라진 포지션 → 매도 체결 기록(체결가는 그날 시가)."""
    pos, pending = st["positions"], st["pending"]
    for sym, p in list(pending.items()):
        if sym in held:
            h = held[sym]
            pos[sym] = {"entry_date": p["date"], "entry": h["avg"], "excg": h["excg"]}
            log(f"매수 체결 {sym} {h['qty']}주 @ {h['avg']}")
            del pending[sym]
        elif p["date"] <= (bars_of(hist, sym) or [{"date": ""}])[-1]["date"]:  # 주문한 날 장이 끝났는데 잔고에 없음
            log(f"매수 미체결(시가가 지정가 위로 갭 상승 등) {sym} → 자동취소됨")
            del pending[sym]
    for sym in [s for s in pos if s not in held]:
        p = pos.pop(sym)
        day = p.get("sell_date", "")
        bar = next((b for b in bars_of(hist, sym) if b["date"] == day), None)
        x = p.get("exit_price") or (bar["open"] if bar else None)  # exit_price: 예전 장중 지정가 매도분
        q = p.get("qty", 0)
        log(f"매도 체결 {sym} ({p.get('reason')}) @ {x}")
        new = not TRADES.exists()
        with TRADES.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new:
                w.writerow(TRADE_FIELDS)
            w.writerow([sym, p["entry_date"], day, q, p["entry"], x, p.get("reason"),
                        f"{x / p['entry'] - 1:.4f}" if x else "", f"{(x - p['entry']) * q:.2f}" if x else ""])


def daily(k=None, now=None):
    now = now or datetime.now(KST)
    if now.weekday() >= 5:
        return
    k = k or KIS()
    if k.mode != "paper":
        raise SystemExit("trader.py는 모의투자(KIS_MODE=paper) 전용입니다. 실전 전환은 별도 검토 후 직접 해제하세요.")
    st = load_json(STATE, {"positions": {}, "pending": {}})
    try:
        daily_trade(k, st, now)
    finally:
        save_json(STATE, st)  # 중간에 오류가 나도 이미 낸 주문은 기록해야 중복 주문을 막음


def daily_trade(k, st, now):
    tkey = now.date().isoformat()  # 오늘 한국 날짜 = 오늘 밤 미국 정규장 날짜
    if st.get("last_daily") == tkey:
        log("오늘 예약주문은 이미 냈음: 건너뜀")
        return
    hm = now.hour * 60 + now.minute
    if not (10 * 60 <= hm < 23 * 60 + 20) or 16 * 60 + 30 <= hm < 16 * 60 + 45:
        log("예약주문 접수시간(10:00~23:20, 16:30~16:45 제외)이 아님: 건너뜀")
        return
    hist = load_json(HISTORY, {})
    held = holdings(k)
    reconcile(st, held, hist)
    pos, pending = st["positions"], st["pending"]

    # 1) 청산: 마지막 미국 종가로 판단 → 오늘 밤 시가에 장개시시장가(MOO) 매도
    for sym, p in pos.items():
        if p.get("sell_date") == tkey or sym not in held:
            continue
        bars = bars_of(hist, sym)
        dates = [b["date"] for b in bars]
        if p["entry_date"] not in dates:
            log(f"{sym}: 진입일 {p['entry_date']} 일봉 없음(데이터 갱신 전?) → 청산 판단 보류")
            continue
        i_entry = dates.index(p["entry_date"])
        n = len(bars) - i_entry  # 보유 거래일 수 (진입일 포함, 마지막 종가까지)
        reason = daily_exit(bars, [b["close"] for b in bars], i_entry - 1, n, p["entry"], STOP_PCT)
        if p.get("sell_date"):
            reason = reason or p.get("reason")  # 전에 낸 매도가 안 된 경우 다시
        if reason:
            p.update(sell_odno=k.resv_order("sell", sym, held[sym]["qty"], None, p["excg"]),
                     sell_date=tkey, reason=reason, qty=held[sym]["qty"])
            p.pop("exit_price", None)
            log(f"예약매도(MOO) {sym} {held[sym]['qty']}주 ({reason}, 보유 {n}일, 종가 {bars[-1]['close']})")

    # 2) 신규 매수: 전날 종가 기준 신호 → 종가 +3% 지정가 예약매수
    sig = load_json(SIGNALS, {"asof": "", "candidates": []})
    if not sig["asof"] or not 0 < (now.date() - date.fromisoformat(sig["asof"])).days <= 4:
        log(f"신호 파일이 오래됨({sig['asof']}): 매수 건너뜀")
    else:
        selling = sum(1 for p in pos.values() if p.get("sell_date") == tkey)
        slots = MAX_POSITIONS - len(pos) + selling - len(pending)  # 오늘 시가에 팔리는 자리는 재사용
        level, why = regime_level(sig["asof"])
        if level == "stop":
            log(f"시장 판정 매수중단: {why}")
            slots = 0
        elif level == "caution":
            log(f"시장 판정 주의: 신규 매수 최대 {CAUTION_SLOTS}종목 ({why})")
            slots = min(slots, CAUTION_SLOTS)
        cash = cash_now(st, held, hist, tkey)
        if slots > 0:
            log(f"매수 가능 현금 ${cash:,.2f}, 빈 자리 {slots}개 → 종목당 예산 ${cash / slots:,.2f}")
        for c in sig["candidates"]:
            if slots <= 0:
                break
            sym = c["symbol"]
            if sym in pos or sym in pending or sym in held:
                continue
            limit = round(c["close"] * (1 + BUY_LIMIT_GAP), 2)
            budget = min(cash / slots, k.max_order_usd)
            qty = int(budget // (limit * (1 + FEE_PER_SIDE)))
            if qty < 1:
                continue  # 1주 가격이 예산보다 비쌈: 다음(더 싼) 후보로
            cash -= qty * limit * (1 + FEE_PER_SIDE)
            try:
                odno = k.resv_order("buy", sym, qty, limit, c["excg"])
            except KISError as e:
                log(f"예약매수 실패 {sym}: {e}")
                continue
            pending[sym] = {"odno": odno, "qty": qty, "date": tkey, "excg": c["excg"],
                            "cost": round(qty * limit * (1 + FEE_PER_SIDE), 2)}
            slots -= 1
            log(f"예약매수 {sym} {qty}주 지정가 {limit} (RSI2 {c['rsi2']}, 종가 {c['close']})")
    st["last_daily"] = tkey


def snapshot(k=None):
    """체결 반영 후 마지막 미국 장 마감 기준 모의 수익률 한 줄 기록. 기준 자본 = CAPITAL_USD."""
    k = k or KIS()
    st = load_json(STATE, {"positions": {}, "pending": {}})
    held = holdings(k)
    reconcile(st, held, load_json(HISTORY, {}))
    save_json(STATE, st)
    mine = {s: h for s, h in held.items() if s in st["positions"]}  # 봇이 산 종목만
    value = {s: k.price(s, h["excg"]) * h["qty"] for s, h in mine.items()}
    cost = sum(h["avg"] * h["qty"] for h in mine.values())
    trades = read_trades()
    realized = sum(float(t["pnl"] or 0) for t in trades)
    fees = FEE_PER_SIDE * (sum(float(t["entry"]) * float(t["qty"] or 0) * 2 for t in trades) + cost)
    capital = CAPITAL_USD
    equity = capital + realized + sum(value.values()) - cost - fees
    now = datetime.now(ET)
    day = (now if now.hour >= 16 else now - timedelta(days=1)).date()  # 장 마감 전이면 전날 세션
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    rows = [r for r in csv.DictReader(DAILY.open(encoding="utf-8-sig"))] if DAILY.exists() else []
    rows = [r for r in rows if r["date"] != day.isoformat()]  # 같은 날 다시 돌리면 덮어씀
    prev = float(rows[-1]["equity"]) if rows else capital
    rows.append({"date": day.isoformat(), "positions": " ".join(f"{s}({value[s] / (mine[s]['avg'] * mine[s]['qty']) - 1:+.1%})"
                                                                   for s in mine) or "-",
                 "invested": f"{cost:.2f}", "unrealized": f"{sum(value.values()) - cost:.2f}",
                 "realized": f"{realized:.2f}", "est_fees": f"{fees:.2f}", "equity": f"{equity:.2f}",
                 "daily_ret": f"{equity / prev - 1:.4f}", "cum_ret": f"{equity / capital - 1:.4f}"})
    with DAILY.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[-1].keys())
        w.writeheader()
        w.writerows(rows)
    r = rows[-1]
    log(f"모의 수익률({r['date']}): 평가 ${equity:,.2f} / 기준 ${capital:,.0f}, 하루 {float(r['daily_ret']):+.2%}, "
        f"누적 {float(r['cum_ret']):+.2%} (수수료 추정 차감), 보유 {r['positions']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("signals", "daily", "snapshot"):
        sys.exit(__doc__)
    try:
        {"signals": signals, "daily": daily, "snapshot": snapshot}[cmd]()
    except Exception:
        log("오류:\n" + traceback.format_exc())
        raise
