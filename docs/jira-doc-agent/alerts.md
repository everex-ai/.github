# jira-doc 알림 체계

jira-doc의 알림은 즉시, 주간, 월간, 분기, 연간의 다섯 주기로 나간다.
담당자도 받는 알림은 Jira comment와 연동 채널로 간다.
팀장만 받는 알림은 팀장 채널로 간다.

- 알림은 모두 스크립트가 만든다.
  - CI agent는 알림 문장을 만들지 않는다.
  - 만드는 곳: `scripts/src/jira/`, `scripts/src/jira/report/`
  - F2 stop 사유 요청 comment만 Jira Automation이 만든다.
- 이 문서의 동작 흐름과 실행 모드는 `docs/jira-doc-agent/README.md`에 있다.

## 용어

이 문서는 아래 용어를 한 이름으로만 사용한다.

- 연동 채널: secret `SLACK_WEBHOOK_URL`의 Slack 채널
  - 담당자와 팀장이 함께 받는 Slack 알림이 간다.
- 팀장 채널: secret `SLACK_LEAD_WEBHOOK_URL`의 Slack 채널
  - 팀장만 받는 알림이 간다.
  - secret이 없으면 팀장 채널 알림을 보내지 않는다.
    - Summary 탭에는 전송 결과 행만 남긴다.
    - 알림 본문은 남기지 않는다(`scripts/apply.py`의 `main`, `scripts/src/jira/issue.py`의 `slack_token_expiry`).
- 반려: ticket이 ready-to-done에서 In Progress로 전환된 일
- 반려 cycle: 반려 뒤 다시 request 전환되어 검수를 받는 한 번의 순환
- Claude 사용량: CI agent 실행의 토큰 수와 API 환산 비용
  - API 환산 비용: claude CLI가 계산한 `total_cost_usd` 값
  - Team plan에서는 이 비용이 청구되지 않는다.
    - 대신 토큰(인증 키)을 발급한 사람의 좌석 사용량 한도를 소모한다(`docs/jira-doc-agent/README.md`의 "설치" 절 표).
- 토큰(인증 키): Jira, GitHub, Claude에 접근하는 인증 키
  - Claude 사용량의 토큰과 구분하려고 늘 "(인증 키)"를 붙인다.
- 정기 보고 메시지: 보고 모드가 매월 팀장 채널로 보내는 Slack 메시지
  - 월간 묶음: 매달 붙는 항목 묶음
  - 분기 묶음: 1·4·7·10월 메시지에 붙는 지난 분기 항목 묶음
  - 연간 묶음: 1월 메시지에 붙는 지난해 항목 묶음
- ❌: 검사 표 결과 칸의 "❌ 보완 필요"(`scripts/src/constant/check.py`의 `RESULT_KO`)

## 주기별 알림

| 주기 | 알림 | 게시 위치 | 받는 사람 | 만드는 곳 |
|---|---|---|---|---|
| 즉시 | F2 stop 사유 요청 comment | Jira comment | 담당자 | Jira Automation F2 |
| 즉시 | 검수 comment | Jira comment | 통과와 보류는 담당자, 검토 요청은 팀장 | `review_comment` |
| 즉시 | 문서화 리뷰 comment(관찰 모드) | Jira comment | 팀장 | `doc_review_comment` |
| 즉시 | 검수 뒤 Slack 알림 | 연동 채널 | 팀장 | `review_slack_text` |
| 즉시 | 팀장 확인 결과 알림 | 연동 채널 | 담당자 | `apply_decision`, `decision_slack_text` |
| 즉시 | 실패 알림 | 연동 채널 | 팀장 | `failure_slack_text`, `run_failure_slack_text` |
| 주간 | 주간 점검 메시지(R4, A1, A3) | 연동 채널 | R4, A1은 담당자, A3는 팀장 | `slack_weekly`, `weekly_lines` |
| 주간 | 토큰(인증 키) 만료 알림 | 팀장 채널 | 팀장 | `slack_token_expiry`, `token_expiry_text` |
| 월간 | 정기 보고 메시지의 월간 묶음 | 팀장 채널 | 팀장 | `scripts/src/jira/report/run.py`의 `run_report` |
| 분기 | 정기 보고 메시지의 분기 묶음 | 팀장 채널 | 팀장 | 같음 |
| 연간 | 정기 보고 메시지의 연간 묶음 | 팀장 채널 | 팀장 | 같음 |

- 만드는 곳 칸의 함수는 따로 적지 않으면 `scripts/src/jira/` 아래 모듈에 있다.
- 팀장 확인 결과 알림은 팀장의 전환 때 나간다.
  - F4가 확인 결과 알림 모드를 실행하고, 그 실행에서 apply.py가 보낸다.
  - 대상 전환: Done(통과), In Progress(보류)
  - 본문은 상태 변경 결과만 담는다.
    - 팀장 확인 결과(통과, 보류)
    - 상태 변경(`ready-to-done → Done` 또는 `ready-to-done → In Progress`)
    - ticket 링크
  - 팀장 계정이 한 전환이 아니면 보내지 않는다(`scripts/src/jira/issue.py`의 `apply_decision`).
  - variable `JIRA_LEAD_ACCOUNT_ID`가 비면 보내지 않고 Summary 탭에 경고를 남긴다.
  - 같은 전환은 한 번만 알린다.
    - 알린 전환 시각을 jira-doc 기록의 `lastDecisionAt`에 남긴다.
- 주간 알림은 매주 수요일 09:00 KST cron으로 나간다(`.github/workflows/jira-doc.yml:14`).
- 정기 보고 메시지는 매월 1일 09:00 KST cron으로 나간다(`.github/workflows/jira-doc.yml:15`).
  - 추정: 실제 발송은 4–5시간 늦을 수 있다.
    - 근거: 주간 점검 cron 3회의 시작 시각(`gh run list --workflow jira-doc.yml`)
      - 2026-09-23–2026-10-07 실행의 시작 시각: 04:06Z–05:13Z
- 누락 알림 comment는 2026-10 알림 체계 재구성 때 없앴다.
  - ticket 키 없이 실행한 정리 모드가 게시하던 comment다.
  - 근거: commit `b9a7e28`의 `scripts/apply.py`의 `apply_alerts`
  - 같은 누락 항목은 주간 점검 메시지로 알린다.

## 받는 사람별 알림과 할 일

- 담당자가 받는 알림과 할 일은 아래와 같다.
  - F2 stop 사유 요청 comment: 중단 이유와 재시작 시점을 남긴다.
  - 팀장 확인 결과 알림(보류): 검수 comment의 요청대로 기록한다.
    - 그 뒤 다시 request 전환한다.
  - 팀장 확인 결과 알림(통과): 할 일이 없다.
  - 주간 점검 메시지(R4, A1): 사유나 진행 상황을 남긴다.
    - 멈춘 ticket이면 stop 전환으로 Backlog에 보낸다.
- 팀장이 받는 알림과 할 일은 아래와 같다.
  - 검수 뒤 Slack 알림과 검수 comment: ticket을 확인하고 직접 전환한다.
    - 전환하면 F4가 담당자에게 팀장 확인 결과 알림을 보낸다.
    - 통과: Done으로 전환한다.
    - 보류: In Progress로 전환한다.
    - 검토 요청: In Progress나 Done으로 전환한다.
  - 문서화 리뷰 comment: 담당자의 기록 습관을 확인한다.
  - 실패 알림: `docs/jira-doc-agent/README.md`의 "실패 처리" 절에 따라 원인을 찾고 다시 실행한다.
    - 실패 알림은 ticket 실패 알림과 실행 실패 알림 두 가지다.
      - 두 알림의 정의는 README의 "실행 실패 알림(5단계)" 절에 있다.
  - 주간 점검 메시지(A3): 머문 ticket을 확인하고 `end` 전환한다.
    - 보완이 필요하면 In Progress로 전환한다.
  - 토큰(인증 키) 만료 알림: "토큰(인증 키) 만료 알림" 절대로 갱신한다.
  - 정기 보고 메시지: 구두 피드백 대상 후보에게 구두 피드백을 한다.
    - 후보의 정의는 "구두 피드백 대상 후보와 개선 여부" 절에 있다.

## 정기 보고 메시지

정기 보고 메시지는 월간, 분기, 연간 묶음을 묶음마다 따로 보낸다.
모든 묶음의 절 순서는 팀장 업무, 팀 전체, 팀원별, Claude 활용 요약이다.

- 보고 대상 달은 지난달이다.
  - Actions 수동 실행(mode `report`)에 `month`(`YYYY-MM`)를 넣으면 그 달로 만든다.
- 시험 ticket은 집계에서 뺀다(`scripts/src/jira/report/fetch.py`의 `collect`).
  - 시험 ticket: 제목이 아래 머리말로 시작하는 ticket
    - `[jira-doc 테스트]`, `[jira-doc 시험]`, `[automation 테스트]`
    - 머리말 목록: `scripts/src/constant/jira.py`의 `TEST_TITLE_PREFIXES`
  - 주간 점검 메시지에서는 빼지 않는다.
- 묶음마다 Slack 메시지 하나를 보낸다(`scripts/src/jira/report/message.py`의 `Report.messages`).
  - 월간 묶음: 매달
  - 분기 묶음: 대상 달이 3, 6, 9, 12월
  - 연간 묶음: 대상 달이 12월
- 메시지마다 40줄 이내, 줄마다 50자 이내로 적는다.
  - 담당자가 많으면 담당자 한 명당 두 줄로 줄이고, 넘치면 "외 n명"으로 적는다.
- 메시지에는 값 위주로 적고, 지표의 뜻은 이 문서에 둔다.
  - 메시지 끝 줄에 이 문서의 경로를 적는다.

| 묶음 | 팀장 업무 | 팀 전체 | 팀원별 | Claude 활용 요약 |
|---|---|---|---|---|
| 월간 | 판정 일치율, 팀장 확인 대기, 팀장 확인 시간 | ticket 현황, 첫 검수 통과율, 최대 검수 반복 횟수, stop 사유 기록률 | 팀 전체 지표와 업데이트 없는 기존 ticket 수, 구두 피드백 대상 후보, 지난달 후보의 개선 여부 | 자동 검수 소요 시간, API 환산 비용(전월 대비, 사용량 많음 표시), 토큰 종류별 수, Claude 사용 ticket Top3 |
| 분기 | 판정 일치율, 검토 요청 비율, 팀장 확인 시간 | KR별 하위 Task의 done 수와 결과 산출물 달성률, 검사 ID별 ❌ 상위 5개, 첫 검수 통과율, 최대 검수 반복 횟수 | 첫 검수 통과율, 최대 검수 반복 횟수, stop 사유 기록률 | 월간과 같음(전 분기 대비, 사용량 많음 표시 없음) |
| 연간 | 판정 일치율(분기별 값) | 첫 검수 통과율(분기별 값), ❌ 상위 3개 검사 ID의 분기별 만족률 | 첫 검수 통과율, 최대 검수 반복 횟수 | 월간과 같음(전년 대비, 분기별 합계 추가, 사용량 많음 표시 없음) |

- 월간과 분기 묶음의 판정 일치율은 두 값을 함께 적는다.
  - 두 값: 기간 값, 최근 20건 값("판정 일치율" 절)
  - 연간 묶음은 연간 값과 분기별 값을 적는다.
- 메시지 제목은 묶음마다 아래와 같다.
  - 월간: `[YYYY-MM jira-doc 월간 보고]`
  - 분기: `[YYYY Qn jira-doc 분기 보고]`
  - 연간: `[YYYY jira-doc 연간 보고]`
- 팀원별 절의 첫 줄에 값 순서를 적고, 담당자마다 값만 적는다.
- 메시지의 표기는 아래와 같다.
  - 괄호 안의 "(n건)": 그 값을 구한 표본 수
    - 예: 팀장 확인 시간의 "(5건)"은 팀장의 전환 5건
  - `평균 390초(120–900초, 24건)`: 자동 검수 소요 시간의 평균
    - 괄호 안은 최소–최대와 request 전환 수다.
  - "전월 대비 +81%": 직전 기간 API 환산 비용 대비 증감
    - 직전 기간 기록이 없으면 "직전 기록 없음"으로 적는다.
  - 토큰 수의 "천", "만", "억": 1,000, 10,000, 100,000,000 단위
  - "후보(A, B: T2)": 구두 피드백 대상 후보 표시
    - 조건 A, B를 만족했고 B의 검사 ID가 T2라는 뜻이다.
  - `-`: 그 기간에 기록이 없음
  - 검사 ID는 메시지 길이 때문에 이름 없이 ID만 적는다.
  - 팀원별 값: " / "는 지표 사이의 구분자이고, "a/b"는 분자/분모다.
    - 예: "0/1 / 3회 / 2/3"
      - 첫 검수 1건 중 통과 0건, 최대 3회, R4 행 3건 중 만족 2건
  - `업데이트 없음 n개: In Progress 3, To Do 1`: 대상 기간 끝 상태별 수
    - 0개인 상태는 적지 않는다.
  - Top3의 `(1) INNO-29 Task Kai 2.10 USD`
    - 순위, ticket 키, work type, 담당자, API 환산 비용이다.
    - 그 아래 줄은 제목 앞 20자와 사유(실행 종류별 횟수)다.
  - "달성률 m/n개": 결과 산출물 달성률. 달성한 예상 산출물 m개 / 예상 산출물 n개
- 지표의 뜻은 아래와 같다.
  - 첫 검수 통과율: ticket별 첫 검수 comment 중 통과인 비율
    - 대상: 대상 기간에 작성된 첫 검수 comment
  - stop 사유 기록률: 검수 comment의 R4 행 중 만족 비율
    - 대상: 대상 기간 검수 comment. 해당 없음은 뺀다.
  - 검토 요청 비율: 검수 comment 중 검수 결과가 검토 요청인 비율
  - 만족률: 검사 ID별로 해당 없음을 뺀 검사 표 행 중 만족인 비율
  - KR: Jira work type이 KR인 ticket. 그 아래 Task의 상위 ticket이다.
  - 결과 산출물 달성률: 결과 산출물 구역의 달성 요약 줄을 합산한 값
    - 줄 꼴: `예상 산출물 n개 중 m개 달성`(`scripts/src/jira/comment.py`의 `deliverables_wiki`가 만듦)
  - 아래 넷은 "ticket 현황과 팀장 확인 지표" 절에 있다.
    - ticket 현황, 최대 검수 반복 횟수
    - 팀장 확인 대기, 팀장 확인 시간
  - Claude 활용 요약 절의 지표는 "Claude 활용 요약 지표" 절에 있다.
- 상위 3개와 상위 5개는 메시지 길이를 줄이려는 값이다.
- 메시지 길이를 줄이려고 아래처럼 적는다(`scripts/src/jira/report/message.py`의 `Report`).
  - Top3는 ticket마다 두 줄로 적는다.
    - 제목은 앞 20자까지 적는다.
    - 둘째 줄이 50자를 넘으면 제목을 더 줄이거나 뺀다.
    - 사유만으로 50자를 넘으면 뒤 갈래를 "외 n종"으로 줄인다.
  - 분기 묶음의 KR 행은 5개까지 적고, 나머지는 "외 n개"로 적는다.
  - 월간 묶음의 팀원별 순서는 이번 달 후보, 지난달 후보, 나머지 담당자 순이다.
  - 분기와 연간 묶음의 팀원별 순서는 이름 순이다.
- Summary 탭에는 팀 합계만 남긴다.
  - 담당자 이름과 담당자별 수치, 구두 피드백 대상 후보는 남기지 않는다.
  - 이유는 "중복 방지와 멘션 규칙" 절의 Summary 탭 항목과 같다.

## 지표 정의

정기 보고 메시지의 지표는 아래 절의 정의를 따른다. `scripts/src/jira/report/`가 Jira 기록과 jira-doc 기록으로 계산한다.

### ticket 현황과 팀장 확인 지표

- 기존 ticket: 아래 두 조건을 모두 만족하는 ticket
  - 대상 달 1일 0시(한국 시간) 전에 만들어짐
  - 그 시각의 상태가 done, Backlog, Deleted가 아님
- 1일 0시의 상태는 Jira 변경 이력으로 되돌려 구한다.
  - 그 시각 전 마지막 상태 변경이 있으면 그 도착 상태
  - 없으면 그 시각 뒤 첫 상태 변경의 출발 상태
  - 상태 변경이 없으면 지금 상태
- 업데이트: 대상 기간 안에 아래 기록이 하나 이상 있음
  - 사람 comment(jira-doc이 남긴 comment 제외)
  - description 수정(결과 산출물 구역 밖이 바뀐 경우만)
    - jira-doc이 고치는 결과 산출물 구역은 사람의 업데이트로 세지 않는다.
    - jira-doc 계정이 팀장 계정이라 수정한 사람으로는 구분할 수 없다.
  - 상태 변경
  - sub-task 추가
  - PR 추가(제목에 ticket 키가 있는 PR 생성)
    - PR 검색이 실패하면 PR 추가는 업데이트에서 빼고 메시지에 한 줄로 알린다.
- 업데이트 없는 기존 ticket은 대상 기간 끝의 상태 이름별로 센다.
  - 예: ready-to-done이면 팀장 확인을 기다리는 ticket이다.
  - 예: In Progress이면 담당자가 진행하지 않은 ticket이다.
- 새 ticket: 대상 기간에 만들어진 ticket. 지금 상태별 수를 적는다.
- 최대 검수 반복 횟수: ticket 하나의 대상 기간 request 전환 횟수 중 최댓값
  - 그 ticket 키를 함께 적는다.
  - 팀원별 값은 그 담당자 ticket 중 최댓값이다.
- 팀장 확인 대기: 대상 기간 끝에 ready-to-done인 ticket 수
  - 진입 뒤 가장 오래 머문 영업일 수를 함께 적는다.
  - 셈하는 끝은 대상 기간 끝과 실행 시각 중 이른 시각이다.
    - 달 중간에 수동 실행해도 아직 오지 않은 날을 세지 않는다.
- 팀장 확인 시간: 팀장 계정이 ready-to-done에서 전환한 경우의 머문 시간 중앙값
  - 머문 시간: 마지막 ready-to-done 진입부터 그 전환까지
  - 자동 보류 전환은 뺀다.
    - 자동 보류 전환: 보류 검수 뒤 apply.py가 In Progress로 되돌린 전환
    - 기준: 전환 직전 검수 comment에 "상태를 in-progress로 되돌림"이 있음
    - 판정 일치율과 같은 기준이다(`scripts/src/jira/report/fetch.py`의 `AUTO_HOLD_NOTE`).
- 담당자는 지금 담당자로 센다. 담당자 변경 이력은 반영하지 않는다.

### Claude 사용량 네 갈래

- 한 달 Claude 사용량을 실행 종류별 네 갈래로 나눈다.

| 갈래 | 실행 | 원인 |
|---|---|---|
| 첫 검수 | ticket의 첫 request 전환에 따른 검수 | done 전환 ticket 수 |
| 반려 cycle 재검수 | 두 번째 이후 request 전환에 따른 검수 | 담당자의 문서화 |
| 재실행·실패 | 3-R 재실행이 있었던 실행, 반영에 실패한 실행, 같은 request 전환을 다시 검수한 실행 | CI agent 쪽 문제, 수동 재실행 |
| 정리 모드 | F3 정리 | 팀장·담당자의 F3 버튼 요청 |

- 같은 request 전환의 재검수는 조건이 있을 때만 재실행·실패로 센다.
  - 조건: 앞서 반영에 성공한 실행이 있음(`scripts/src/jira/report/parse.py`의 `mark_reruns`)
  - 앞선 실행이 모두 반영 실패이면 첫 성공 실행은 원래 갈래로 센다.

- 월 합계는 실행 날짜(한국 시간) 기준으로 센다.
- 사용량 많음은 아래 두 조건을 모두 만족할 때다.
  - 이번 달 API 환산 비용이 전월의 1.5배 이상
  - 전월 검수 실행이 10건 이상
    - 전월 실행이 적으면 조금만 늘어도 1.5배를 넘기 때문이다.
- 판정 기준은 토큰 수가 아니라 API 환산 비용이다.
  - 토큰 종류마다 단가가 달라 토큰 수 합계로는 비교하기 어렵다.
  - 토큰 수는 종류별로 함께 적는다.
- 사용량 많음 판정은 월간 묶음의 API 환산 비용 줄에 "사용량 많음"으로 표시한다.
  - 판정 근거는 Summary 탭에 남긴다.
- 사용량 기록은 2026-10-15 10:49(한국 시간)부터 있다.
  - 근거: `runs`를 남기는 `add_run`이 PR #23으로 main에 들어간 시각
  - 그래서 사용량 많음의 첫 판정은 2027년 1월 메시지(12월 대 11월)다.

### Claude 활용 요약 지표

- 자동 검수 소요 시간: request 전환부터 첫 검수 comment까지 걸린 시간
  - request 전환마다 재고, 평균과 최소–최대를 초로 적는다.
  - 다음 request 전환 전에 검수 comment가 없으면 뺀다.
  - CI agent의 처리 시간이라 팀장 업무가 아니다.
  - `metrics.md`의 J4(피드백 대기 시간)와 같은 계산이다.
- API 환산 비용 줄: 대상 기간 실행의 API 환산 비용 합
  - 메시지에는 줄 이름을 `API 환산 금액`으로 적는다.
  - 직전 기간 대비 증감을 함께 적는다.
  - 직전 기간: 월간은 전월, 분기는 전 분기, 연간은 전년
- 토큰: 대상 기간 실행의 토큰 수를 종류별로 합한 값
  - Team plan 사용 한도는 토큰 수로 공개되지 않아 비율을 적지 않는다.
    - 근거: https://support.claude.com/en/articles/9266767-what-is-the-team-plan

| 종류 | 메시지 표기 | 뜻 |
|---|---|---|
| input | input | cache에 저장하지도, cache에서 읽지도 않은 입력 토큰 |
| output | output | CI agent가 만든 출력 토큰 |
| cacheWrite | cache 쓰기(지시·기록 저장) | 이번 요청에서 cache에 새로 저장한 입력 토큰 |
| cacheRead | cache 읽기(저장분 재사용) | 앞 요청이 저장한 cache에서 다시 읽은 입력 토큰 |

- 표의 뜻은 API 응답 사용량 필드의 정의를 옮긴 것이다.
  - 근거: https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md "Tracking cache performance"
  - 원문: `cache_creation_input_tokens: Number of tokens written to the cache when creating a new entry.`
  - 원문: `cache_read_input_tokens: Number of tokens retrieved from the cache for this request.`
- 추정: CI agent는 턴마다 지시 파일과 읽은 ticket 기록을 다시 보낸다.
  - 처음 보낸 부분은 cache 쓰기로 잡힌다.
  - 다음 턴에 다시 보낸 같은 부분은 cache 읽기로 잡힌다.
- 단가는 종류마다 다르다(`claude-sonnet-5-5`, USD/100만 토큰).
  - input 2, output 10, cache 쓰기 2.5–4, cache 읽기 0.1
  - 근거: `~/.claude/scripts/prices.json`(2026-10-12 가격 페이지에서 옮긴 값)
- Claude 사용 ticket Top3: API 환산 비용 합이 큰 ticket 3개
  - 대상 기간 실행만 합한다.
  - 같은 비용이면 ticket 키 순이다.
  - 사유: 그 ticket의 대상 기간 실행을 "Claude 사용량 네 갈래"로 센 횟수
    - 0회인 갈래는 적지 않는다.

### 구두 피드백 대상 후보와 개선 여부

- 구두 피드백 대상 후보는 담당자 단위로 매달 고른다.
  - 조건 A: 한 ticket이 그 달에 반려 2회 이상
  - 조건 B: 같은 검사 ID가 그 달에 ❌ 2회 이상
    - 그 담당자 ticket의 검수 comment와 문서화 리뷰 comment를 센다.
  - A 또는 B를 만족하면 후보다.
- 후보마다 근거로 ticket 키와 반려 횟수, 검사 ID와 ❌ 횟수를 적는다.
- 지난 피드백 개선 여부는 지난달 후보를 같은 규칙으로 다시 계산해 정한다.
  - 그래서 팀장이 피드백 기록을 따로 남기지 않는다.
  - 결과 값은 아래 넷 중 하나다.
    - 재발: 지적 검사 ID 중 하나가 이번 달에 다시 ❌
    - 개선: 재발이 없고 첫 검수 통과율이 지난달보다 높음
    - 유지: 재발과 개선이 아닌 경우
    - 판단 불가: 이번 달 검수가 없음
  - 실제 구두 피드백 여부는 반영되지 않는다.
    - 결과는 "후보로 알린 뒤의 변화"로 해석한다.

### 판정 일치율

- 판정 일치율은 CI agent의 검수 결과와 팀장 전환을 비교해 구한다.

| CI agent의 검수 결과 | 팀장이 Done 전환 | 팀장이 In Progress 전환 |
|---|---|---|
| 통과 | 일치 | 불일치 |
| 보류 | 불일치 | 일치 |
| 검토 요청 | 일치율에서 뺌 | 일치율에서 뺌 |

- 팀장 계정이 한 전환만 센다.
  - 전환한 사람은 Jira 변경 이력(changelog)의 작성자로 확인한다.
  - 담당자가 직접 되돌린 경우는 뺀다.
- 보류 검수 comment에 "상태를 in-progress로 되돌림"이 있으면 뺀다.
  - 2026-10 알림 체계 재구성 전에는 apply.py가 보류 ticket을 되돌렸다.
    - 근거: commit `b9a7e28`의 `scripts/apply.py`의 `review_transition`
    - 그 전환도 팀장 계정으로 기록된다.
  - 그 전환은 팀장의 판단이 아니다.
  - 추정: 그래서 2026년 4분기의 일치율 표본이 적다.
- 검토 요청 비율은 따로 적는다.
- 분기값과 최근 20건 일치율을 함께 적는다.
  - 20건은 도입 기준 "20건 중 4건 이하 뒤집힘"과 같은 크기다(`docs/jira-doc-agent/metrics.md`의 J7).
- 추정: 팀장이 검수 comment를 읽고 판단하므로 일치율이 높게 나올 수 있다.

## Claude 사용량 기록

CI agent를 실행할 때마다 apply.py가 Claude 사용량을 기록한다.
기록 위치는 그 ticket의 jira-doc 기록 `runs`다(`scripts/src/jira/runs.py`의 `add_run`).

- 사용량은 CI agent가 아니라 `scripts/save_output.py`가 실행 결과 파일에서 읽는다.
  - 실행 결과 파일: CI agent 실행 결과 JSON
    - claude-code-action이 남긴다.
  - 값은 claude CLI가 API 응답의 사용량을 모아 남긴 값이다.
    - 근거: https://code.claude.com/docs/en/agent-sdk/python.md "ResultMessage"의 `usage`, `model_usage`
    - 같은 문서는 이 값을 청구서가 아닌 추정치로 다루라고 한다.
      - 원문: `Treat model_usage as an estimate, not a billing statement.`
  - 3-2, 3-R 단계가 실행마다 `out/_usage.json`에 저장하고, 4단계가 합산한다.
  - 3-1 단계 로그에도 토큰 수 줄이 남는다(`scripts/save_output.py --exec-summary`).
- `runs`의 항목은 아래 표와 같다.

| 키 | 값 |
|---|---|
| `at` | 실행 시각(ISO 8601, 한국 시간) |
| `mode` | review, digest |
| `verdict` | pass, fix, escalate, failed, digest |
| `requestNo` | 몇 번째 request 전환에 대한 검수인지. 정리 모드는 null |
| `retry` | 3-R 재실행 포함 여부 |
| `costUsd` | API 환산 비용 합(3단계와 3-R 단계) |
| `tokens` | `input`, `output`, `cacheWrite`, `cacheRead` |
| `models` | 사용한 모델 이름 목록 |

- `runs`는 최근 100건만 남긴다(`scripts/src/jira/runs.py`의 `add_run`).
  - 기록 크기가 30,000바이트를 넘으면 오래된 항목부터 더 지운다.
  - 추정: issue property 값의 크기 상한(32KB)을 넘지 않게 하려는 것이다.
- 정리 모드는 ticket 키가 필수라 실행 하나가 ticket 하나다.
  - 그래서 사용량을 ticket과 담당자에 나눌 수 있다.

## 토큰(인증 키) 만료 알림

주간 점검 모드에서 apply.py가 variable `TOKEN_EXPIRY`의 만료일을 확인한다.
30일 이하로 남은 항목을 팀장 채널로 알린다(`scripts/src/jira/issue.py`의 `slack_token_expiry`).

- 갱신해 `TOKEN_EXPIRY`를 고칠 때까지 매주 다시 알린다.
- `TOKEN_EXPIRY`가 없거나 JSON이 틀리면 알림을 보내지 않고 Summary 탭에 경고만 남긴다.
  - 날짜 형식이 틀린 항목은 그 항목만 경고하고, 나머지 항목은 알린다.
- 알림의 남은 날 표시는 아래 셋 중 하나다(`scripts/src/jira/slack.py`의 `token_expiry_text`).
  - 남은 날 n일, 오늘 만료, 만료됨
- 만료일은 공개돼도 문제없는 값이라 secret이 아니라 variable에 둔다.
- 이 알림은 주간 점검 모드의 1단계(Jira 수집)가 성공해야 나간다.
  - 1단계가 실패하면 4단계가 실행되지 않기 때문이다(`.github/workflows/jira-doc.yml:158`).
  - 그래서 `JIRA_API_TOKEN`이 이미 만료됐으면 이 알림 대신 실행 실패 알림이 나간다.
  - 팀장은 만료 30일 전 알림을 받으면 그 주 안에 갱신한다.
- 2026-10-14에 등록할 값은 아래 표와 같다.
  - 모든 토큰(인증 키)은 만료 1년으로 발급했다(팀장 확인, 2026-10-14).

| 토큰(인증 키) | 발급일 | 만료일 | 갱신할 곳 |
|---|---|---|---|
| `JIRA_API_TOKEN` | 2026-09-15 | 2027-09-15 | Atlassian 계정의 API 토큰 화면에서 발급, secret 교체 |
| `ORG_READ_TOKEN` | 2026-09-15 | 2027-09-15 | GitHub Settings의 Personal access tokens에서 발급, secret 교체 |
| `F1 PAT` | 2026-09-15 | 2027-09-15 | GitHub Settings의 Personal access tokens에서 발급, Jira Automation F1 설정 교체 |
| `CLAUDE_CODE_OAUTH_TOKEN` | 2026-10-12 | 2027-10-12 | `claude setup-token`으로 발급, secret 교체 |

- 등록 명령은 아래와 같다. 갱신할 때도 값만 바꿔 다시 실행한다.
  - 처음 등록할 때는 `POST`, 고칠 때는 `PATCH repos/everex-ai/.github/actions/variables/TOKEN_EXPIRY`를 사용한다.

```bash
gh api -X POST repos/everex-ai/.github/actions/variables -f name=TOKEN_EXPIRY \
  -f value='{"JIRA_API_TOKEN":"2027-09-15","ORG_READ_TOKEN":"2027-09-15","F1 PAT":"2027-09-15","CLAUDE_CODE_OAUTH_TOKEN":"2027-10-12"}'
```

- `CLAUDE_CODE_OAUTH_TOKEN`은 pr-review를 설치한 repo의 secret에도 있다.
  - 갱신하면 그 repo들의 secret도 교체한다(`docs/pr-review-agent/README.md`의 "대상 repo에 설치" 절 2번).

## 중복 방지와 멘션 규칙

- 주간 점검 메시지는 중복을 걸러 내지 않는다(`scripts/src/jira/slack.py`의 `weekly_lines`).
  - 사유나 활동을 남길 때까지 매주 같은 항목이 다시 올라온다.
  - A3 항목은 팀장이 ready-to-done에서 전환할 때까지 다시 올라온다.
    - 전환은 `end` 전환이나 In Progress 전환이다(`scripts/src/jira/precheck/run.py`의 `run_scan`).
- 주간 점검 메시지는 절마다 15건까지 적고, 나머지는 "외 n건"으로 줄인다.
  - 근거: `scripts/src/constant/slack.py`의 `ALERT_LIMIT`
- 주간 점검 메시지는 알림이 없는 주에도 "점검 결과 이상 없음"으로 전송된다(`scripts/src/jira/issue.py`의 `slack_weekly`).
- Jira comment의 멘션은 담당자와 팀장만 남긴다(`scripts/src/jira/comment.py`의 `sanitize_mentions`).
- Slack 멘션은 Jira accountId와 Slack 멤버 ID 대응표로 만든다.
  - 대응표 등록은 `docs/jira-doc-agent/README.md`의 "설치" 절 5번에 있다.
  - 대응표에 없는 사람은 멘션되지 않고 아래처럼 표시된다(`scripts/src/jira/slack.py`의 `slack_mention`, `lead_mention`).
    - 담당자: Jira 표시 이름. 담당자가 없으면 "담당자 미지정"
    - 팀장(검수 뒤 Slack 알림): "팀장"
    - 팀장(실패 알림): "팀장(Slack 멘션 없음)"
      - 팀장 accountId가 비면 "팀장 미지정"
- 정기 보고 메시지에는 Slack 멘션을 넣지 않는다.
  - 담당자는 Jira 표시 이름으로 적는다.
- Summary 탭의 Slack 메시지 사본에는 멘션 대신 이름을 적는다(`scripts/src/jira/issue.py`의 `slack_weekly`, `scripts/apply.py`의 `main`).
  - 이유: public repo의 Actions 실행 화면은 누구나 열람할 수 있다.
    - 조건: GitHub에 로그인한 사람
    - 멘션(`<@U…>`)을 그대로 남기면 Slack 멤버 ID가 드러난다.
    - 근거: https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs
      - 원문: `You must be logged in to a GitHub account to view workflow run information, including for public repositories.`
  - 같은 이유로 정기 보고 메시지의 사본에는 팀 합계만 남긴다.
