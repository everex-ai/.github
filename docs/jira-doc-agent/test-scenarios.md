# jira-doc 테스트 시나리오와 문서·코드 대조 항목

이 문서는 jira-doc이 의도대로, 목적에 맞게 동작하는지 확인하는 시나리오를 담는다.
시나리오는 work type별 41개, 공통 7개, 알림 정합성 10개다.
문서와 코드가 다르게 적힌 곳 19개도 함께 모았다.

- 알림 정합성: 문서에 적힌 알림의 의도와 코드 동작이 맞는지 여부
- work type별 시나리오는 담당자가 ticket을 작성하는 흐름 순서로 배치했다.
  - 순서: 작성, 진행, request 전환, 검수 뒤
- 실행 결과는 "결과 기록 양식" 절의 표에 적는다.
  - 적는 사람은 "용어" 절의 실행자다.
- 동작 흐름과 검사 규칙은 `docs/jira-doc-agent/README.md`에 있다.
- 알림의 주기와 받는 사람은 `docs/jira-doc-agent/alerts.md`에 있다.

## 용어

이 문서는 아래 용어를 한 이름으로만 사용한다.

- 실행 수단: 시나리오를 확인하는 아래 세 가지 방법
  - L1(오프라인): `pytest tests/jira`로 확인한다.
    - 테스트는 Jira와 Slack을 가짜 객체로 대신한다(`tests/jira/test_render.py:161`).
  - L2(실제 실행): 새 시험 ticket에 jira-doc을 실제로 실행한다.
    - Jira와 Slack에 실제로 게시한다.
  - L3(운영 관찰): Actions 실행 기록과 Jira를 조회만 한다.
- 실행자: L2를 실행하고 결과를 적는 사람
  - 지금은 팀장이 맡는다.
- 개발자: 테스트 코드를 작성하고 L1을 실행하는 사람
- ticket 작성 흐름의 단계: 담당자가 Jira를 사용하는 순서
  - 작성: ticket을 만들고 사람 구역을 작성한다.
  - 진행: In Progress에서 comment, sub-task, PR을 남긴다.
  - request 전환: 결과와 미달성 사유를 남기고 request 전환한다.
  - 검수 뒤: 보류를 보완하거나, 정정하거나, 완료 기준을 고친다.
  - 근거: `docs/jira-doc-agent/README.md:154-173`
- 시나리오 ID: 접두어로 대상을 나눈다.
  - TK: Task, BG: Bug, IS: Issue
  - CM: work type 공통, AL: 알림 정합성
  - DC: 문서·코드 대조 항목
- 기준 구성: 통과 조건을 모두 채운 시험 ticket의 구성
  - work type별 구성은 "시험 ticket 준비" 절에 있다.
- 기대 결과: 시나리오를 실행한 뒤 나와야 하는 결과
- 중점 확인: 기대 결과 밖에 실행자가 따로 확인할 것
- 완료 기준: 완료 판단에 사용하는 사람 구역
  - Task는 예상 산출물, Bug는 개선(To-Be)이다.
  - 근거: `docs/jira-doc-agent/README.md:558`의 A2 대상
- 끝 기준: 예상 산출물 한 항목이 끝났다고 판단할 기준
  - 예: 수치, 산출물 형태, 대상(`prompts/types/task/review.md:47`)
- 요청 문장: 검수 comment에서 담당자에게 남길 기록을 요구하는 문장
  - 근거: `docs/jira-doc-agent/README.md:391-394`
- 워크플로 단계: `.github/workflows/jira-doc.yml`의 스텝 번호
  - 1단계는 입력 수집, 3-1단계는 CI agent 실행 결과 요약이다.
  - 4단계는 Jira와 Slack 반영이다.
  - 근거: `docs/jira-doc-agent/README.md:207-217`
- 팀장 accountId: repo variable `JIRA_LEAD_ACCOUNT_ID`의 값
- 대응표: Jira accountId와 Slack 멤버 ID의 대응표
  - secret `SLACK_USERS_JSON`에 둔다(`docs/jira-doc-agent/alerts.md:394-395`).
- 좌석: Team plan(회사의 Claude 조직용 요금제)의 구성원 권한
- KR: Jira work type이 KR인 ticket
  - Task의 상위 ticket이다.
  - 근거: `docs/jira-doc-agent/alerts.md:152`
- Atlassian MCP: Claude가 Jira를 조회하는 연결 도구
  - MCP: Model Context Protocol
- 아래 용어는 README "용어" 절(`docs/jira-doc-agent/README.md:40-145`)의 뜻을 따른다.
  - CI agent, 검수 결과, 검사 ID, 스크립트 검사
  - 문서화 리뷰, 관찰 모드와 게이트 모드
  - request 전환, stop 전환, `end` 전환, F1–F4
- 팀장 전환: 팀장이 ready-to-done ticket의 상태를 바꾸는 전환
  - 도착 상태는 Done이나 In Progress다.
  - F4가 이 전환 때 실행된다(`docs/jira-doc-agent/README.md:99-101`).
  - 사람 구역, TL;DR, Summary 탭, 실행 모드
- 아래 용어는 alerts.md "용어" 절(`docs/jira-doc-agent/alerts.md:13-34`)의 뜻을 따른다.
  - 연동 채널, 팀장 채널, cron 실행 시각

### 검사 ID

이 문서에 나오는 검사 ID의 뜻은 아래 표와 같다.

| 검사 ID | 뜻 |
|---|---|
| T1, T2, T3 | Task 예상 산출물 작성, 진행 배경 작성, 예상 산출물과 결과 산출물의 1:1 대응 |
| T4–T9 | Task 문서화 리뷰: 진행 배경 충실도, 예상 산출물 분할 단위, 진행 기록 comment, sub-task 기록, 초과 달성·미달성 사유, 진행 배경부터 결과까지의 연결 |
| B1–B4 | Bug 기본 정보·첨부, 개선(To-Be) 작성, 원인·해결 근거 링크, To-Be 동작 확인 comment |
| B5–B7 | Bug 문서화 리뷰: 문제(As-Is) 재현 정보, 원인 분석 기록, 해결 확인 기록 |
| I1–I3 | Issue 이슈 유형 선택, 이슈 내용 작성, 대응 결과의 근거 |
| I4, I5 | Issue 문서화 리뷰: 이슈 내용 충실도, 논의와 결정 기록 |
| R2 | 1년 뒤에도 이해 가능한 기록(근거 링크 포함) |
| R4 | stop 사유 comment |
| A1, A2, A3 | In Progress 5영업일 무활동, 완료 기준의 사후 변경, ready-to-done 3영업일 대기 |

- 근거: `docs/jira-doc-agent/README.md:106-116`, `:558-561`
- 문서화 리뷰 기준표: `prompts/types/task/review.md:30-35`
  - Bug는 `prompts/types/bug/review.md:40-42`
  - Issue는 `prompts/types/issue/review.md:37-38`

## 테스트 목적과 판정 기준

테스트의 목적은 `docs/jira-doc-agent/metrics.md`의 지표가 의도대로 나오는지 확인하는 것이다.
시나리오 하나가 통과하려면 기대 결과와 중점 확인이 모두 맞아야 한다.

| 지표 | 시나리오에서 확인하는 것 | 해당 시나리오 |
|---|---|---|
| J1 리뷰 범위 | request 전환마다 검수 comment가 달림 | request 전환이 있는 모든 L2 시나리오 |
| J3 발견·조치 | 첫 검수의 ❌가 보완 뒤 ✅로 바뀜 | CM-1, CM-2, CM-3 |
| J4 피드백 대기 시간 | request 전환부터 검수 comment까지의 시간 | request 전환이 있는 모든 L2 시나리오 |
| J7 판정 일치율 | 팀장 전환 방향과 검수 결과의 비교 | AL-4 |
| J8 오류 신호 | 실패 알림, "정정:" comment | CM-4, CM-5 |

- 지표의 정의: `docs/jira-doc-agent/metrics.md:84-90`
- 기대 결과와 다르면 실행자가 "결과 기록 양식" 절의 원인 분류로 나눈다.

## 시나리오 작성 규칙과 표 읽는 법

work type별 시나리오 표는 여섯 열이다.
열 이름은 ID, 단계, 입력, 기대 결과, 중점 확인, 수단이다.
알림 정합성 표는 ID, 알림, 중점 확인, 수단의 네 열이다.

- 실행자는 시험 ticket 하나에 기록 누락이나 부실을 하나만 넣는다.
  - 나머지 기록은 기준 구성대로 둔다.
  - 그래야 기대 결과와 다를 때 원인을 하나로 좁힐 수 있다.
- 검수 모드 시나리오의 기대 결과는 "검수 결과(원인)"로 적는다.
  - 원인: 검수 결과를 정한 검사 ID, "미달성", "문서화 리뷰" 중 하나
  - 예: "보류(T2)"는 T2가 보완 필요여서 보류라는 뜻이다.
  - 예: "보류(미달성)"은 사유 없는 미달성 항목 때문에 보류라는 뜻이다.
  - 원인이 위 셋에 없으면 괄호 없이 검수 결과만 적는다.
    - 그 원인은 시나리오의 입력 열에 있다.
- 검수 결과와 따로 확인할 검사 결과는 쉼표 뒤에 적는다.
  - 예: "통과, T4 보완 필요"
  - 관찰 모드에서는 문서화 리뷰가 검수 결과를 바꾸지 않는다.
    - 근거: `docs/jira-doc-agent/README.md:125-128`
- 정리 모드 시나리오는 "정리:" 뒤에 TL;DR이나 결과 산출물 구역의 출력을 적는다.
- 검수가 끝나지 않는 시나리오는 처리 결과(실패, 건너뜀)를 적는다.
- 수단 열의 L1은 아래 두 가지로 나뉜다.
  - 기존: 이미 있는 테스트
    - 테스트 이름은 표 아래에 적었다.
  - 추가할 테스트: 아직 없는 테스트다.
    - 개발자가 코드 변경 요청에서 작성한다.

## 실행 수단

개발자는 L1을 바로 실행한다.
L2는 Jira와 Slack에 게시하고 비용이 들어 실행자가 실행한다.

### L1

- 명령: `.venv/bin/pytest tests/jira`
  - 2026-10-19 실행 결과: 308 passed
- `scripts/apply.py`를 직접 실행하는 것은 L1이 아니다.
  - apply.py는 Jira와 Slack에 게시하기 때문이다.

### L2

- 실행자가 Actions 수동 실행과 Jira 전환을 한다.
  - 전환: request 전환, stop 전환, F3 버튼, 팀장 전환
  - 전환이 F1–F4를 실행한다(`docs/jira-doc-agent/README.md:82-103`).
- 수동 실행 명령은 아래와 같다.
  - 검수 모드: `gh workflow run jira-doc.yml -f mode=review -f issueKey=<ticket 키>`
  - 정리 모드: `-f mode=digest -f issueKey=<ticket 키>`
  - 확인 결과 알림 모드: `-f mode=decision -f issueKey=<ticket 키>`
  - 주간 점검 모드: `-f mode=alerts`
  - 보고 모드: `-f mode=report -f month=<YYYY-MM>`
  - 입력 이름의 근거: `.github/workflows/jira-doc.yml:19-33`
- L2의 영향은 아래와 같다.
  - 검수 모드는 TL;DR, description, comment를 갱신한다.
    - 근거: `docs/jira-doc-agent/README.md:238-241`
  - Slack은 연동 채널과 팀장 채널로 실제 전송된다.
    - 근거: `.github/workflows/jira-doc.yml:163`, `:166`
  - 실행마다 좌석 사용량 한도를 소모한다(`docs/jira-doc-agent/alerts.md:26-27`).
- 추정: 검수 모드 실행 1회의 API 환산 비용은 0.1–0.4 USD다.
  - 근거: 아래 두 실행의 3-1단계 로그 `total_cost_usd`
  - 실행 38363210173: Issue INNO-36 검수 모드, 0.115 USD
  - 실행 38315057107: Task INNO-54 검수 모드, 0.393 USD

### L3

- 조회만 하는 명령을 사용한다.
  - `gh run list --workflow jira-doc.yml`
  - `gh run view <실행 ID> --log`
  - Jira 조회: Atlassian MCP의 조회 도구, `scripts/jira.sh`의 읽기 명령
- Summary 탭은 Actions 실행 화면에서 확인한다.

### L2로 확인할 수 없는 것

- 시험 ticket의 담당자와 팀장이 같은 사람이면 L2로 멘션 대상을 가릴 수 없다.
  - 예: 검수 comment가 담당자를 멘션했는지, 팀장을 멘션했는지
  - 예: 팀장이 아닌 사람의 전환을 건너뛰는지
- 이 확인은 L1으로만 한다.

## 시험 ticket 준비

실행자는 시험 ticket을 시나리오마다 새로 만든다.
지난 시험 ticket은 사용하지 않는다.
이름은 `[jira-doc 테스트] <시나리오 ID> <짧은 설명>`으로 짓는다.

- 시나리오마다 ticket을 하나씩 만들지만 예외가 있다.
  - CM-1–CM-3은 이어지는 흐름이라 ticket 하나를 함께 사용한다.
  - AL 시나리오는 AL-4에 하나, AL-6에 둘만 만든다.
    - 나머지 AL은 TK, BG, IS, CM 실행 결과로 확인한다.
- 실행자는 ticket을 만들 때 아래 필드를 함께 넣는다.
  - 기한(duedate), parent(KR), Team
  - parent와 Team이 없으면 In Progress 전환이 되지 않는다.
    - 근거: Jira 전환 오류 문구(2026-10-08 INNO 시험 Task)
    - 원문: `'Assignee', 'Parent', 'Team' field를 지정해야 In Progress로 상태 변경이 가능합니다.`
  - parent로 사용할 KR은 실행 전에 팀장이 정한다.
- 실행자는 comment를 첫 In Progress 전환 뒤에 남긴다.
  - 첫 In Progress 전환이 작업 기간의 시작이다(`docs/jira-doc-agent/README.md:156`).

### 시험 ticket이 운영 알림과 보고에 주는 영향

- 지금 코드는 시험 ticket을 정기 보고에서 빼지 않는다.
  - 보고의 검색 조건에 제목 조건이 없다(`scripts/src/jira/report/fetch.py:95-115`).
  - 그래서 시험한 달의 정기 보고에 시험 ticket이 집계된다.
  - 팀장 결정 필요: 제목 머리말로 빼는 규칙을 추가할지 정한다.
- 주간 점검의 cron 실행은 시험 ticket도 연동 채널로 알린다.
  - cron 실행 시각: 매주 수요일(`.github/workflows/jira-doc.yml:14`)
  - 실행자는 시험 기간에 팀원에게 시험 ticket 알림임을 미리 알린다.

### work type별 기준 구성

- Task 기준 구성은 아래와 같다.
  - 진행 배경: 왜, 대상, 맥락을 2줄 이상 적는다.
  - 예상 산출물: 끝 기준이 있는 항목 2개를 적는다.
  - 진행 comment: 무엇을 했고 결과가 무엇인지 적는다.
  - 결과 comment: 항목마다 결과와 근거 링크(PR, 리포트)를 남긴다.
- Bug 기준 구성은 아래와 같다.
  - 기본 정보: 발생 기기, 발생 일자, 발생 장비, 발생 계정을 모두 적는다.
  - 문제(As-Is): 재현 절차, 조건, 빈도를 적는다.
  - 개선(To-Be)을 적고, 첨부 파일을 1개 이상 올린다.
  - 원인과 해결 comment에 근거 링크를 붙인다.
  - To-Be 동작 확인 comment에 반영 버전, 환경, 확인 방법을 적는다.
- Issue 기준 구성은 아래와 같다.
  - 이슈 유형을 하나 선택한다.
  - 이슈 내용: 제기 이유와 결정할 것을 2줄 이상 적는다.
  - 논의 comment에 검토한 대안을 남긴다.
  - 결정 comment에 대응 결과(수용, 반려, 이관)와 이유를 남긴다.
- 기준 구성의 근거
  - T2 2줄, I2 2줄 또는 1줄 40자: `scripts/src/jira/precheck/template.py`
  - B1 항목 이름: `scripts/src/jira/precheck/template.py:12-17`
  - 문서화 리뷰 항목: 각 work type 검수 파일의 "문서화 리뷰" 절

## Task 시나리오

Task는 예상 산출물마다 결과가 있고 모두 달성해야 통과다.
보류는 주로 사유 없는 미달성과 스크립트 검사 보완 필요에서 나온다.

- 기대 결과의 근거
  - 검수 결과: `prompts/types/task/review.md:87` "검수 결과" 절
  - 문서화 리뷰: `prompts/types/task/review.md:42` "문서화 리뷰" 절
  - apply.py의 재결정: `docs/jira-doc-agent/README.md:290-320`

| ID | 단계 | 입력 | 기대 결과 | 중점 확인 | 수단 |
|---|---|---|---|---|---|
| TK-1 | 작성 | 진행 배경을 비우거나 1줄만 작성 | 보류(T2) | CI agent의 통과를 보류로 바꾸는지 | L1, L2 |
| TK-2 | 작성 | 진행 배경을 구체적으로 작성하지 않음(왜, 대상, 맥락 없음) | 통과, T4 보완 필요 | 문서화 리뷰 comment의 피드백 | L2 |
| TK-3 | 작성 | 예상 산출물을 비움 | 보류(T1) | 결과 산출물 구역 처리 | L1, L2 |
| TK-4 | 작성 | 예상 산출물을 추상적으로 설정("성능 개선") | 통과, T5 보완 필요 | 끝 기준 판단 | L2 |
| TK-5 | 작성 | 추상적 예상 산출물에 하위 항목으로 끝 기준을 적음 | 통과, T5 만족 | TK-4와 결과 비교 | L2 |
| TK-6 | 진행 | 진행 과정을 comment로 남기지 않음 | 보류(미달성), T6 보완 필요 | 미달성 사유 "근거 부족" | L2 |
| TK-7 | 진행 | 진행 comment가 추상적, 주관적("진행 중", "잘 됨") | 통과, T6 보완 필요 | 상태만 적은 기록을 세지 않는지 | L2 |
| TK-8 | 진행 | '개발 완료'와 PR 링크만 남김 | 보류(미달성) | '개발' 표현의 달성 근거 | L2 |
| TK-9 | 진행 | sub-task를 만들고 기록을 남기지 않음 | 통과, T7 보완 필요 | sub-task 기록 수집 | L2 |
| TK-10 | 진행 | 진행 중에 F3 버튼 | 정리: 진행 중 항목은 미달성 | 검수 comment, Slack 없음 | L2 |
| TK-11 | request 전환 | 기준 구성 그대로 | 통과 | 다른 Task 시나리오와 비교할 기준 구성의 결과 | L2 |
| TK-12 | request 전환 | 모든 항목 달성, 근거 링크 없음 | 보류(R2) | 검사 표와 검수 결과 일치 | L2 |
| TK-13 | request 전환 | 미달성 항목이 있으나 미달성 사유를 남기지 않음 | 보류(미달성), T8 보완 필요 | "사유 미기재" 표시, 요청 문장 | L2 |
| TK-14 | request 전환 | 미달성 항목이 있으나 사유를 온전히 남김 | 검토 요청 | 사유 인정 기준 | L2 |
| TK-15 | request 전환 | 사유를 온전히 남긴 것과 남기지 않은 것이 혼재됨 | 보류(미달성) | 사유 없는 항목만 요청하는지 | L2 |
| TK-16 | request 전환 | 예상에 없던 결과를 추가 사유 없이 남김 | 통과, T8 보완 필요 | 초과 달성 표시 | L2 |
| TK-17 | request 전환 | 결과 번호가 예상 산출물과 다름 | 검토 요청(T3) | 바꾼 이유 문구 | L1 |
| TK-18 | 검수 뒤 | request 전환 뒤 예상 산출물 수정 | 검토 요청(A2) | A2 기준 시각 | L2 |

- 사유를 온전히 남김: 미달성 항목마다 사람이 남긴 사유가 있다.
  - "근거 부족으로 확인 불가"는 사유로 세지 않는다(`docs/jira-doc-agent/README.md:317`).
- TK-8의 기준: `prompts/types/task/deliverables.md`의 "예상 산출물 표현별 달성 근거"
- TK-12는 CI agent의 판정을 확인하는 시나리오다.
  - apply.py는 모두 달성이면 R2를 다시 확인하지 않는다.
    - 근거: `scripts/src/jira/verdict.py`의 `recheck_verdict`
  - 그래서 CI agent가 통과를 내면 R2가 ❌여도 통과로 남는다.
  - 실행자는 검사 표의 R2와 검수 결과가 서로 맞는지 확인한다.
- 실행자는 TK-18을 아래 순서로 만든다.
  - request 전환, 예상 산출물 수정, 검수 모드 수동 실행
  - 다시 request 전환하면 A2 기준 시각이 새로 정해진다.
    - 근거: `scripts/src/jira/precheck/history.py:152-167`
- L1 테스트
  - TK-1, TK-3: 추가할 테스트
    - CI agent가 통과를 내고 T1이나 T2가 보완 필요이면 보류가 되는지 확인한다.
    - 지금 테스트는 R4가 보완 필요인 경우만 확인한다(`tests/jira/test_render.py:1385`).
  - TK-17: 기존 `test_t3_mismatch_detail`(`tests/jira/test_render.py:871`)

## Bug 시나리오

Bug는 원인·해결 근거 링크와 To-Be 동작 확인 comment가 있어야 통과다.
지난 시험의 Bug 결과는 보류뿐이라, 통과와 검토 요청은 첫 실행 사례가 된다.

- 지난 시험 결과의 근거: Actions 실행 38363095959(INNO-34, 보류)
- 기대 결과의 근거
  - 검수 결과: `prompts/types/bug/review.md:79` "검수 결과" 절
  - 문서화 리뷰: `prompts/types/bug/review.md:49` "문서화 리뷰" 절

| ID | 단계 | 입력 | 기대 결과 | 중점 확인 | 수단 |
|---|---|---|---|---|---|
| BG-1 | 작성 | 기본 정보의 발생 기기, 일자, 장비, 계정 중 일부를 비움 | 보류(B1) | 비어 있는 항목 이름 표시 | L1, L2 |
| BG-2 | 작성 | 첨부 파일 없음 | 보류(B1) | 첨부 자료 구역이 아닌 첨부 파일로 판단 | L1, L2 |
| BG-3 | 작성 | 문제(As-Is)에 재현 절차, 조건, 빈도 없이 현상만 적음 | 통과, B5 보완 필요 | 문서화 리뷰 피드백 | L2 |
| BG-4 | 작성 | 개선(To-Be)을 비움 | 보류(B2) | 스크립트 검사 우선 | L1, L2 |
| BG-5 | 진행 | 원인 분석 과정 없이 결론만 comment로 남김 | 통과, B6 보완 필요 | 문서화 리뷰 피드백 | L2 |
| BG-6 | 진행 | 원인과 해결을 남겼으나 근거 링크 없음 | 보류(B3) | 검사 표와 검수 결과 일치 | L2 |
| BG-7 | 진행 | 원인과 해결이 서로 맞지 않음 | 검토 요청 | 모순 판단 | L2 |
| BG-8 | 진행 | 원인 미확인 상태에서 F3 버튼 | 정리: TL;DR에 "(미확인)" | 시도 기록 한 줄, 원인·해결 소제목 표시 | L2 |
| BG-9 | request 전환 | 기준 구성 그대로 | 통과 | Bug 통과의 첫 실행 사례 | L2 |
| BG-10 | request 전환 | To-Be 동작 확인 comment 없음 | 보류(B4) | 요청 문장 | L2 |
| BG-11 | request 전환 | To-Be 동작 확인 comment에 반영 버전, 환경, 확인 방법 없음 | 통과, B7 보완 필요 | 문서화 리뷰 피드백 | L2 |
| BG-12 | request 전환 | 재현 불가로 종료하려 함 | 검토 요청 | 종료 사유 판단 | L2 |
| BG-13 | 검수 뒤 | request 전환 뒤 개선(To-Be) 수정 | 검토 요청(A2) | A2 대상 구역 | L2 |

- BG-3에서 문제(As-Is)는 1줄 이상이라 B1은 만족이다.
  - B1은 문제 구역에 내용 1줄 이상을 요구한다(`scripts/src/jira/precheck/template.py:77-110`).
- BG-6, BG-10은 CI agent의 판정을 확인하는 시나리오다.
  - B3, B4는 CI agent가 판단하고 apply.py가 다시 정하지 않는다.
    - 근거: `docs/jira-doc-agent/README.md:311-316`
  - 실행자는 검사 표의 B3, B4와 검수 결과가 서로 맞는지 확인한다.
- 실행자는 BG-13을 TK-18과 같은 순서로 만든다.
- L1 테스트
  - BG-1, BG-2: 기존 `test_bug_template_b1_fails`(`tests/jira/test_render.py:747`)
  - BG-4: 기존 `test_bug_template_b2_fails_with_placeholder_only`(`tests/jira/test_render.py:753`)
  - BG-1, BG-2, BG-4: 추가할 테스트
    - CI agent가 통과를 내고 B1이나 B2가 보완 필요이면 보류가 되는지 확인한다.

## Issue 시나리오

Issue는 대응 결과(수용, 반려, 이관)와 이유, 근거가 있어야 통과다.
지난 시험의 Issue 결과도 보류뿐이라, 통과와 검토 요청은 첫 실행 사례가 된다.

- 지난 시험 결과의 근거: Actions 실행 38363336636(INNO-36, 보류)
- 기대 결과의 근거
  - 검수 결과: `prompts/types/issue/review.md:74` "검수 결과" 절
  - 문서화 리뷰: `prompts/types/issue/review.md:48` "문서화 리뷰" 절

| ID | 단계 | 입력 | 기대 결과 | 중점 확인 | 수단 |
|---|---|---|---|---|---|
| IS-1 | 작성 | 이슈 유형을 선택하지 않음(빈 체크박스) | 보류(I1) | 체크 목록 선택 항목의 취소선 표시를 선택으로 인정 | L1, L2 |
| IS-2 | 작성 | 이슈 내용 1줄, 40자 미만 | 보류(I2) | 경계값 | L1, L2 |
| IS-3 | 작성 | 이슈 내용에 제기 이유와 결정할 것이 없음 | 통과, I4 보완 필요 | 문서화 리뷰 피드백 | L2 |
| IS-4 | 진행 | 논의 comment 없이 결정만 남김 | 통과, I5 보완 필요 | 검토한 대안 기록 | L2 |
| IS-5 | 진행 | comment마다 대응 결과가 다름 | 검토 요청 | 최종 결정 판단 | L2 |
| IS-6 | 진행 | 대응 미정 상태에서 F3 버튼 | 정리: TL;DR 첫 항목 "검토 중:" | 정리 모드 형식 | L2 |
| IS-7 | request 전환 | 기준 구성 그대로(수용 결정) | 통과 | Issue 통과의 첫 실행 사례 | L2 |
| IS-8 | request 전환 | 반려 결정과 반려 이유 comment | 통과 | 반려도 대응 결과로 인정 | L2 |
| IS-9 | request 전환 | 이관 결정과 이관 ticket 키 | 통과 | I3 근거로 ticket 키 인정 | L2 |
| IS-10 | request 전환 | 대응 결과만 있고 이유, 근거 없음 | 보류(I3) | 검사 표와 검수 결과 일치 | L2 |

- Issue에는 검수 뒤 단계의 A2 시나리오가 없다.
  - A2는 Task와 Bug만 계산한다(`docs/jira-doc-agent/README.md:298`).
- IS-1의 취소선 처리 근거: `scripts/src/jira/precheck/template.py:20`
- IS-10은 CI agent의 판정을 확인하는 시나리오다.
  - I3도 apply.py가 다시 정하지 않는다(`docs/jira-doc-agent/README.md:311-316`).
- L1 테스트
  - IS-1: 기존 `test_issue_type_counts_struck_item_as_selected`(`tests/jira/test_render.py:1563`)
  - IS-2: 추가할 테스트
    - 1줄 39자와 40자의 결과가 갈리는지 확인한다.

## work type 공통 시나리오

실행자는 work type과 관계없는 흐름을 Task ticket으로 확인한다.
CM-1–CM-3은 같은 ticket에서 차례로 실행한다.

| ID | 단계 | 입력 | 기대 결과 | 중점 확인 | 수단 |
|---|---|---|---|---|---|
| CM-1 | 진행 | stop 뒤 사유 없이 재진행하고 request 전환 | 보류(R4) | F2 stop 사유 요청 comment를 사유로 세지 않음 | L2 |
| CM-2 | 검수 뒤 | CM-1 뒤 "stop 사유:" comment를 남기고 request 전환 | 통과, R4 만족 | R4 내용 칸과 검수 뒤 Slack 알림의 "늦게 기록" 줄 | L2 |
| CM-3 | 검수 뒤 | 보류 요청대로 보완하고 request 전환 | 통과 | J3 발견·조치 계산 자료 | L2 |
| CM-4 | 검수 뒤 | "정정:" comment 뒤 F3 버튼 | 정리: TL;DR에 정정 내용 반영 | 원래 근거보다 우선 | L2 |
| CM-5 | request 전환 | CI agent 출력 검사 실패 | 실패 | Jira 반영 없음, 실패 알림 | L1 |
| CM-6 | request 전환 | 게이트 모드 | 보류(문서화 리뷰) | 검수 comment에 문서화 리뷰 항목이 들어감 | L1 |
| CM-7 | request 전환 | Story나 sub-task | 건너뜀 | 알림 없음 | L1 |

- 근거
  - CM-1, CM-2: `docs/jira-doc-agent/README.md:322-342`
  - CM-3: `docs/jira-doc-agent/README.md:324-326`
  - CM-4: `docs/jira-doc-agent/README.md:418-422`
  - CM-5: `docs/jira-doc-agent/README.md:233-235`
  - CM-6: `docs/jira-doc-agent/README.md:129-130`, `:301`
  - CM-7: `scripts/src/jira/collect.py:248-250`
- CM-6은 L1으로만 확인한다.
  - `JIRA_DOC_GATE`는 repo variable이라 운영 실행에도 적용된다(`.github/workflows/jira-doc.yml:51`).
- L1 테스트
  - CM-5: 기존 `test_failure_reasons`(`tests/jira/test_render.py:1040`)
  - CM-5: 기존 `test_main_failure_sends_one_slack_and_exits_one`(`:1127`)
  - CM-6: 기존 `test_issue_gate_doc_review_fail_turns_pass_into_hold`(`:1528`)
  - CM-7: 추가할 테스트
    - 대상이 아닌 work type과 sub-task를 건너뛰는지 확인한다.

## 문서·코드 대조 항목

문서 문장 19곳이 코드 동작이나 실제 줄 번호와 다르다.
모든 항목은 2026-10-19에 원본 줄을 출력해 확인했다.

- 확인 명령: 문서 위치와 코드 위치의 줄을 `sed -n '<줄>p' <파일>`로 출력했다.
- 고칠지와 어떻게 고칠지는 이 문서에서 정하지 않는다.
- 표의 위치는 아래처럼 줄여 적었다.
  - README.md, alerts.md, metrics.md: `docs/jira-doc-agent/`
  - `issue.py`, `slack.py`, `verdict.py`: `scripts/src/jira/`
  - `run.py`: `scripts/src/jira/precheck/`
  - `message.py`: `scripts/src/jira/report/`
  - `constant/slack.py`: `scripts/src/constant/slack.py`
  - `check.py`: `scripts/src/constant/check.py`
  - `apply.py`: `scripts/`
  - `jira-doc.yml`: `.github/workflows/`
  - `test_render.py`: `tests/jira/`

| ID | 문서 위치 | 문서 내용 | 코드 동작(위치) |
|---|---|---|---|
| DC-1 | README.md:181-186, :430-545 | 실행 모드 표와 흐름도에 확인 결과 알림 모드가 없음 | 확인 결과 알림 모드가 있음(`jira-doc.yml:17`, `:49`) |
| DC-2 | README.md:183 | 검수 모드가 "상태 전환"을 함 | 상태를 바꾸지 않음(README.md:245, `check.py`의 `STATE_NOTE`) |
| DC-3 | README.md:819-821 | 상태 전환 실패가 검수 comment에 남음 | 상태 전환이 없음(DC-2와 같은 위치) |
| DC-4 | README.md:825 | 실패한 ticket에는 Jira에 아무것도 남기지 않음 | comment 게시 뒤 기록 저장에서 Jira 오류가 나면 부분 반영 뒤 실패 처리(`issue.py:350-363`, `apply.py:109-119`) |
| DC-5 | README.md:134-139 | jira-doc 기록 값은 네 가지 | `lastMode`, `docFails`, `lastDecisionAt`도 있음(`issue.py:353-361`, `:457`) |
| DC-6 | README.md:404 | 관찰 모드의 검수 모드에서 게시 | 문서화 리뷰 항목이 없으면 게시하지 않음(`issue.py:331`) |
| DC-7 | README.md:764 | 대응표 사용처에 팀장 확인 결과 알림이 없음 | 그 알림도 대응표를 읽음(`issue.py:445`) |
| DC-8 | alerts.md:21, README.md:765 | 팀장 채널 secret이 없으면 Summary 탭에 남김 | 본문 없이 결과 행만 남음(`issue.py:506`, `apply.py:95-98`) |
| DC-9 | alerts.md:391 | A3는 `end` 전환까지 다시 올라옴 | ready-to-done에서 나가면 풀림(`run.py:127`) |
| DC-10 | `constant/slack.py:15`, `run.py:139` | A3 조치로 "미루는 이유 comment", "보류 comment"를 안내 | comment로는 A3가 풀리지 않음(`run.py:127`) |
| DC-11 | alerts.md:396 | 대응표에 없으면 이름이나 "팀장"으로 표시 | "담당자 미지정", "팀장 미지정", "팀장(Slack 멘션 없음)"도 사용함(`slack.py:82-88`, `:122-127`) |
| DC-12 | alerts.md "중복 방지와 멘션 규칙" 절 | 주간 점검 메시지의 건수 상한이 없음 | 절마다 15건 상한(`constant/slack.py:27`) |
| DC-13 | alerts.md:59, README.md:781 | 팀장 계정의 전환인지 늘 확인 | 팀장 accountId가 비면 확인하지 않음(`issue.py:436`) |
| DC-14 | `apply.py:94` 주석, `test_render.py:1879` docstring | 12월 보고의 연간 묶음은 두 번째 메시지 | 세 번째 메시지(`message.py:360-364`) |
| DC-15 | alerts.md:62-63, README.md:32 | cron 줄이 `jira-doc.yml:13`, `:14` | 실제는 `:14`, `:15` |
| DC-16 | README.md:232, :685, :692, :702, :853 | 워크플로 줄 번호 | 1–11줄 밀림. 예: README.md:685의 `:92`는 실제 `:103` |
| DC-17 | metrics.md:58 | 관찰 모드의 품질 검사는 T4–T8 5개 | T4–T9 6개(`check.py:71`) |
| DC-18 | alerts.md:53 | 팀장 확인 결과 알림은 F4가 보냄 | F4는 실행만 시작하고 apply.py가 보냄(`issue.py:454`) |
| DC-19 | `apply.py:24-25` docstring | 담당자는 팀장 전환 뒤 확인 결과 알림으로 결과를 받음 | 통과와 보류 검수 comment가 담당자를 바로 멘션함(`issue.py:322-323`) |

## 알림 정합성 시나리오

알림 다섯 개에서 문서의 의도와 코드의 동작이 다르다.
그중 세 개는 고칠 방향을 팀장이 정해야 해서 "팀장 결정 필요"를 붙였다.

- 의도와 동작이 다른 알림: AL-1, AL-4, AL-5, AL-6, AL-7
- 팀장 결정 필요: AL-1, AL-5, AL-7
- 의도의 출처: `docs/jira-doc-agent/alerts.md`의 "주기별 알림" 절(`:36-70`)
- 차이의 세부는 "문서·코드 대조 항목" 절의 DC 번호로 가리킨다.

| ID | 알림 | 중점 확인 | 수단 |
|---|---|---|---|
| AL-1 | 검수 comment | 검수 결과별 멘션 대상, 담당자와 팀장 외 멘션 제거 | L1 |
| AL-2 | 문서화 리뷰 comment | 관찰 모드에서만 게시, 팀장 멘션 | L1, L2 |
| AL-3 | 검수 뒤 Slack 알림 | 팀장만 멘션, 검수 결과별 이유 줄 | L1, L2 |
| AL-4 | 팀장 확인 결과 알림 | 한 번만 알림, 팀장 아닌 전환 건너뜀 | L1, L2 |
| AL-5 | 실패 알림 | 실행 모드별 발송 여부 | L1 |
| AL-6 | 주간 점검 메시지 | R4, A1, A3 경계값과 해소 조건 | L1, L2 |
| AL-7 | 토큰(인증 키) 만료 알림 | 30일 경계, 1단계 실패 때 | L1 |
| AL-8 | 정기 보고 메시지 | 묶음 수, 줄 수와 줄 길이 상한, 멘션 없음 | L1, L2 |
| AL-9 | F2 stop 사유 요청 comment | 문구, 담당자 멘션 | L2 |
| AL-10 | Summary 탭 사본 | Slack 멤버 ID가 남지 않음 | L2 |

### AL-1 검수 comment

- 의도가 두 곳에 다르게 적혀 있다(DC-19).
  - alerts.md는 통과와 보류는 담당자가 받는다고 적는다(`docs/jira-doc-agent/alerts.md:41`).
  - 코드는 alerts.md와 같다(`scripts/src/jira/issue.py:320-323`).
  - 그래서 담당자는 팀장 전환 전에 Jira 알림을 받는다.
- 팀장 결정 필요: 통과와 보류에서 담당자를 바로 멘션할지 정한다.
- L1: 추가할 테스트
  - 검수 결과별 멘션 대상을 확인한다.
  - `sanitize_mentions`가 담당자와 팀장 외 멘션을 지우는지 확인한다(`scripts/src/jira/comment.py:159`).

### AL-2 문서화 리뷰 comment

- 의도: 관찰 모드의 검수 모드에서 팀장에게 게시한다(`docs/jira-doc-agent/alerts.md:42`).
  - 게시 조건의 차이는 DC-6에 있다.
- L1: 기존 `test_bug_observe_doc_review_goes_to_lead_comment_only`(`tests/jira/test_render.py:1513`)
- L2: 실행자는 TK, BG, IS 실행마다 comment 첫 줄과 팀장 멘션을 확인한다.

### AL-3 검수 뒤 Slack 알림

- 의도: 검수 결과와 관계없이 팀장에게 보낸다(`docs/jira-doc-agent/alerts.md:43`).
- 코드는 의도와 같다(`scripts/src/jira/issue.py:366-383`).
  - 대응표에 팀장이 없으면 멘션 대신 "팀장"으로 적는다(`:370`).
- L1: 기존 `test_hold_does_not_transition_and_mentions_lead_only`(`tests/jira/test_render.py:258`)
- L1: 기존 `test_slack_reasons_show_late_r4`(`tests/jira/test_render.py:1424`)
- L2: 실행자는 연동 채널에서 아래 네 가지를 확인한다.
  - 첫 줄: `[<ticket 키>] 검수 결과: <결과>. 상태 변경 없음`
  - 검수 결과별 이유 줄
  - 요청 묶음은 보류에만 있음
    - 요청 묶음: 요청 문장을 모은 줄들(`scripts/src/jira/slack.py:201-245`)
  - 검수 comment 링크

### AL-4 팀장 확인 결과 알림

- 의도: 팀장이 Done이나 In Progress로 전환하면 담당자에게 알린다(`docs/jira-doc-agent/alerts.md:53-61`).
  - 팀장 accountId의 확인 조건 차이는 DC-13에 있다.
- 한 번만 알리는 기록 `lastDecisionAt`은 전송에 성공했을 때만 남는다(`scripts/src/jira/issue.py:456-458`).
  - 그래서 전송에 실패하면 다음 실행에서 다시 보낸다.
- L1: 기존 테스트(`tests/jira/test_render.py:2027-2095`)
  - `test_decision_hold_notifies_assignee`, `test_decision_pass_notifies_assignee`
  - `test_decision_skips`, `test_decision_mode_requires_issue`
  - `test_decision_same_transition_is_sent_once`
- L1: 추가할 테스트
  - 팀장 accountId가 빈 경우
  - 전송 실패 뒤 다시 실행한 경우
- L2: 실행자는 아래 순서로 확인한다.
  1. 시험 ticket을 request 전환하고 검수를 기다린다.
  2. 팀장이 Done으로 전환한다.
     - F4가 확인 결과 알림 모드를 실행한다.
  3. 같은 ticket에 확인 결과 알림 모드를 수동으로 다시 실행한다.
  4. 연동 채널에 알림이 한 번만 왔는지 확인한다.
  5. 검수 결과와 전환 방향을 J7 판정 일치율과 같은 기준으로 기록한다.

### AL-5 실패 알림

- 의도: 실패하면 팀장에게 알린다(`docs/jira-doc-agent/alerts.md:45`).
- 코드는 검수 모드와 정리 모드의 실패만 알린다(`scripts/apply.py:102-143`).
  - 주간 점검, 보고, 확인 결과 알림 모드에는 실패 알림 경로가 없다(`scripts/apply.py:75-100`).
  - 워크플로 1단계가 실패하면 4단계가 실행되지 않아 알림이 없다(`.github/workflows/jira-doc.yml:158`).
- 팀장 결정 필요: 다른 실행 모드의 실패도 알릴지 정한다.
- L1: 기존 `test_main_failure_sends_one_slack_and_exits_one`(`tests/jira/test_render.py:1127`)
- L1: 추가할 테스트
  - 팀장 accountId가 비면 "팀장 미지정"으로 적는지 확인한다(`scripts/src/jira/slack.py:122-127`).

### AL-6 주간 점검 메시지

- 의도: R4, A1은 담당자, A3는 팀장에게 매주 알린다(`docs/jira-doc-agent/alerts.md:46`).
- 코드의 조건은 아래와 같다.
  - R4: 지금 상태가 Backlog이고 stop 뒤 24시간 이상(`scripts/src/jira/precheck/run.py:99`)
  - A1: In Progress이고 마지막 활동 뒤 5영업일 이상(`:116`)
  - A3: ready-to-done이고 진입 뒤 3영업일 이상(`:127-132`)
  - 알림이 없으면 "점검 결과 이상 없음"을 보낸다(`scripts/src/jira/issue.py:482-483`).
- A3의 해소 조건과 건수 상한의 차이는 DC-9, DC-10, DC-12에 있다.
- L1: 기존 테스트
  - `test_ready_wait_alert_after_3_business_days`(`tests/jira/test_render.py:838`)
  - `test_ready_wait_no_alert_after_2_business_days`(`:851`)
  - `test_stale_alert_message_and_stop_detail`(`:793`)
- L1: 추가할 테스트
  - R4 24시간 경계와 Backlog가 아닐 때
  - A1 4영업일과 5영업일 경계
  - 15건을 넘을 때, 알림이 없을 때
- L2: 실행자는 아래 순서로 확인한다.
  1. R4용 ticket을 stop 전환 뒤 사유 없이 24시간 넘게 Backlog에 둔다.
  2. A3용 ticket을 request 전환 뒤 3영업일 넘게 둔다.
  3. 주간 점검 모드를 수동 실행하고 두 ticket이 올라오는지 확인한다.
  4. A3용 ticket에 comment만 남기고 다음 주에도 올라오는지 확인한다.

### AL-7 토큰(인증 키) 만료 알림

- 의도: 만료 30일 전부터 매주 팀장 채널로 알린다(`docs/jira-doc-agent/alerts.md:355-363`).
- 코드는 주간 점검 모드의 4단계에서 보낸다(`scripts/apply.py:75-79`).
  - 1단계가 실패하면 4단계가 없는 것은 AL-5와 같다.
  - 그래서 `JIRA_API_TOKEN`이 만료되면 그 만료 알림은 나갈 수 없다.
- 팀장 채널 secret이 없을 때의 차이는 DC-8에 있다.
- 팀장 결정 필요: `JIRA_API_TOKEN` 만료를 다른 경로로 알릴지 정한다.
- L1: 기존 `test_token_expiry_sends_only_30_days_or_less`(`tests/jira/test_render.py:1723`)
- L1: 기존 `test_slack_token_expiry_posts_to_lead_channel`(`:1785`)

### AL-8 정기 보고 메시지

- 의도: 묶음마다 팀장 채널로 메시지 하나를 보낸다(`docs/jira-doc-agent/alerts.md:96-106`).
  - 메시지마다 40줄 이내, 줄마다 50자 이내다(`:105`).
- 코드: 월간은 매달, 분기는 3·6·9·12월, 연간은 12월에 붙는다(`scripts/src/jira/report/message.py:360-364`).
  - 12월 보고의 메시지 수 차이는 DC-14에 있다.
- L1: 기존 `tests/jira/test_report.py`의 묶음 수와 길이 테스트
- L2: 실행자는 보고 모드를 `month=2026-09`로 수동 실행하고 아래를 확인한다.
  - 팀장 채널에 월간, 분기 메시지 2건이 옴
  - 메시지마다 40줄, 줄마다 50자 이내
  - Slack 멘션(`<@`)이 없음
- 2026-09는 새 시험 ticket을 만들기 전의 달이다.
  - 그래서 이 실행에는 새 시험 ticket이 집계되지 않는다.

### AL-9 F2 stop 사유 요청 comment

- 의도: stop 전환 때 담당자를 멘션한 안내 comment를 단다(`docs/jira-doc-agent/README.md:87-94`).
  - 이 comment는 repo가 아니라 Jira Automation F2 설정에서 만든다.
- L2: 실행자는 CM-1에서 stop 전환 뒤 아래를 확인한다.
  - 안내 문구 두 줄이 README의 문구와 같음
  - 담당자 멘션이 있음

### AL-10 Summary 탭 사본

- 의도: Summary 탭의 Slack 사본에는 멘션 대신 이름을 적는다(`docs/jira-doc-agent/alerts.md:399-405`).
  - public repo의 실행 화면은 GitHub에 로그인한 누구나 볼 수 있다.
  - 근거: `docs/jira-doc-agent/alerts.md:400-404`의 GitHub 문서 원문
- L2: 실행자는 Summary 탭에 `<@U`가 없는지 확인한다.
  - 대상: 주간 점검 모드와 검수 모드 실행

## 결과 기록 양식

실행자는 시나리오를 실행할 때마다 아래 표에 한 행을 적는다.
기대 결과와 다르면 원인 분류를 함께 적는다.

| ID | 실행일 | Actions 실행 ID | ticket 키 | 기대 결과 | 실제 결과 | 일치 | 대기 시간(분) | 원인 분류 | 비고 |
|---|---|---|---|---|---|---|---|---|---|
| TK-1 | | | | 보류(T2) | | | | | |

- 실제 결과는 기대 결과와 같은 표기로 적는다.
  - 표기: "시나리오 작성 규칙과 표 읽는 법" 절
- 대기 시간은 J4 계산과 같다.
  - request 전환 시각부터 검수 comment 작성 시각까지다.
  - 정리 모드와 L1은 빈칸으로 둔다.
- 원인 분류는 아래 다섯 값 중 하나다.
  - CI agent 판단: 지시 파일과 다르게 판단했다.
  - 지시 파일: 지시 파일의 기준이 기대 결과와 다르다.
  - 스크립트: precheck.py, apply.py의 계산이 문서와 다르다.
  - 문서: 기대 결과의 근거 문서가 틀렸다.
  - 실행 환경: 좌석 사용량 한도, 권한, 네트워크 오류
- AL 시나리오는 확인 항목마다 맞음, 다름을 적는다.
  - 다름이면 메시지 원문 위치를 비고에 적는다.
    - 원문 위치: Slack 메시지 링크, Jira comment 링크

## 실행 순서와 비용 추정

L2는 기준 구성 시나리오부터 실행하고, 기다리는 시간이 긴 시나리오를 먼저 준비한다.
추정: 시험 ticket은 약 45개, 실행은 약 50회다.

- 실행 순서는 아래와 같다.
  1. 개발자: 추가할 테스트를 코드 변경 요청으로 작성하고 L1을 실행한다.
  2. 실행자: AL-6의 R4용, A3용 ticket을 먼저 만들어 둔다.
  3. 실행자: 기준 구성 시나리오 TK-11, BG-9, IS-7을 실행한다.
     - 기준 구성이 통과하지 않으면 다른 시나리오를 실행하지 않는다.
  4. 실행자: 첫 실행 사례인 BG-7, BG-12, IS-5, IS-8, IS-9를 실행한다.
  5. 실행자: 작성 단계와 request 전환 단계의 나머지 시나리오를 실행한다.
  6. 실행자: 진행 단계와 정리 모드 시나리오를 실행한다.
  7. 실행자: 검수 뒤 단계의 TK-18, BG-13, CM-1–CM-4, AL-4를 실행한다.
  8. 실행자: AL-6의 주간 점검 모드와 AL-8의 보고 모드를 실행한다.
- 추정: 시험 ticket 수는 아래를 합산한 값이다.
  - L2 시나리오 Task 17개, Bug 13개, Issue 10개
  - CM-1–CM-3에 1개, CM-4에 1개
  - AL-4에 1개, AL-6에 2개
- 추정: 실행 수는 ticket마다 1회에 추가 실행 7회를 합산한 값이다.
  - 추가 실행: TK-18, BG-13, CM-2, CM-3, AL-4, AL-6, AL-8
- 추정: L2 전체의 API 환산 비용은 5–20 USD다.
  - 실행 약 50회에 실행당 0.1–0.4 USD를 곱했다.
  - 실행당 값의 근거는 "L2" 절에 있다.
- 한 번에 많이 실행하면 좌석 사용량 한도에 걸릴 수 있다.
  - 지난 시험에서 한도 초과로 4단계가 실패한 적이 있다.
    - 근거: Actions 실행 38356731083
  - 실행자는 하루 실행 수를 나눠 잡는다.
