# alwaysberunningfetch

[alwaysberunning.net](https://alwaysberunning.net)(ABR)에 올라오는 **Android: Netrunner 대회 결과를 매일 자동 수집**해서,
대회마다 어떤 Corp/Runner identity가 쓰였고 어떤 성적을 냈는지 통계 페이지를 생성하는 자동화입니다.

- **팩션 도넛 차트** — corp/runner 팩션별 점유율
- **Identity별 바 차트** — 엔트리 수 + 탑컷 진출 수(진한 부분)
- **대회별 상세 페이지** — 우승자, 순위표, 덱리스트 링크(NetrunnerDB)
- **시즌 전체 집계** — 여러 대회를 합친 메타 현황
- **`docs/data/summary.json`** — 개인 사이트에서 가져다 쓸 수 있는 JSON 요약

GitHub Actions가 매일 실행하고 결과를 `docs/`에 커밋하므로, **GitHub Pages를 켜면 그대로 웹사이트**가 됩니다.

## 동작 구조

```
ABR API ──> data/ (원본 캐시) ──> 통계 계산 ──> docs/ (정적 사이트 + summary.json)
  │
  └─ /api/tournaments/results  종료된 대회 목록
     /api/entries?id=N         대회별 순위/identity/덱리스트
     NetrunnerDB /api/2.0/public/cards  identity -> 팩션 매핑
```

- `src/abr.py` — API 클라이언트 (요청 간 1초 간격, data/에 캐시)
- `src/stats.py` — identity/팩션별 엔트리 수, 탑컷 진출, 우승자 계산
- `src/sitegen.py` — 자체 포함 정적 HTML 생성 (인라인 SVG, 라이트/다크 지원)
- `src/main.py` — 진입점

## 사용법

```bash
pip install -r requirements.txt

python src/main.py            # fetch -> 통계 -> docs/ 생성
python src/main.py --offline  # 네트워크 없이 캐시로만 재생성
python src/main.py --probe    # API 응답 필드 구조 확인 (디버그)

# 네트워크 없이 개발할 때: 샘플 데이터 생성
python tests/make_fixtures.py && python src/main.py --offline
```

## 설정 (`config/settings.yaml`)

| 항목 | 설명 |
|---|---|
| `season_start` | 이 날짜 이후 종료된 대회만 수집 |
| `formats` | 포함할 포맷 (기본: standard) |
| `min_players` | 최소 참가자 수 (기본 8명) |
| `scan_limit` | 스캔할 최근 대회 수 |
| `tiers` | ABR 대회 type -> 티어 배지 이름 (NSG Competitive Season 기준: Worlds > Continentals > Megacity > District > Casual) |

## 자동 실행 & 웹사이트로 공개하기

1. `.github/workflows/fetch.yml`이 **매일 03:17 UTC**에 실행되어 `data/`, `docs/`를 커밋합니다.
   (Actions 탭 → fetch → Run workflow로 수동 실행도 가능)
2. GitHub 저장소 → **Settings → Pages → Branch: main(또는 기본 브랜치), 폴더: `/docs`** 선택.
3. `https://<계정>.github.io/alwaysberunningfetch/` 에서 통계 페이지가 열립니다.

개인 사이트에 붙일 때는 `<iframe>`으로 페이지를 임베드하거나,
`docs/data/summary.json`을 fetch해서 원하는 형태로 직접 렌더링하면 됩니다.

## 참고

- 데이터 출처: [alwaysberunning.net](https://alwaysberunning.net) (API 이용 조건: 백링크 표기 — 생성 페이지 푸터에 포함됨)
- Identity 정보: [NetrunnerDB](https://netrunnerdb.com)
- ABR에는 경기 단위(match) 결과가 대부분 없어서 **identity 승률은 계산하지 않습니다.**
  대신 엔트리 수 대비 **탑컷 진출**을 성적 지표로 사용합니다.
