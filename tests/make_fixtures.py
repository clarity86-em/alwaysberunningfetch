#!/usr/bin/env python3
"""오프라인 개발용 샘플 데이터를 data/에 생성한다.

    python tests/make_fixtures.py && python src/main.py --offline

실제 데이터가 아니라 무작위 샘플이다 (제목에 SAMPLE 표기).
"""

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
random.seed(7)

CORPS = [
    ("Ob Superheavy Logistics: Extract. Export. Excel.", "weyland-consortium"),
    ("A Teia: IP Recovery", "jinteki"),
    ("Haas-Bioroid: Precision Design", "haas-bioroid"),
    ("NBN: Reality Plus", "nbn"),
    ("Thule Subsea: Safety Below", "jinteki"),
    ("Weyland Consortium: Built to Last", "weyland-consortium"),
]
RUNNERS = [
    ("Hoshiko Shiro: Untold Protagonist", "anarch"),
    ("Arissana Rocha Nahu: Street Artist", "shaper"),
    ("Lat: Ethical Freelancer", "shaper"),
    ("Sable: Sébastien Louveaux", "criminal"),
    ("Esâ Afontov: Eco-Insurrectionist", "anarch"),
    ("Zahya Sadeghi: Versatile Smuggler", "criminal"),
]
TOURNAMENTS = [
    {"id": 9001, "title": "Seoul District Championship (SAMPLE)", "date": "2026.03.14.",
     "type": "district championship", "format": "standard", "players_count": 16,
     "top_count": 4, "url": "https://alwaysberunning.net/tournaments/9001", "concluded": True},
    {"id": 9002, "title": "APAC Megacity Championship (SAMPLE)", "date": "2026.05.30.",
     "type": "megacity championship", "format": "standard", "players_count": 24,
     "top_count": 8, "url": "https://alwaysberunning.net/tournaments/9002", "concluded": True},
]


def main():
    (ROOT / "data" / "tournaments").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "results.json").write_text(json.dumps(TOURNAMENTS, indent=1))

    id_map = {t: {"faction": f, "side": "corp"} for t, f in CORPS}
    id_map.update({t: {"faction": f, "side": "runner"} for t, f in RUNNERS})
    (ROOT / "data" / "nrdb_identities.json").write_text(
        json.dumps(id_map, ensure_ascii=False, indent=1)
    )

    for t in TOURNAMENTS:
        n, cut = t["players_count"], t["top_count"]
        entries = []
        for i in range(1, n + 1):
            c, r = random.choice(CORPS), random.choice(RUNNERS)
            entries.append({
                "user_name": f"player{i:02d}", "user_import_name": None,
                "rank_swiss": i, "rank_top": i if i <= cut else None,
                "corp_deck_identity_title": c[0], "corp_deck_identity_faction": c[1],
                "corp_deck_url": f"https://netrunnerdb.com/en/decklist/{10000 + i}",
                "corp_deck_title": "sample corp deck",
                "runner_deck_identity_title": r[0], "runner_deck_identity_faction": r[1],
                "runner_deck_url": f"https://netrunnerdb.com/en/decklist/{20000 + i}",
                "runner_deck_title": "sample runner deck",
            })
        (ROOT / "data" / "tournaments" / f"{t['id']}.json").write_text(
            json.dumps(entries, ensure_ascii=False, indent=1)
        )
    print("샘플 데이터 생성 완료 (data/)")


if __name__ == "__main__":
    main()
