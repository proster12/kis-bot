"""카카오톡 '나에게 보내기' 연결만 시험합니다. 결과는 Actions 실행 화면 맨 위에 표시됩니다."""
# 실행 방법: Actions 탭 → 카카오 알림 테스트 → Run workflow
import sys

from bot import notify

ok = notify.send_kakao("[미국주식 브리핑] 카카오 알림 연결 테스트입니다.", "https://github.com")
sys.exit(0 if ok else 1)
