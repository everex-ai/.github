# jira-doc: AI팀 업무 문서화 agent 설치와 운영

jira-doc(AI팀 업무 문서화 agent)은 Jira task의 기록을 근거로 결과 문서를 작성하고 검수하는 agent다. GitHub Actions 워크플로, 스크립트, CI agent(Actions 워크플로 안에서 실행되는 Claude)가 읽는 지시 파일로 이루어지며, 이 문서는 그 파일, 설치, 실행 모드, 검수 뒤 흐름, 운영 방법을 다룬다.

- jira-doc이 작성해 Jira와 Slack에 남기는 결과 문서는 아래와 같다(`scripts/apply.py`의 `apply_issue`, `apply_alerts`, `slack_weekly`).
  - TL;DR: Jira task의 요약 필드
  - 결과 산출물 구역: Jira description 안의 `h2. 결과 산출물` 구역. 예상 산출물마다 달성 여부와 근거를 적는다.
  - 검수 comment: 검수 결과(통과, 검토 요청, 보류)와 검사 표를 담은 comment
  - 문서화 리뷰 comment: Task 기록의 품질 검사(문서화 리뷰) 결과를 팀장에게 공유하는 comment
  - 누락 알림 comment: stop 사유 comment가 없거나 영업일 기준 5일 이상 활동이 없는 task의 담당자에게 알리는 comment
  - 실패 알림 comment: CI agent의 출력(verdict.json)이 없거나 형식이 틀려 검수하지 못했을 때 팀장에게 알리는 comment
  - 검수 뒤 Slack 알림과 주간 점검 메시지
- 설계 문서는 claude.ai 프로젝트(claude.ai의 Projects 기능) "Jira 기반 AI팀 업무 프로세스 수립"의 `task-doc-agent/AI팀_업무문서화_agent_도입계획_v0.1.md`다.

## 용어

이 문서는 아래 용어를 한 이름으로만 사용한다.

- Actions 워크플로: `.github/workflows/jira-doc.yml`. 아래 네 단계로 실행된다(Actions 워크플로의 스텝 이름).
  1. 입력 수집
  2. 스크립트 검사. 스텝 이름은 "결정론적 검사 (precheck)"다.
  3. CI agent 문서화. 스텝 이름은 "Claude 문서화"다.
  4. 반영
- Jira 워크플로: Jira task의 상태와 상태 사이 전환의 체계
- CI agent: Actions 워크플로 3단계에서 실행되는 Claude. `ctx/`의 입력을 읽어 TL;DR과 판정 파일 verdict.json을 작성한다(3단계의 `--allowedTools`).
- 스크립트 검사: CI agent의 판단 없이 precheck.py가 규칙대로 계산하는 검사. 목록은 "파일" 절에 있다.
- 실행 모드: 아래 세 가지. 괄호 안은 Actions 워크플로 입력 `mode`의 값이다(`workflow_dispatch`의 `inputs`).
  - 검수 모드(review)
  - 정리 모드(digest)
  - 주간 점검 모드(alerts)
- 검사 ID: T1, R4처럼 검사 항목에 붙인 코드. 이름과 검사 내용은 `prompts/rules.md`의 "검사 항목" 표에 있다.
  - 앞 글자 T, B, I는 검사 대상 work type(Task, Bug, Issue)이고, R과 A는 여러 work type에 적용하는 검사다(같은 표의 "대상" 칸).
- 관찰 모드와 게이트 모드: 문서화 리뷰(Task 기록의 품질 검사 T4–T8) 결과를 다루는 두 방식. 변수 `JIRA_DOC_GATE`가 `false`이면 관찰 모드, `true`이면 게이트 모드다(`scripts/apply.py`의 `main`).
  - 관찰 모드: 문서화 리뷰 결과를 팀장용 문서화 리뷰 comment로만 공유하고 검수 결과에 넣지 않는다.
  - 게이트 모드: 문서화 리뷰 결과를 검수 comment에 넣고, 보완 필요 항목이 있으면 통과를 보류로 바꾼다.
- Summary 탭: GitHub Actions 실행 화면의 요약 탭. apply.py가 task별 결과 표를 남긴다(`scripts/apply.py`의 `Summary`).
- Jira 전환: 이름과 도착 상태는 INNO-29의 전환 목록과 같다. 전환 목록은 Jira REST API `GET /issue/<task 키>/transitions`로 조회하며, 터미널에서는 `scripts/jira.sh transitions INNO-29`를 실행한다.
  - request 전환: 담당자가 완료를 요청해 task를 In Progress에서 ready-to-done으로 보내는 전환
  - stop 전환: 진행 중인 task를 Backlog로 보내는 전환
  - `end` 전환: ready-to-done에서 done으로 가는 전환
- repository_dispatch: 외부 서비스가 GitHub API로 Actions 워크플로를 시작하는 이벤트. 이벤트의 종류 값으로 실행 모드가 정해진다(Actions 워크플로의 `MODE`).
- F1, F2, F3: jira-doc과 연결된 Jira Automation(Jira의 자동화 규칙)
  - F1: request 전환 때 repository_dispatch(종류 `jira-doc-review`)를 보내 Actions 워크플로를 검수 모드로 실행한다.
    - 근거: INNO-29의 request 전환으로 repository_dispatch 실행 `36821640590`이 생겼다(`gh run list --repo everex-ai/.github --workflow jira-doc.yml`)
  - F2(stop 사유 요청): stop 전환(In Progress에서 Backlog로 상태 변경) 때 실행된다. Jira 안에서 담당자를 멘션한 아래 안내 comment를 단다.
    - `현재 티켓을 Backlog로 이동시킴(stop 전환). 이유와 재시작 시점에 대한 사유를 아래 comment에 남겨야 함.`
    - `다시 In Progress로 상태를 변경한 이후에 사유를 남길 때는 "stop 사유:"로 시작하는 comment를 남겨야 사유로 인정됨`
    - GitHub와는 관계없다.
    - stop 뒤 24시간 안에 사유 comment가 없으면 그 task는 stop 사유 comment (R4) 누락 항목이 되어 주간 점검 메시지에 올라간다(`scripts/precheck.py`의 `run_scan`). task 키 없이 실행한 정리 모드에서는 누락 알림 comment도 달린다.
    - F2의 안내 comment는 사유로 세지 않는다(`prompts/rules.md`의 "검사 항목" 표의 R4).
    - precheck.py는 stop 뒤 다음 상태 변경 전까지 사람이 남긴 comment를 stop 사유로 센다. 그 뒤에 남긴 사람 comment는 "stop 사유:"로 시작할 때만 stop 사유로 센다(`scripts/precheck.py`의 `stop_events`, "검수 뒤 흐름" 절의 늦은 사유).
  - F3(문서 정리 지금 실행): Jira task 화면의 Automation(번개) 버튼 "문서 정리 지금 실행"으로 repository_dispatch(종류 `jira-doc-manual`)를 보내 Actions 워크플로를 정리 모드로 실행한다.

## 파일

jira-doc의 파일은 아래 표와 같다. Actions 워크플로에서 Jira에 기록하는 스크립트는 apply.py 하나다(Actions 워크플로 각 단계의 `run`).

| 경로 | 역할 |
|---|---|
| `.github/workflows/jira-doc.yml` | Actions 워크플로. 네 단계와 실행 모드 세 가지 |
| `scripts/jira_api.py` | Jira REST API v2 클라이언트(`Jira`)와 description 구역 처리 함수(`find_section`, `set_section`) |
| `scripts/collect.py` | 1단계. Jira와 GitHub에서 입력을 모아 `ctx/`에 저장 |
| `scripts/precheck.py` | 2단계. 스크립트 검사 9개와 문서화 리뷰용 수치(docFacts) 계산 |
| `scripts/apply.py` | 4단계. verdict.json 검증, 결과 산출물 구역과 comment 조립, Jira 반영, 상태 전환, Slack 알림. 주간 점검 모드에서는 Jira에 기록하지 않고 Slack 메시지만 전송 |
| `scripts/jira.sh` | 사람이 터미널에서 Jira를 조회하거나 고칠 때 사용하는 curl 래퍼 |
| `prompts/rules.md` | 문서화 규칙, 검사 항목, 검수 결과 기준 |
| `prompts/review.md`, `prompts/digest.md` | 검수 모드, 정리 모드에서 CI agent가 읽는 지시 |
| `prompts/types/{task,bug,issue}.md` | work type별 구역, 출력 형식, 검수 결과 기준 |
| `schemas/verdict.json` | verdict.json의 스키마 |
| `config/slack-users.json` | Jira accountId와 Slack 멤버 ID 짝. 주간 점검 메시지와 검수 뒤 Slack 알림의 멘션에 사용 |

- 스크립트 검사 9개는 아래와 같다(`scripts/precheck.py`의 `run_issue_dir`, `run_scan`).
  - 예상 산출물 작성 (T1), 진행 배경 작성 (T2)
  - 기본 정보와 문제(As-Is) 작성, 첨부 파일 (B1), 개선(To-Be) 작성 (B2)
  - 이슈 유형 선택 (I1), 이슈 내용 작성 (I2)
  - stop 사유 comment (R4), 완료 기준의 사후 변경 (A2)
  - 영업일 기준 5일 이상 활동 없음 (A1). 주간 점검 모드와, task 키 없이 실행한 정리 모드에서만 계산한다.
- docFacts는 CI agent가 문서화 리뷰를 판단할 때 인용하는 수치다(`scripts/precheck.py`의 `doc_facts`).
  - 진행 배경의 줄 수와 글자 수
  - 작업 기간(첫 In Progress 전환부터 마지막 request 전환까지), 그 기간의 영업일 수, 사람 comment 수
  - sub-task마다 description 줄 수, 사람 comment 수, PR(pull request) 수

## 설치

설치는 파일 배치, GitHub 설정, Jira Automation, Jira 전환, Slack 멤버 ID의 다섯 단계다.

1. "파일" 절의 파일이 이 repo의 기본 브랜치(main)에 있어야 한다.
   - F1과 F3의 repository_dispatch 실행은 기본 브랜치의 코드를 사용한다(INNO-29의 request 전환으로 생긴 실행 `36821640590`의 브랜치가 main, `gh run list --repo everex-ai/.github --workflow jira-doc.yml`).
   - 다른 브랜치의 코드는 Actions 수동 실행에서 브랜치를 골라 시험한다(`gh workflow run jira-doc.yml --ref <브랜치>`).
2. repo의 Settings > Secrets and variables > Actions에 아래 표의 값을 등록한다.
   - Actions 워크플로가 읽는 secret 6개와 variable 5개가 모두 표에 있다(Actions 워크플로의 `env`).

| 이름 | 종류 | 값 |
|---|---|---|
| `JIRA_CLOUD_ID` | secret | `https://<site>.atlassian.net/_edge/tenant_info`의 cloudId |
| `JIRA_EMAIL` | secret | `JIRA_API_TOKEN`을 발급한 계정 이메일 |
| `JIRA_API_TOKEN` | secret | Jira scoped API 토큰(`read:jira-work`, `write:jira-work`) |
| `ORG_READ_TOKEN` | secret | Organization 전체 repo의 Pull requests 읽기 권한이 있는 GitHub fine-grained PAT(권한과 대상 repo를 골라 발급하는 personal access token). 없으면 PR 수집을 건너뜀(`scripts/collect.py`의 `gh`) |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret | `claude setup-token`으로 발급한 Claude 구독 토큰 |
| `SLACK_WEBHOOK_URL` | secret | 주간 점검 메시지와 검수 뒤 Slack 알림을 받을 Slack 채널의 Incoming Webhook URL |
| `JIRA_DOC_GATE` | variable | `false`(관찰 모드). 게이트 모드에서는 `true` |
| `JIRA_PROJECT_KEY` | variable | `INNO` |
| `JIRA_LEAD_ACCOUNT_ID` | variable | 팀장의 Atlassian accountId |
| `JIRA_TLDR_FIELD_ID` | variable | `customfield_10650` |
| `JIRA_SITE_URL` | variable (선택) | `https://<site>.atlassian.net`. 비우면 Jira serverInfo API로 알아냄(`scripts/jira_api.py`의 `site_url`) |

3. Jira Automation F1, F2, F3을 만들고 켠다.
   - 시험 중에는 F1과 F3을 꺼 두고 Actions 수동 실행(Actions 워크플로의 `workflow_dispatch`)으로 확인할 수 있다.
4. Jira 워크플로의 전환을 아래처럼 준비한다.
   - ready-to-done에서 In Progress로 가는 전환이 있어야 하고, jira-doc이 사용하는 Jira 계정이 그 전환을 실행할 수 있어야 한다.
     - 검수 결과가 보류이면 apply.py가 이 전환을 실행한다(`scripts/apply.py`의 `review_transition`, `scripts/jira_api.py`의 `transition_to`).
   - `end` 전환에 Administrator 제한을 건다. 통과와 검토 요청 task는 팀장이 확인한 뒤 done으로 직접 전환하기 때문이다("검수 뒤 흐름" 절).
     - apply.py는 `end` 전환을 실행하지 않는다(`scripts/apply.py`의 `review_transition`).
5. `config/slack-users.json`에 담당자들과 팀장의 Jira accountId와 Slack 멤버 ID를 넣는다.
   - 파일에 없는 사람은 멘션되지 않는다. Slack 메시지에는 담당자의 Jira 표시 이름 또는 "팀장"이 표시된다(`scripts/apply.py`의 `slack_mention`, `apply_issue`).

## 실행 모드

주기 실행은 주간 점검 모드뿐이다(Actions 워크플로의 `schedule`에 cron 하나). 검수 모드와 정리 모드는 Jira Automation이나 Actions 수동 실행으로 시작한다.

| 실행 모드 | 시작 | 하는 일 | CI agent |
|---|---|---|---|
| 검수 모드(review) | F1, Actions 수동 실행(task 키 필수) | TL;DR, 결과 산출물 구역(Task만), 검수 comment, 문서화 리뷰 comment(Task, 관찰 모드), 상태 전환과 Slack 알림 | 실행 |
| 정리 모드(digest), task 키 지정 | F3, Actions 수동 실행 | 그 task의 TL;DR과 결과 산출물 구역을 지금까지의 기록으로 다시 작성. comment와 검수 결과 없음 | 실행 |
| 정리 모드(digest), task 키 없음 | Actions 수동 실행 | 최근 3일 안에 사람이 바꾼 In Progress, Backlog task를 정리(실행당 최대 20건). Done, Deleted가 아닌 task 전체에서 누락 항목(R4, A1)을 찾아 누락 알림 comment를 담당자 멘션과 함께 게시 | 실행 |
| 주간 점검 모드(alerts) | 매주 수요일 09:00 KST cron, Actions 수동 실행 | stop 사유 comment가 없는 task(R4)와 영업일 기준 5일 이상 활동이 없는 task(A1)를 Slack 메시지 한 건으로 보고 | 실행하지 않음 |

- 이 표의 근거는 아래와 같다.
  - `scripts/collect.py`의 `main`
  - `scripts/apply.py`의 `apply_issue`, `apply_alerts`, `slack_weekly`
  - Actions 워크플로의 `schedule`과 3단계의 `if`
- 누락 알림 comment는 같은 알림을 한 번만 단다. 보낸 누락 알림 코드(알림 종류와 날짜로 만든 코드. 예: `STALE:2026-09-15`)를 task의 jira-doc 기록(Jira issue property `ai-doc-agent`)에 저장하기 때문이다(`scripts/apply.py`의 `apply_alerts`).

## 검수 뒤 흐름

검수 모드는 검수 결과에 따라 task 상태를 바꾸거나 유지하고, `SLACK_WEBHOOK_URL`의 Slack 채널로 Slack 알림을 보낸다(`scripts/apply.py`의 `apply_issue`, `review_slack_text`).

- 이 흐름은 관찰 모드와 게이트 모드에서 같다. 상태를 정하는 함수가 모드 값을 받지 않는다(`scripts/apply.py`의 `review_transition`).
- 검수 결과의 조건은 `prompts/rules.md`의 "검수 결과 (검수 모드)" 절에 있다.

| 검수 결과 | 조건 | 상태 | Slack 알림 |
|---|---|---|---|
| 통과 | 예상 산출물이 모두 달성이고, 필수 검사(`prompts/rules.md` 검사 항목 표에서 실패 시 보류나 검토 요청으로 이어지는 검사)가 모두 만족 | ready-to-done 유지 | 팀장 멘션. task 링크와 통과 이유. 팀장이 확인한 뒤 done으로 직접 전환 |
| 검토 요청 | 미달성 항목마다 담당자가 comment로 남긴 사유가 있음, 또는 팀장 판단이 필요함(예상 산출물이 모호함, ready-to-done 뒤 예상 산출물이 바뀜, CI agent 출력 오류, 근거끼리 모순) | ready-to-done 유지 | 팀장 멘션. task 링크와 검토 요청 이유. 팀장이 확인한 뒤 In Progress 또는 done으로 직접 전환 |
| 보류 | 사유 없는 미달성 항목이 있음, 또는 담당자가 고칠 수 있는 누락이 있음(예상 산출물·진행 배경·Bug의 기본 정보와 문제(As-Is)와 개선(To-Be)·Issue 유형과 내용의 템플릿 누락, stop 사유 comment 없음, 근거 링크 없음, Bug의 To-Be 동작 확인 comment 없음) | In Progress로 자동 전환 | 담당자 멘션. task 링크, 보류 이유, 요청 |

- apply.py는 CI agent가 정한 검수 결과를 아래 경우에 다시 정한다(`scripts/apply.py`의 `apply_issue`, `recheck_verdict`).
  - 완료 기준의 사후 변경 (A2)이 실패이면 검토 요청으로 바꾼다.
  - 스크립트 검사 중 T1, T2, B1, B2, I1, I2, R4에 실패가 있으면 통과를 보류로 바꾼다.
  - 예상 산출물과 결과 산출물의 1:1 대응 (T3)이 맞지 않으면 검토 요청으로 바꾼다.
  - Task의 미달성 항목마다 담당자의 사유가 있으면 검토 요청으로 바꾼다. 통과인데 미달성 항목이 있으면 보류로 바꾼다.
  - 게이트 모드에서 문서화 리뷰에 보완 필요 항목이 있으면 통과를 보류로 바꾼다.
  - 아래 검사의 실패와 Bug, Issue의 미달성 항목은 다시 확인하지 않는다. 아래 검사는 CI agent가 판단한다.
    - 원인과 해결의 근거 링크 (B3), To-Be 동작 확인 comment (B4)
    - 대응 결과의 근거 (I3), 1년 뒤에도 이해 가능한 기록 (R2)
  - 바꾼 이유는 검수 comment의 검수 결과 줄 아래에 적힌다.
- verdict.json이 없거나 형식이 틀리면 결과를 "실패"로 처리한다(`scripts/apply.py`의 `apply_issue`, `failure_comment`).
  - 팀장을 멘션한 실패 알림 comment만 달고, 상태 전환과 Slack 알림은 하지 않는다.
- 보류로 In Progress가 된 task는 담당자가 요청대로 기록하거나 미달성 사유를 comment로 남긴 뒤 다시 request 전환을 한다. 그러면 F1이 다시 검수한다.
- 사유 없이 stop했다가 다시 진행한 task는 stop 사유 comment (R4)가 보완 필요가 되어 검수 결과가 보류가 된다(`scripts/precheck.py`의 `check_r4`, `scripts/apply.py`의 `apply_issue`).
  - 늦은 사유: stop 뒤 다음 상태 변경 이후에 남긴, "stop 사유:"로 시작하는 사람 comment. precheck.py는 이 comment를 stop 뒤 언제 남겼든 그 stop의 사유로 센다(`scripts/precheck.py`의 `stop_events`).
  - "stop 사유:" 머리말을 요구하는 이유는 다시 진행한 뒤에 남긴 진행 상황 comment를 stop 사유로 세지 않기 위해서다. precheck.py는 다음 상태 변경 뒤의 사람 comment 중 머리말이 있는 comment만 확인한다(`scripts/precheck.py`의 `stop_events`, `prompts/rules.md`의 "검사 항목" 표 아래 R4 사유 인정 규칙).
  - 보류의 요청에는 "stop(<날짜>)의 사유를 'stop 사유:'로 시작하는 comment로 남긴 뒤 다시 request 전환(담당자가 완료를 요청해 task를 ready-to-done 상태로 보내는 Jira 전환) 필요"가 들어간다(`scripts/apply.py`의 `apply_issue`).
  - 늦은 사유를 남기고 다시 request 전환하면 R4는 만족이 된다. 검수 comment의 검사 표에는 R4 내용 칸에 "늦게 기록", stop 날짜, 늦은 사유 comment 링크가 표시된다(`scripts/precheck.py`의 `check_r4`).
  - 이때 검수 결과는 R4가 만족인 상태에서 위 표의 조건대로 정해진다(`scripts/apply.py`의 `apply_issue`, `recheck_verdict`).
  - 통과와 검토 요청의 Slack 알림에도 R4의 "늦게 기록" 줄이 들어가 팀장이 확인할 수 있다(`scripts/apply.py`의 `review_slack_reasons`).
- "근거 부족으로 확인 불가"는 CI agent가 근거를 찾지 못했다는 표시라 담당자의 사유로 세지 않는다(`scripts/apply.py`의 `owner_reason`).
- 검수 결과가 보류인데 지금 상태가 ready-to-done이 아니면(예: Actions 수동 실행) 상태를 바꾸지 않는다(`scripts/apply.py`의 `review_transition`).
  - 그 사실은 검수 comment와 Summary 탭에 남는다.
- 상태 전환이나 Slack 전송에 실패하면 실패 내용이 Summary 탭에 남는다(`scripts/apply.py`의 `apply_issue`, `slack_post`).
  - 상태 전환 실패는 검수 comment에도 남는다.

## 시험 실행 (INNO-29)

시험 실행은 시험용 task(예: INNO-29)를 Actions 수동 실행으로 세 실행 모드에서 실행하고, 단계별 로그와 결과 문서를 확인하는 절차다.

1. Actions 탭에서 Actions 워크플로 `jira-doc`을 고르고 Run workflow를 누른다. issueKey에 `INNO-29`, mode에 `review`를 넣는다.
2. 로그에서 Jira 읽기를 확인한다.
   - 1단계: `[collect] INNO-29: 수집 완료 (comment …)`(`scripts/collect.py`의 `collect_issue`)
   - 2단계: `[precheck] INNO-29 (Task): 예상 산출물 n개, 실패 …`(`scripts/precheck.py`의 `run_issue_dir`)
3. Summary 탭의 "jira-doc 실행 결과" 표에서 Jira 반영을 확인한다.
   - 행은 `| INNO-29 | Task | review | 통과 | … |` 꼴이고, 결과 칸은 통과, 검토 요청, 보류, 실패 중 하나다(`scripts/apply.py`의 `Summary.row`).
4. INNO-29를 열어 아래를 확인한다.
   - TL;DR과 결과 산출물 구역이 바뀌었고, 진행 배경과 예상 산출물은 그대로다. apply.py는 description에서 결과 산출물 구역만 바꾼다(`scripts/jira_api.py`의 `set_section`, `scripts/apply.py`의 `apply_issue`).
   - 첫 줄이 `[ai-doc-agent]`인 검수 comment가 달렸다.
5. 검수 comment는 아래 순서로 구성된다(`scripts/apply.py`의 `review_comment`, `apply_issue`).
   1. 검수 결과 줄
   2. apply.py가 CI agent의 검수 결과를 바꾼 이유와 상태 처리
   3. 달성 요약, 결과 요약, 요청
   4. 검사 표
   5. 담당자 또는 팀장 멘션
   - 검사 표는 "예상 산출물 작성 (T1)"처럼 이름과 ID를 함께 표시하고, 순서는 `scripts/apply.py`의 `CHECK_NAMES` 순서로 고정이다.
   - 결과 칸은 모든 검사 항목에서 ✅ 만족, ❌ 보완 필요, ➖ 해당 없음 중 하나다(`scripts/apply.py`의 `RESULT_KO`).
   - 검수 결과를 바꾼 이유에 나오는 검사도 "완료 기준의 사후 변경 (A2)"처럼 이름과 ID를 함께 표시한다.
6. 결과 산출물 구역은 아래처럼 구성된다(`scripts/apply.py`의 `deliverables_wiki`).
   - 회색 안내 줄 다음에 달성 요약("예상 산출물 3개 중 1개 달성") 한 줄이 나오고, 예상 산출물 순서대로 달성 여부가 붙는다.
   - 미달성 항목에는 "미달성 사유"가 붙고, CI agent가 요청을 작성했으면 "요청"이 붙는다.
   - 예상에 없던 결과는 "초과 달성"에 "추가 사유"와 함께 따로 나온다. 기록에 사유가 없으면 "사유 미기재"로 표시된다.
7. 관찰 모드에서는 문서화 리뷰 comment가 하나 더 달린다(`scripts/apply.py`의 `doc_review_comment`).
   - 둘째 줄은 `문서화 리뷰 결과(관찰 모드): 통과` 또는 `문서화 리뷰 결과(관찰 모드): 보완 필요`다.
   - 그 아래에 문서화 리뷰 항목 5개(진행 배경 충실도 (T4), 예상 산출물 분할 단위 (T5), 진행 기록 comment (T6), sub-task 기록 (T7), 초과 달성·미달성 사유 (T8))의 검사 표와 항목별 피드백이 나온다.
   - 마지막에 담당자에게 보낼 요청 후보(게이트 모드였다면 검수 comment의 요청에 들어갈 문장)와 팀장 멘션이 나온다.
   - Summary 탭에는 문서화 리뷰 결과 제목 줄과 검사 표만 나온다(`scripts/apply.py`의 `doc_review_md`).
8. "검수 뒤 흐름" 절의 표대로 상태가 바뀌거나 유지되고 Slack 알림이 가는지 확인한다.
9. mode를 `digest`, issueKey에 task 키를 넣어 실행하면 TL;DR과 결과 산출물 구역만 갱신되고 comment는 달리지 않는다(F3과 같은 동작).
   - 결과 산출물 구역의 회색 안내 줄 끝에 `(F3 정리: Jira task 화면의 문서 정리 버튼)`이 붙는다(`scripts/apply.py`의 `apply_issue`).
10. mode를 `alerts`, issueKey를 비워 실행하면 주간 점검 메시지가 Slack 채널로 간다. 메시지 본문은 Summary 탭에도 남는다(`scripts/apply.py`의 `slack_weekly`).

## 운영 중 자주 하는 일

- 특정 task를 다시 검수: Actions에서 Run workflow(issueKey, review)를 실행한다. 또는 Jira에서 request 전환을 다시 하면 F1이 실행된다.
- 담당자의 정정 comment를 바로 반영하거나 진행 중에 중간 정리: Jira task 화면의 Automation(번개) 버튼에서 "문서 정리 지금 실행"(F3)을 누른다.
  - TL;DR과 결과 산출물 구역이 함께 다시 작성된다.
- 게이트 모드로 바꾸기: 변수 `JIRA_DOC_GATE`를 `true`로 바꾼다.
  - 문서화 리뷰(T4–T8)가 담당자용 검수 comment에 들어가고, 보완 필요 항목이 있으면 통과가 보류로 바뀐다(`scripts/apply.py`의 `apply_issue`).
  - 팀장용 문서화 리뷰 comment는 달리지 않는다.
- jira-doc 기록 지우기: `scripts/jira.sh prop-del <task 키>`를 실행한다(`scripts/jira.sh`의 `prop-del`).
  - jira-doc 기록은 task마다 저장하는 Jira issue property `ai-doc-agent`다. 마지막 실행 시각, 사람 입력의 해시, 마지막 검수 결과, 보낸 누락 알림 코드가 들어 있다(`scripts/apply.py`의 `apply_issue`).
  - 지우면 task 키 없이 실행한 정리 모드가 입력이 같아도 그 task를 다시 정리하고, 이미 보낸 누락 알림 comment를 다시 달 수 있다(`scripts/collect.py`의 `collect_issue`, `scripts/apply.py`의 `apply_alerts`).
- 주간 점검 메시지를 수요일 전에 확인: Actions에서 Run workflow(mode `alerts`)를 실행한다.
  - 시험 전송이 아니라 실제 Slack 채널로 전송된다(`scripts/apply.py`의 `slack_weekly`).

## 알아 둘 것

- jira-doc이 사용하는 Jira 계정이 팀장 개인 계정이므로 jira-doc의 comment도 팀장 이름으로 달린다.
  - 그래서 jira-doc의 모든 comment는 첫 줄이 `[ai-doc-agent]`다(`scripts/apply.py`의 `agent_comment`).
  - 스크립트는 이 표식과 Automation 작성자를 확인해 사람의 comment와 구분한다(`scripts/jira_api.py`의 `is_agent_comment`).
  - 팀장이 직접 작성하는 comment에는 이 표식을 넣지 않는다.
- 결과 산출물 구역과 TL;DR은 실행 때마다 통째로 다시 작성된다(`scripts/jira_api.py`의 `set_section`, `scripts/apply.py`의 `apply_issue`).
  - 고칠 내용은 "정정:"으로 시작하는 comment로 남긴다.
- 정리 모드의 결과 산출물 구역은 사유가 기록에 없는 미달성, 초과 달성 항목에 "사유 미기재"를 표시하지 않는다. 진행 중이라 아직 사유가 없을 수 있기 때문이다.
  - "사유 미기재" 표시와 초과 달성·미달성 사유 (T8) 판정은 검수 모드에서만 한다(`scripts/apply.py`의 `deliverables_wiki`, `apply_issue`).
- task 키를 지정한 실행은 입력이 지난 실행과 같아도 건너뛰지 않는다. task 키 없이 실행한 정리 모드만 사람의 입력이 지난 실행과 같은 task를 건너뛴다(`scripts/collect.py`의 `collect_issue`, `human_input_hash`).
  - 사람의 입력은 아래와 같다(`scripts/collect.py`의 `HUMAN_SECTIONS`, `human_input_hash`).
    - 사람이 작성하는 description 구역
      - Task: 진행 배경, 예상 산출물
      - Bug: 기본 정보, 문제(As-Is), 개선(To-Be), 첨부 자료(필수)
      - Issue: 이슈 유형, 이슈 내용
    - 사람 comment, sub-task, PR, 상태 전환
- 팀장용 문서화 리뷰 comment의 팀장 멘션은 jira-doc이 팀장 계정으로 작성한다.
  - 추정: Jira는 자기 자신을 멘션한 comment에 알림을 보내지 않을 수 있다.
  - 관찰 모드 동안은 Summary 탭이나 JQL(Jira 검색 조건) `project = INNO AND comment ~ "문서화 리뷰"`로 문서화 리뷰 comment를 모아 확인한다.
- 주간 점검 메시지는 중복을 걸러 내지 않는다(`scripts/apply.py`의 `weekly_lines`, `slack_weekly`).
  - 사유나 활동을 남길 때까지 매주 같은 항목이 다시 올라온다.
- 미연결 PR(제목에 task 키가 없는 병합 PR) 보고는 주간 점검 메시지에서 뺐다(`scripts/apply.py`의 `ALERT_SECTIONS`).
  - AI팀의 GitHub organization을 전사 GitHub organization과 통합한 뒤, 팀장이 이 보고를 다시 넣을지 검토한다.
  - task 키 없이 실행한 정리 모드의 Summary 탭에는 미연결 PR 목록이 남는다(`scripts/apply.py`의 `main`).
- 토큰 네 개는 모두 1년 만료다.
  - `JIRA_API_TOKEN`
  - F1의 PAT: F1이 GitHub에 repository_dispatch를 보낼 때 사용하는 GitHub 토큰이다. Jira Automation 설정에 있다.
  - `ORG_READ_TOKEN`
  - `CLAUDE_CODE_OAUTH_TOKEN`
- 여러 task가 동시에 request 전환되면 task마다 Actions 워크플로 실행이 따로 생겨 동시에 실행된다(Actions 워크플로의 `concurrency`).
  - 같은 task의 실행은 앞 실행이 끝날 때까지 대기한다(`group`이 task 키 기준, `cancel-in-progress: false`).
- Actions 워크플로 실행을 취소해도 4단계(반영)는 실행된다. 4단계의 실행 조건이 `always() && steps.collect.outcome == 'success'`이기 때문이다(Actions 워크플로 4단계의 `if`).
  - 검수 모드에서 3단계가 끝나기 전에 취소하면 verdict.json이 없어 실패 알림 comment가 달린다(예: INNO-29의 comment `57997`).
- 영업일 수는 토요일과 일요일만 뺀 날 수이고, 공휴일은 빼지 않는다(`scripts/precheck.py`의 `business_days_since`).
