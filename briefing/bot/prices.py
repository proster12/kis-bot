"""미국주식 일봉 시세 가져오기 (yfinance, 키 불필요)."""
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
CLOSE_SAFE = dt.time(16, 15)  # 정규장 마감(16:00 ET) 후 종가가 확정될 여유


def drop_incomplete(df, now_et=None):
    """미국장이 아직 안 끝났으면 오늘 봉(장중 가격)을 빼서 확정 종가만 남깁니다."""
    now_et = now_et or dt.datetime.now(ET)
    if len(df) and df.index[-1].date() == now_et.date() and now_et.time() < CLOSE_SAFE:
        return df.iloc[:-1]
    return df


def fetch_daily(tickers, period="1y"):
    """티커별 일봉 DataFrame(Open/High/Low/Close/Volume)을 dict로 돌려줍니다.

    실패한 티커는 빠지고 로그에 남습니다.
    """
    import yfinance as yf

    out = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(period=period, auto_adjust=True)
            if df is None or df.empty:
                log.warning("%s: 시세 없음", t)
                continue
            df.index = pd.to_datetime(df.index).tz_localize(None)  # 미국 동부 날짜 기준
            out[t] = drop_incomplete(df[["Open", "High", "Low", "Close", "Volume"]].dropna())
        except Exception as e:  # 네트워크 오류 등
            log.warning("%s: 시세 조회 실패 (%s)", t, e)
    return out
