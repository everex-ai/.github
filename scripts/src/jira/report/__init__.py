"""jira-doc 정기 보고(report 모드): 월간, 분기, 연간 묶음 메시지를 Jira 기록으로 계산해 Slack 본문을 만든다.

- 진입 함수는 run.py의 run_report다. scripts/apply.py의 main이 --mode report일 때 부르고, 반환한 메시지를 묶음마다 팀장
  채널로 보낸다.
- 지표 정의는 docs/jira-doc-agent/alerts.md(3절 지표 정의, 4절 알림 체계)를 따른다.
- Jira는 읽기만 한다(scripts/src/jira/client.py의 Jira.search, changelog, comments, prop_get).
- PR은 gh CLI의 검색(gh search prs)으로 읽기만 한다. 실패하면 PR 추가를 업데이트 기준에서 뺀다.
- 검수 comment와 문서화 리뷰 comment의 문구는 scripts/src/jira/comment.py의 review_comment, doc_review_comment,
  check_table과 scripts/src/constant/check.py의 VERDICT_KO, RESULT_KO가 만든다. 이 패키지는 comment.py를 불러오지 않고
  VERDICT_KO와 검수 결과 첫 줄 문구(parse.py의 VERDICT_PREFIX, VERDICT_SUFFIX)를 따로 둔다. 값이 같은지는
  tests/jira/test_report.py가 확인한다.
- 달, 분기, 해는 한국 시간으로 나눈다.
- Summary 탭(summ.extra)에는 팀 합계만 남긴다. 담당자 이름, 담당자별 수치, 구두 피드백 대상 후보는 Slack 본문에만 적는다.
  public repo의 Actions 실행 화면은 GitHub에 로그인한 누구나 열람할 수 있기 때문이다.

모듈 구성(import는 위에서 아래 방향으로만 한다): model, status, period, text, parse, fetch, metrics, message, run
"""
