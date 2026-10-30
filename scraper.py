"""공지사항/공모전 → 디스코드 알림 봇

새 글이 올라오면 디스코드 채널로 알려줍니다.
GitHub Actions에서 1시간마다 자동 실행되도록 설계되어 있어요.

글 찾는 방식: 페이지 안의 모든 링크 중 '글 주소 패턴'에 맞는 것만 골라냅니다.
사이트 디자인(클래스 이름)이 바뀌어도 글 주소 형식만 같으면 계속 동작해요.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ================== 설정 (여기만 고치면 됩니다) ==================
BOARDS = [
    {
        "name": "경영경제융합학부 공지",
        "url": "https://sobe.ulsan.ac.kr/sobe/5631",
        # 글 링크 예: .../sobe/5631?action=view&no=282518  → 글 번호 = no
        "post_pattern": r"action=view.*?[?&]no=(\d+)|[?&]no=(\d+).*?action=view",
        "keywords": [],  # 비우면 모든 새 글 알림
        "color": 0x1E5AA8,
    },
    # 게시판을 더 추가하려면 위 블록을 복사해서 붙여넣으세요.
]
SEEN_FILE = Path(__file__).parent / "seen.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
# =================================================================


def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_title(text, prefixes):
    title = " ".join(text.split())
    for p in prefixes:
        if title.startswith(p):
            title = title[len(p):].strip()
    return title


def parse_posts(html, board):
    """페이지 HTML에서 글 목록(번호, 제목, 링크)을 뽑아냅니다. 화면 위쪽 글이 먼저."""
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(board["post_pattern"])
    prefixes = board.get("strip_prefix", [])

    posts = {}  # 글 번호 → 정보 (고정 공지가 두 번 나와도 한 번만)
    for a in soup.find_all("a", href=True):
        link = urljoin(board["url"], a["href"])
        m = pattern.search(link)
        if not m:
            continue
        post_id = next(g for g in m.groups() if g)
        title = clean_title(a.get_text(" "), prefixes)
        if post_id not in posts:
            posts[post_id] = {"id": post_id, "title": title, "link": link}
        elif len(title) > len(posts[post_id]["title"]):
            # 같은 글 링크가 여러 개(썸네일 + 제목)면 글자가 가장 긴 걸 제목으로
            posts[post_id]["title"] = title
    return [p for p in posts.values() if p["title"]]


def fetch_posts(board):
    res = requests.get(board["url"], headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = res.apparent_encoding  # 한글 깨짐 방지
    return parse_posts(res.text, board)


def send_discord(webhook, board, post):
    payload = {
        "embeds": [{
            "author": {"name": f"📢 {board['name']}"},
            "title": post["title"][:256],
            "url": post["link"],
            "color": board.get("color", 0x5865F2),
        }]
    }
    for _ in range(3):
        r = requests.post(webhook, json=payload, timeout=10)
        if r.status_code == 429:  # 너무 빨리 보내면 잠깐 대기 후 재시도
            time.sleep(float(r.json().get("retry_after", 2)))
            continue
        r.raise_for_status()
        break
    time.sleep(1)


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("※ DISCORD_WEBHOOK_URL이 없어서 알림은 보내지 않고 확인만 합니다.")
    seen = load_seen()
    failed = []

    for board in BOARDS:
        name = board["name"]
        try:
            posts = fetch_posts(board)
        except Exception as e:
            print(f"[{name}] 불러오기 실패: {e}")
            failed.append(name)
            continue

        print(f"[{name}] 글 {len(posts)}개 확인")
        if not posts:
            print(f"  ⚠️ 글을 하나도 못 찾았어요. 사이트 구조가 바뀌었을 수 있어요.")
            failed.append(name)
            continue

        first_run = name not in seen
        known = set(seen.get(name, []))
        new_posts = [p for p in posts if p["id"] not in known]

        if first_run:
            # 첫 실행엔 기존 글을 '이미 본 글'로만 저장 (알림 폭탄 방지)
            print(f"  첫 실행: 기존 글 {len(posts)}개 저장, 알림은 보내지 않음")
        else:
            kw = board.get("keywords") or []
            for p in reversed(new_posts):  # 오래된 글부터 순서대로
                if kw and not any(k in p["title"] for k in kw):
                    continue
                print(f"  새 글: {p['title']}")
                if webhook:
                    send_discord(webhook, board, p)

        # 지금 목록 + 예전에 본 글, 최근 500개만 기억
        current = [p["id"] for p in posts]
        seen[name] = (current + [i for i in seen.get(name, []) if i not in set(current)])[:500]

    save_seen(seen)
    if failed and len(failed) == len(BOARDS):
        raise SystemExit("모든 게시판 확인에 실패했어요.")


if __name__ == "__main__":
    main()
