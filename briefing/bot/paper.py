"""가상 매매(모의 운용). 실제 주문은 절대 내지 않습니다.

신호대로 가상 계좌에서 사고팔아 성적을 기록합니다.
상태는 data/paper_state.json, 거래 내역은 data/trades.csv 에 저장되고
GitHub Actions가 저장소에 커밋해 다음 실행으로 이어집니다.
"""
import csv
import json
import math
from datetime import date
from pathlib import Path

STATE = Path("data/paper_state.json")
TRADES = Path("data/trades.csv")


def load(cfg):
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"cash": cfg["starting_cash_usd"], "start_cash": cfg["starting_cash_usd"],
            "positions": {}, "last_signal_date": None}


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _log_trade(row):
    new = not TRADES.exists()
    TRADES.parent.mkdir(parents=True, exist_ok=True)
    with TRADES.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "ticker", "side", "shares", "price", "amount", "fee", "pnl", "reason"])
        if new:
            w.writeheader()
        w.writerow(row)


def step(state, signals, cfg):
    """신호 목록으로 가상 매매를 한 번 실행하고, 이번에 일어난 거래 목록을 돌려줍니다."""
    if not signals:
        return []
    sig_date = max(s.date for s in signals)
    if state.get("last_signal_date") == sig_date:
        return []  # 같은 거래일을 두 번 처리하지 않음 (수동 재실행 대비)

    fee_rate = cfg.get("fee_pct", 0.25) / 100
    cooldown = cfg.get("cooldown_days", 5)
    blocked = state.setdefault("sold_on", {})  # 매도한 종목 → 매도일 (재매수 대기용)
    by_ticker = {s.ticker: s for s in signals}
    trades = []

    # 1) 보유 종목: 손절 / 익절 / 매도 신호
    for t, pos in list(state["positions"].items()):
        s = by_ticker.get(t)
        if s is None:
            continue
        chg = (s.close / pos["avg_price"] - 1) * 100
        reason = None
        if chg <= -cfg["stop_loss_pct"]:
            reason = f"손절 {chg:.1f}%"
        elif chg >= cfg["take_profit_pct"]:
            reason = f"익절 {chg:+.1f}%"
        elif s.action == "SELL":
            reason = "매도 신호: " + ", ".join(s.reasons)
        if reason:
            amount = pos["shares"] * s.close
            fee = amount * fee_rate
            pnl = amount - fee - pos["cost"]
            state["cash"] += amount - fee
            del state["positions"][t]
            blocked[t] = s.date
            row = {"date": s.date, "ticker": t, "side": "SELL", "shares": pos["shares"],
                   "price": round(s.close, 2), "amount": round(amount, 2), "fee": round(fee, 2),
                   "pnl": round(pnl, 2), "reason": reason}
            _log_trade(row)
            trades.append(row)

    # 2) 매수 신호 (보유하지 않은 종목, 자리와 현금이 있을 때, 정수 주만)
    for s in sorted(signals, key=lambda x: x.rsi):  # 더 과매도인 종목부터
        if s.action != "BUY" or s.ticker in state["positions"]:
            continue
        sold = blocked.get(s.ticker)
        if sold and (date.fromisoformat(s.date) - date.fromisoformat(sold)).days < cooldown:
            continue  # 최근 매도한 종목은 대기 기간 동안 재매수 안 함
        if len(state["positions"]) >= cfg["max_positions"]:
            break
        budget = min(cfg["order_usd"], state["cash"])
        shares = math.floor(budget / (s.close * (1 + fee_rate)))
        if shares < 1:
            continue
        amount = shares * s.close
        fee = amount * fee_rate
        state["cash"] -= amount + fee
        state["positions"][s.ticker] = {"shares": shares, "avg_price": s.close,
                                        "cost": amount + fee, "date": s.date}
        row = {"date": s.date, "ticker": s.ticker, "side": "BUY", "shares": shares,
               "price": round(s.close, 2), "amount": round(amount, 2), "fee": round(fee, 2),
               "pnl": "", "reason": "매수 신호: " + ", ".join(s.reasons)}
        _log_trade(row)
        trades.append(row)

    state["last_signal_date"] = sig_date
    return trades


def equity(state, last_prices):
    value = sum(p["shares"] * last_prices.get(t, p["avg_price"]) for t, p in state["positions"].items())
    total = state["cash"] + value
    return {"cash": state["cash"], "positions_value": value, "total": total,
            "return_pct": (total / state["start_cash"] - 1) * 100}
