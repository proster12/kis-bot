"""모의투자 연결 확인.
  python smoke_test.py          토큰, 현재가, 잔고 조회
  python smoke_test.py --order  + 체결 안 될 가격으로 1주 매수 후 즉시 취소 (미국 장중에만)
"""
import sys

from kis import KIS

k = KIS()
assert k.mode == "paper", "이 테스트는 모의투자(KIS_MODE=paper)에서만 실행합니다"
print("1) 토큰 OK")

p = k.price("AAPL")
print("2) AAPL 현재가:", p)

holdings, summary = k.balance()
print(f"3) 잔고 조회 OK, 보유종목 {len(holdings)}개")
for h in holdings:
    print("   ", h.get("ovrs_pdno"), h.get("ovrs_cblc_qty"), "주")

if "--order" in sys.argv:
    limit = round(p * 0.5, 2)  # 현재가의 절반: 체결되지 않게
    odno = k.order("buy", "AAPL", 1, limit)
    print(f"4) 매수 주문 접수: 주문번호 {odno}, 지정가 ${limit}")
    k.cancel(odno, "AAPL", 1)
    print("5) 주문 취소 OK")

print("모든 확인 완료")
