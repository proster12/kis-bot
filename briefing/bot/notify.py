"""알림 보내기: 카카오톡 '나에게 보내기' / 텔레그램. 설정된 것만 보냅니다.

카카오톡 기본 텍스트 메시지는 200자 제한이 있어 요약 + '자세히 보기' 링크로 보내고,
전체 보고서는 GitHub Actions 실행 화면(요약)과 저장소의 reports 폴더에 남깁니다.
"""
import json
import logging
import os

import requests

log = logging.getLogger(__name__)


def _kakao_access_token():
    key = os.getenv("KAKAO_REST_API_KEY")
    refresh = os.getenv("KAKAO_REFRESH_TOKEN")
    if not (key and refresh):
        return None
    data = {"grant_type": "refresh_token", "client_id": key, "refresh_token": refresh}
    if os.getenv("KAKAO_CLIENT_SECRET"):
        data["client_secret"] = os.getenv("KAKAO_CLIENT_SECRET")
    r = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=15)
    r.raise_for_status()
    body = r.json()
    if body.get("refresh_token"):
        # 만료가 1개월 이내로 남으면 새 리프레시 토큰이 발급됩니다 → 시크릿을 바꿔 줘야 함
        log.warning("카카오 리프레시 토큰이 새로 발급됐습니다. GitHub 시크릿 KAKAO_REFRESH_TOKEN을 갱신하세요.")
        print("::warning::카카오 리프레시 토큰 갱신 필요 — 실행 로그의 안내를 확인하세요")
    return body["access_token"]


def send_kakao(short_text, link_url):
    try:
        token = _kakao_access_token()
    except Exception as e:
        log.warning("카카오 토큰 발급 실패: %s", e)
        return False
    if not token:
        return False
    template = {
        "object_type": "text",
        "text": short_text[:200],
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": "자세히 보기",
    }
    r = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    if r.status_code != 200:
        log.warning("카카오 전송 실패: %s %s", r.status_code, r.text)
        return False
    return True


def send_telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    ok = True
    for i in range(0, len(text), 4000):  # 텔레그램 4096자 제한
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text[i:i + 4000],
                                "disable_web_page_preview": "true"}, timeout=15)
        ok &= r.status_code == 200
    return ok
