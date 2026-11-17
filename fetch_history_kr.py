"""국내 백테스트용 장기 일봉: 코스피200 + 코스닥150 x 약 2년 → ~/kis-bot-data/kr_history.json
시장필터용 KODEX 200(069500) → ~/kis-bot-data/kr_index.json
  python fetch_history_kr.py   (중간에 멈춰도 다시 실행하면 이어서 받음)
"""
import io
import json
import urllib.request
import zipfile
from datetime import date, timedelta

from kis import DATA_DIR, KIS, KISError

OUT, INDEX = DATA_DIR / "kr_history.json", DATA_DIR / "kr_index.json"
PAGES = 5
MASTER = "https://new.real.download.dws.co.kr/common/master/{}_code.mst.zip"  # 한투 공식 종목 마스터


def master(market):
    """(단축코드, 이름, 뒷부분 고정폭 필드) 목록. 형식은 한투 공식 예제(stocks_info/kis_*_code_mst.py) 기준."""
    with urllib.request.urlopen(MASTER.format(market), timeout=30) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    tail = 228 if market == "kospi" else 222
    text = z.read(z.namelist()[0]).decode("cp949")
    return [(row[:9].strip(), row[21:len(row) - tail].strip(), row[-tail:])
            for row in text.splitlines(keepends=True)]


def universe():
    kospi200 = {code: name for code, name, f in master("kospi") if f[:2] == "ST" and f[18] not in " 0"}
    kosdaq150 = {code: name for code, name, f in master("kosdaq") if f[35] == "Y"}
    return kospi200, kosdaq150


def daily(k, code, end):
    start = (date.fromisoformat(end) - timedelta(days=150)).strftime("%Y%m%d")  # 100거래일이 들어가는 기간
    r = k._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": start,
                        "FID_INPUT_DATE_2": end.replace("-", ""), "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
    return [{"date": f"{b['stck_bsop_date'][:4]}-{b['stck_bsop_date'][4:6]}-{b['stck_bsop_date'][6:]}",
             "open": float(b["stck_oprc"]), "high": float(b["stck_hgpr"]), "low": float(b["stck_lwpr"]),
             "close": float(b["stck_clpr"]), "volume": int(b["acml_vol"])}
            for b in r.get("output2") or [] if b.get("stck_bsop_date") and float(b.get("stck_clpr") or 0) > 0]


def history(k, code):
    bars, end = [], date.today().isoformat()
    for _ in range(PAGES):
        new = [b for b in daily(k, code, end) if not bars or b["date"] < bars[-1]["date"]]
        if not new:
            break
        bars += new
        end = (date.fromisoformat(bars[-1]["date"]) - timedelta(days=1)).isoformat()
    return bars


if __name__ == "__main__":
    kospi200, kosdaq150 = universe()
    print(f"코스피200 {len(kospi200)}개, 코스닥150 {len(kosdaq150)}개")
    assert 180 <= len(kospi200) <= 220 and 130 <= len(kosdaq150) <= 170, "마스터 파일 형식이 바뀐 듯: 목록 개수 이상"
    k = KIS()
    DATA_DIR.mkdir(exist_ok=True)
    INDEX.write_text(json.dumps(history(k, "069500")), encoding="utf-8")
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    codes = {**kospi200, **kosdaq150}
    for i, (code, name) in enumerate(codes.items(), 1):
        if code in data:
            continue
        try:
            data[code] = history(k, code)
        except KISError as e:
            print(f"{code} {name}: {e}")
            continue
        print(f"[{i}/{len(codes)}] {code} {name}: {len(data[code])}일", flush=True)
        if i % 25 == 0:
            OUT.write_text(json.dumps(data), encoding="utf-8")
    OUT.write_text(json.dumps(data), encoding="utf-8")
    (DATA_DIR / "kr_names.json").write_text(json.dumps(codes, ensure_ascii=False), encoding="utf-8")
    print(f"\n완료: {sum(1 for v in data.values() if v)}개 종목 → {OUT}")
