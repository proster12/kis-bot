"""미국주식 아침 브리핑 + 가상 매매 봇 (실제 주문 없음).

실행: python main.py
"""
import datetime as dt
import logging
import os
from pathlib import Path

import yaml

from bot import insider, news, notify, paper, prices, signals

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("main")

ACTION_KO = {"BUY": "🟢매수후보", "SELL": "🔴매도후보", "HOLD": "⚪관망"}


def build_report(sigs, news_map, insider_map, trades, eq, state, missing):
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# 미국주식 브리핑 ({today})", ""]

    L += ["## 1. 신호", "", "| 종목 | 종가($) | 등락 | RSI | 신호 | 이유 |", "|---|---:|---:|---:|---|---|"]
    for s in sorted(sigs, key=lambda x: ("BUY", "SELL", "HOLD").index(x.action)):
        L.append(f"| {s.ticker} | {s.close:,.2f} | {s.change_pct:+.1f}% | {s.rsi:.0f} | "
                 f"{ACTION_KO[s.action]} | {', '.join(s.reasons) or '-'} |")
    if missing:
        L += ["", f"시세를 못 받은 종목: {', '.join(missing)}"]

    L += ["", "## 2. 내부자 장내 매수 (SEC Form 4)", ""]
    any_buy = False
    for t, ins in insider_map.items():
        for b in ins["buys"]:
            any_buy = True
            L.append(f"- **{t}** {b['date']} {b['owner']}({b['role'] or '내부자'}) "
                     f"{b['shares']:,.0f}주 @ ${b['price']:,.2f} = ${b['value']:,.0f}")
        if ins["sell_count"]:
            L.append(f"- {t}: 장내 매도 {ins['sell_count']}건, 합계 ${ins['sell_value']:,.0f}")
        if ins.get("error"):
            L.append(f"- {t}: 조회 실패 ({ins['error']})")
    if not any_buy:
        L.append("- 기간 내 장내 매수 없음")

    L += ["", "## 3. 뉴스", ""]
    for t, items in news_map.items():
        for n in items:
            L.append(f"- **{t}** [{n['title']}]({n['link']})")

    L += ["", "## 4. 가상 계좌 (모의 운용, 실제 주문 아님)", "",
          f"- 총자산 ${eq['total']:,.2f} (시작 대비 {eq['return_pct']:+.2f}%)",
          f"- 현금 ${eq['cash']:,.2f} / 주식 ${eq['positions_value']:,.2f}"]
    for t, p in state["positions"].items():
        L.append(f"- 보유 {t} {p['shares']}주, 평단 ${p['avg_price']:,.2f} ({p['date']} 매수)")
    if trades:
        L += ["", "오늘 가상 거래:"]
        for tr in trades:
            pnl = f", 손익 ${tr['pnl']:+,.2f}" if tr["pnl"] != "" else ""
            L.append(f"- {tr['side']} {tr['ticker']} {tr['shares']}주 @ ${tr['price']}{pnl} — {tr['reason']}")
    L += ["", "_투자 판단 참고용 자동 보고서입니다._"]
    return "\n".join(L)


def build_short(sigs, insider_map, trades, eq):
    buys = [s.ticker for s in sigs if s.action == "BUY"]
    sells = [s.ticker for s in sigs if s.action == "SELL"]
    ins = sorted({t for t, v in insider_map.items() if v["buys"]})
    parts = ["[미국주식 브리핑]",
             f"매수후보: {', '.join(buys) or '없음'}",
             f"매도후보: {', '.join(sells) or '없음'}",
             f"내부자매수: {', '.join(ins) or '없음'}",
             f"가상계좌 {eq['return_pct']:+.1f}% (${eq['total']:,.0f})"]
    if trades:
        parts.append("가상거래: " + ", ".join(f"{t['side']} {t['ticker']}" for t in trades))
    return "\n".join(parts)


def load_watchlist(cfg):
    """universe.txt 형식(한 줄에 '티커 [거래소]', #은 주석)을 읽고, 없으면 settings의 watchlist."""
    f = cfg.get("watchlist_file")
    if f and Path(f).exists():
        out = []
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line:
                out.append(line.split()[0].upper())
        if out:
            return out
    return cfg["watchlist"]


def run(fetch_prices=prices.fetch_daily, get_news=news.headlines, get_insider=None, send=True):
    cfg = yaml.safe_load(Path("config/settings.yaml").read_text(encoding="utf-8"))
    tickers = load_watchlist(cfg)

    data = fetch_prices(tickers)
    sigs = [s for t, df in data.items() if (s := signals.evaluate(t, df, cfg["signals"]))]
    missing = [t for t in tickers if t not in data]
    if not sigs:
        raise SystemExit("시세를 하나도 받지 못했습니다. 네트워크/데이터 소스를 확인하세요.")

    news_map = {t: get_news(t, cfg["news"]["per_ticker"]) for t in data}

    if get_insider is None:
        client = insider.SecClient()
        get_insider = lambda t: insider.recent_trades(  # noqa: E731
            client, t, cfg["insider"]["lookback_days"], cfg["insider"]["max_filings_per_ticker"])
    insider_map = {t: get_insider(t) for t in data}

    state = paper.load(cfg["paper"])
    trades = paper.step(state, sigs, cfg["paper"])
    paper.save(state)
    eq = paper.equity(state, {s.ticker: s.close for s in sigs})

    report = build_report(sigs, news_map, insider_map, trades, eq, state, missing)
    Path("reports").mkdir(exist_ok=True)
    Path(f"reports/{max(s.date for s in sigs)}.md").write_text(report, encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report)
    print(report)

    if send:
        run_url = "https://github.com"
        if os.getenv("GITHUB_REPOSITORY"):
            run_url = (f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/"
                       f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}")
        short = build_short(sigs, insider_map, trades, eq)
        sent_k = notify.send_kakao(short, run_url)
        sent_t = notify.send_telegram(report)
        log.info("알림 전송 — 카카오:%s 텔레그램:%s", sent_k, sent_t)
    return report


if __name__ == "__main__":
    run()
