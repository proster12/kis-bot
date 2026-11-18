"""종목 뉴스 헤드라인.

- 기본: 야후 파이낸스 RSS (키 불필요)
- FINNHUB_API_KEY 환경변수가 있으면 Finnhub 회사 뉴스를 우선 사용
"""
import datetime as dt
import logging
import os

import requests

log = logging.getLogger(__name__)
YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US"
FINNHUB = "https://finnhub.io/api/v1/company-news"


def _finnhub(ticker, n, key):
    today = dt.date.today()
    r = requests.get(
        FINNHUB,
        params={"symbol": ticker, "from": str(today - dt.timedelta(days=3)), "to": str(today), "token": key},
        timeout=15,
    )
    r.raise_for_status()
    items = sorted(r.json(), key=lambda x: x.get("datetime", 0), reverse=True)
    return [{"title": i.get("headline", ""), "link": i.get("url", ""), "source": i.get("source", "")} for i in items[:n]]


def _yahoo(ticker, n):
    import feedparser

    feed = feedparser.parse(YAHOO_RSS.format(t=ticker))
    return [{"title": e.get("title", ""), "link": e.get("link", ""), "source": "Yahoo"} for e in feed.entries[:n]]


def headlines(ticker, n=3):
    key = os.getenv("FINNHUB_API_KEY")
    try:
        if key:
            return _finnhub(ticker, n, key)
        return _yahoo(ticker, n)
    except Exception as e:
        log.warning("%s: 뉴스 조회 실패 (%s)", ticker, e)
        return []
