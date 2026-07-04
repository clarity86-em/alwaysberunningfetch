#!/usr/bin/env python3
"""alwaysberunningfetch 파이프라인 진입점.

    python src/main.py              # ABR에서 대회/엔트리 fetch -> 통계 -> docs/ 사이트 생성
    python src/main.py --offline    # 네트워크 없이 캐시(data/)만으로 사이트 재생성
    python src/main.py --probe      # API 응답의 실제 필드 구조를 출력 (스키마 확인용)
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import abr
from sitegen import build_site
from stats import tournament_stats

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config" / "settings.yaml"


def load_settings():
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_abr_date(s):
    """ABR 날짜 문자열 파싱 ('2026.01.17.' 등). 실패하면 None."""
    if not s:
        return None
    s = str(s).strip().strip(".")
    for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def select_tournaments(results, settings):
    season_start = date.fromisoformat(str(settings.get("season_start", "1970-01-01")))
    formats = {f.lower() for f in settings.get("formats", [])}
    exclude = {t.lower() for t in settings.get("exclude_types", [])}
    min_players = settings.get("min_players", 0)
    selected, skipped = [], {"date": 0, "format": 0, "type": 0, "players": 0}
    for t in results:
        d = parse_abr_date(t.get("date"))
        if d is None or d < season_start:
            skipped["date"] += 1
            continue
        if formats and (t.get("format") or "").lower() not in formats:
            skipped["format"] += 1
            continue
        if (t.get("type") or "").lower() in exclude:
            skipped["type"] += 1
            continue
        players = t.get("players_count") or 0
        if players < min_players:
            skipped["players"] += 1
            continue
        selected.append(t)
    print(f"대회 {len(results)}개 중 {len(selected)}개 선택 (제외: {skipped})")
    return selected


def probe_matchdata(settings):
    """matchdata가 있는 대회에 대해 후보 엔드포인트를 시도해 스키마를 파악한다."""
    import requests as rq

    results = abr.fetch_results(limit=50)
    targets = [t for t in results if t.get("matchdata") and (t.get("players_count") or 0) >= 8][:3]
    if not targets:
        print("matchdata=true 대회를 찾지 못함")
        return 0
    candidates = [
        "https://alwaysberunning.net/api/matchdata?id={id}",
        "https://alwaysberunning.net/api/tournaments/matchdata?id={id}",
        "https://alwaysberunning.net/tjsons/{id}.json",
        "https://alwaysberunning.net/api/entries?id={id}&matchdata=1",
    ]
    for t in targets:
        print(f"\n===== 대회 {t['id']} ({t.get('title')}) players={t.get('players_count')} =====")
        for url_tpl in candidates:
            url = url_tpl.format(id=t["id"])
            try:
                r = rq.get(url, headers=abr.HEADERS, timeout=30)
                ctype = r.headers.get("content-type", "")
                print(f"\n--- GET {url} -> {r.status_code} ({ctype})")
                if r.status_code == 200:
                    body = r.text
                    print(body[:2500])
            except Exception as e:
                print(f"\n--- GET {url} -> 예외: {e}")
    return 0


def probe(settings):
    """API 실제 응답의 키 구조를 출력 — 스키마 검증/디버그용."""
    results = abr.fetch_results(limit=10)
    print(f"== /api/tournaments/results (총 {len(results)}건) ==")
    if results:
        print(json.dumps(results[0], ensure_ascii=False, indent=2)[:3000])
        types = sorted({(t.get("type") or "?") for t in results})
        print(f"type 값들: {types}")
    for t in results:
        if (t.get("players_count") or 0) >= 8:
            entries = abr.fetch_entries(t["id"], refresh=True)
            print(f"\n== /api/entries?id={t['id']} ({t.get('title')}) — {len(entries)}건 ==")
            if entries:
                print(json.dumps(entries[0], ensure_ascii=False, indent=2)[:3000])
                keys = sorted({k for e in entries for k in e})
                print(f"엔트리 키 전체: {keys}")
            break
    return 0


def run(offline=False):
    settings = load_settings()
    results = abr.fetch_results(limit=settings.get("scan_limit", 200), offline=offline)
    selected = select_tournaments(results, settings)

    # 최근 종료 대회는 결과가 늦게 갱신될 수 있어 2주간은 다시 fetch
    refresh_after = date.today() - timedelta(days=14)
    per_tournament = []
    want_titles = set()
    raw = []
    for t in selected:
        d = parse_abr_date(t.get("date"))
        refresh = bool(d and d >= refresh_after)
        try:
            entries = abr.fetch_entries(t["id"], refresh=refresh and not offline, offline=offline)
        except Exception as e:
            print(f"경고: 대회 {t['id']} 엔트리 fetch 실패: {e}", file=sys.stderr)
            entries = abr.fetch_entries(t["id"], offline=True)
        if not entries:
            continue
        raw.append((t, entries))
        for e in entries:
            for side in ("corp", "runner"):
                title = e.get(f"{side}_deck_identity_title")
                if title:
                    want_titles.add(title)

    id_map = abr.identity_map(offline=offline, want_titles=want_titles)
    for t, entries in raw:
        per_tournament.append(tournament_stats(t, entries, id_map))

    if not per_tournament:
        print("통계를 낼 대회가 없습니다 (필터를 확인하세요)", file=sys.stderr)
        return 1

    index = build_site(per_tournament, settings)
    print(f"사이트 생성 완료: {index} (대회 {len(per_tournament)}개)")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="네트워크 없이 캐시만 사용")
    parser.add_argument("--probe", action="store_true", help="API 응답 구조 출력")
    parser.add_argument("--probe-matchdata", action="store_true", help="matchdata 엔드포인트 탐색")
    args = parser.parse_args()
    if args.probe:
        sys.exit(probe(load_settings()))
    if args.probe_matchdata:
        sys.exit(probe_matchdata(load_settings()))
    sys.exit(run(offline=args.offline))


if __name__ == "__main__":
    main()
