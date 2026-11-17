"""하락 구간에서 상승 전환(재진입) 신호 비교 백테스트. 지수 ETF 기준(미국 SPY, 국내 KODEX 200).
하락 구간: 지수 종가가 50일선보다 5% 이상 아래로 내려간 날 시작 → 50일선을 회복하면 끝.
구간마다 각 신호가 처음 나온 날의 다음 날 시가에 샀다고 보고 5·20거래일 뒤 수익률, 가짜 신호 비율을 비교.
  ① 50일선 회복  ② 팔로스루 데이(저점 후 4일째 이후 +1.5% 이상, 거래량 증가)
  ③ 시장 폭 반전(50일선 위 종목 비율이 20% 이하였다가 50% 이상)  ④ VIX 30 이상 → 25 미만(미국)
  ⑤ 외국인 3일 연속 순매수(국내)
  python reentry_backtest.py        미국
  python reentry_backtest.py --kr   국내
  --long 을 붙이면 약 20년 지수 데이터(fetch_index_long.py)로 ①② 위주 검증 (③④⑤는 최근 데이터만 있음)
"""
import json
import statistics
import sys
from datetime import date, datetime

from kis import DATA_DIR

DEPTH = 0.05  # 50일선 대비 이만큼 아래면 하락 구간 시작
FTD_GAIN, FTD_MIN_DAY = 0.015, 3
BREADTH_LOW, BREADTH_HIGH = 0.20, 0.50


def load(name):
    p = DATA_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def ma50(closes):
    return [None if i < 49 else sum(closes[i - 49:i + 1]) / 50 for i in range(len(closes))]


def breadth_by_date(history):
    """날짜 → 50일선 위 종목 비율."""
    above, total = {}, {}
    for raw in history.values():
        bars = sorted(raw, key=lambda b: b["date"])
        m = ma50([b["close"] for b in bars])
        for b, v in zip(bars, m):
            if v is not None:
                total[b["date"]] = total.get(b["date"], 0) + 1
                above[b["date"]] = above.get(b["date"], 0) + (b["close"] > v)
    return {d: above[d] / total[d] for d in total if total[d] >= 50}


def find_episodes(idx, extra):
    """하락 구간 목록과 구간마다 신호별 첫 발생일(인덱스), 전체 발생 횟수."""
    closes = [b["close"] for b in idx]
    m = ma50(closes)
    episodes, ep = [], None
    for i in range(50, len(idx)):
        if ep is None and closes[i] < m[i] * (1 - DEPTH):
            ep = {"start": i, "low": i, "first": {}, "count": {}, "breadth_min": 1.0, "vix_max": 0.0, "fpos": 0}
            continue
        if ep is None:
            continue
        d = idx[i]["date"]
        fired = []
        if closes[i] > m[i]:
            fired.append("① 50일선 회복")
        ret = closes[i] / closes[i - 1] - 1
        if i - ep["low"] >= FTD_MIN_DAY and ret >= FTD_GAIN and idx[i]["volume"] > idx[i - 1]["volume"]:
            fired.append("② 팔로스루 데이")
        br = extra["breadth"].get(d)
        if br is not None:
            if ep["breadth_min"] <= BREADTH_LOW and br >= BREADTH_HIGH:
                fired.append("③ 시장 폭 반전")
            ep["breadth_min"] = min(ep["breadth_min"], br)
        vix = extra["vix"].get(d)
        if vix is not None:
            if ep["vix_max"] >= 30 and vix < 25:
                fired.append("④ VIX 진정")
            ep["vix_max"] = max(ep["vix_max"], vix)
        fl = extra["flows"].get(d)
        if fl is not None:
            ep["fpos"] = ep["fpos"] + 1 if fl > 0 else 0
            if ep["fpos"] == 3:
                fired.append("⑤ 외국인 3일 순매수")
        for s in fired:
            ep["first"].setdefault(s, i)
            ep["count"][s] = ep["count"].get(s, 0) + 1
        if closes[i] < closes[ep["low"]]:
            ep["low"] = i
        if closes[i] > m[i]:
            ep["end"] = i
            episodes.append(ep)
            ep = None
    if ep:
        episodes.append(ep)  # 진행 중
    return episodes


def outcome(idx, i, low_close):
    """신호일 i 다음 날 시가 진입 → (5일 수익, 20일 수익, 20일 안에 저점 경신 여부, 저점 대비 진입가)."""
    if i + 1 >= len(idx):
        return None
    entry = idx[i + 1]["open"]
    r = lambda n: idx[i + n]["close"] / entry - 1 if i + n < len(idx) else None  # noqa: E731
    later = [b["close"] for b in idx[i + 1:i + 21]]
    return r(5), r(20), min(later) < low_close, entry / low_close - 1


def selftest():
    # 50일 100 → 10일간 하락(저점 80) → 반등. 구간이 잡히고 ① 회복 신호가 나와야 함
    closes = [100] * 55 + [94, 90, 86, 83, 80, 82, 84, 86, 90, 95, 101, 103]
    idx = [{"date": f"d{i:03d}", "open": c, "close": c, "volume": 100 + i} for i, c in enumerate(closes)]
    eps = find_episodes(idx, {"breadth": {}, "vix": {}, "flows": {}})
    assert len(eps) == 1 and idx[eps[0]["low"]]["close"] == 80
    assert idx[eps[0]["first"]["① 50일선 회복"]]["close"] == 101
    assert "② 팔로스루 데이" in eps[0]["first"]
    print("selftest OK")


def main(kr, long):
    if kr:
        idx = sorted(load("kr_index_long.json" if long else "kr_index.json"), key=lambda b: b["date"])
        if datetime.now().strftime("%H%M") < "1540":  # 장 마감 전 오늘 봉은 미완성
            idx = [b for b in idx if b["date"] != date.today().isoformat()]
        flows = {f["date"]: f["foreign"] for f in load("kr_flows.json") or []}
        extra = {"breadth": breadth_by_date(load("kr_history.json")), "vix": {}, "flows": flows}
        name = "국내 KODEX 200"
    else:
        idx = sorted(load("spy_long.json" if long else "spy.json"), key=lambda b: b["date"])
        vix = {b["date"]: b["close"] for b in load("vix.json") or []}
        extra = {"breadth": breadth_by_date(load("history.json")), "vix": vix, "flows": {}}
        name = "미국 SPY"
    eps = find_episodes(idx, extra)
    print(f"{name} {idx[0]['date']} ~ {idx[-1]['date']}: 하락 구간 {len(eps)}개 (50일선 -{DEPTH:.0%} 이하에서 시작)")
    for ep in eps:
        low = idx[ep["low"]]
        end = idx[ep["end"]]["date"] if "end" in ep else "진행 중"
        print(f"  {idx[ep['start']]['date']} 시작, 저점 {low['date']} ({low['close'] / idx[ep['start']]['close'] - 1:+.1%}), "
              f"끝 {end}  | " + ", ".join(f"{s[:1]} {idx[i]['date'][5:]}" for s, i in sorted(ep["first"].items())))

    # 모든 날 무작위 진입 기준선
    base20 = [idx[i + 20]["close"] / idx[i + 1]["open"] - 1 for i in range(50, len(idx) - 20)]
    print(f"\n{'신호':18}{'구간':>4}{'총발생':>6}{'5일 평균':>9}{'20일 평균':>9}{'20일 승률':>9}{'가짜(저점경신)':>13}{'저점 대비 진입':>13}")
    signals = sorted({s for ep in eps for s in ep["first"]}) + ["(비교) 구간 시작일"]
    for s in signals:
        rows, total = [], 0
        for ep in eps:
            i = ep["start"] if s.startswith("(비교)") else ep["first"].get(s)
            total += 1 if s.startswith("(비교)") else ep["count"].get(s, 0)
            if i is None:
                continue
            # 비교 기준은 시작일 당시까지의 최저가(=시작일 종가), 신호는 신호일까지의 구간 저점
            low_close = min(b["close"] for b in idx[ep["start"]:i + 1])
            o = outcome(idx, i, low_close)
            if o:
                rows.append(o)
        r5 = [o[0] for o in rows if o[0] is not None]
        r20 = [o[1] for o in rows if o[1] is not None]
        fmt = lambda xs: f"{statistics.mean(xs):+.1%}" if xs else "-"  # noqa: E731
        print(f"{s:18}{len(rows):4}{total:6}{fmt(r5):>9}{fmt(r20):>9}"
              f"{(f'{sum(x > 0 for x in r20) / len(r20):.0%}' if r20 else '-'):>9}"
              f"{(f'{sum(o[2] for o in rows)}/{len(rows)}' if rows else '-'):>13}{fmt([o[3] for o in rows]):>13}")
    print(f"{'(비교) 아무 날이나':18}{'':4}{len(base20):6}{'':>9}{statistics.mean(base20):+9.1%}"
          f"{sum(x > 0 for x in base20) / len(base20):9.0%}")
    print("\n가짜 = 진입 후 20일 안에 구간 저점보다 더 내려감. 저점 대비 진입 = 진입가가 그때까지의 저점보다 몇 % 위인지.")


if __name__ == "__main__":
    selftest()
    main("--kr" in sys.argv, "--long" in sys.argv)
