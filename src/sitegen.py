"""통계 -> 정적 HTML 사이트 생성 (docs/ 아래, GitHub Pages용).

외부 리소스 없이 완전히 자체 포함된 페이지를 만든다 (인라인 SVG 차트 + CSS).
라이트/다크 모드는 CSS 변수 + prefers-color-scheme으로 처리.
색상은 dataviz 검증을 통과한 팔레트 (팩션별 고정 색).

구조:
  index.html            포맷별(Standard/Startup) 최신 메타 통계 + 전체 대회 목록
  meta/<slug>.html      메타 그룹(포맷 x 카드풀 x 밴리스트)별 상세 통계
  t/<id>.html           대회별 상세 (우승, 차트, 순위표)
  data/summary.json     기계가독 요약

캐주얼 포함/제외: 각 통계 블록을 두 버전으로 렌더링하고
체크박스 + CSS :has()로 전환한다 (JS 없음).
"""

import html
import json
import math
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path

from stats import (
    FACTION_LABELS,
    aggregate,
    aggregate_winrates,
    faction_breakdown,
    norm_title,
    shorten_identity,
)

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# 팩션 -> (라이트, 다크) — dataviz validate_palette.js 통과 색상
FACTION_COLORS = {
    "haas-bioroid": ("#4a3aa7", "#9085e9"),
    "jinteki": ("#e34948", "#e66767"),
    "nbn": ("#eda100", "#c98500"),
    "weyland-consortium": ("#008300", "#008300"),
    "anarch": ("#eb6834", "#d95926"),
    "criminal": ("#2a78d6", "#3987e5"),
    "shaper": ("#1baf7a", "#199e70"),
    "adam": ("#eda100", "#c98500"),
    "apex": ("#e34948", "#e66767"),
    "sunny-lebeau": ("#e87ba4", "#d55181"),
    "neutral-corp": ("#898781", "#898781"),
    "neutral-runner": ("#898781", "#898781"),
    "unknown": ("#898781", "#898781"),
}

FORMAT_LABELS = {"standard": "Standard", "startup": "Startup", "eternal": "Eternal"}


def esc(s):
    return html.escape(str(s if s is not None else ""))


# ---------------------------------------------------------------- 메타 그룹

def banlist_version(mwl):
    """'Standard Ban List 26.05' -> '26.05'. 인식 불가면 None."""
    m = re.search(r"(\d{2}\.\d{2})", mwl or "")
    return m.group(1) if m else None


def derive_banlist(t, settings):
    """대회 날짜로 밴리스트 버전 판정 (발효일 표 기반).

    ABR의 주최자 입력(mwl)은 오표기가 많아 신뢰하지 않는다.
    발효일 표에 없는 날짜만 mwl 파싱으로 폴백.
    """
    if t["format"] == "startup":
        return ""  # startup 메타는 카드풀로만 구분
    schedule = (settings.get("banlist_schedule") or {}).get(t["format"]) or []
    parts = str(t.get("date") or "").strip(".").split(".")
    if len(parts) == 3 and len(parts[0]) == 4:
        d = f"{parts[0]}-{parts[1]}-{parts[2]}"
        best = None
        for entry in schedule:
            frm = str(entry.get("from"))
            if frm <= d and (best is None or frm > best[0]):
                best = (frm, str(entry.get("version")))
        if best:
            return best[1]
    return banlist_version(t["mwl"])


def derive_cardpool(t, settings):
    """대회 날짜로 카드풀(확장) 판정 — 주최자 입력 오표기 보정.

    출시일 표에서 대회 날짜 이전에 나온 가장 최신 확장을 고른다.
    날짜 파싱 실패 또는 표가 비어 있으면 ABR 값 그대로.
    """
    schedule = settings.get("cardpool_schedule") or []
    parts = str(t.get("date") or "").strip(".").split(".")
    if len(parts) == 3 and len(parts[0]) == 4 and schedule:
        d = f"{parts[0]}-{parts[1]}-{parts[2]}"
        best = None
        for entry in schedule:
            frm = str(entry.get("from"))
            if frm <= d and (best is None or frm > best[0]):
                best = (frm, str(entry.get("cardpool")))
        if best:
            return best[1]
    return t.get("cardpool") or "?"


def meta_key(t):
    # startup은 최신 확장(카드풀)이 메타를 결정 — 밴리스트 버전은 묶는다.
    # standard는 밴리스트에 따라 메타가 크게 바뀌므로 카드풀 x 밴리스트로 구분.
    # banlist/cardpool은 annotate()에서 날짜 기반으로 미리 계산됨.
    return (t["format"], t["cardpool"], t.get("banlist") or None)


def meta_label(key):
    fmt, cardpool, ban = key
    parts = [FORMAT_LABELS.get(fmt, fmt or "?"), cardpool]
    if fmt != "startup":
        parts.append(ban if ban else "밴리스트 미상")
    return " · ".join(parts)


def meta_slug(key):
    fmt, cardpool, ban = key
    raw = f"{fmt}-{cardpool}" + ("" if fmt == "startup" else f"-{ban or 'etc'}")
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def group_by_meta(per_tournament):
    """{key: [tournaments]} — 각 포맷 안에서 최신 대회 날짜 기준 내림차순 정렬된 키 목록도 반환."""
    groups = defaultdict(list)
    for t in per_tournament:
        groups[meta_key(t)].append(t)
    order = sorted(
        groups,
        key=lambda k: (k[0], max(t["date"] for t in groups[k])),
        reverse=True,
    )
    # 포맷 우선순위: standard, startup, 나머지
    fmt_rank = {"standard": 0, "startup": 1}
    order.sort(key=lambda k: fmt_rank.get(k[0], 9))
    return groups, order


# ---------------------------------------------------------------- CSS / 페이지 골격

def faction_css():
    light = "\n".join(f"  --f-{n}: {c[0]};" for n, c in FACTION_COLORS.items())
    dark = "\n".join(f"    --f-{n}: {c[1]};" for n, c in FACTION_COLORS.items())
    return light, dark


def base_css():
    light, dark = faction_css()
    return f"""
:root {{
  --page: #f9f9f7; --surface: #fcfcfb;
  --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --win-overlay: rgba(0,0,0,0.45);
{light}
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --page: #0d0d0d; --surface: #1a1a19;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --win-overlay: rgba(255,255,255,0.60);
{dark}
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--page); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 15px; line-height: 1.5;
}}
main {{ max-width: 1080px; margin: 0 auto; padding: 24px 20px 64px; }}
h1 {{ font-size: 26px; margin: 8px 0 2px; }}
h2 {{ font-size: 19px; margin: 0 0 12px; }}
h3 {{ font-size: 15px; margin: 0 0 8px; color: var(--ink-2); }}
.sub {{ color: var(--ink-2); margin: 0 0 24px; }}
.sub a {{ color: inherit; }}
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 20px; margin: 0 0 20px;
}}
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
@media (max-width: 860px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 20px; }}
.kpi {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; }}
.kpi .label {{ font-size: 13px; color: var(--ink-2); }}
.kpi .value {{ font-size: 30px; font-weight: 600; }}
.donut-wrap {{ display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }}
.legend {{ list-style: none; margin: 0; padding: 0; font-size: 13.5px; }}
.legend li {{ display: flex; align-items: baseline; gap: 8px; margin: 3px 0; }}
.legend .sw {{ width: 10px; height: 10px; border-radius: 3px; flex: none; transform: translateY(1px); }}
.legend .n {{ color: var(--ink-2); font-variant-numeric: tabular-nums; }}
.bars {{ display: grid; grid-template-columns: minmax(140px, max-content) 1fr max-content; gap: 4px 10px; align-items: center; margin-top: 14px; }}
.bars .name {{ font-size: 13px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 230px; }}
.bars .val {{ font-size: 12.5px; color: var(--ink-2); font-variant-numeric: tabular-nums; white-space: nowrap; }}
.bars svg {{ display: block; width: 100%; height: 18px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--grid); }}
th {{ color: var(--ink-2); font-weight: 600; font-size: 12.5px; white-space: nowrap; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr:hover td {{ background: color-mix(in srgb, var(--ink) 4%, transparent); }}
a {{ color: inherit; }}
.badge {{
  display: inline-block; font-size: 11.5px; font-weight: 600; padding: 1px 8px;
  border: 1px solid var(--border); border-radius: 999px; color: var(--ink-2);
  white-space: nowrap;
}}
.badge.comp {{
  color: var(--ink); border-color: var(--baseline);
  background: color-mix(in srgb, var(--ink) 9%, transparent);
}}
.winner .who {{ font-size: 17px; font-weight: 600; }}
.winner-decks {{ display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; margin-top: 4px; }}
.idtag {{ font-size: 13px; color: var(--ink-2); }}
.idtag .dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 3px; margin-right: 5px; }}
.idtag .decklink {{ font-weight: 650; color: var(--ink); }}
footer {{ color: var(--muted); font-size: 12.5px; margin-top: 40px; }}
footer a {{ color: inherit; }}
.crumb {{ font-size: 13px; color: var(--ink-2); }}
.toggle-row {{
  display: flex; align-items: center; gap: 8px; margin: 0 0 18px;
  font-size: 14px; color: var(--ink-2);
}}
.toggle-row input {{ width: 16px; height: 16px; accent-color: var(--ink); }}
.agg-note {{ font-size: 13px; color: var(--ink-2); margin: 0 0 10px; }}
.agg-comp {{ display: none; }}
body:has(#comp-only:checked) .agg-all {{ display: none; }}
body:has(#comp-only:checked) .agg-comp {{ display: block; }}
.meta-links {{ list-style: none; margin: 0; padding: 0; font-size: 14px; }}
.meta-links li {{ margin: 4px 0; }}
.meta-links .n {{ color: var(--ink-2); font-size: 12.5px; }}
.tier-chips {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 0 0 12px; }}
.chips-label {{ font-size: 12.5px; color: var(--ink-2); margin-right: 4px; }}
.chip {{
  display: inline-flex; align-items: center; gap: 5px; cursor: pointer;
  font-size: 12.5px; padding: 2px 10px; border-radius: 999px;
  border: 1px solid var(--border); color: var(--muted); user-select: none;
}}
.chip input {{ display: none; }}
.chip span {{ font-variant-numeric: tabular-nums; font-size: 11.5px; }}
.chip.on {{ color: var(--ink); border-color: var(--baseline); background: color-mix(in srgb, var(--ink) 5%, transparent); }}
.chip-all {{
  font-size: 12px; padding: 2px 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border); background: none; color: var(--ink-2);
  font-family: inherit;
}}
.fmt-filter {{ display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin: 0 0 12px; }}
.fmtbtn {{
  background: none; border: none; padding: 0; cursor: pointer; font-family: inherit;
  font-size: 13px; color: var(--ink); font-weight: 600;
}}
.fmtbtn .n {{ color: var(--ink-2); font-weight: 400; font-size: 12px; }}
.fmtbtn.off {{ color: var(--muted); font-weight: 400; text-decoration: line-through; }}
.fmtbtn.off .n {{ color: var(--muted); }}
.table-foot {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 12px; flex-wrap: wrap; }}
.pcount {{ font-size: 12.5px; color: var(--ink-2); }}
.pager {{ display: flex; gap: 4px; flex-wrap: wrap; }}
.pager button {{
  min-width: 28px; height: 26px; font-size: 12.5px; cursor: pointer;
  border: 1px solid var(--border); border-radius: 6px; background: none;
  color: var(--ink-2); font-family: inherit; font-variant-numeric: tabular-nums;
}}
.pager button.cur {{ color: var(--ink); border-color: var(--baseline); font-weight: 700; }}
.pager button:hover {{ background: color-mix(in srgb, var(--ink) 6%, transparent); }}
.d-short {{ display: none; }}
.mob-extra {{ display: none; font-size: 12px; color: var(--ink-2); margin-top: 3px; }}
.champs {{ table-layout: fixed; width: 100%; }}
.champs th:nth-child(1) {{ width: 74px; }}
.champs th:nth-child(2) {{ width: 34%; }}
.champs .trunc {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.cell-flex {{ display: flex; align-items: baseline; gap: 6px; min-width: 0; }}
.cell-flex .trunc {{ flex: 1 1 auto; min-width: 0; }}
.players {{ flex: none; color: var(--ink-2); font-size: 12px; white-space: nowrap; }}
@media (max-width: 640px) {{
  main {{ padding: 16px 10px 48px; }}
  .card {{ padding: 14px 12px; }}
  /* identity 바(엔트리/승률 공통): 이름 폭 축소 -> 그래프 공간 확보 */
  .bars {{ gap: 4px 8px; }}
  .bars .name {{ max-width: 128px; font-size: 12px; }}
  .bars .val {{ font-size: 11.5px; }}
  /* 2. 대회 목록: 짧은 날짜, 카드풀 서브라인 숨김, 인원/우승은 탭해서 펼침 */
  .d-full {{ display: none; }}
  .d-short {{ display: inline; }}
  th, td {{ padding: 6px 5px; }}
  td .n {{ display: none; }}
  th.col-players, td.col-players, th.col-winner, td.col-winner {{ display: none; }}
  [data-ptable] tbody tr {{ cursor: pointer; }}
  tr.open .mob-extra {{ display: block; }}
  .badge {{ font-size: 10.5px; padding: 1px 6px; }}
  /* 최근 공식 대회 우승 표: 폭 압축, 인원 표기는 숨김 */
  .champs th, .champs td {{ font-size: 12px; padding: 6px 4px; }}
  .champs .players {{ display: none; }}
  .champs th:nth-child(1) {{ width: 58px; }}
  .champs .idtag {{ font-size: 12px; }}
  /* 순위표: 플레이어 이름 칸 폭 제한 + 긴 이름 줄바꿈 */
  .standings td:nth-child(2), .standings th:nth-child(2) {{
    max-width: 88px; overflow-wrap: anywhere; font-size: 12px;
  }}
  .standings td:first-child, .standings th:first-child {{ padding-left: 2px; padding-right: 2px; }}
  .standings td:last-child, .standings th:last-child {{ padding-left: 2px; padding-right: 2px; }}
}}
"""


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#14141a"/>
<g fill="none" stroke-width="11">
<path d="M32 15 A17 17 0 0 1 49 32" stroke="#9085e9"/>
<path d="M49 32 A17 17 0 0 1 26.75 48.17" stroke="#e66767"/>
<path d="M26.75 48.17 A17 17 0 0 1 15.83 37.25" stroke="#eda100"/>
<path d="M15.83 37.25 A17 17 0 0 1 32 15" stroke="#2fae2f"/>
</g>
</svg>
"""


def page(title, body, scripts="", root=""):
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex">
<title>{esc(title)}</title>
<link rel="icon" type="image/svg+xml" href="{root}favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="{root}favicon-32.png">
<link rel="apple-touch-icon" href="{root}apple-touch-icon.png">
<style>{base_css()}</style>
</head>
<body>
<main>
{body}
<footer>Data: <a href="https://alwaysberunning.net" target="_blank" rel="noopener">alwaysberunning.net</a> ·
Identities: <a href="https://netrunnerdb.com" target="_blank" rel="noopener">NetrunnerDB</a> ·
자동 생성 (<a href="https://github.com/clarity86-em/alwaysberunningfetch" target="_blank" rel="noopener">alwaysberunningfetch</a>)</footer>
</main>
{f'<script>{scripts}</script>' if scripts else ''}
</body>
</html>"""


# ---------------------------------------------------------------- 차트 조각

def svg_donut(pairs, total, aria_label):
    """pairs: [(faction, {count, cut})] -> 도넛 SVG. 세그먼트 간 2px 서피스 갭."""
    if total <= 0:
        return "<p class='sub'>데이터 없음</p>"
    cx = cy = 90
    r_out, r_in = 82, 52
    parts = [f'<svg width="180" height="180" viewBox="0 0 180 180" role="img" aria-label="{esc(aria_label)}">']
    start = -math.pi / 2
    for faction, row in pairs:
        frac = row["count"] / total
        end = start + frac * 2 * math.pi
        share = f"{row['count']}/{total} ({frac * 100:.0f}%)"
        label = FACTION_LABELS.get(faction, faction)
        if frac >= 0.999:
            parts.append(
                f'<circle cx="{cx}" cy="{cy}" r="{(r_out + r_in) / 2}" fill="none" '
                f'stroke="var(--f-{faction})" stroke-width="{r_out - r_in}">'
                f"<title>{esc(label)}: {share}</title></circle>"
            )
        else:
            large = 1 if (end - start) > math.pi else 0
            x0o, y0o = cx + r_out * math.cos(start), cy + r_out * math.sin(start)
            x1o, y1o = cx + r_out * math.cos(end), cy + r_out * math.sin(end)
            x1i, y1i = cx + r_in * math.cos(end), cy + r_in * math.sin(end)
            x0i, y0i = cx + r_in * math.cos(start), cy + r_in * math.sin(start)
            d = (
                f"M {x0o:.2f} {y0o:.2f} A {r_out} {r_out} 0 {large} 1 {x1o:.2f} {y1o:.2f} "
                f"L {x1i:.2f} {y1i:.2f} A {r_in} {r_in} 0 {large} 0 {x0i:.2f} {y0i:.2f} Z"
            )
            parts.append(
                f'<path d="{d}" fill="var(--f-{faction})" stroke="var(--surface)" stroke-width="2">'
                f"<title>{esc(label)}: {share}</title></path>"
            )
        if frac >= 0.12:
            mid = (start + end) / 2
            rm = (r_out + r_in) / 2
            tx, ty = cx + rm * math.cos(mid), cy + rm * math.sin(mid)
            parts.append(
                f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" dominant-baseline="central" '
                f'font-size="12" font-weight="600" fill="#fff">{frac * 100:.0f}%</text>'
            )
        start = end
    parts.append("</svg>")
    return "".join(parts)


def donut_block(pairs, total, side_label):
    legend = ["<ul class='legend'>"]
    for faction, row in pairs:
        pct = row["count"] / total * 100 if total else 0
        legend.append(
            f"<li><span class='sw' style='background:var(--f-{faction})'></span>"
            f"{esc(FACTION_LABELS.get(faction, faction))} "
            f"<span class='n'>{row['count']} · {pct:.0f}%</span></li>"
        )
    legend.append("</ul>")
    return (
        f"<div class='donut-wrap'>{svg_donut(pairs, total, side_label + ' faction share')}"
        + "".join(legend)
        + "</div>"
    )


def bars_block(identity_rows, has_cut):
    """identity별 가로 바: 길이 = 엔트리 수, 진한 부분 = 탑컷 진출 수."""
    rows = sorted(identity_rows.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    if not rows:
        return "<p class='sub'>데이터 없음</p>"
    max_count = rows[0][1]["count"]
    out = ["<div class='bars'>"]
    for title, row in rows:
        wins = row.get("wins", 0)
        w_total = row["count"] / max_count * 100
        w_cut = row["cut"] / max_count * 100
        w_win = wins / max_count * 100
        color = f"var(--f-{row['faction'] if row['faction'] in FACTION_COLORS else 'unknown'})"
        val_txt = f"{row['count']}/{row['cut']}/{wins}" if has_cut else str(row["count"])
        tip = f"{title}: 출전 {row['count']}" + (
            f", 컷 {row['cut']}, 우승 {wins}" if has_cut else (f", 우승 {wins}" if wins else "")
        )
        bar = [
            f'<svg viewBox="0 0 100 18" preserveAspectRatio="none" role="img" aria-label="{esc(tip)}">'
        ]
        bar.append(
            f'<rect x="0" y="2" width="{w_total:.2f}" height="14" rx="1.2" fill="{color}" opacity="0.25"/>'
        )
        if row["cut"]:
            bar.append(
                f'<rect x="0" y="2" width="{max(w_cut, 1.5):.2f}" height="14" rx="1.2" fill="{color}"/>'
            )
        if wins:
            w = max(w_win, 1.5)
            bar.append(
                f'<rect x="0" y="2" width="{w:.2f}" height="14" rx="1.2" fill="{color}"/>'
                f'<rect x="0" y="2" width="{w:.2f}" height="14" rx="1.2" fill="var(--win-overlay)"/>'
            )
        bar.append(f"<title>{esc(tip)}</title></svg>")
        name = esc(shorten_identity(title))
        if row.get("nrdb_id"):
            name = (
                f"<a href='https://netrunnerdb.com/en/card/{esc(row['nrdb_id'])}' "
                f"target='_blank' rel='noopener'>{name}</a>"
            )
        out.append(f"<div class='name' title='{esc(title)}'>{name}</div>")
        out.append("".join(bar))
        out.append(f"<div class='val'>{val_txt}</div>")
    out.append("</div>")
    return "".join(out)


def ident_slug(title):
    """identity 제목 -> URL용 슬러그 ('Méliès U: ...' -> 'melies-u-only-the-brightest')."""
    ascii_t = unicodedata.normalize("NFKD", norm_title(title)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_t.lower()).strip("-") or "id"


def winrate_bars(rows_dict, faction_of, min_games, link_of=None, sort_by="winrate"):
    """identity별 승률 바 (50% 기준선). link_of(title) -> href면 이름에 링크."""
    rows = [
        (title, r, r["wins"] / r["games"] * 100)
        for title, r in rows_dict.items()
        if r["games"] >= min_games
    ]
    if not rows:
        return f"<p class='sub'>{min_games}게임 이상인 identity가 없습니다.</p>"
    if sort_by == "games":
        rows.sort(key=lambda x: (-x[1]["games"], -x[2]))
    else:
        rows.sort(key=lambda x: -x[2])
    out = ["<div class='bars wr'>"]
    for title, r, wr in rows:
        faction = faction_of.get(norm_title(title).casefold(), "unknown")
        color = f"var(--f-{faction if faction in FACTION_COLORS else 'unknown'})"
        tie_txt = f" (무 {r['ties']})" if r["ties"] else ""
        tip = f"{title}: 승률 {wr:.1f}%, {r['games']}게임{tie_txt}"
        bar = [
            f'<svg viewBox="0 0 100 18" preserveAspectRatio="none" role="img" aria-label="{esc(tip)}">',
            f'<rect x="0" y="2" width="{max(wr, 1.0):.2f}" height="14" rx="1.2" fill="{color}"/>',
            '<rect x="49.6" y="0" width="0.8" height="18" fill="var(--baseline)"/>',
            f"<title>{esc(tip)}</title></svg>",
        ]
        name = esc(shorten_identity(title))
        if link_of:
            name = f"<a href='{esc(link_of(title))}'>{name}</a>"
        out.append(f"<div class='name' title='{esc(title)}'>{name}</div>")
        out.append("".join(bar))
        out.append(f"<div class='val'>{wr:.0f}% · {r['games']}판</div>")
    out.append("</div>")
    excluded = [t for t, r in rows_dict.items() if r["games"] < min_games]
    if excluded:
        names = ", ".join(shorten_identity(t) for t in sorted(excluded))
        out.append(
            f"<p class='agg-note' style='margin-top:8px'>표본 부족({min_games}게임 미만) 제외: {esc(names)}</p>"
        )
    return "".join(out)


def winrate_block(wr, faction_of, min_games, heading="Identity 승률", matchup_base=None):
    """승률 카드 — 사이드 밸런스 + corp/runner 승률 바.

    matchup_base가 있으면 identity 이름을 매치업 상세 페이지로 링크.
    """
    if not wr:
        return ""
    corp_pct = wr["corp_side_wins"] / wr["games"] * 100
    n_t = wr.get("tournaments")
    src = f"matchdata가 업로드된 대회 {n_t}개 · " if n_t else ""
    note = (
        f"<p class='agg-note'>{src}총 {wr['games']}게임 기준</p>"
        f"<p style='margin:0 0 14px;font-size:14.5px'><b>사이드 밸런스:</b> "
        f"Corp {corp_pct:.1f}% · Runner {100 - corp_pct:.1f}%</p>"
    )
    def linker(side):
        if not matchup_base:
            return None
        return lambda title: f"{matchup_base}{side}-{ident_slug(title)}.html"
    return (
        f"<div class='card'><h2>{esc(heading)}</h2>" + note
        + "<div class='grid2'>"
        + f"<div><h3>Corp</h3>{winrate_bars(wr['corp'], faction_of, min_games, link_of=linker('corp'))}</div>"
        + f"<div><h3>Runner</h3>{winrate_bars(wr['runner'], faction_of, min_games, link_of=linker('runner'))}</div>"
        + "</div></div>"
    )


def _faction_lookup(identity_rows_by_side):
    """{corp: {title: row}, runner: {...}} -> {정규화된 title: faction}"""
    lookup = {}
    for side_rows in identity_rows_by_side:
        for title, row in side_rows.items():
            lookup[norm_title(title).casefold()] = row.get("faction", "unknown")
    return lookup


def idtag(deck):
    """identity 태그 — 덱리스트가 올라온 경우에만 링크 (볼드 = 덱리스트 있음)."""
    if not deck:
        return ""
    f = deck["faction"] if deck["faction"] in FACTION_COLORS else "unknown"
    name = esc(shorten_identity(deck["identity"]))
    if deck.get("url"):
        name = (
            f"<a class='decklink' href='{esc(deck['url'])}' title='덱리스트 보기' "
            f"target='_blank' rel='noopener'>{name}</a>"
        )
    return f"<span class='idtag'><span class='dot' style='background:var(--f-{f})'></span>{name}</span>"


# 최근 우승 보드에 포함할 챔피언십 (Standard 경쟁 티어)
CHAMPIONSHIP_TYPES = {
    "worlds",
    "intercontinental championship",
    "continental championship",
    "megacity championship",
    "district championship",
}
def tier_filter_order(per_tournament, settings):
    """티어 필터 칩 순서 — (데이터에 있는 티어 + always 티어)를 위상(rank) 순으로."""
    tiers = settings.get("tiers") or {}
    present = {t.get("tier_label") or t["type"] or "?" for t in per_tournament}
    entries = {}
    for info in tiers.values():
        label = info.get("label")
        if label and (label in present or info.get("always")):
            entries[label] = info.get("rank", 99)
    for label in present:
        entries.setdefault(label, 99)
    return [l for l, _ in sorted(entries.items(), key=lambda kv: (kv[1], kv[0]))]


def champion_board(per_tournament):
    """공식 챔피언십(Standard) 우승 요약 표 — 전체, 10개씩 페이지네이션."""
    champs = [
        t for t in per_tournament
        if t["format"] == "standard" and t["type"] in CHAMPIONSHIP_TYPES and t["winner"]
    ]
    champs.sort(key=lambda t: t.get("date") or "", reverse=True)
    if not champs:
        return ""
    rows = []
    for t in champs:
        w = t["winner"]
        corp_full = w["corp"]["identity"] if w.get("corp") else ""
        runner_full = w["runner"]["identity"] if w.get("runner") else ""
        rows.append(
            f"<tr><td class='col-date'>{esc(short_date(t['date']))}</td>"
            f"<td><div class='cell-flex'><div class='trunc' title='{esc(t['title'])}'>"
            f"<a href='t/{t['id']}.html'>{esc(t['title'])}</a></div>"
            f"<span class='players'>({t['players']}명)</span></div></td>"
            f"<td><div class='trunc' title='{esc(corp_full)}'>{idtag(w.get('corp')) or '—'}</div></td>"
            f"<td><div class='trunc' title='{esc(runner_full)}'>{idtag(w.get('runner')) or '—'}</div></td></tr>"
        )
    return (
        "<div class='card'><h2>최근 공식 대회 우승</h2>"
        "<p class='agg-note'>Standard의 District/Megacity/Continentals/Worlds</p>"
        "<div data-ptable>"
        "<table class='champs'><thead><tr><th>날짜</th><th>대회</th>"
        "<th>Corp 우승</th><th>Runner 우승</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        "<div class='table-foot'><span class='pcount'></span><nav class='pager'></nav></div>"
        "</div></div>"
    )


def tier_badge(t):
    label = t.get("tier_label") or t["type"] or "?"
    cls = "badge comp" if t.get("formality") == "competitive" else "badge"
    return f"<span class='{cls}'>{esc(label)}</span>"


# ---------------------------------------------------------------- 페이지 블록

def side_section(stats_side, side, has_cut):
    pairs = faction_breakdown(stats_side, side)
    total = sum(r["count"] for _, r in pairs)
    side_label = "Corp" if side == "corp" else "Runner"
    note = (
        "<p class='agg-note'>표기: 출전/컷 진출/우승 · 바 색: 연함=출전, 진함=컷, 강조(맨 앞)=우승</p>"
        if has_cut
        else ""
    )
    return (
        f"<div class='card'><h2>{side_label}</h2>"
        + donut_block(pairs, total, side_label)
        + "<h3 style='margin-top:16px'>Identity별 엔트리</h3>"
        + note
        + bars_block(stats_side, has_cut)
        + "</div>"
    )


def toggle_html():
    return (
        "<div class='toggle-row'><input type='checkbox' id='comp-only'>"
        "<label for='comp-only'>경쟁 대회만 보기 (캐주얼 티어 제외)</label></div>"
    )


def agg_charts(tournaments, min_wr_games=None, matchup_base=None):
    if min_wr_games is None:
        # 표본이 작은 startup은 5게임, standard는 10게임 이상만 승률 표시
        is_startup = bool(tournaments) and tournaments[0].get("format") == "startup"
        min_wr_games = 5 if is_startup else 10
    tournaments, removed = strip_illegal(tournaments)
    agg = aggregate(tournaments)
    has_cut = any(t["cut_size"] > 0 for t in tournaments)
    removed_txt = (
        f" · 사용 불가 identity(밴/로테이션/미상) 엔트리 {removed}개 제외" if removed else ""
    )
    note = f"<p class='agg-note'>대회 {agg['tournaments']}개 · 엔트리 {agg['players']}{removed_txt}</p>"
    html_out = note + (
        "<div class='grid2'>"
        + side_section(agg["corp"], "corp", has_cut)
        + side_section(agg["runner"], "runner", has_cut)
        + "</div>"
    )
    wr = aggregate_winrates(tournaments)
    if wr:
        faction_of = _faction_lookup([agg["corp"], agg["runner"]])
        html_out += winrate_block(wr, faction_of, min_wr_games, matchup_base=matchup_base)
    return html_out


def agg_variants(tournaments, matchup_base=None):
    """캐주얼 포함(기본)/경쟁만 두 버전 — 체크박스로 전환."""
    comp = [t for t in tournaments if t.get("formality") == "competitive"]
    all_html = f"<div class='agg-all'>{agg_charts(tournaments, matchup_base=matchup_base)}</div>"
    if comp:
        comp_html = f"<div class='agg-comp'>{agg_charts(comp, matchup_base=matchup_base)}</div>"
    else:
        comp_html = "<div class='agg-comp'><p class='sub'>이 그룹에는 경쟁 티어 대회가 없습니다.</p></div>"
    return all_html + comp_html


def short_date(date_str):
    """'2026.06.27.' -> '26.06.27' (모바일용)."""
    parts = str(date_str).strip(".").split(".")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[0][2:]}.{parts[1]}.{parts[2]}"
    return date_str


def tournament_table(tournaments, prefix="", show_meta=True, tier_order=None):
    """페이지네이션(10개/페이지) + 티어 필터가 붙는 대회 목록. JS 없으면 전체 표시.

    모바일(<=640px)에서는 인원/우승 열을 숨기고 행을 탭하면 펼쳐서 보여준다.
    """
    rows = []
    for t in sorted(tournaments, key=lambda x: x.get("date") or "", reverse=True):
        w = t["winner"]
        winner = (
            f"{esc(w['player'])} {idtag(w.get('corp'))} {idtag(w.get('runner'))}" if w else "—"
        )
        meta_td = ""
        if show_meta:
            ban = t.get("banlist")
            meta = f"{FORMAT_LABELS.get(t['format'], t['format'])}"
            tip = f"{t['cardpool']}" + (f" · {ban}" if ban else "")
            meta_td = f"<td title='{esc(tip)}'>{esc(meta)}<div class='n' style='font-size:11.5px;color:var(--muted)'>{esc(t['cardpool'])}{' · ' + ban if ban else ''}</div></td>"
        tier = t.get("tier_label") or t["type"] or "?"
        fmt_label = FORMAT_LABELS.get(t["format"], t["format"] or "?")
        mob_extra = (
            f"<div class='mob-extra'>인원 {t['players']}명 · 우승: {winner}</div>"
        )
        rows.append(
            f"<tr data-tier='{esc(tier)}' data-format='{esc(fmt_label)}'>"
            f"<td class='col-date'><span class='d-full'>{esc(t['date'])}</span>"
            f"<span class='d-short'>{esc(short_date(t['date']))}</span></td>"
            f"<td><a href='{prefix}t/{t['id']}.html'>{esc(t['title'])}</a>{mob_extra}</td>"
            + meta_td
            + f"<td>{tier_badge(t)}</td>"
            f"<td class='num col-players'>{t['players']}</td>"
            f"<td class='col-winner'>{winner}</td></tr>"
        )
    meta_th = "<th>포맷</th>" if show_meta else ""
    fmt_filter = (
        "<div class='fmt-filter'><span class='chips-label'>포맷 필터</span></div>"
        if show_meta
        else ""
    )
    pinned = esc("|".join(tier_order)) if tier_order else ""
    return (
        f"<div data-ptable data-pinned='{pinned}'>"
        "<div class='tier-chips'><span class='chips-label'>티어 필터</span></div>"
        + fmt_filter
        + "<table><thead><tr><th>날짜</th><th>대회</th>"
        + meta_th
        + "<th>티어</th><th class='num col-players'>인원</th><th class='col-winner'>우승</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
        "<div class='table-foot'><span class='pcount'></span><nav class='pager'></nav></div>"
        "</div>"
    )


def tier_legend(tournaments, settings):
    """티어 안내 — 데이터에 등장하는 티어 + always 지정 티어, 위상 순."""
    tiers = settings.get("tiers") or {}
    present = {}
    for t in tournaments:
        info = tiers.get(t["type"]) or {}
        label = info.get("label") or t["type"] or "?"
        if label not in present:
            present[label] = (
                info.get("rank", 99),
                info.get("formality", "casual"),
                info.get("desc", ""),
            )
    # 아직 대회가 없어도 항상 안내에 포함할 티어 (always: true)
    for info in tiers.values():
        if info.get("always") and info.get("label") not in present:
            present[info["label"]] = (
                info.get("rank", 99),
                info.get("formality", "casual"),
                info.get("desc", ""),
            )
    if not present:
        return ""
    rows = []
    for label, (rank, formality, desc) in sorted(present.items(), key=lambda kv: (kv[1][0], kv[0])):
        cls = "badge comp" if formality == "competitive" else "badge"
        kind = "경쟁" if formality == "competitive" else "캐주얼"
        rows.append(
            f"<tr><td><span class='{cls}'>{esc(label)}</span></td>"
            f"<td style='white-space:nowrap'>{esc(kind)}</td><td>{esc(desc)}</td></tr>"
        )
    return (
        "<div class='card'><h2>대회 티어 안내</h2>"
        "<p class='agg-note'>위상이 높은 순서. '경쟁' 티어가 상단 통계의 \"경쟁 대회만 보기\"에 포함되는 대회입니다.</p>"
        "<table><thead><tr><th>티어</th><th>구분</th><th>설명</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


PTABLE_JS = """
document.querySelectorAll('[data-ptable]').forEach(function (wrap) {
  var PER = 10;
  var rows = Array.prototype.slice.call(wrap.querySelectorAll('tbody tr'));
  if (!rows.length) return;
  var chipBox = wrap.querySelector('.tier-chips');
  var pager = wrap.querySelector('.pager');
  var pcount = wrap.querySelector('.pcount');
  var page = 1;
  var active = null;   // null이면 티어 필터 없음 (페이지네이션만)
  if (chipBox) {
    var counts = {};
    rows.forEach(function (r) { var t = r.dataset.tier; counts[t] = (counts[t] || 0) + 1; });
    // 항상 노출할 티어(대회 0개 포함) 먼저, 나머지는 관측 순서대로
    var pinned = (wrap.dataset.pinned || '').split('|').filter(Boolean);
    var tierNames = pinned.slice();
    pinned.forEach(function (t) { if (!(t in counts)) counts[t] = 0; });
    Object.keys(counts).forEach(function (t) {
      if (tierNames.indexOf(t) === -1) tierNames.push(t);
    });
    active = {};
    tierNames.forEach(function (t) { active[t] = true; });

    tierNames.forEach(function (t) {
      var lab = document.createElement('label');
      lab.className = 'chip on';
      var cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = true;
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(' ' + t + ' '));
      var n = document.createElement('span'); n.textContent = counts[t];
      lab.appendChild(n);
      cb.addEventListener('change', function () {
        active[t] = cb.checked;
        lab.classList.toggle('on', cb.checked);
        page = 1; render();
      });
      chipBox.appendChild(lab);
    });
    if (tierNames.length > 1) {
      var allBtn = document.createElement('button');
      allBtn.type = 'button'; allBtn.className = 'chip-all'; allBtn.textContent = '전체 선택/해제';
      allBtn.addEventListener('click', function () {
        var anyOff = tierNames.some(function (t) { return !active[t]; });
        chipBox.querySelectorAll('label.chip input').forEach(function (cb) {
          if (cb.checked !== anyOff) { cb.checked = anyOff; cb.dispatchEvent(new Event('change')); }
        });
      });
      chipBox.appendChild(allBtn);
    }
  }

  rows.forEach(function (r) {
    r.addEventListener('click', function (e) {
      if (e.target.closest('a, input, label, button')) return;
      r.classList.toggle('open');
    });
  });

  // 포맷 필터 (텍스트 토글) — 컨테이너가 있고 포맷이 2개 이상일 때만
  var fmtBox = wrap.querySelector('.fmt-filter');
  var activeF = null;
  if (fmtBox) {
    var fcounts = {};
    rows.forEach(function (r) {
      var f = r.dataset.format;
      if (f) fcounts[f] = (fcounts[f] || 0) + 1;
    });
    var fmts = Object.keys(fcounts);
    if (fmts.length > 1) {
      activeF = {};
      fmts.forEach(function (f) { activeF[f] = true; });
      fmts.forEach(function (f) {
        var b = document.createElement('button');
        b.type = 'button'; b.className = 'fmtbtn';
        b.appendChild(document.createTextNode(f + ' '));
        var n = document.createElement('span'); n.className = 'n'; n.textContent = fcounts[f];
        b.appendChild(n);
        b.addEventListener('click', function () {
          activeF[f] = !activeF[f];
          b.classList.toggle('off', !activeF[f]);
          page = 1; render();
        });
        fmtBox.appendChild(b);
      });
    } else {
      fmtBox.style.display = 'none';
    }
  }

  function render() {
    var vis = rows.filter(function (r) {
      if (active && !active[r.dataset.tier]) return false;
      if (activeF && r.dataset.format && !activeF[r.dataset.format]) return false;
      return true;
    });
    rows.forEach(function (r) { r.style.display = 'none'; });
    var pages = Math.max(1, Math.ceil(vis.length / PER));
    if (page > pages) page = pages;
    vis.slice((page - 1) * PER, page * PER).forEach(function (r) { r.style.display = ''; });
    pcount.textContent = vis.length
      ? vis.length + '개 대회' + (pages > 1 ? ' · ' + page + '/' + pages + ' 페이지' : '')
      : '조건에 맞는 대회가 없습니다 — 위의 티어/포맷 필터를 확인하세요';
    pager.innerHTML = '';
    if (pages > 1) {
      for (var p = 1; p <= pages; p++) {
        (function (p) {
          var b = document.createElement('button');
          b.type = 'button'; b.textContent = p;
          if (p === page) b.className = 'cur';
          b.addEventListener('click', function () { page = p; render(); });
          pager.appendChild(b);
        })(p);
      }
    }
  }
  render();
});
"""


# ---------------------------------------------------------------- 페이지 렌더링

def render_index(per_tournament, settings):
    groups, order = group_by_meta(per_tournament)
    n_comp = sum(1 for t in per_tournament if t.get("formality") == "competitive")
    total_entries = sum(t["players"] for t in per_tournament)

    head = f"""
<h1>{esc(settings.get('site_title', 'Netrunner Meta Tracker'))}</h1>
<p class="sub">{esc(settings.get('site_subtitle', ''))} — 시즌 {esc(settings.get('season_start', ''))}~</p>
<div class="kpis">
<div class="kpi"><div class="label">대회</div><div class="value">{len(per_tournament)}</div></div>
<div class="kpi"><div class="label">경쟁 티어 대회</div><div class="value">{n_comp}</div></div>
<div class="kpi"><div class="label">총 엔트리</div><div class="value">{total_entries}</div></div>
<div class="kpi"><div class="label">메타</div><div class="value">{len(groups)}</div></div>
</div>
""" + toggle_html()

    sections = []
    seen_formats = []
    for key in order:
        if key[0] not in seen_formats:
            seen_formats.append(key[0])
    for fmt in seen_formats:
        fmt_keys = [k for k in order if k[0] == fmt]
        latest = fmt_keys[0]
        sec = [f"<h2 style='font-size:22px;margin:28px 0 12px'>{esc(FORMAT_LABELS.get(fmt, fmt))}</h2>"]
        sec.append(
            f"<div class='card'><h2>{esc(meta_label(latest))} <span class='badge'>현재 메타</span></h2>"
            + agg_variants(groups[latest], matchup_base=f"matchup/{meta_slug(latest)}/")
            + "</div>"
        )
        if len(fmt_keys) > 1:
            links = ["<div class='card'><h3>이전 메타</h3><ul class='meta-links'>"]
            for k in fmt_keys[1:]:
                ts = groups[k]
                dates = sorted(t["date"] for t in ts)
                links.append(
                    f"<li><a href='meta/{meta_slug(k)}.html'>{esc(meta_label(k))}</a> "
                    f"<span class='n'>대회 {len(ts)}개 · {esc(dates[0])}~{esc(dates[-1])}</span></li>"
                )
            links.append("</ul></div>")
            sec.append("".join(links))
        sections.append("".join(sec))

    champs = champion_board(per_tournament)
    table = (
        "<div class='card'><h2>전체 대회 목록</h2>"
        + tournament_table(per_tournament, tier_order=tier_filter_order(per_tournament, settings))
        + "</div>"
    )
    legend = tier_legend(per_tournament, settings)
    return page(
        settings.get("site_title", "Netrunner Meta Tracker"),
        head + "".join(sections) + champs + table + legend,
        scripts=PTABLE_JS,
    )


def render_meta(key, tournaments, settings):
    label = meta_label(key)
    dates = sorted(t["date"] for t in tournaments)
    head = f"""
<p class="crumb"><a href="../index.html">← 전체 통계</a></p>
<h1>{esc(label)}</h1>
<p class="sub">{esc(dates[0])} ~ {esc(dates[-1])} · 대회 {len(tournaments)}개</p>
""" + toggle_html()
    body = agg_variants(tournaments, matchup_base=f"../matchup/{meta_slug(key)}/")
    table = (
        "<div class='card'><h2>대회 목록</h2>"
        + tournament_table(tournaments, prefix="../", show_meta=False)
        + "</div>"
    )
    return page(label, head + body + table, scripts=PTABLE_JS, root="../")


def render_tournament(t, settings):
    has_cut = t["cut_size"] > 0
    ban = t.get("banlist")
    meta_txt = f"{FORMAT_LABELS.get(t['format'], t['format'])} · {t['cardpool']}" + (
        f" · {ban}" if ban else ""
    )
    head = f"""
<p class="crumb"><a href="../index.html">← 전체 통계</a></p>
<h1>{esc(t['title'])}</h1>
<p class="sub">{esc(t['date'])} · {tier_badge(t)} · <b>{esc(meta_txt)}</b> · {t['players']}명
{'· 탑컷 ' + str(t['cut_size']) + '명' if has_cut else ''}
{f"· <a href='https://alwaysberunning.net/tournaments/{t['id']}' target='_blank' rel='noopener'>ABR에서 보기</a>" if t['id'] else ''}</p>
"""
    winner_html = ""
    if t["winner"]:
        w = t["winner"]
        winner_html = (
            "<div class='card'><h3>우승</h3><div class='winner'>"
            f"<div class='who'>{esc(w['player'])}</div>"
            f"<div class='winner-decks'>{idtag(w.get('corp'))} {idtag(w.get('runner'))}</div>"
            "</div></div>"
        )
    charts = (
        "<div class='grid2'>"
        + side_section(t["corp"], "corp", has_cut)
        + side_section(t["runner"], "runner", has_cut)
        + "</div>"
    )
    if t.get("winrates"):
        faction_of = _faction_lookup([t["corp"], t["runner"]])
        charts += winrate_block(
            t["winrates"], faction_of, min_games=3,
            heading="이 대회의 identity 승률",
            matchup_base=f"../matchup/{meta_slug(meta_key(t))}/",
        )
    rows = []
    for s in t["standings"]:
        rank = s["rank_top"] or s["rank_swiss"] or ""
        rows.append(
            f"<tr><td class='num'>{rank}</td><td>{esc(s['player'])}</td>"
            f"<td>{idtag(s.get('corp'))}</td><td>{idtag(s.get('runner'))}</td>"
            f"<td class='num'>{s['rank_swiss'] or ''}</td></tr>"
        )
    table = (
        "<div class='card'><h2>순위표</h2>"
        "<p class='agg-note'>진하게 표시된 identity는 클릭하면 업로드된 덱리스트로 이동합니다.</p><table class='standings'>"
        "<thead><tr><th class='num'>#</th><th>플레이어</th><th>Corp</th><th>Runner</th>"
        "<th class='num'>스위스</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    return page(t["title"], head + winner_html + charts + table, root="../")


def _group_decks(ts, side, title):
    """메타 그룹 대회들에서 (side, identity)의 카드 확보된 공개 덱 목록."""
    key = norm_title(title).casefold()
    return [
        d
        for t in ts
        for d in t.get("decks", [])
        if d["side"] == side and norm_title(d["identity"]).casefold() == key and d.get("cards")
    ]


def card_blocks(decks, color_css, card_idx, baseline_wr=None):
    """identity의 카드 통계 — 채용률 top 15 + 카드별 승률."""
    n = len(decks)
    if n < 3:
        return "<p class='sub'>공개된 덱리스트 표본이 부족해 카드 통계를 생략합니다 (3덱 미만).</p>"
    stat = defaultdict(lambda: {"decks": 0, "qty": 0, "wins": 0.0, "games": 0})
    for d in decks:
        for code, q in d["cards"].items():
            code = str(code)
            info = card_idx.get(code) or {}
            if info.get("type") == "identity":
                continue
            s = stat[code]
            s["decks"] += 1
            try:
                s["qty"] += int(q)
            except (TypeError, ValueError):
                s["qty"] += 1
            s["wins"] += d["wins"]
            s["games"] += d["games"]

    def card_name(code):
        t = (card_idx.get(code) or {}).get("title") or f"#{code}"
        return (
            f"<a href='https://netrunnerdb.com/en/card/{esc(code)}' "
            f"target='_blank' rel='noopener'>{esc(t)}</a>"
        )

    # 1) 채용률 top 15
    top = sorted(stat.items(), key=lambda kv: (-kv[1]["decks"], kv[0]))[:15]
    incl = ["<div class='bars'>"]
    for code, s in top:
        pct = s["decks"] / n * 100
        avg = s["qty"] / s["decks"]
        tip = f"{s['decks']}/{n}덱 채용 · 평균 {avg:.1f}장"
        incl.append(f"<div class='name' title='{esc(tip)}'>{card_name(code)}</div>")
        incl.append(
            f'<svg viewBox="0 0 100 18" preserveAspectRatio="none" role="img" aria-label="{esc(tip)}">'
            f'<rect x="0" y="2" width="{max(pct, 1):.1f}" height="14" rx="1.2" fill="{color_css}" opacity="0.55"/>'
            f"<title>{esc(tip)}</title></svg>"
        )
        incl.append(f"<div class='val'>{pct:.0f}% · x{avg:.1f}</div>")
    incl.append("</div>")

    # 2) 카드별 승률 (충분한 표본 + 변별력 있는 카드만)
    wr_rows = [
        (code, s, s["wins"] / s["games"] * 100)
        for code, s in stat.items()
        if s["games"] >= 20 and s["decks"] >= 4 and s["decks"] <= n * 0.95
    ]
    wr_rows.sort(key=lambda x: -x[2])
    wr_rows = wr_rows[:15]
    if wr_rows:
        wrb = ["<div class='bars wr'>"]
        for code, s, wrv in wr_rows:
            tip = f"채용 덱 승률 {wrv:.1f}% · {s['decks']}덱 {s['games']}게임"
            wrb.append(f"<div class='name' title='{esc(tip)}'>{card_name(code)}</div>")
            wrb.append(
                f'<svg viewBox="0 0 100 18" preserveAspectRatio="none" role="img" aria-label="{esc(tip)}">'
                f'<rect x="0" y="2" width="{max(wrv, 1.0):.2f}" height="14" rx="1.2" fill="{color_css}"/>'
                '<rect x="49.6" y="0" width="0.8" height="18" fill="var(--baseline)"/>'
                f"<title>{esc(tip)}</title></svg>"
            )
            wrb.append(f"<div class='val'>{wrv:.0f}% · {s['decks']}덱</div>")
        wrb.append("</div>")
        wr_html = "".join(wrb)
    else:
        wr_html = "<p class='sub'>표본 기준(4덱·20게임 이상)을 만족하는 카드가 없습니다.</p>"

    base_txt = f" · 이 identity 평균 승률 {baseline_wr:.0f}%" if baseline_wr is not None else ""
    return (
        f"<h3 style='margin-top:18px'>많이 쓰는 카드</h3>"
        f"<p class='agg-note'>덱리스트 공개 {n}덱 기준 · 채용률 · x = 평균 장수</p>"
        + "".join(incl)
        + "<h3 style='margin-top:18px'>카드별 승률</h3>"
        f"<p class='agg-note'>해당 카드를 채용한 덱들의 게임 승률 · 4덱/20게임 이상 · "
        f"항상 채용되는 카드(95%+)는 제외{base_txt}</p>"
        + wr_html
    )


def _matchup_rows(wr, side, title):
    """매치업 딕셔너리에서 title(side) 관점의 상대별 전적을 뽑는다.

    반환: {상대 identity: {"wins": title 기준 승수, "ties", "games"}}
    """
    rows = {}
    key_norm = norm_title(title)
    for key, mu in (wr.get("matchups") or {}).items():
        corp_t, _, runner_t = key.partition("\t")
        if side == "corp" and corp_t == key_norm:
            opp, wins = runner_t, mu["corp_wins"]
        elif side == "runner" and runner_t == key_norm:
            opp, wins = corp_t, mu["games"] - mu["corp_wins"]
        else:
            continue
        rows[opp] = {"wins": wins, "ties": mu["ties"], "games": mu["games"]}
    return rows


def render_matchup(key, side, title, wr_all, wr_comp, ts_all, ts_comp, faction_of, nrdb_of, card_idx):
    """identity 하나의 매치업별 승률 + 카드 통계 페이지."""
    opp_side = "runner" if side == "corp" else "corp"
    side_label = "Corp" if side == "corp" else "Runner"
    opp_label = "Runner" if side == "corp" else "Corp"
    faction = faction_of.get(norm_title(title).casefold(), "unknown")
    f_css = faction if faction in FACTION_COLORS else "unknown"
    nrdb_id = nrdb_of.get(norm_title(title).casefold())
    title_html = esc(shorten_identity(title))
    if nrdb_id:
        title_html = (
            f"<a href='https://netrunnerdb.com/en/card/{esc(nrdb_id)}' target='_blank' "
            f"rel='noopener' title='NetrunnerDB에서 카드 보기'>{title_html}</a>"
        )

    def variant(wr, ts):
        if not wr or norm_title(title) not in wr.get(side, {}):
            return "<p class='sub'>이 구분에는 이 identity의 게임 기록이 없습니다.</p>"
        me = wr[side][norm_title(title)]
        my_wr = me["wins"] / me["games"] * 100
        tie_txt = f" · 무 {me['ties']}" if me["ties"] else ""
        head = (
            f"<p style='margin:0 0 14px;font-size:14.5px'><b>종합 승률 {my_wr:.1f}%</b>"
            f" · {me['games']}판{tie_txt}</p>"
        )
        rows = _matchup_rows(wr, side, title)
        link_of = lambda opp: f"{opp_side}-{ident_slug(opp)}.html"
        bars = winrate_bars(rows, faction_of, min_games=1, link_of=link_of, sort_by="games")
        cards = card_blocks(
            _group_decks(ts, side, title),
            f"var(--f-{f_css})",
            card_idx,
            baseline_wr=my_wr,
        )
        return head + f"<h3>상대 {opp_label} identity별 승률 (게임 수 순)</h3>" + bars + cards

    body = f"""
<p class="crumb"><a href="../../index.html">← 전체 통계</a> ·
<a href="../../meta/{meta_slug(key)}.html">{esc(meta_label(key))}</a></p>
<h1><span class='dot' style='display:inline-block;width:14px;height:14px;border-radius:4px;background:var(--f-{f_css});margin-right:6px'></span>{title_html}</h1>
<p class="sub">{side_label} · {esc(meta_label(key))}</p>
""" + toggle_html() + (
        f"<div class='card'><div class='agg-all'>{variant(wr_all, ts_all)}</div>"
        f"<div class='agg-comp'>{variant(wr_comp, ts_comp)}</div>"
        "<p class='agg-note' style='margin-top:10px'>상대 identity를 클릭하면 그쪽 관점의 매치업 페이지로 이동합니다. "
        "표본이 작은 매치업(수 판 이하)은 참고만 하세요.</p></div>"
    )
    return page(f"{shorten_identity(title)} 매치업", body, root="../../")


# ---------------------------------------------------------------- 빌드

def _illegal_titles(t, settings):
    """이 대회의 메타에서 사용 불가능한 identity 제목들.

    - 카드 코드가 Standard 합법 범위 밖 (로테이션된 옛 ID, 자리표시자 등)
    - 대회 시점 밴리스트에 밴된 ID (standard만, NRDB 자동 동기화)
    코드 미상 identity는 판단하지 않고 유지.
    """
    ranges = settings.get("standard_legal_id_ranges") or []
    banned = (settings.get("_banned_by_version") or {}).get(t.get("banlist") or "") or set()
    bad = set()
    for side in ("corp", "runner"):
        for title, row in t[side].items():
            code = row.get("nrdb_id")
            if not code:
                continue
            try:
                c = int(code)
            except (TypeError, ValueError):
                continue
            if ranges and not any(lo <= c <= hi for lo, hi in ranges):
                bad.add(title)
            elif t["format"] == "standard" and str(code) in banned:
                bad.add(title)
    return bad


def strip_illegal(tournaments):
    """집계용 사본 — 사용 불가 identity의 엔트리/승률/매치업을 제거.

    대회 상세 페이지는 원본(t)을 그대로 쓰므로 영향 없음.
    반환: (필터된 목록, 제외된 엔트리 수)
    """
    out, removed = [], 0
    for t in tournaments:
        bad = t.get("_illegal") or set()
        if not bad:
            out.append(t)
            continue
        t2 = dict(t)
        for side in ("corp", "runner"):
            t2[side] = {k: v for k, v in t[side].items() if k not in bad}
            removed += sum(v["count"] for k, v in t[side].items() if k in bad)
        wr = t.get("winrates")
        if wr:
            badn = {norm_title(b).casefold() for b in bad}
            wr2 = dict(wr)
            for side in ("corp", "runner"):
                wr2[side] = {
                    k: v for k, v in wr[side].items() if norm_title(k).casefold() not in badn
                }
            wr2["matchups"] = {
                k: v
                for k, v in (wr.get("matchups") or {}).items()
                if not any(norm_title(p).casefold() in badn for p in k.split("\t"))
            }
            t2["winrates"] = wr2
        out.append(t2)
    return out, removed


def annotate(per_tournament, settings):
    tiers = settings.get("tiers") or {}
    for t in per_tournament:
        info = tiers.get(t["type"]) or {}
        t["formality"] = info.get("formality", "casual")
        t["tier_label"] = info.get("label")
        t["banlist"] = derive_banlist(t, settings)
        t["cardpool"] = derive_cardpool(t, settings)
        t["_illegal"] = _illegal_titles(t, settings)
    return per_tournament


def build_site(per_tournament, settings):
    annotate(per_tournament, settings)
    DOCS.mkdir(parents=True, exist_ok=True)
    # 생성 페이지 디렉터리는 매번 비우고 다시 만든다 (슬러그가 바뀌어도 고아 파일 없음)
    for sub in ("t", "meta", "matchup"):
        shutil.rmtree(DOCS / sub, ignore_errors=True)
    (DOCS / "t").mkdir(exist_ok=True)
    (DOCS / "meta").mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("")
    (DOCS / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")
    (DOCS / "index.html").write_text(render_index(per_tournament, settings), encoding="utf-8")
    groups, order = group_by_meta(per_tournament)
    for key in order:
        (DOCS / "meta" / f"{meta_slug(key)}.html").write_text(
            render_meta(key, groups[key], settings), encoding="utf-8"
        )
        # 매치업 상세 페이지 (메타 그룹 x identity) — 사용 불가 identity 제외
        ts, _ = strip_illegal(groups[key])
        wr_all = aggregate_winrates(ts)
        if not wr_all:
            continue
        comp = [t for t in ts if t.get("formality") == "competitive"]
        wr_comp = aggregate_winrates(comp)
        agg = aggregate(ts)
        faction_of = _faction_lookup([agg["corp"], agg["runner"]])
        nrdb_of = {
            norm_title(t2).casefold(): row.get("nrdb_id")
            for side_rows in (agg["corp"], agg["runner"])
            for t2, row in side_rows.items()
            if row.get("nrdb_id")
        }
        mdir = DOCS / "matchup" / meta_slug(key)
        mdir.mkdir(parents=True, exist_ok=True)
        for side in ("corp", "runner"):
            for title in wr_all[side]:
                (mdir / f"{side}-{ident_slug(title)}.html").write_text(
                    render_matchup(
                        key, side, title, wr_all, wr_comp, ts, comp,
                        faction_of, nrdb_of, settings.get("_card_index") or {},
                    ),
                    encoding="utf-8",
                )
    for t in per_tournament:
        (DOCS / "t" / f"{t['id']}.html").write_text(render_tournament(t, settings), encoding="utf-8")
    summary = {
        "season_start": settings.get("season_start"),
        "tournaments": [
            {
                "id": t["id"],
                "title": t["title"],
                "date": t["date"],
                "type": t["type"],
                "format": t["format"],
                "cardpool": t["cardpool"],
                "banlist": t.get("banlist"),
                "formality": t["formality"],
                "players": t["players"],
                "cut_size": t["cut_size"],
                "winner": t["winner"],
                "corp": t["corp"],
                "runner": t["runner"],
                "winrates": t.get("winrates"),
            }
            for t in per_tournament
        ],
    }
    (DOCS / "data").mkdir(exist_ok=True)
    with open(DOCS / "data" / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return DOCS / "index.html"
