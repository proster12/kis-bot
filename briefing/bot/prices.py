"""미국주식 일봉 시세 가져오기 (yfinance, 키 불필요)."""
import logging

import pandas as pd

log = logging.getLogger(__name__)


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
            df.index = pd.to_datetime(df.index).tz_localize(None)
            out[t] = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        except Exception as e:  # 네트워크 오류 등
            log.warning("%s: 시세 조회 실패 (%s)", t, e)
    return out
