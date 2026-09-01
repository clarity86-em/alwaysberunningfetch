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
        fmt = (t.get("format") or "").lower()
        if formats and fmt not in formats:
            skipped["format"] += 1
            continue
        if (t.get("type") or "").lower() in exclude:
            skipped["type"] += 1
            continue
        if isinstance(min_players, dict):
            threshold = min_players.get(fmt, min_players.get("default", 0))
        else:
            threshold = min_players
        players = t.get("players_count") or 0
        if players < threshold:
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


def probe_cobra(settings):
    """Cobra 덱 공개 여부 전수 조사 + 파싱 검증 (수집 활성화 전 사전 확인).

    1) 공개 확인된 대회(5041)로 파서 검증
    2) 캐시된 모든 tjson 대회에 대해 1위 플레이어의 view_decks를 찔러
       덱 공개/비공개를 집계
    """
    import glob
    import re as _re

    # 슬러그형 URL(tjson uploadedfrom)에서 standings_data가 왜 HTML을 주는지 진단
    base = "https://tournaments.nullsignal.games"
    for tid in ("5041", "Q090", "374Y"):
        for suffix, headers in (
            ("/players/standings_data", None),
            ("/players/standings_data", {"Accept": "application/json"}),
        ):
            url = f"{base}/tournaments/{tid}{suffix}"
            try:
                import requests as _rq

                resp = _rq.get(url, headers={**abr.HEADERS, **(headers or {})},
                               timeout=30, allow_redirects=True)
                print(f"GET {tid}{suffix} accept={bool(headers)} -> {resp.status_code} "
                      f"({resp.headers.get('content-type')}) final={resp.url}")
                print(f"  body[:150]: {resp.text[:150]!r}")
            except Exception as e:
                print(f"GET {url} 예외: {e}")

    known = f"{base}/tournaments/5041"
    try:
        r = abr._get_text(f"{known}/players/standings_data")
        print(f"standings_data -> {r.status_code} ({r.headers.get('content-type')})")
        data = r.json()
        pids_open = []
        for stage in data.get("stages") or []:
            print(f"  stage: any_decks_viewable={stage.get('any_decks_viewable')}, "
                  f"standings={len(stage.get('standings') or [])}")
            for s in stage.get("standings") or []:
                pol = s.get("policy") or {}
                if pol.get("view_decks"):
                    pid = (s.get("player") or {}).get("id")
                    if pid and pid not in pids_open:
                        pids_open.append(pid)
        print(f"  view_decks 허용 플레이어: {len(pids_open)}명 -> {pids_open[:5]}")
        for pid in pids_open[:2]:
            for label, url in (
                ("view_decks(HTML)", f"{known}/players/{pid}/view_decks"),
                ("beta decks(JSON)", f"{base}/beta/tournaments/5041/players/{pid}/decks"),
            ):
                r2 = abr._get_text(url)
                body = r2.text
                parsed = abr.parse_cobra_decks(body)
                print(f"  [{label}] {pid} -> {r2.status_code}, {len(body)}b, "
                      f"corp_deck입력={'corp_deck' in body}, 파싱={'OK' if parsed else 'X'}")
                if label.startswith("beta") and r2.status_code == 200:
                    print(f"    body[:400]: {body[:400]}")
                if parsed:
                    for side, deck in parsed.items():
                        if deck:
                            det = deck.get("details") or {}
                            print(f"    {side}: {det.get('identity_title')!r} 카드 {len(deck.get('cards') or [])}종")
    except Exception as e:
        print(f"검증 실패: {e}")
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


def sync_schedules(settings, selected, offline):
    """NRDB 공식 데이터로 밴리스트/카드풀 발효일 표를 자동 보강 (수동 항목 우선)."""
    import re as _re

    # 1) 밴리스트: NRDB mwl의 'Standard Ban List XX.YY' + date_start (+ 밴 카드 목록)
    sched = settings.setdefault("banlist_schedule", {}).setdefault("standard", [])
    manual = {str(e["version"]) for e in sched}
    banned_by_version = {}
    for m in abr.fetch_nrdb_mwl(offline=offline):
        # 'Standard Ban List XX.YY' 또는 'Standard Balance Update XX.YY' (2026.08부터 명칭 변경)
        name_l = (m.get("name") or "").lower()
        if not name_l.startswith("standard") or (
            "ban list" not in name_l and "balance update" not in name_l
        ):
            continue
        name = m.get("name") or ""
        ver = _re.search(r"(\d{2}\.\d{2})", name)
        if not ver:
            continue
        if m.get("banned"):
            banned_by_version[ver.group(1)] = set(m["banned"])
        if m.get("date_start") and ver.group(1) not in manual:
            sched.append({"version": ver.group(1), "from": m["date_start"]})
            print(f"밴리스트 자동 추가: {ver.group(1)} (발효 {m['date_start']})")
    settings["_banned_by_version"] = banned_by_version

    # 2) 카드풀: 대회에 등장했는데 표에 없는 확장 -> NRDB 팩 출시일로 추가
    cp_sched = settings.setdefault("cardpool_schedule", [])
    known = {str(e["cardpool"]) for e in cp_sched}
    observed = {t.get("cardpool") for t in selected if t.get("cardpool")}
    unknown = sorted(observed - known - {"?"})
    if unknown:
        packs = abr.fetch_nrdb_packs(offline=offline)
        for name in unknown:
            release = packs.get(name)
            if release:
                cp_sched.append({"cardpool": name, "from": release})
                print(f"카드풀 자동 추가: {name} (출시 {release})")


def run(offline=False):
    settings = load_settings()
    results = abr.fetch_results(limit=settings.get("scan_limit", 200), offline=offline)
    selected = select_tournaments(results, settings)
    sync_schedules(settings, selected, offline)

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
        tjson = None
        if t.get("matchdata"):
            try:
                tjson = abr.fetch_tjson(t["id"], refresh=refresh and not offline, offline=offline)
            except Exception as e:
                print(f"경고: 대회 {t['id']} matchdata fetch 실패: {e}", file=sys.stderr)
        raw.append((t, entries, tjson))
        for e in entries:
            for side in ("corp", "runner"):
                title = e.get(f"{side}_deck_identity_title")
                if title:
                    want_titles.add(title)

    id_map = abr.identity_map(offline=offline, want_titles=want_titles)
    for t, entries, tjson in raw:
        per_tournament.append(tournament_stats(t, entries, id_map, tjson=tjson))

    # 공개된 덱리스트의 카드 목록 수집/부착 (카드 통계용, 영구 캐시)
    deck_cache, fetched = {}, 0
    for t in per_tournament:
        for d in t.get("decks", []):
            tok = abr.deck_token(d["url"])
            if not tok:
                continue
            if tok not in deck_cache:
                try:
                    deck_cache[tok] = abr.fetch_decklist(d["url"], offline=offline)
                except Exception as e:
                    print(f"경고: 덱리스트 {tok} fetch 실패: {e}", file=sys.stderr)
                    deck_cache[tok] = None
                fetched += 1
                if not offline and fetched % 200 == 0:
                    print(f"덱리스트 처리 중... {fetched}개")
            dl = deck_cache[tok]
            if dl:
                d["cards"] = dl.get("cards") or {}
    with_cards = sum(1 for t in per_tournament for d in t.get("decks", []) if d.get("cards"))
    print(f"덱리스트: 고유 {len(deck_cache)}개, 카드 확보 {with_cards}건")

    # Cobra(NSG 대회 플랫폼) 공개 덱 보완 수집 — NRDB 링크가 없는 플레이어 대상.
    # tjson의 uploadedfrom 링크가 Cobra 대회 URL, players[].id가 Cobra 플레이어 id.
    from stats import norm_title, player_name

    cobra_rows, cobra_ts = 0, 0
    for (t_src, entries, tjson), t in zip(raw, per_tournament):
        if not tjson:
            continue
        curl = abr.cobra_tournament_url(tjson)
        if not curl:
            continue
        d = parse_abr_date(t_src.get("date"))
        refresh = bool(d and d >= refresh_after) and not offline
        try:
            curl = abr.resolve_cobra_url(curl, offline=offline)
            if not curl:
                continue
            viewable = abr.fetch_cobra_viewable(curl, refresh=refresh, offline=offline)
        except Exception as e:
            print(f"경고: cobra {curl} 조회 실패: {e}", file=sys.stderr)
            continue
        if not viewable:
            continue
        # ABR에 NRDB 덱이 이미 있는 (플레이어, 사이드)는 건너뜀
        have = set()
        for e in entries:
            pkey = norm_title(player_name(e)).casefold()
            for side in ("corp", "runner"):
                if e.get(f"{side}_deck_url"):
                    have.add((pkey, side))
        name_of, ident_of = {}, {}
        for p in tjson.get("players") or []:
            if p.get("id") is None:
                continue
            name_of[p["id"]] = p.get("name")
            ident_of[p["id"]] = {
                "corp": p.get("corpIdentity") or "",
                "runner": p.get("runnerIdentity") or "",
            }
        perf_map = (t.get("winrates") or {}).get("players") or {}
        added_here = 0
        for pid in viewable:
            name = name_of.get(pid)
            if not name:
                continue
            pkey = norm_title(str(name)).casefold()
            need = [s for s in ("corp", "runner") if (pkey, s) not in have]
            if not need:
                continue
            try:
                got = abr.fetch_cobra_decks(curl, pid, refresh=refresh, offline=offline)
            except Exception as e:
                print(f"경고: cobra 덱 {curl}/{pid} 실패: {e}", file=sys.stderr)
                continue
            if not got:
                continue
            for side in need:
                deck = got.get(side)
                if not deck or not deck.get("cards"):
                    continue
                perf = (perf_map.get(pkey) or {}).get(side) or {}
                t["decks"].append(
                    {
                        "side": side,
                        "identity": deck.get("identity")
                        or ident_of.get(pid, {}).get(side, ""),
                        "url": f"{curl}/players/{pid}/view_decks",
                        "wins": perf.get("wins", 0.0),
                        "ties": perf.get("ties", 0),
                        "games": perf.get("games", 0),
                        "cards": deck["cards"],
                    }
                )
                added_here += 1
        if added_here:
            cobra_rows += added_here
            cobra_ts += 1
    print(f"Cobra 공개 덱: {cobra_ts}개 대회에서 {cobra_rows}건 보완")

    # 카드 코드 -> 이름/타입 인덱스 (표시용)
    want_codes = set()
    for t in per_tournament:
        for d in t.get("decks", []):
            want_codes.update((d.get("cards") or {}).keys())
    settings["_card_index"] = abr.card_index(offline=offline, want_codes=sorted(want_codes))

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
    parser.add_argument("--probe-cobra", action="store_true", help="Cobra 덱 페이지 파싱 검증")
    args = parser.parse_args()
    if args.probe:
        sys.exit(probe(load_settings()))
    if args.probe_matchdata:
        sys.exit(probe_matchdata(load_settings()))
    if args.probe_cobra:
        sys.exit(probe_cobra(load_settings()))
    sys.exit(run(offline=args.offline))


if __name__ == "__main__":
    main()
