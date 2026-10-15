# 리버풀 소식

리버풀 관련 소식을 한 페이지에 모아 매시간 자동으로 업데이트하는 개인용 사이트.

| 내용 | 출처 |
|---|---|
| 뉴스 (영국) | BBC Sport, The Guardian, Liverpool Echo, Sky Sports, This Is Anfield |
| 뉴스 (스페인·이탈리아·독일·프랑스) | 구글 뉴스 나라별 검색 |
| 레딧 | r/LiverpoolFC 인기글 |
| 부상·결장 | 프리미어리그 공식 판타지(FPL) |
| 일정·결과·순위·대회 현황 | ESPN |

- 기사 제목은 무료 구글 번역으로 한국어로 바꾸고, 누르면 원문 기사로 이동한다.
- `build.py`가 위 정보를 모아 `site/index.html`을 만들고, GitHub Actions(`.github/workflows/update.yml`)가 매시간 실행해 GitHub Pages에 올린다.
- 출처나 번역이 실패해도 사이트는 멈추지 않고, 실패한 부분만 표시된다.

내 PC에서 직접 만들어 보기:

```
python build.py
```
