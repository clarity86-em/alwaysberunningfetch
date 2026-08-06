"""대회 엔트리 -> identity 통계 계산.

ABR 엔트리 필드 (apidoc 기준, 방어적으로 여러 키를 시도):
  user_name / user_import_name    플레이어 이름
  rank_swiss                      스위스 순위
  rank_top                        탑컷 순위 (컷 진출 못 하면 null/0)
  corp_deck_identity_title        코퍼 identity 이름
  runner_deck_identity_title      러너 identity 이름
  corp_deck_identity_faction      팩션 코드 (없을 수 있음 -> NRDB 매핑으로 보완)
  corp_deck_url                   NetrunnerDB 덱리스트 링크
"""

import re
from collections import defaultdict

CORP_FACTIONS = ["haas-bioroid", "jinteki", "nbn", "weyland-consortium", "neutral-corp"]
RUNNER_FACTIONS = ["anarch", "criminal", "shaper", "adam", "apex", "sunny-lebeau", "neutral-runner"]

FACTION_LABELS = {
    "haas-bioroid": "Haas-Bioroid",
    "jinteki": "Jinteki",
    "nbn": "NBN",
    "weyland-consortium": "Weyland",
    "neutral-corp": "Neutral (Corp)",
    "anarch": "Anarch",
    "criminal": "Criminal",
    "shaper": "Shaper",
    "adam": "Adam",
    "apex": "Apex",
    "sunny-lebeau": "Sunny Lebeau",
    "neutral-runner": "Neutral (Runner)",
    "unknown": "Unknown",
}


def _first(entry, *keys):
    for k in keys:
        v = entry.get(k)
        if v not in (None, "", 0):
            return v
    return None


def player_name(entry):
    return _first(entry, "user_name", "user_import_name") or "?"


def normalize_faction(faction, side):
    """ABR은 팩션 코드를 12자로 잘라서 준다 ('weyland-cons' 등) — 접두사 매칭으로 복원."""
    if not faction:
        return None
    if faction == "neutral":
        return f"neutral-{side}"
    known = CORP_FACTIONS + RUNNER_FACTIONS
    if faction in known:
        return faction
    for full in known:
        if full.startswith(faction):
            return full
    return faction


def deck_info(entry, side, id_map):
    """(identity_title, faction, deck_url, nrdb_card_id) — side는 'corp' 또는 'runner'."""
    title = _first(entry, f"{side}_deck_identity_title", f"{side}_deck_identity")
    faction = _first(entry, f"{side}_deck_identity_faction", f"{side}_deck_faction")
    url = _first(entry, f"{side}_deck_url")
    nrdb_id = _first(entry, f"{side}_deck_identity_id")
    if not faction and title and title in id_map:
        faction = id_map[title]["faction"]
    faction = normalize_faction(faction, side)
    return (title or "Unknown", faction or "unknown", url, nrdb_id)


def norm_title(title):
    """소스 간 표기 차이 정규화 (둥근따옴표 vs 곧은따옴표, 공백)."""
    if not title:
        return title
    for a, b in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        title = title.replace(a, b)
    return " ".join(title.split())


def shorten_identity(title):
    """'Haas-Bioroid: Precision Design' -> 'HB: Precision Design' 같은 축약."""
    replacements = {
        "Haas-Bioroid": "HB",
        "Weyland Consortium": "Weyland",
        "Near-Earth Hub": "NEH",
    }
    for full, short in replacements.items():
        title = title.replace(full, short)
    return title


def _loose_key(title):
    """오타 관용 매칭 키: 따옴표/공백 정규화 + 소문자화 + 0→o + 영숫자만.

    NRTM 업로드에는 손으로 친 identity 이름이 섞여 '0mission'을 'Omission'으로
    쓰는 식의 오타가 있다. 이 키가 같으면 같은 identity로 본다.
    """
    t = norm_title(title or "").casefold().replace("0", "o")
    return re.sub(r"[^a-z0-9]+", "", t)


def _canon_index(id_map):
    """NRDB identity 목록 -> {loose_key: 정식 제목}. 키 충돌 항목은 제외."""
    idx, dupes = {}, set()
    for title in id_map or {}:
        k = _loose_key(title)
        if k in idx and idx[k] != norm_title(title):
            dupes.add(k)
        else:
            idx[k] = norm_title(title)
    for k in dupes:
        idx.pop(k, None)
    return idx


def parse_matchdata(tjson, id_map=None):
    """NRTM 토너먼트 JSON -> identity별 승/무/게임 수.

    점수 규약: 3=승, 1=무, 0=패. 스위스 한 테이블 = 두 게임
    (p1 corp vs p2 runner, p1 runner vs p2 corp). 컷/단판 게임은
    role 필드로 판별. 파싱할 수 없는 테이블은 건너뛴다.

    반환: {"corp": {identity: {wins, ties, games}}, "runner": {...},
           "corp_side_wins": float, "games": int}  (게임 0개면 None)
    무승부는 양쪽에 0.5승으로 계산.
    """
    if not tjson or not isinstance(tjson, dict):
        return None
    canon = _canon_index(id_map)

    def _canon_title(title):
        if not title:
            return title
        return canon.get(_loose_key(title), title)

    idents = {}
    for p in (tjson.get("players") or []) + (tjson.get("eliminationPlayers") or []):
        pid = p.get("id")
        if pid is not None and pid not in idents:
            idents[pid] = (
                _canon_title(p.get("corpIdentity")),
                _canon_title(p.get("runnerIdentity")),
            )

    names = {}
    for p in (tjson.get("players") or []) + (tjson.get("eliminationPlayers") or []):
        pid = p.get("id")
        if pid is not None and p.get("name"):
            names.setdefault(pid, norm_title(str(p["name"])).casefold())

    res = {
        "corp": defaultdict(lambda: {"wins": 0.0, "ties": 0, "games": 0}),
        "runner": defaultdict(lambda: {"wins": 0.0, "ties": 0, "games": 0}),
    }
    matchups = defaultdict(lambda: {"corp_wins": 0.0, "ties": 0, "games": 0})
    players_perf = defaultdict(
        lambda: {
            "corp": {"wins": 0.0, "ties": 0, "games": 0},
            "runner": {"wins": 0.0, "ties": 0, "games": 0},
        }
    )
    state = {"games": 0, "corp_side_wins": 0.0}
    _complement = {3: 0, 0: 3, 1: 1}

    def record(corp_title, runner_title, corp_score, runner_score, corp_pid=None, runner_pid=None):
        if corp_score is None and runner_score is not None:
            corp_score = _complement.get(runner_score)
        if runner_score is None and corp_score is not None:
            runner_score = _complement.get(corp_score)
        if corp_score == 3 and runner_score == 0:
            cw, tie = 1.0, False
        elif corp_score == 0 and runner_score == 3:
            cw, tie = 0.0, False
        elif corp_score == 1 and runner_score == 1:
            cw, tie = 0.5, True
        else:
            return  # 미진행(0-0)이거나 알 수 없는 조합
        state["games"] += 1
        state["corp_side_wins"] += cw
        for side, title, w in (("corp", corp_title, cw), ("runner", runner_title, 1.0 - cw)):
            if title:
                row = res[side][norm_title(title)]
                row["games"] += 1
                row["wins"] += w
                if tie:
                    row["ties"] += 1
        if corp_title and runner_title:
            mu = matchups[norm_title(corp_title) + "\t" + norm_title(runner_title)]
            mu["games"] += 1
            mu["corp_wins"] += cw
            if tie:
                mu["ties"] += 1
        for pid, pside, w in ((corp_pid, "corp", cw), (runner_pid, "runner", 1.0 - cw)):
            name = names.get(pid)
            if name:
                pr = players_perf[name][pside]
                pr["games"] += 1
                pr["wins"] += w
                if tie:
                    pr["ties"] += 1

    for rnd in tjson.get("rounds") or []:
        for tbl in rnd or []:
            p1, p2 = tbl.get("player1") or {}, tbl.get("player2") or {}
            id1, id2 = p1.get("id"), p2.get("id")
            if id1 is None or id2 is None:
                continue  # 부전승(bye)
            c1, r1 = idents.get(id1, (None, None))
            c2, r2 = idents.get(id2, (None, None))
            role1 = (p1.get("role") or "").lower()
            if role1 in ("corp", "runner"):
                # 단판 (컷 라운드 등): p1이 role1 측을 플레이
                winner1 = p1.get("winner")
                if winner1 is None and p1.get("combinedScore") is not None:
                    winner1 = (p1.get("combinedScore") or 0) > (p2.get("combinedScore") or 0)
                if winner1 is None:
                    continue
                if role1 == "corp":
                    record(c1, r2, 3 if winner1 else 0, 0 if winner1 else 3, id1, id2)
                else:
                    record(c2, r1, 0 if winner1 else 3, 3 if winner1 else 0, id2, id1)
            else:
                # 스위스 양판
                record(c1, r2, p1.get("corpScore"), p2.get("runnerScore"), id1, id2)
                record(c2, r1, p2.get("corpScore"), p1.get("runnerScore"), id2, id1)

    if state["games"] == 0:
        return None
    return {
        "corp": dict(res["corp"]),
        "runner": dict(res["runner"]),
        "matchups": dict(matchups),  # "코퍼\t러너" -> {corp_wins, ties, games}
        "players": {k: dict(v) for k, v in players_perf.items()},  # 정규화된 이름 -> 사이드별 전적
        "corp_side_wins": state["corp_side_wins"],
        "games": state["games"],
    }


def aggregate_winrates(per_tournament):
    """여러 대회의 winrates 합산. matchdata가 있는 대회가 없으면 None."""
    agg = {
        "corp": defaultdict(lambda: {"wins": 0.0, "ties": 0, "games": 0}),
        "runner": defaultdict(lambda: {"wins": 0.0, "ties": 0, "games": 0}),
    }
    matchups = defaultdict(lambda: {"corp_wins": 0.0, "ties": 0, "games": 0})
    n_t, games, corp_wins = 0, 0, 0.0
    for t in per_tournament:
        wr = t.get("winrates")
        if not wr:
            continue
        n_t += 1
        games += wr["games"]
        corp_wins += wr["corp_side_wins"]
        for side in ("corp", "runner"):
            for title, row in wr[side].items():
                a = agg[side][title]
                a["wins"] += row["wins"]
                a["ties"] += row["ties"]
                a["games"] += row["games"]
        for key, mu in (wr.get("matchups") or {}).items():
            m = matchups[key]
            m["games"] += mu["games"]
            m["corp_wins"] += mu["corp_wins"]
            m["ties"] += mu["ties"]
    if n_t == 0:
        return None
    return {
        "corp": dict(agg["corp"]),
        "runner": dict(agg["runner"]),
        "matchups": dict(matchups),
        "corp_side_wins": corp_wins,
        "games": games,
        "tournaments": n_t,
    }


def tournament_stats(tournament, entries, id_map, tjson=None):
    """대회 하나의 identity 통계.

    반환: {corp: {identity: row}, runner: {...}, players, cut_size, winner, standings}
    row = {count, cut, faction, best_rank, ranks: [...]}  (cut = 탑컷 진출 수)
    """
    players = len(entries)
    sides = {"corp": defaultdict(lambda: _new_row()), "runner": defaultdict(lambda: _new_row())}
    standings = []
    cut_ranks = {}
    winrates = parse_matchdata(tjson, id_map)
    player_perf = (winrates or {}).get("players") or {}
    decks = []  # 덱리스트가 공개된 엔트리 (카드 통계용, 플레이어 전적 조인)

    for e in entries:
        rank_swiss = e.get("rank_swiss") or None
        rank_top = e.get("rank_top") or None
        row_std = {"player": player_name(e), "rank_swiss": rank_swiss, "rank_top": rank_top}
        for side in ("corp", "runner"):
            title, faction, url, nrdb_id = deck_info(e, side, id_map)
            row = sides[side][title]
            row["count"] += 1
            row["faction"] = faction
            if nrdb_id:
                row["nrdb_id"] = nrdb_id
            if rank_top:
                row["cut"] += 1
            if rank_swiss:
                row["ranks"].append(rank_swiss)
                if row["best_rank"] is None or rank_swiss < row["best_rank"]:
                    row["best_rank"] = rank_swiss
            row_std[side] = {"identity": title, "faction": faction, "url": url, "nrdb_id": nrdb_id}
            if url:
                perf = (
                    player_perf.get(norm_title(row_std["player"]).casefold()) or {}
                ).get(side) or {}
                decks.append(
                    {
                        "side": side,
                        "identity": title,
                        "url": url,
                        "wins": perf.get("wins", 0.0),
                        "ties": perf.get("ties", 0),
                        "games": perf.get("games", 0),
                    }
                )
        standings.append(row_std)
        if rank_top:
            cut_ranks[rank_top] = row_std

    standings.sort(key=lambda r: (r["rank_top"] or 10**6, r["rank_swiss"] or 10**6))
    winner = None
    if cut_ranks:
        winner = cut_ranks.get(min(cut_ranks))
    elif standings and standings[0]["rank_swiss"] == 1:
        winner = standings[0]
    if winner:
        for side in ("corp", "runner"):
            deck = winner.get(side)
            if deck and deck.get("identity") in sides[side]:
                sides[side][deck["identity"]]["wins"] += 1

    return {
        "id": tournament.get("id"),
        "title": tournament.get("title", "?"),
        "date": tournament.get("date", ""),
        "type": (tournament.get("type") or "").lower(),
        "format": (tournament.get("format") or "").lower(),
        "cardpool": tournament.get("cardpool") or "?",
        "mwl": tournament.get("mwl") or "",
        "url": tournament.get("url"),
        "players": players,
        "cut_size": sum(1 for e in entries if e.get("rank_top")),
        "corp": dict(sides["corp"]),
        "runner": dict(sides["runner"]),
        "winner": winner,
        "standings": standings,
        "winrates": winrates,
        "decks": decks,
    }


def _new_row():
    return {
        "count": 0, "cut": 0, "wins": 0,
        "faction": "unknown", "nrdb_id": None, "best_rank": None, "ranks": [],
    }


def faction_breakdown(identity_rows, side):
    """identity 통계 -> 팩션별 (count, cut) 합계. 고정 팩션 순서 유지."""
    order = CORP_FACTIONS if side == "corp" else RUNNER_FACTIONS
    totals = defaultdict(lambda: {"count": 0, "cut": 0})
    for row in identity_rows.values():
        f = row["faction"] if row["faction"] in order else "unknown"
        totals[f]["count"] += row["count"]
        totals[f]["cut"] += row["cut"]
    return [(f, totals[f]) for f in order + ["unknown"] if totals[f]["count"] > 0]


def aggregate(per_tournament):
    """여러 대회를 합친 시즌 통계 (단순 합산)."""
    agg = {"corp": defaultdict(_new_row), "runner": defaultdict(_new_row)}
    total_players = 0
    for t in per_tournament:
        total_players += t["players"]
        for side in ("corp", "runner"):
            for title, row in t[side].items():
                a = agg[side][title]
                a["count"] += row["count"]
                a["cut"] += row["cut"]
                a["wins"] += row.get("wins", 0)
                a["faction"] = row["faction"]
                a["nrdb_id"] = a["nrdb_id"] or row.get("nrdb_id")
                a["ranks"].extend(row["ranks"])
                if row["best_rank"] is not None and (
                    a["best_rank"] is None or row["best_rank"] < a["best_rank"]
                ):
                    a["best_rank"] = row["best_rank"]
    return {
        "tournaments": len(per_tournament),
        "players": total_players,
        "corp": dict(agg["corp"]),
        "runner": dict(agg["runner"]),
    }
