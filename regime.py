"""시장 상황 판정(정량): normal(정상) / caution(주의) / stop(매수중단).
  미국: VIX 25 이상 주의, 30 이상 또는 SPY 하루 -3% 이하 매수중단
  국내: 외국인 순매수가 최근 60일 대비 -1 표준편차 이하 주의, -2 이하 또는 코스피 하루 -3% 이하 매수중단
뉴스는 아침 예약 작업에서 Claude가 news_level/news_summary로 덧붙임(더 엄격하게만 바꿀 수 있음).
재진입 현황(reentry): 하락 구간(50일선 -5% 이하) 여부와 팔로스루 데이·50일선 회복 등 신호 (참고용, 봇 동작엔 영향 없음).
  python regime.py   VIX·외국인 수급·KODEX 200 갱신 + 최신 판정과 국내 뉴스 제목 → ~/kis-bot-data/market_regime.json
"""
import json
import statistics
from datetime import date, datetime, timedelta

from kis import DATA_DIR, KIS

VIX_FILE, FLOW_FILE, REGIME = DATA_DIR / "vix.json", DATA_DIR / "kr_flows.json", DATA_DIR / "market_regime.json"
KR_INDEX = DATA_DIR / "kr_index.json"
VIX_CAUTION, VIX_STOP = 25, 30
INDEX_DROP_STOP = -0.03
FLOW_Z_CAUTION, FLOW_Z_STOP, FLOW_WINDOW = -1.0, -2.0, 60
LEVELS = ("normal", "caution", "stop")


def iso(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def merge(old, new):
    rows = {r["date"]: r for r in old}
    rows.update({r["date"]: r for r in new})
    return sorted(rows.values(), key=lambda r: r["date"])


def fetch_vix(k, pages=6):
    bars, end = [], date.today()
    for _ in range(pages):  # 한 번에 100거래일
        r = k._call("GET", "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice", "FHKST03030100",
                    params={"FID_COND_MRKT_DIV_CODE": "N", "FID_INPUT_ISCD": "VIX",
                            "FID_INPUT_DATE_1": (end - timedelta(days=150)).strftime("%Y%m%d"),
                            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"), "FID_PERIOD_DIV_CODE": "D"})
        new = [{"date": iso(b["stck_bsop_date"]), "close": float(b["ovrs_nmix_prpr"])}
               for b in r.get("output2") or [] if b.get("stck_bsop_date")]
        new = [b for b in new if not bars or b["date"] < bars[-1]["date"]]
        if not new:
            break
        bars += new
        end = date.fromisoformat(bars[-1]["date"]) - timedelta(days=1)
    return bars


def kr_session_done():
    """국내 장 마감(15:30) 전이면 오늘 데이터는 장중 값이라 판정에서 제외."""
    return datetime.now().strftime("%H%M") >= "1540"


def reentry_status(idx, extra):
    """하락 구간 여부와 재진입 신호 현황 한 줄 (reentry_backtest와 같은 규칙)."""
    from reentry_backtest import find_episodes, ma50
    idx = sorted(idx, key=lambda b: b["date"])
    eps = find_episodes(idx, {"breadth": {}, "vix": {}, "flows": {}, **extra})
    gap = idx[-1]["close"] / ma50([b["close"] for b in idx])[-1] - 1
    if eps and "end" not in eps[-1]:
        ep = eps[-1]
        start, low = idx[ep["start"]], idx[ep["low"]]
        sig = ", ".join(f"{s} {idx[i]['date']}" for s, i in sorted(ep["first"].items())) or "아직 없음"
        return (f"하락 구간 진행 중 ({start['date']}부터, 저점 {low['date']} {low['close'] / start['close'] - 1:+.1%}, "
                f"현재 50일선 대비 {gap:+.1%}). 재진입 신호: {sig}")
    if eps and len(idx) - 1 - eps[-1]["end"] <= 20:
        return f"하락 구간 종료: {idx[eps[-1]['end']]['date']} 50일선 회복 (현재 50일선 대비 {gap:+.1%})"
    return f"하락 구간 아님 (50일선 대비 {gap:+.1%})"


def fetch_flows(k):
    """코스피 시장 투자자별 매매동향(일별, 약 300거래일). foreign 단위: 백만원."""
    today = date.today().strftime("%Y%m%d")
    done = kr_session_done()
    r = k._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market", "FHPTJ04040000",
                params={"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001", "FID_INPUT_DATE_1": today,
                        "FID_INPUT_ISCD_1": "KSP", "FID_INPUT_DATE_2": today, "FID_INPUT_ISCD_2": "0001"})
    return [{"date": iso(x["stck_bsop_date"]), "foreign": int(x["frgn_ntby_tr_pbmn"]),
             "institution": int(x["orgn_ntby_tr_pbmn"]), "index_ret": float(x["bstp_nmix_prdy_ctrt"]) / 100}
            for x in r.get("output") or [] if x.get("stck_bsop_date") and (done or x["stck_bsop_date"] != today)]


def kr_news(k, n=20):
    r = k._call("GET", "/uapi/domestic-stock/v1/quotations/news-title", "FHKST01011800",
                params={"FID_NEWS_OFER_ENTP_CODE": "", "FID_COND_MRKT_CLS_CODE": "", "FID_INPUT_ISCD": "",
                        "FID_TITL_CNTT": "", "FID_INPUT_DATE_1": "", "FID_INPUT_HOUR_1": "",
                        "FID_RANK_SORT_CLS_CODE": "", "FID_INPUT_SRNO": ""})
    return [f"{x['data_dt'][4:6]}/{x['data_dt'][6:]} {x['data_tm'][:2]}:{x['data_tm'][2:4]} "
            f"[{x.get('dorg', '')}] {x['hts_pbnt_titl_cntt']}" for x in (r.get("output") or [])[:n]]


def us_levels():
    """날짜 → (판정, 이유 목록). 백테스트와 trader가 같은 규칙을 씀."""
    spy = sorted(load(DATA_DIR / "spy.json"), key=lambda b: b["date"])
    spy_ret = {b["date"]: b["close"] / a["close"] - 1 for a, b in zip(spy, spy[1:])}
    out = {}
    for b in load(VIX_FILE):
        v, r = b["close"], spy_ret.get(b["date"])
        reasons = [f"VIX {v:.1f} ≥ {VIX_STOP}"] if v >= VIX_STOP else []
        if r is not None and r <= INDEX_DROP_STOP:
            reasons.append(f"SPY 하루 {r:+.1%}")
        if reasons:
            out[b["date"]] = ("stop", reasons)
        elif v >= VIX_CAUTION:
            out[b["date"]] = ("caution", [f"VIX {v:.1f} ≥ {VIX_CAUTION}"])
        else:
            out[b["date"]] = ("normal", [f"VIX {v:.1f}"])
    return out


def kr_levels():
    flows = load(FLOW_FILE)
    out = {}
    for i, f in enumerate(flows):
        prev = [x["foreign"] for x in flows[max(0, i - FLOW_WINDOW):i]]
        z = (f["foreign"] - statistics.mean(prev)) / statistics.stdev(prev) if len(prev) >= 20 else 0.0
        reasons = [f"외국인 순매수 {f['foreign'] / 1e6:+.2f}조원 (60일 대비 {z:+.1f}σ)"]
        if f["index_ret"] <= INDEX_DROP_STOP:
            out[f["date"]] = ("stop", [f"코스피 하루 {f['index_ret']:+.1%}"] + reasons)
        elif z <= FLOW_Z_STOP:
            out[f["date"]] = ("stop", reasons)
        else:
            out[f["date"]] = ("caution" if z <= FLOW_Z_CAUTION else "normal", reasons)
    return out


def selftest():
    global VIX_FILE, FLOW_FILE
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    saved = VIX_FILE, FLOW_FILE
    VIX_FILE, FLOW_FILE = tmp / "vix.json", tmp / "flows.json"
    VIX_FILE.write_text(json.dumps([{"date": "2026-01-01", "close": 20}, {"date": "2026-01-02", "close": 26},
                                    {"date": "2026-01-03", "close": 31}]))
    lv = us_levels()
    assert [lv[d][0] for d in ("2026-01-01", "2026-01-02", "2026-01-03")] == ["normal", "caution", "stop"]
    flows = [{"date": f"2026-02-{i + 1:02d}", "foreign": 100 * (-1) ** i, "index_ret": 0.0} for i in range(25)]
    flows += [{"date": "2026-02-26", "foreign": -1000, "index_ret": 0.0},
              {"date": "2026-02-27", "foreign": 0, "index_ret": -0.05}]
    FLOW_FILE.write_text(json.dumps(flows))
    lv = kr_levels()
    assert lv["2026-02-26"][0] == "stop" and lv["2026-02-27"][0] == "stop" and lv["2026-02-10"][0] == "normal"
    VIX_FILE, FLOW_FILE = saved
    closes = [100] * 55 + [94, 90, 86]
    bars = [{"date": f"d{i:03d}", "open": c, "close": c, "volume": 1} for i, c in enumerate(closes)]
    assert reentry_status(bars, {}).startswith("하락 구간 진행 중")
    assert reentry_status(bars[:55], {}).startswith("하락 구간 아님")
    print("regime selftest OK")


if __name__ == "__main__":
    selftest()
    k = KIS()
    VIX_FILE.write_text(json.dumps(merge(load(VIX_FILE), fetch_vix(k, pages=6 if not VIX_FILE.exists() else 1))))
    FLOW_FILE.write_text(json.dumps(merge(load(FLOW_FILE), fetch_flows(k))))
    from fetch_history_kr import daily as kr_daily
    kidx = merge(load(KR_INDEX), kr_daily(k, "069500", date.today().isoformat()))
    if not kr_session_done():
        kidx = [b for b in kidx if b["date"] != date.today().isoformat()]
    KR_INDEX.write_text(json.dumps(kidx))
    us, kr = us_levels(), kr_levels()
    du, dk = max(us), max(kr)
    old = json.loads(REGIME.read_text(encoding="utf-8")) if REGIME.exists() else {}
    regime = {"updated": datetime.now().isoformat(timespec="minutes"),
              "us": {"date": du, "level": us[du][0], "reasons": us[du][1]},
              "kr": {"date": dk, "level": kr[dk][0], "reasons": kr[dk][1]},
              "kr_news": kr_news(k)}
    regime["us"]["reentry"] = reentry_status(load(DATA_DIR / "spy.json"),
                                             {"vix": {b["date"]: b["close"] for b in load(VIX_FILE)}})
    regime["kr"]["reentry"] = reentry_status(kidx, {"flows": {f["date"]: f["foreign"] for f in load(FLOW_FILE)}})
    for m in ("us", "kr"):  # 같은 날짜에 이미 Claude가 남긴 뉴스 판정은 유지
        if old.get(m, {}).get("date") == regime[m]["date"]:
            regime[m].update({x: old[m][x] for x in ("news_level", "news_summary") if x in old[m]})
    REGIME.write_text(json.dumps(regime, ensure_ascii=False, indent=1), encoding="utf-8")
    for m, name in (("us", "미국"), ("kr", "국내")):
        print(f"{name} {regime[m]['date']}: {regime[m]['level']}  " + " / ".join(regime[m]["reasons"]))
        print(f"  재진입: {regime[m]['reentry']}")
