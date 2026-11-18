"""미국 내부자 거래(SEC Form 4) — 외국인 수급 정보 대신 쓰는 수급 신호.

SEC EDGAR는 무료이며 키가 필요 없지만, 요청 헤더에 "이름 이메일" 형식의
User-Agent를 넣어야 합니다 (환경변수 SEC_USER_AGENT). 초당 10회 이하로 요청합니다.

거래코드: P = 장내 매수(가장 의미 있는 신호), S = 장내 매도
"""
import datetime as dt
import logging
import os
import time
import xml.etree.ElementTree as ET

import requests

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"


class SecClient:
    def __init__(self):
        ua = os.getenv("SEC_USER_AGENT") or "trading-bot contact@example.com"
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self._last = 0.0
        self._cik_map = None

    def get(self, url):
        wait = 0.15 - (time.time() - self._last)  # 초당 10회 미만 유지
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        r = self.s.get(url, timeout=20)
        r.raise_for_status()
        return r

    def cik(self, ticker):
        if self._cik_map is None:
            data = self.get(TICKERS_URL).json()
            self._cik_map = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
        return self._cik_map.get(ticker.upper())


def _text(node, path):
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else ""


def parse_form4(xml_text):
    """Form 4 XML에서 비파생(보통주) 거래 목록을 뽑습니다."""
    root = ET.fromstring(xml_text)
    owner = _text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    role = ""
    if rel is not None:
        role = _text(rel, "officerTitle") or ("Director" if _text(rel, "isDirector") in ("1", "true") else "")
        if not role and _text(rel, "isTenPercentOwner") in ("1", "true"):
            role = "10% 주주"
    rows = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(tx, "transactionCoding/transactionCode")
        try:
            shares = float(_text(tx, "transactionAmounts/transactionShares/value") or 0)
            price = float(_text(tx, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        rows.append({
            "owner": owner, "role": role, "code": code,
            "date": _text(tx, "transactionDate/value"),
            "shares": shares, "price": price, "value": shares * price,
        })
    return rows


def recent_trades(client: SecClient, ticker, lookback_days=14, max_filings=15):
    """최근 lookback_days 동안의 장내 매수(P)/매도(S) 요약."""
    summary = {"ticker": ticker, "buys": [], "sell_value": 0.0, "sell_count": 0, "error": None}
    try:
        cik = client.cik(ticker)
        if not cik:  # ETF 등 내부자 공시가 없는 종목
            summary["skipped"] = True
            return summary
        recent = client.get(SUBMISSIONS_URL.format(cik=cik)).json()["filings"]["recent"]
        since = dt.date.today() - dt.timedelta(days=lookback_days)
        checked = 0
        for form, acc, fdate, doc in zip(recent["form"], recent["accessionNumber"],
                                         recent["filingDate"], recent["primaryDocument"]):
            if form != "4":
                continue
            if dt.date.fromisoformat(fdate) < since or checked >= max_filings:
                break
            checked += 1
            raw_doc = doc.split("/")[-1]  # 'xslF345X05/xxx.xml' → 'xxx.xml' (원본 XML)
            url = DOC_URL.format(cik=cik, acc=acc.replace("-", ""), doc=raw_doc)
            try:
                for row in parse_form4(client.get(url).text):
                    if row["code"] == "P":
                        summary["buys"].append(row)
                    elif row["code"] == "S":
                        summary["sell_value"] += row["value"]
                        summary["sell_count"] += 1
            except Exception as e:
                log.warning("%s: Form 4 파싱 실패 %s (%s)", ticker, acc, e)
    except Exception as e:
        log.warning("%s: 내부자 거래 조회 실패 (%s)", ticker, e)
        summary["error"] = str(e)
    return summary
