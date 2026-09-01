"""alwaysberunning.net(ABR) + NetrunnerDB API 클라이언트.

ABR API (https://alwaysberunning.net/apidoc):
  - GET /api/tournaments/results?limit=N   종료된 대회 목록 (최신순)
  - GET /api/entries?id=T                  대회 참가 엔트리 (순위/identity/덱리스트)

NetrunnerDB API:
  - GET /api/2.0/public/cards              카드 데이터 (identity -> faction 매핑용)

응답은 data/ 아래에 캐시된다. --offline 실행 시 캐시/픽스처만 사용.
"""

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TOURNAMENTS_DIR = DATA_DIR / "tournaments"
MATCHES_DIR = DATA_DIR / "matches"
RESULTS_CACHE = DATA_DIR / "results.json"
NRDB_CACHE = DATA_DIR / "nrdb_identities.json"

ABR_BASE = "https://alwaysberunning.net"
NRDB_CARDS_URL = "https://netrunnerdb.com/api/2.0/public/cards"
NRDB_MWL_URL = "https://netrunnerdb.com/api/2.0/public/mwl"
NRDB_PACKS_URL = "https://netrunnerdb.com/api/2.0/public/packs"
NRDB_DECKLIST_V2 = "https://netrunnerdb.com/api/2.0/public/decklist/{}"
NRDB_DECKLIST_V3 = "https://netrunnerdb.com/api/v3/public/decklists/{}"
MWL_CACHE = DATA_DIR / "nrdb_mwl.json"
PACKS_CACHE = DATA_DIR / "nrdb_packs.json"
CARD_INDEX_CACHE = DATA_DIR / "nrdb_card_index.json"
DECKLISTS_DIR = DATA_DIR / "decklists"

_cards_memo = None  # 같은 실행 안에서 NRDB 전체 카드 fetch는 1번만

HEADERS = {"User-Agent": "alwaysberunningfetch (github.com/clarity86-em/alwaysberunningfetch)"}
TIMEOUT = 60
REQUEST_DELAY = 1.0  # ABR에 부담을 주지 않도록 요청 간 간격(초)

_last_request = [0.0]


def _get_json(url, params=None):
    wait = REQUEST_DELAY - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    _last_request[0] = time.monotonic()
    resp.raise_for_status()
    return resp.json()


def _read_cache(path):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _write_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def fetch_results(limit=200, offline=False):
    """종료된 대회 목록 (누적).

    ABR API는 최근 limit개만 주므로, 캐시와 병합해서 한 번 본 대회는
    목록 뒤로 밀려나도 계속 유지한다 (최신 응답이 기존 항목을 갱신).
    """
    cached = _read_cache(RESULTS_CACHE) or []
    if offline:
        if not cached:
            raise RuntimeError("대회 목록 캐시가 없습니다 (data/results.json)")
        return cached
    try:
        data = _get_json(f"{ABR_BASE}/api/tournaments/results", {"limit": limit})
    except requests.RequestException as e:
        print(f"경고: 대회 목록 fetch 실패, 캐시 사용: {e}", file=sys.stderr)
        if not cached:
            raise
        return cached
    by_id = {t["id"]: t for t in cached if t.get("id") is not None}
    for t in data:
        if t.get("id") is not None:
            by_id[t["id"]] = t
    merged = sorted(by_id.values(), key=lambda t: t.get("date") or "", reverse=True)
    _write_cache(RESULTS_CACHE, merged)
    return merged


def fetch_entries(tournament_id, refresh=False, offline=False):
    """대회 엔트리 목록. 캐시가 있으면 재사용(refresh=True면 다시 fetch)."""
    cache_path = TOURNAMENTS_DIR / f"{tournament_id}.json"
    cached = _read_cache(cache_path)
    if cached is not None and not refresh:
        return cached
    if offline:
        return cached  # 캐시 없으면 None — 호출자가 스킵
    data = _get_json(f"{ABR_BASE}/api/entries", {"id": tournament_id})
    _write_cache(cache_path, data)
    return data


def fetch_tjson(tournament_id, refresh=False, offline=False):
    """대회의 NRTM 토너먼트 JSON (라운드별 경기 결과 포함, /tjsons/<id>.json).

    matchdata가 없는 대회는 404 -> {"unavailable": True} 마커를 캐시하고 None 반환.
    """
    cache_path = MATCHES_DIR / f"{tournament_id}.json"
    cached = _read_cache(cache_path)
    if cached is not None and not refresh:
        return None if cached.get("unavailable") else cached
    if offline:
        return None if (cached is None or cached.get("unavailable")) else cached
    try:
        data = _get_json(f"{ABR_BASE}/tjsons/{tournament_id}.json")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            _write_cache(cache_path, {"unavailable": True})
            return None
        raise
    _write_cache(cache_path, data)
    return data


def fetch_nrdb_mwl(offline=False):
    """NRDB 공식 밴리스트 목록 [{name, date_start, banned}] — 발효일/밴 카드 동기화용."""
    if not offline:
        try:
            data = _get_json(NRDB_MWL_URL)
            entries = []
            for m in data.get("data") or []:
                banned = [
                    code
                    for code, rule in (m.get("cards") or {}).items()
                    if isinstance(rule, dict) and rule.get("deck_limit") == 0
                ]
                entries.append(
                    {
                        "name": m.get("name") or "",
                        "date_start": m.get("date_start") or "",
                        "banned": banned,
                    }
                )
            if entries:
                _write_cache(MWL_CACHE, entries)
                return entries
        except requests.RequestException as e:
            print(f"경고: NRDB mwl fetch 실패, 캐시 사용: {e}", file=sys.stderr)
    return _read_cache(MWL_CACHE) or []


def fetch_nrdb_packs(offline=False):
    """NRDB 팩 목록 {이름: 출시일} — 새 확장 출시일 자동 감지용."""
    if not offline:
        try:
            data = _get_json(NRDB_PACKS_URL)
            packs = {
                p["name"]: p.get("date_release") or ""
                for p in (data.get("data") or [])
                if p.get("name")
            }
            if packs:
                _write_cache(PACKS_CACHE, packs)
                return packs
        except requests.RequestException as e:
            print(f"경고: NRDB packs fetch 실패, 캐시 사용: {e}", file=sys.stderr)
    return _read_cache(PACKS_CACHE) or {}


def _fetch_all_cards(offline=False):
    """NRDB 전체 카드 목록 (실행당 1회 fetch, 메모이즈). 실패/오프라인 -> None."""
    global _cards_memo
    if _cards_memo is not None:
        return _cards_memo
    if offline:
        return None
    try:
        data = _get_json(NRDB_CARDS_URL)
        _cards_memo = data.get("data") or []
        return _cards_memo
    except requests.RequestException as e:
        print(f"경고: NRDB cards fetch 실패: {e}", file=sys.stderr)
        return None


def identity_map(offline=False, want_titles=()):
    """NRDB 카드 데이터에서 identity 제목 -> {faction, side} 매핑을 만든다.

    캐시에 없는 identity(want_titles)가 있으면 온라인일 때 갱신을 시도한다.
    """
    cached = _read_cache(NRDB_CACHE) or {}
    missing = [t for t in want_titles if t and t not in cached]
    if offline or (cached and not missing):
        return cached
    data = _fetch_all_cards(offline)
    if data is None:
        return cached
    mapping = {}
    for card in data:
        if card.get("type_code") == "identity":
            mapping[card["title"]] = {
                "faction": card.get("faction_code", "unknown"),
                "side": card.get("side_code", "unknown"),
            }
    if mapping:
        _write_cache(NRDB_CACHE, mapping)
        return mapping
    return cached


def card_index(offline=False, want_codes=()):
    """카드 코드 -> {title, type, faction} 인덱스 (카드 통계 표시용)."""
    cached = _read_cache(CARD_INDEX_CACHE) or {}
    missing = [c for c in want_codes if c not in cached]
    # 구버전 캐시(팩션 없음)면 온라인일 때 갱신
    stale = bool(cached) and "faction" not in next(iter(cached.values()), {})
    if offline or (cached and not missing and not stale):
        return cached
    data = _fetch_all_cards(offline)
    if data is None:
        return cached
    idx = {}
    for c in data:
        if not c.get("code"):
            continue
        faction = c.get("faction_code") or "unknown"
        if faction == "neutral":
            faction = f"neutral-{c.get('side_code') or 'corp'}"
        idx[c["code"]] = {
            "title": c.get("title") or "",
            "type": c.get("type_code") or "",
            "faction": faction,
        }
    if idx:
        _write_cache(CARD_INDEX_CACHE, idx)
        return idx
    return cached


def _get_text(url):
    """HTML 페이지 fetch (JSON 아님) — _get_json과 같은 속도 제한 공유."""
    wait = REQUEST_DELAY - (time.monotonic() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    _last_request[0] = time.monotonic()
    return resp


COBRA_DECKS_DIR = DATA_DIR / "cobra_decks"


def cobra_tournament_url(tjson):
    """tjson의 uploadedfrom 링크에서 Cobra 대회 URL 추출. 없으면 None."""
    for link in (tjson or {}).get("links") or []:
        href = link.get("href") or ""
        if link.get("rel") == "uploadedfrom" and "/tournaments/" in href:
            return href.rstrip("/")
    return None


def parse_cobra_decks(html_text):
    """Cobra view_decks 페이지에서 corp/runner 덱 JSON 추출.

    페이지에 <input id="corp_deck" value="{...json...}"> 형태로 덱 전체가
    들어 있다 (HTML 이스케이프됨). 반환: {"corp": deck|None, "runner": deck|None}
    덱 입력 자체가 없으면 None (비공개/권한 없음).
    """
    import html as _html

    out, found = {}, False
    for side in ("corp", "runner"):
        m = re.search(
            rf"id=[\"']{side}_deck[\"'][^>]*value=[\"'](.*?)[\"']\s*/?>",
            html_text,
            re.S,
        )
        if not m:
            # value가 id보다 앞에 오는 속성 순서도 시도
            m = re.search(
                rf"value=[\"'](.*?)[\"'][^>]*id=[\"']{side}_deck[\"']",
                html_text,
                re.S,
            )
        deck = None
        if m:
            found = True
            raw = _html.unescape(m.group(1)).strip()
            if raw and raw not in ("null", "{}"):
                try:
                    deck = json.loads(raw)
                except ValueError:
                    deck = None
        out[side] = deck
    return out if found else None


def fetch_cobra_viewable(cobra_url, refresh=False, offline=False):
    """Cobra 대회에서 덱이 공개된 플레이어 id 목록 (standings_data 기반).

    대회당 요청 1건. 결과는 캐시되고 refresh=True(최근 대회)일 때만 갱신.
    """
    slug = cobra_url.rstrip("/").split("/")[-1]
    path = COBRA_DECKS_DIR / f"{slug}-index.json"
    cached = _read_cache(path)
    if cached is not None and not refresh:
        return cached.get("viewable") or []
    if offline:
        return (cached or {}).get("viewable") or []
    try:
        resp = _get_text(f"{cobra_url}/players/standings_data")
        data = resp.json() if resp.status_code == 200 else {}
    except (requests.RequestException, ValueError) as e:
        print(f"경고: cobra standings_data {slug} 실패: {e}", file=sys.stderr)
        return (cached or {}).get("viewable") or []
    viewable = []
    for stage in data.get("stages") or []:
        for s in stage.get("standings") or []:
            if (s.get("policy") or {}).get("view_decks"):
                pid = (s.get("player") or {}).get("id")
                if pid is not None and pid not in viewable:
                    viewable.append(pid)
    _write_cache(path, {"viewable": viewable, "checked": date.today().isoformat()})
    return viewable


def fetch_cobra_decks(cobra_url, player_id, refresh=False, offline=False):
    """Cobra 공개 덱 fetch: {side: {"identity":..., "cards": {code: qty}}}.

    덱 공개 설정이 아닌 대회/플레이어는 unavailable 마커를 캐시.
    refresh=True(최근 대회)일 때만 마커를 다시 확인한다.
    """
    slug = cobra_url.rstrip("/").split("/")[-1]
    path = COBRA_DECKS_DIR / f"{slug}-{player_id}.json"
    cached = _read_cache(path)
    if cached is not None:
        if not cached.get("unavailable"):
            return cached
        if offline or not refresh:
            return None
    elif offline:
        return None
    try:
        resp = _get_text(f"{cobra_url}/players/{player_id}/view_decks")
    except requests.RequestException as e:
        print(f"경고: cobra 덱 {slug}/{player_id} fetch 실패: {e}", file=sys.stderr)
        return None
    parsed = parse_cobra_decks(resp.text) if resp.status_code == 200 else None
    if not parsed:
        _write_cache(path, {"unavailable": True, "checked": date.today().isoformat()})
        return None
    out = {}
    for side, deck in parsed.items():
        if not deck or not deck.get("cards"):
            continue
        cards = {}
        for c in deck["cards"]:
            code = c.get("nrdb_printing_id")
            qty = c.get("quantity")
            if code is None or not qty:
                continue
            code = str(code).zfill(5)
            cards[code] = cards.get(code, 0) + int(qty)
        if cards:
            out[side] = {
                "identity": (deck.get("details") or {}).get("identity_title") or "",
                "cards": cards,
            }
    if not out:
        _write_cache(path, {"unavailable": True, "checked": date.today().isoformat()})
        return None
    _write_cache(path, out)
    return out


def deck_token(url):
    """덱리스트 URL에서 id/uuid 추출. 없으면 None."""
    m = re.search(r"/decklist/([A-Za-z0-9-]+)", url or "")
    return m.group(1) if m else None


def fetch_decklist(url, offline=False):
    """NRDB 덱리스트의 카드 목록 {code: 수량}. 캐시 영구 (덱리스트는 불변).

    v2 API(숫자 id) 우선, 404면 v3(uuid) 시도. 못 찾으면 marker 캐시 후 None.
    """
    token = deck_token(url)
    if not token:
        return None
    path = DECKLISTS_DIR / f"{token}.json"
    cached = _read_cache(path)
    if cached is not None:
        if not cached.get("unavailable"):
            return cached
        # 비공개/실패 덱은 1주에 한 번만 재시도 (나중에 공개되는 경우 대비)
        if offline:
            return None
        checked = str(cached.get("checked") or "1970-01-01")
        try:
            age = (date.today() - date.fromisoformat(checked)).days
        except ValueError:
            age = 999
        if age < 7:
            return None
    elif offline:
        return None
    for api in (NRDB_DECKLIST_V2, NRDB_DECKLIST_V3):
        try:
            data = _get_json(api.format(token))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (400, 404):
                continue
            raise
        except requests.RequestException as e:
            print(f"경고: 덱리스트 {token} fetch 실패: {e}", file=sys.stderr)
            return None
        cards = None
        d = data.get("data")
        if isinstance(d, list) and d:
            cards = d[0].get("cards")
        elif isinstance(d, dict):
            cards = (d.get("attributes") or {}).get("card_slots")
        if cards:
            out = {"cards": cards}
            _write_cache(path, out)
            return out
    _write_cache(path, {"unavailable": True, "checked": date.today().isoformat()})
    return None
