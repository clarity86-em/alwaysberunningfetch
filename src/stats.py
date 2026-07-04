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
    """(identity_title, faction, deck_url) — side는 'corp' 또는 'runner'."""
    title = _first(entry, f"{side}_deck_identity_title", f"{side}_deck_identity")
    faction = _first(entry, f"{side}_deck_identity_faction", f"{side}_deck_faction")
    url = _first(entry, f"{side}_deck_url")
    if not faction and title and title in id_map:
        faction = id_map[title]["faction"]
    faction = normalize_faction(faction, side)
    return (title or "Unknown", faction or "unknown", url)


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


def tournament_stats(tournament, entries, id_map):
    """대회 하나의 identity 통계.

    반환: {corp: {identity: row}, runner: {...}, players, cut_size, winner, standings}
    row = {count, cut, faction, best_rank, ranks: [...]}  (cut = 탑컷 진출 수)
    """
    players = len(entries)
    sides = {"corp": defaultdict(lambda: _new_row()), "runner": defaultdict(lambda: _new_row())}
    standings = []
    cut_ranks = {}

    for e in entries:
        rank_swiss = e.get("rank_swiss") or None
        rank_top = e.get("rank_top") or None
        row_std = {"player": player_name(e), "rank_swiss": rank_swiss, "rank_top": rank_top}
        for side in ("corp", "runner"):
            title, faction, url = deck_info(e, side, id_map)
            row = sides[side][title]
            row["count"] += 1
            row["faction"] = faction
            if rank_top:
                row["cut"] += 1
            if rank_swiss:
                row["ranks"].append(rank_swiss)
                if row["best_rank"] is None or rank_swiss < row["best_rank"]:
                    row["best_rank"] = rank_swiss
            row_std[side] = {"identity": title, "faction": faction, "url": url}
        standings.append(row_std)
        if rank_top:
            cut_ranks[rank_top] = row_std

    standings.sort(key=lambda r: (r["rank_top"] or 10**6, r["rank_swiss"] or 10**6))
    winner = None
    if cut_ranks:
        winner = cut_ranks.get(min(cut_ranks))
    elif standings and standings[0]["rank_swiss"] == 1:
        winner = standings[0]

    return {
        "id": tournament.get("id"),
        "title": tournament.get("title", "?"),
        "date": tournament.get("date", ""),
        "type": (tournament.get("type") or "").lower(),
        "format": (tournament.get("format") or "").lower(),
        "url": tournament.get("url"),
        "players": players,
        "cut_size": sum(1 for e in entries if e.get("rank_top")),
        "corp": dict(sides["corp"]),
        "runner": dict(sides["runner"]),
        "winner": winner,
        "standings": standings,
    }


def _new_row():
    return {"count": 0, "cut": 0, "faction": "unknown", "best_rank": None, "ranks": []}


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
                a["faction"] = row["faction"]
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
