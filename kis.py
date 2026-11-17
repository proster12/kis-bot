"""한국투자증권 Open API 해외주식(미국) 최소 클라이언트. 표준 라이브러리만 사용."""
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
# 생성 데이터(일봉, 리포트)는 PC 로컬에: 드라이브 폴더는 새 파일 쓰기가 간헐적으로 실패함
DATA_DIR = Path.home() / "kis-bot-data"

URLS = {"paper": "https://openapivts.koreainvestment.com:29443",
        "live": "https://openapi.koreainvestment.com:9443"}
# (실전, 모의) TR ID. 모의 매도는 규칙이 달라 표로 관리
TR = {"balance": ("TTTS3012R", "VTTS3012R"),
      "buy": ("TTTT1002U", "VTTT1002U"),
      "sell": ("TTTT1006U", "VTTT1001U"),
      "cancel": ("TTTT1004U", "VTTT1004U")}
# 시세 API와 주문 API의 거래소 코드 표기가 다름
PRICE_EXCD = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
# 순위 API: (경로, TR ID, 고정 파라미터)
RANK = {"surge": ("/uapi/overseas-stock/v1/ranking/volume-surge", "HHDFS76270000",
                  {"MINX": "9"}),  # 거래량급증: 120분 전 대비
        "gainers": ("/uapi/overseas-stock/v1/ranking/updown-rate", "HHDFS76290000",
                    {"NDAY": "0", "GUBN": "1"})}  # 당일 상승률


def unfinished_from():
    """이 날짜(YYYYMMDD, 미국 동부) 이후 일봉은 미완성: 정규장 마감(16:00 ET) 전에는 오늘 봉 제외.
    한투는 장 시작 전에도 주간거래·프리마켓 값으로 오늘 날짜 봉을 줌."""
    now = datetime.now(ZoneInfo("America/New_York"))
    return (now + timedelta(days=1 if now.hour >= 16 else 0)).strftime("%Y%m%d")


class KISError(Exception):
    pass


def load_env(path=ROOT / ".env"):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _send(req):
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # KIS는 오류도 JSON 본문으로 내려줌 (예: 토큰 1분 1회 제한)
        try:
            return json.loads(e.read())
        except ValueError:
            raise KISError(f"HTTP {e.code}") from None
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        # 연결 끊김·타임아웃: 호출한 쪽에서 재시도하도록 오류 응답처럼 돌려줌
        return {"msg_cd": "NETWORK", "msg1": f"네트워크 오류: {e}"}


class KIS:
    def __init__(self):
        load_env()
        self.mode = os.environ.get("KIS_MODE", "paper")
        if self.mode not in URLS:
            raise ValueError("KIS_MODE는 paper 또는 live 여야 합니다")
        self.base = URLS[self.mode]
        self.key = os.environ["KIS_APP_KEY"]
        self.secret = os.environ["KIS_APP_SECRET"]
        self.cano, self.prdt = os.environ["KIS_ACCOUNT"].split("-")
        self.max_order_usd = float(os.environ.get("KIS_MAX_ORDER_USD", "100"))
        self.max_order_krw = float(os.environ.get("KIS_MAX_ORDER_KRW", "500000"))  # 국내 주문 1건 한도
        self.min_interval = 1.0 if self.mode == "paper" else 0.06  # 실전은 초당 20건 제한
        self._last_call = 0.0
        # 토큰은 PC 로컬에 저장: 드라이브 동기화로 다른 PC와 섞이거나 유출되지 않게
        self.token_file = Path.home() / ".kis" / f"token_{self.mode}.json"
        self.token_file.parent.mkdir(exist_ok=True)
        self.token = self._token()

    def _token(self):
        # 토큰은 24시간 유효, 재발급은 1분 1회 제한이라 파일에 캐시
        if self.token_file.exists():
            t = json.loads(self.token_file.read_text())
            if t["expires_at"] > time.time() + 600:
                return t["token"]
        body = {"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret}
        req = urllib.request.Request(self.base + "/oauth2/tokenP", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"}, method="POST")
        res = _send(req)
        if "access_token" not in res:
            raise KISError(f"토큰 발급 실패: {res.get('error_code')} {res.get('error_description')}")
        self.token_file.write_text(json.dumps(
            {"token": res["access_token"], "expires_at": time.time() + int(res["expires_in"])}))
        return res["access_token"]

    def _call(self, method, path, tr_id, params=None, body=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"content-type": "application/json; charset=utf-8",
                   "authorization": f"Bearer {self.token}", "appkey": self.key,
                   "appsecret": self.secret, "tr_id": tr_id, "custtype": "P"}
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(3):
            # 호출 간격을 일정하게: 모의 서버는 사실상 초당 1건(0.66초 간격에선 1/3이 거절됨)
            time.sleep(max(0.0, self._last_call + self.min_interval + attempt - time.time()))
            self._last_call = time.time()
            res = _send(urllib.request.Request(url, data=data, headers=headers, method=method))
            # 초당 거래건수 초과는 재시도. 연결 끊김은 조회(GET)만: 주문은 서버에 도달했을 수 있어 중복 위험
            if not (res.get("msg_cd") == "EGW00201" or (res.get("msg_cd") == "NETWORK" and method == "GET")):
                break
        if res.get("rt_cd") != "0":
            raise KISError(f"{tr_id}: {res.get('msg_cd')} {res.get('msg1')}")
        return res

    def _tr(self, name):
        return TR[name][self.mode == "paper"]

    def _account(self, excg):
        return {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "OVRS_EXCG_CD": excg}

    def price(self, symbol, excg="NASD"):
        r = self._call("GET", "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
                       params={"AUTH": "", "EXCD": PRICE_EXCD[excg], "SYMB": symbol})
        return float(r["output"]["last"])

    def daily(self, symbol, excg="NASD", end=""):
        """end(YYYYMMDD, 빈 값=오늘)까지의 일봉(수정주가) 최대 100개. 스크리너 입력 형식의 dict 리스트, 최신순."""
        r = self._call("GET", "/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000",
                       params={"AUTH": "", "EXCD": PRICE_EXCD[excg], "SYMB": symbol,
                               "GUBN": "0", "BYMD": end, "MODP": "1"})
        return [{"date": f"{b['xymd'][:4]}-{b['xymd'][4:6]}-{b['xymd'][6:]}",
                 "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                 "close": float(b["clos"]), "volume": int(b["tvol"])}
                for b in r.get("output2") or [] if b.get("xymd") and b.get("clos") and b["xymd"] < unfinished_from()]

    def ranking(self, kind, excg="NASD", vol_rang="4"):
        """순위 목록(dict 리스트: symb, name, last, rate, tvol ...). vol_rang 4 = 10만주 이상."""
        path, tr_id, extra = RANK[kind]
        r = self._call("GET", path, tr_id, params={"EXCD": PRICE_EXCD[excg], "VOL_RANG": vol_rang,
                                                   "KEYB": "", "AUTH": "", **extra})
        return r.get("output2") or []

    def balance(self, excg="NASD"):
        """(보유종목 리스트, 요약) 반환."""
        # shortcut: 연속조회(페이지) 미처리, 보유종목이 한 페이지를 넘으면 CTX_AREA 처리 추가
        r = self._call("GET", "/uapi/overseas-stock/v1/trading/inquire-balance", self._tr("balance"),
                       params={**self._account(excg), "TR_CRCY_CD": "USD",
                               "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})
        return r["output1"], r["output2"]

    def order(self, side, symbol, qty, price, excg="NASD"):
        """지정가 주문. 주문번호 반환."""
        if side not in ("buy", "sell"):
            raise ValueError(side)
        if int(qty) != qty or qty <= 0 or price <= 0:
            raise ValueError(f"잘못된 수량/가격: {qty} @ {price}")
        # 한도는 매수에만: 매도까지 막으면 값이 오른 포지션을 청산하지 못함
        if side == "buy" and qty * price > self.max_order_usd:
            raise KISError(f"주문금액 ${qty * price:.2f} > 한도 ${self.max_order_usd} (KIS_MAX_ORDER_USD)")
        body = {**self._account(excg), "PDNO": symbol, "ORD_QTY": str(int(qty)),
                "OVRS_ORD_UNPR": f"{price:.2f}" if price >= 1 else f"{price:.4f}",
                "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00",
                "SLL_TYPE": "00" if side == "sell" else ""}
        r = self._call("POST", "/uapi/overseas-stock/v1/trading/order", self._tr(side), body=body)
        return r["output"]["ODNO"]

    def resv_order(self, side, symbol, qty, price=None, excg="NASD"):
        """미국 예약주문: 한국시간 10:00~23:20 접수(16:30~16:45 제외) → 다음 정규장 시작 때 전송,
        미체결분은 장 마감 후 자동취소. 매수는 지정가, 매도 price=None이면 장개시시장가(MOO). 예약주문번호 반환."""
        if side not in ("buy", "sell") or (side == "buy" and price is None):
            raise ValueError(f"{side} {price}")
        if int(qty) != qty or qty <= 0 or (price is not None and price <= 0):
            raise ValueError(f"잘못된 수량/가격: {qty} @ {price}")
        if side == "buy" and qty * price > self.max_order_usd:
            raise KISError(f"주문금액 ${qty * price:.2f} > 한도 ${self.max_order_usd} (KIS_MAX_ORDER_USD)")
        tr = {"buy": ("TTTT3014U", "VTTT3014U"), "sell": ("TTTT3016U", "VTTT3016U")}[side][self.mode == "paper"]
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": symbol, "OVRS_EXCG_CD": excg,
                "FT_ORD_QTY": str(int(qty)), "ORD_SVR_DVSN_CD": "0",
                "FT_ORD_UNPR3": "0" if price is None else (f"{price:.2f}" if price >= 1 else f"{price:.4f}"),
                "ORD_DVSN": "31" if price is None else "00"}  # 31: MOO(장개시시장가), 00: 지정가
        r = self._call("POST", "/uapi/overseas-stock/v1/trading/order-resv", tr, body=body)
        out = r.get("output") or {}
        return out.get("OVRS_RSVN_ODNO") or out.get("ODNO") or ""

    # --- 국내주식 ---
    def kr_price(self, code):
        r = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                       params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        return float(r["output"]["stck_prpr"])

    def kr_holdings(self):
        """{종목코드: {qty, avg}}"""
        r = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
                       "VTTC8434R" if self.mode == "paper" else "TTTC8434R",
                       params={"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "",
                               "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                               "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                               "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
        return {x["pdno"]: {"qty": int(x["hldg_qty"]), "avg": float(x["pchs_avg_pric"])}
                for x in r.get("output1") or [] if int(x.get("hldg_qty") or 0) > 0}

    def kr_order(self, side, code, qty, est_price):
        """국내 시장가 주문(장중만). est_price는 매수 한도 확인용 예상가. 주문번호 반환."""
        if side not in ("buy", "sell") or int(qty) != qty or qty <= 0:
            raise ValueError(f"{side} {qty}")
        if side == "buy" and qty * est_price > self.max_order_krw:
            raise KISError(f"주문금액 {qty * est_price:,.0f}원 > 한도 {self.max_order_krw:,.0f}원 (KIS_MAX_ORDER_KRW)")
        tr = {"buy": ("TTTC0012U", "VTTC0012U"), "sell": ("TTTC0011U", "VTTC0011U")}[side][self.mode == "paper"]
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": code, "ORD_DVSN": "01",  # 01: 시장가
                "ORD_QTY": str(int(qty)), "ORD_UNPR": "0", "EXCG_ID_DVSN_CD": "KRX",
                "SLL_TYPE": "01" if side == "sell" else "", "CNDT_PRIC": ""}
        r = self._call("POST", "/uapi/domestic-stock/v1/trading/order-cash", tr, body=body)
        return (r.get("output") or {}).get("ODNO", "")

    def cancel(self, odno, symbol, qty, excg="NASD"):
        body = {**self._account(excg), "PDNO": symbol, "ORGN_ODNO": odno,
                "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": str(int(qty)),
                "OVRS_ORD_UNPR": "0", "ORD_SVR_DVSN_CD": "0"}
        self._call("POST", "/uapi/overseas-stock/v1/trading/order-rvsecncl", self._tr("cancel"), body=body)
