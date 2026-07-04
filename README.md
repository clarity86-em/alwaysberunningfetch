# alwaysberunningfetch

온라인 사이트를 **주기적으로 자동 fetch**해서 변화를 감지하고, 데이터를 저장하고, 알림을 보내는 자동화 프로젝트입니다.

별도 서버 없이 **GitHub Actions**로 계속 돌아가며(기본: 30분마다), 감시할 사이트는 `config/sites.yaml`에 적기만 하면 됩니다.

## 동작 방식

1. `config/sites.yaml`에 등록된 각 사이트를 fetch
2. CSS 선택자(선택 사항)로 원하는 부분만 추출
3. 이전 실행 결과와 비교해서 **변화 감지**
4. 변화가 있으면:
   - `data/` 폴더에 스냅샷 저장 (git으로 이력 관리)
   - Discord/Slack 웹훅으로 알림 전송 (설정한 경우)

## 빠른 시작

### 1. 감시할 사이트 등록

`config/sites.yaml`을 편집하세요:

```yaml
sites:
  - name: example            # 사이트를 구분할 이름 (영문, 파일명에 사용)
    url: https://example.com # 가져올 주소
    selector: "h1"           # (선택) 이 CSS 선택자에 해당하는 부분만 감시
    # selector를 생략하면 페이지 전체를 감시합니다
```

### 2. 로컬에서 테스트

```bash
pip install -r requirements.txt
python src/fetch.py            # 전체 사이트 1회 실행
python src/fetch.py --site example  # 특정 사이트만 실행
```

### 3. 자동 실행 (GitHub Actions)

`main` 브랜치에 푸시하면 `.github/workflows/fetch.yml`이 30분마다 자동 실행됩니다.
주기를 바꾸려면 workflow 파일의 `cron` 값을 수정하세요.

GitHub 저장소 → Actions 탭 → "Run workflow" 버튼으로 수동 실행도 가능합니다.

### 4. (선택) 변화 감지 시 알림 받기

GitHub 저장소 → Settings → Secrets and variables → Actions 에서 secret을 추가하세요:

- `WEBHOOK_URL`: Discord 또는 Slack의 Incoming Webhook URL

설정하면 감시 대상에 변화가 생길 때마다 메시지가 전송됩니다.

## 폴더 구조

```
config/sites.yaml        # 감시할 사이트 목록 (여기만 수정하면 됨)
src/fetch.py             # 메인 스크립트
data/state.json          # 마지막 실행 상태 (해시)
data/snapshots/          # 변화가 있을 때마다 저장되는 스냅샷
.github/workflows/fetch.yml  # 주기 실행 설정
```

## 자주 하는 수정

| 하고 싶은 것 | 방법 |
|---|---|
| 감시 주기 변경 | `.github/workflows/fetch.yml`의 `cron` 수정 (예: `0 * * * *` = 매시간) |
| 사이트 추가/삭제 | `config/sites.yaml` 편집 |
| 페이지 일부만 감시 | 해당 사이트에 `selector` 추가 (브라우저 개발자도구로 CSS 선택자 확인) |
| 로그인이 필요한 사이트 | `headers`에 쿠키/토큰 추가 (secret 사용 권장) — 아래 참고 |

로그인·버튼 클릭 등 실제 브라우저 조작이 필요한 사이트(JavaScript로만 렌더링되는 페이지 포함)는
requests만으로는 안 될 수 있습니다. 그런 경우 Playwright 기반으로 확장이 필요하니 이슈로 남겨주세요.
