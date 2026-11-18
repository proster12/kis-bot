"""기술지표와 매매 신호.

규칙(단순하고 설명 가능한 것부터 시작):
- 매수 후보: RSI < rsi_buy (과매도)  또는  단기 이평선이 장기 이평선을 위로 돌파(골든크로스)
- 매도 후보: RSI > rsi_sell (과매수)  또는  단기 이평선이 장기 이평선을 아래로 돌파(데드크로스)
"""
from dataclasses import dataclass

import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder 방식 RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


@dataclass
class Signal:
    ticker: str
    date: str
    close: float
    change_pct: float
    rsi: float
    sma_fast: float
    sma_slow: float
    action: str        # BUY / SELL / HOLD
    reasons: list


def evaluate(ticker: str, df: pd.DataFrame, cfg: dict) -> Signal | None:
    need = max(cfg["sma_slow"], cfg["rsi_period"]) + 2
    if len(df) < need:
        return None
    close = df["Close"]
    r = rsi(close, cfg["rsi_period"])
    fast = close.rolling(cfg["sma_fast"]).mean()
    slow = close.rolling(cfg["sma_slow"]).mean()

    last_r = float(r.iloc[-1])
    reasons, action = [], "HOLD"

    crossed_up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
    crossed_dn = fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]

    if last_r < cfg["rsi_buy"]:
        reasons.append(f"RSI {last_r:.0f} 과매도")
    if crossed_up:
        reasons.append(f"골든크로스({cfg['sma_fast']}일>{cfg['sma_slow']}일)")
    if reasons:
        action = "BUY"

    sell_reasons = []
    if last_r > cfg["rsi_sell"]:
        sell_reasons.append(f"RSI {last_r:.0f} 과매수")
    if crossed_dn:
        sell_reasons.append(f"데드크로스({cfg['sma_fast']}일<{cfg['sma_slow']}일)")
    if sell_reasons:
        # 매수·매도 조건이 동시에 나오면 보수적으로 관망
        action = "HOLD" if action == "BUY" else "SELL"
        reasons += sell_reasons

    return Signal(
        ticker=ticker,
        date=str(df.index[-1].date()),
        close=float(close.iloc[-1]),
        change_pct=float((close.iloc[-1] / close.iloc[-2] - 1) * 100),
        rsi=last_r,
        sma_fast=float(fast.iloc[-1]),
        sma_slow=float(slow.iloc[-1]),
        action=action,
        reasons=reasons,
    )
