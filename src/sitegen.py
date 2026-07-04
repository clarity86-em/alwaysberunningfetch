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
from collections import defaultdict
from pathlib import Path

from stats import FACTION_LABELS, aggregate, faction_breakdown, shorten_identity

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


def meta_key(t):
    return (t["format"], t["cardpool"], banlist_version(t["mwl"]))


def meta_label(key):
    fmt, cardpool, ban = key
    parts = [FORMAT_LABELS.get(fmt, fmt or "?"), cardpool]
    parts.append(f"밴리스트 {ban}" if ban else "밴리스트 미상")
    return " · ".join(parts)


def meta_slug(key):
    fmt, cardpool, ban = key
    raw = f"{fmt}-{cardpool}-{ban or 'etc'}"
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
{light}
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --page: #0d0d0d; --surface: #1a1a19;
    --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --baseline: #383835;
    --border: rgba(255,255,255,0.10);
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
.badge.comp {{ color: var(--ink); border-color: var(--baseline); }}
.winner {{ display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }}
.winner .who {{ font-size: 17px; font-weight: 600; }}
.idtag {{ font-size: 13px; color: var(--ink-2); }}
.idtag .dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 3px; margin-right: 5px; }}
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
"""


def page(title, body):
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex">
<title>{esc(title)}</title>
<style>{base_css()}</style>
</head>
<body>
<main>
{body}
<footer>Data: <a href="https://alwaysberunning.net">alwaysberunning.net</a> ·
Identities: <a href="https://netrunnerdb.com">NetrunnerDB</a> ·
자동 생성 (<a href="https://github.com/clarity86-em/alwaysberunningfetch">alwaysberunningfetch</a>)</footer>
</main>
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
        w_total = row["count"] / max_count * 100
        w_cut = row["cut"] / max_count * 100
        color = f"var(--f-{row['faction'] if row['faction'] in FACTION_COLORS else 'unknown'})"
        cut_txt = f" · 컷 {row['cut']}" if (has_cut and row["cut"]) else ""
        tip = f"{title}: {row['count']} entries" + (f", top cut {row['cut']}" if row["cut"] else "")
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
        bar.append(f"<title>{esc(tip)}</title></svg>")
        out.append(f"<div class='name' title='{esc(title)}'>{esc(shorten_identity(title))}</div>")
        out.append("".join(bar))
        out.append(f"<div class='val'>{row['count']}{cut_txt}</div>")
    out.append("</div>")
    return "".join(out)


def idtag(deck):
    if not deck:
        return ""
    f = deck["faction"] if deck["faction"] in FACTION_COLORS else "unknown"
    name = esc(shorten_identity(deck["identity"]))
    if deck.get("url"):
        name = f"<a href='{esc(deck['url'])}'>{name}</a>"
    return f"<span class='idtag'><span class='dot' style='background:var(--f-{f})'></span>{name}</span>"


def tier_badge(t):
    label = t.get("tier_label") or t["type"] or "?"
    cls = "badge comp" if t.get("formality") == "competitive" else "badge"
    return f"<span class='{cls}'>{esc(label)}</span>"


# ---------------------------------------------------------------- 페이지 블록

def side_section(stats_side, side, has_cut):
    pairs = faction_breakdown(stats_side, side)
    total = sum(r["count"] for _, r in pairs)
    side_label = "Corp" if side == "corp" else "Runner"
    return (
        f"<div class='card'><h2>{side_label}</h2>"
        + donut_block(pairs, total, side_label)
        + f"<h3 style='margin-top:16px'>Identity별 엔트리{' (진한 부분 = 탑컷 진출)' if has_cut else ''}</h3>"
        + bars_block(stats_side, has_cut)
        + "</div>"
    )


def toggle_html():
    return (
        "<div class='toggle-row'><input type='checkbox' id='comp-only'>"
        "<label for='comp-only'>경쟁 대회만 보기 (캐주얼 티어 제외)</label></div>"
    )


def agg_charts(tournaments):
    agg = aggregate(tournaments)
    has_cut = any(t["cut_size"] > 0 for t in tournaments)
    note = f"<p class='agg-note'>대회 {agg['tournaments']}개 · 엔트리 {agg['players']}</p>"
    return note + (
        "<div class='grid2'>"
        + side_section(agg["corp"], "corp", has_cut)
        + side_section(agg["runner"], "runner", has_cut)
        + "</div>"
    )


def agg_variants(tournaments):
    """캐주얼 포함(기본)/경쟁만 두 버전 — 체크박스로 전환."""
    comp = [t for t in tournaments if t.get("formality") == "competitive"]
    all_html = f"<div class='agg-all'>{agg_charts(tournaments)}</div>"
    if comp:
        comp_html = f"<div class='agg-comp'>{agg_charts(comp)}</div>"
    else:
        comp_html = "<div class='agg-comp'><p class='sub'>이 그룹에는 경쟁 티어 대회가 없습니다.</p></div>"
    return all_html + comp_html


def tournament_table(tournaments, prefix="", show_meta=True):
    rows = []
    for t in sorted(tournaments, key=lambda x: x.get("date") or "", reverse=True):
        w = t["winner"]
        winner = (
            f"{esc(w['player'])} {idtag(w.get('corp'))} {idtag(w.get('runner'))}" if w else "—"
        )
        meta_td = ""
        if show_meta:
            ban = banlist_version(t["mwl"])
            meta = f"{FORMAT_LABELS.get(t['format'], t['format'])}"
            tip = f"{t['cardpool']}" + (f" · 밴리스트 {ban}" if ban else "")
            meta_td = f"<td title='{esc(tip)}'>{esc(meta)}<div class='n' style='font-size:11.5px;color:var(--muted)'>{esc(t['cardpool'])}{' · ' + ban if ban else ''}</div></td>"
        rows.append(
            f"<tr><td>{esc(t['date'])}</td>"
            f"<td><a href='{prefix}t/{t['id']}.html'>{esc(t['title'])}</a></td>"
            + meta_td
            + f"<td>{tier_badge(t)}</td>"
            f"<td class='num'>{t['players']}</td><td>{winner}</td></tr>"
        )
    meta_th = "<th>포맷</th>" if show_meta else ""
    return (
        "<table><thead><tr><th>날짜</th><th>대회</th>"
        + meta_th
        + "<th>티어</th><th class='num'>인원</th><th>우승</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


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
<div class="kpi"><div class="label">메타 (포맷×카드풀×밴리스트)</div><div class="value">{len(groups)}</div></div>
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
            + agg_variants(groups[latest])
            + f"<p style='margin:12px 0 0'><a href='meta/{meta_slug(latest)}.html'>이 메타의 대회 목록 →</a></p></div>"
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

    table = (
        "<div class='card'><h2>전체 대회 목록</h2>"
        + tournament_table(per_tournament)
        + "</div>"
    )
    return page(
        settings.get("site_title", "Netrunner Meta Tracker"),
        head + "".join(sections) + table,
    )


def render_meta(key, tournaments, settings):
    label = meta_label(key)
    dates = sorted(t["date"] for t in tournaments)
    head = f"""
<p class="crumb"><a href="../index.html">← 전체 통계</a></p>
<h1>{esc(label)}</h1>
<p class="sub">{esc(dates[0])} ~ {esc(dates[-1])} · 대회 {len(tournaments)}개</p>
""" + toggle_html()
    body = agg_variants(tournaments)
    table = (
        "<div class='card'><h2>대회 목록</h2>"
        + tournament_table(tournaments, prefix="../", show_meta=False)
        + "</div>"
    )
    return page(label, head + body + table)


def render_tournament(t, settings):
    has_cut = t["cut_size"] > 0
    ban = banlist_version(t["mwl"])
    meta_txt = f"{FORMAT_LABELS.get(t['format'], t['format'])} · {t['cardpool']}" + (
        f" · 밴리스트 {ban}" if ban else ""
    )
    head = f"""
<p class="crumb"><a href="../index.html">← 전체 통계</a></p>
<h1>{esc(t['title'])}</h1>
<p class="sub">{esc(t['date'])} · {tier_badge(t)} · <b>{esc(meta_txt)}</b> · {t['players']}명
{'· 탑컷 ' + str(t['cut_size']) + '명' if has_cut else ''}
{f"· <a href='https://alwaysberunning.net/tournaments/{t['id']}'>ABR에서 보기</a>" if t['id'] else ''}</p>
"""
    winner_html = ""
    if t["winner"]:
        w = t["winner"]
        winner_html = (
            "<div class='card'><h3>우승</h3><div class='winner'>"
            f"<span class='who'>{esc(w['player'])}</span>"
            f"{idtag(w.get('corp'))} {idtag(w.get('runner'))}</div></div>"
        )
    charts = (
        "<div class='grid2'>"
        + side_section(t["corp"], "corp", has_cut)
        + side_section(t["runner"], "runner", has_cut)
        + "</div>"
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
        "<div class='card'><h2>순위표</h2><table>"
        "<thead><tr><th class='num'>#</th><th>플레이어</th><th>Corp</th><th>Runner</th>"
        "<th class='num'>스위스</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    return page(t["title"], head + winner_html + charts + table)


# ---------------------------------------------------------------- 빌드

def annotate(per_tournament, settings):
    tiers = settings.get("tiers") or {}
    for t in per_tournament:
        info = tiers.get(t["type"]) or {}
        t["formality"] = info.get("formality", "casual")
        t["tier_label"] = info.get("label")
    return per_tournament


def build_site(per_tournament, settings):
    annotate(per_tournament, settings)
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "t").mkdir(exist_ok=True)
    (DOCS / "meta").mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("")
    (DOCS / "index.html").write_text(render_index(per_tournament, settings), encoding="utf-8")
    groups, order = group_by_meta(per_tournament)
    for key in order:
        (DOCS / "meta" / f"{meta_slug(key)}.html").write_text(
            render_meta(key, groups[key], settings), encoding="utf-8"
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
                "banlist": banlist_version(t["mwl"]),
                "formality": t["formality"],
                "players": t["players"],
                "cut_size": t["cut_size"],
                "winner": t["winner"],
                "corp": t["corp"],
                "runner": t["runner"],
            }
            for t in per_tournament
        ],
    }
    (DOCS / "data").mkdir(exist_ok=True)
    with open(DOCS / "data" / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return DOCS / "index.html"
