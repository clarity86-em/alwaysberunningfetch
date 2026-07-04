"""alwaysberunning.net(ABR) + NetrunnerDB API 클라이언트.

ABR API (https://alwaysberunning.net/apidoc):
  - GET /api/tournaments/results?limit=N   종료된 대회 목록 (최신순)
  - GET /api/entries?id=T                  대회 참가 엔트리 (순위/identity/덱리스트)

NetrunnerDB API:
  - GET /api/2.0/public/cards              카드 데이터 (identity -> faction 매핑용)

응답은 data/ 아래에 캐시된다. --offline 실행 시 캐시/픽스처만 사용.
"""

import json
import sys
import time
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
    """종료된 대회 목록. 온라인이면 새로 받아 캐시 갱신."""
    if not offline:
        try:
            data = _get_json(f"{ABR_BASE}/api/tournaments/results", {"limit": limit})
            _write_cache(RESULTS_CACHE, data)
            return data
        except requests.RequestException as e:
            print(f"경고: 대회 목록 fetch 실패, 캐시 사용: {e}", file=sys.stderr)
    cached = _read_cache(RESULTS_CACHE)
    if cached is None:
        raise RuntimeError("대회 목록 캐시가 없습니다 (data/results.json)")
    return cached


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


def identity_map(offline=False, want_titles=()):
    """NRDB 카드 데이터에서 identity 제목 -> {faction, side} 매핑을 만든다.

    캐시에 없는 identity(want_titles)가 있으면 온라인일 때 갱신을 시도한다.
    """
    cached = _read_cache(NRDB_CACHE) or {}
    missing = [t for t in want_titles if t and t not in cached]
    if offline or (cached and not missing):
        return cached
    try:
        data = _get_json(NRDB_CARDS_URL)
    except requests.RequestException as e:
        print(f"경고: NRDB fetch 실패, 캐시 사용: {e}", file=sys.stderr)
        return cached
    mapping = {}
    for card in data.get("data", []):
        if card.get("type_code") == "identity":
            mapping[card["title"]] = {
                "faction": card.get("faction_code", "unknown"),
                "side": card.get("side_code", "unknown"),
            }
    if mapping:
        _write_cache(NRDB_CACHE, mapping)
        return mapping
    return cached
