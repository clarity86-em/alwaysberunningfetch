#!/usr/bin/env python3
"""alwaysberunningfetch — 등록된 사이트를 fetch해서 변화를 감지한다.

사용법:
    python src/fetch.py                # config/sites.yaml의 모든 사이트 실행
    python src/fetch.py --site NAME    # 특정 사이트만 실행

변화가 감지되면:
    - data/snapshots/<name>/<timestamp>.txt 에 스냅샷 저장
    - 환경변수 WEBHOOK_URL이 있으면 Discord/Slack 웹훅으로 알림 전송

종료 코드는 항상 0 (변화 유무는 로그와 data/로 확인). 개별 사이트의
fetch 실패는 다른 사이트 실행을 막지 않는다.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sites.yaml"
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}
TIMEOUT = 30


def load_sites():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    sites = config.get("sites") or []
    for site in sites:
        if not site.get("name") or not site.get("url"):
            raise ValueError(f"sites.yaml 항목에 name과 url이 필요합니다: {site}")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", site["name"]):
            raise ValueError(f"name은 영문/숫자/하이픈만 가능합니다: {site['name']}")
    return sites


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def fetch_site(site):
    """사이트를 가져와 감시 대상 텍스트를 반환한다."""
    headers = {**DEFAULT_HEADERS, **(site.get("headers") or {})}
    resp = requests.get(site["url"], headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    selector = site.get("selector")
    if selector:
        elements = soup.select(selector)
        if not elements:
            raise ValueError(f"선택자 '{selector}'에 해당하는 요소가 없습니다")
        text = "\n---\n".join(el.get_text(" ", strip=True) for el in elements)
    else:
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
    return text


def save_snapshot(name, text, timestamp, digest):
    site_dir = SNAPSHOT_DIR / name
    site_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{digest[:8]}.txt"
    path = site_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


def notify(message):
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        return
    # Discord는 {"content": ...}, Slack은 {"text": ...} — 둘 다 넣으면 각자 자기 것만 읽는다.
    payload = {"content": message, "text": message}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        print("  알림 전송 완료")
    except requests.RequestException as e:
        print(f"  알림 전송 실패: {e}", file=sys.stderr)


def run(only_site=None):
    sites = load_sites()
    if only_site:
        sites = [s for s in sites if s["name"] == only_site]
        if not sites:
            print(f"'{only_site}' 사이트가 config/sites.yaml에 없습니다", file=sys.stderr)
            return 1

    state = load_state()
    now = datetime.now(timezone.utc)
    changed = []

    for site in sites:
        name = site["name"]
        print(f"[{name}] {site['url']}")
        try:
            text = fetch_site(site)
        except Exception as e:
            print(f"  실패: {e}", file=sys.stderr)
            continue

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        previous = state.get(name, {}).get("hash")

        if digest == previous:
            print("  변화 없음")
        else:
            label = "변화 감지!" if previous else "최초 실행 — 기준 스냅샷 저장"
            print(f"  {label}")
            path = save_snapshot(name, text, now, digest)
            print(f"  스냅샷: {path.relative_to(ROOT)}")
            if previous:
                changed.append(name)

        state[name] = {
            "hash": digest,
            "url": site["url"],
            "last_checked": now.isoformat(),
        }

    save_state(state)

    if changed:
        names = ", ".join(changed)
        notify(f"🔔 사이트 변화 감지: {names} ({now.strftime('%Y-%m-%d %H:%M UTC')})")
    return 0


def main():
    parser = argparse.ArgumentParser(description="등록된 사이트를 fetch해서 변화를 감지")
    parser.add_argument("--site", help="이 이름의 사이트만 실행")
    args = parser.parse_args()
    sys.exit(run(only_site=args.site))


if __name__ == "__main__":
    main()
