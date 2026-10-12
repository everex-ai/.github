# jira-doc: AI팀 업무 문서화 agent 설치와 운영

jira-doc(AI팀 업무 문서화 agent)은 Jira task의 기록을 근거로 결과 문서를 작성하고 검수하는 agent다. GitHub Actions 워크플로, 스크립트, CI agent(Actions 워크플로 안에서 실행되는 Claude)가 읽는 지시 파일로 이루어지며, 이 문서는 그 파일, 설치, 실행 모드, 검수 뒤 흐름, 운영 방법을 다룬다.

- jira-doc이 작성해 Jira와 Slack에 남기는 결과 문서는 아래와 같다(`scripts/apply.py`의 `apply_issue`, `apply_alerts`, `slack_weekly`).
  - TL;DR: Jira task의 요약 필드
  - 결과 산출물 구역: Jira description 안의 `h2. 결과 산출물` 구역. 예상 산출물마다 달성 여부와 근거를 적는다.
  - 검수 comment: 검수 결과(통과, 검토 요청, 보류)와 검사 표를 담은 comment
  - 문서화 리뷰 comment: 담당자의 기록 습관을 검사한 문서화 리뷰 결과를 팀장에게 공유하는 comment. 문서화 리뷰 항목은 Task 6개(T4–T9), Bug 3개(B5–B7), Issue 2개(I4–I5)다(`scripts/apply.py`의 `DOC_CHECKS`).
  - 누락 알림 comment: stop 사유 comment가 없거나 영업일 기준 5일 이상 활동이 없는 task의 담당자에게 알리는 comment
  - 실패 알림(Slack): CI agent의 구조화 출력이 없거나, 구조화 출력으로 저장한 파일(verdict.json, 검수 모드의 tldr.wiki와 comment.wiki)이 없거나 verdict.json 형식이 틀려 task를 반영하지 못했을 때 팀장에게 알리는 Slack 메시지. 한 실행의 실패를 한 건으로 모아 보내고 Jira에는 comment를 남기지 않는다(`scripts/apply.py`의 `failure_slack_text`, `main`). 전환 전에는 Jira 실패 알림 comment(첫 문구 "자동 검수 실행 실패:")를 남겼다.
  - 검수 뒤 Slack 알림과 주간 점검 메시지
- 설계 문서는 claude.ai 프로젝트(claude.ai의 Projects 기능) "Jira 기반 AI팀 업무 프로세스 수립"의 `task-doc-agent/AI팀_업무문서화_agent_도입계획_v0.1.md`다.

## 용어

이 문서는 아래 용어를 한 이름으로만 사용한다.

- Actions 워크플로: `.github/workflows/jira-doc.yml`. 아래 단계로 실행된다(Actions 워크플로의 스텝 이름).
  1. 입력 수집
  2. 스크립트 검사. 스텝 이름은 "결정론적 검사 (precheck)"다.
  3. CI agent 문서화. 스텝 이름은 "Claude 문서화"다.
     - 바로 앞의 3-0 단계("3-0. 스키마 준비")가 `schemas/agent-output.json`을 한 줄 JSON으로 만들어 3단계의 `--json-schema` 인자로 넘긴다(`.github/workflows/jira-doc.yml:66-79`, 3단계의 `claude_args`).
     - 바로 뒤의 3-1 단계("3-1. Claude 실행 결과 요약")가 CI agent의 실행 결과를 로그에 남긴다. 자세한 내용은 "알아 둘 것" 절에 있다.
     - 3-2 단계("3-2. 출력 저장")가 `scripts/save_output.py`로 CI agent의 구조화 출력(JSON)을 `out/<task 키>/` 파일로 저장한다(`.github/workflows/jira-doc.yml:124-130`).
     - 3-R 단계("3-R. Claude 문서화 다시 실행", "3-R. 출력 다시 저장")는 검수 모드에서 3-2 단계가 `retry=true`를 출력했을 때 3단계와 3-2 단계를 1회 더 실행한다(`.github/workflows/jira-doc.yml:133-157`, `scripts/save_output.py`의 `main`).
  4. 반영
- Jira 워크플로: Jira task의 상태와 상태 사이 전환의 체계
- CI agent: Actions 워크플로 3단계에서 실행되는 Claude. `ctx/`의 입력을 읽어 TL;DR, 판정 객체, 검수 comment의 결과 요약을 구조화 출력(JSON)으로 낸다. 파일은 작성하지 않는다(3단계 prompt의 "결과는 구조화 출력으로만 낸다. 파일은 작성하지 않는다.", 형식은 `schemas/agent-output.json`).
  - `out/<task 키>/`의 파일(verdict.json, tldr.wiki, comment.wiki)은 3-2 단계의 `scripts/save_output.py`가 구조화 출력으로 만든다. 구조화 출력에 없는 부분은 판정 없음 기본값으로 채운다(`scripts/save_output.py`의 `build_verdict`).
  - 3단계의 `--allowedTools`에는 읽기 규칙 `Read(ctx/**)`, `Read(prompts/**)`, `Read(schemas/**)`만 있어 CI agent는 파일을 작성할 수 없다(`.github/workflows/jira-doc.yml:98`). 구조화 출력 전환 전에 있던 쓰기 규칙 `Edit(out/**)`와 `Edit(/${{ github.workspace }}/out/**)`는 전환 뒤 삭제했다.
  - 구조화 출력 전환 전에는 CI agent가 `out/`에 파일을 작성했다. 절대 경로 규칙을 둔 이유: 2026-10-12 시험 실행 22회(실행 38223203394–38223731012) 중 11회가 Write 권한 거부 3회 뒤 verdict.json 없이 끝났다. 절대 경로 규칙을 추가한 뒤 같은 날 실행 12회(38226434145, 38258447642–38258742976의 10회, 38259344639)는 모두 verdict.json을 남겼다.
    - 근거는 각 실행 로그(`gh run view <실행 ID> --log`)의 3-1 단계 `permission_denials=` 줄과 4단계 `[apply]` 줄이다.
    - 추정: CI agent가 출력 파일을 절대 경로로 작성하려 했고, 상대 경로 규칙이 그 경로를 허용하지 않았다.
  - 승인할 사람이 없는 실행이라 허용 규칙에 없는 도구 호출은 모두 자동으로 거부된다(https://code.claude.com/docs/en/headless.md: "In a `-p` run with no host, these requests are denied either way"). 거부되어도 CI agent는 멈추지 않고 실행을 끝낸다. 예: 실행 38223698527은 Write 거부 3회가 있었는데도 3-1 단계 결과가 `is_error=False`, `num_turns=13`이었다.
- 스크립트 검사: CI agent의 판단 없이 precheck.py가 규칙대로 계산하는 검사. 목록은 "파일" 절에 있다.
- 실행 모드: 아래 세 가지. 괄호 안은 Actions 워크플로 입력 `mode`의 값이다(`workflow_dispatch`의 `inputs`).
  - 검수 모드(review)
  - 정리 모드(digest)
  - 주간 점검 모드(alerts)
- 검사 ID: T1, R4처럼 검사 항목에 붙인 코드. 이름과 검사 내용은 아래 두 곳의 "검사 항목" 표에 있다.
  - 공통 검사(R2, R3, R4, A1): `prompts/rules.md`
  - work type별 검사: `prompts/types/<work type>/review.md`(work type은 task, bug, issue)
  - 앞 글자 T, B, I는 검사 대상 work type(Task, Bug, Issue)이고, R과 A는 여러 work type에 적용하는 검사다.
- 관찰 모드와 게이트 모드: 문서화 리뷰(담당자의 기록 습관 검사. Task T4–T9, Bug B5–B7, Issue I4–I5) 결과를 다루는 두 방식. 변수 `JIRA_DOC_GATE`가 `false`이면 관찰 모드, `true`이면 게이트 모드다(`scripts/apply.py`의 `main`).
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
| `.github/workflows/jira-doc.yml` | Actions 워크플로. 단계(1, 2, 3-0, 3, 3-1, 3-2, 3-R, 4)와 실행 모드 세 가지 |
| `scripts/jira_api.py` | Jira REST API v2 클라이언트(`Jira`)와 description 구역 처리 함수(`find_section`, `set_section`) |
| `scripts/collect.py` | 1단계. Jira와 GitHub에서 입력을 모아 `ctx/`에 저장 |
| `scripts/precheck.py` | 2단계. 스크립트 검사 10개와 문서화 리뷰용 수치(docFacts) 계산 |
| `scripts/save_output.py` | 3-2 단계와 3-R 단계. CI agent의 구조화 출력을 `out/<task 키>/` 파일(verdict.json, tldr.wiki, comment.wiki, 원인을 적은 `_agent_error.txt`)과 `out/_failed.txt`로 저장하고, 검수 모드에서 판정이 없으면 `retry=true`를 출력. 표준 라이브러리만 사용 |
| `scripts/apply.py` | 4단계. verdict.json 검증, 결과 산출물 구역과 comment 조립, Jira 반영, 상태 전환, Slack 알림. 반영하지 못한 task는 Slack 실패 알림 한 건으로 알리고 종료 코드 1로 끝남. 주간 점검 모드에서는 Jira에 기록하지 않고 Slack 메시지만 전송 |
| `scripts/jira.sh` | 사람이 터미널에서 Jira를 조회하거나 고칠 때 사용하는 curl 래퍼 |
| `prompts/rules.md` | 모든 work type과 실행 모드에 공통인 지시: 문서화 규칙, 신뢰 경계, 입력, 작성 규칙, 공통 검사 항목, 문서화 리뷰 공통 규칙, 검수 결과 기준 |
| `prompts/review.md`, `prompts/digest.md` | 검수 모드, 정리 모드의 공통 절차. issue.json(1단계가 `ctx/<task 키>/`에 저장하는 task 정보 파일)의 work type으로 아래 work type별 지시 파일을 가리킨다 |
| `prompts/types/{task,bug,issue}/review.md` | work type별 검수 모드 지시: 구역, 검사 항목, 문서화 리뷰 항목, 출력 형식, 검수 결과 기준 |
| `prompts/types/{task,bug,issue}/digest.md` | work type별 정리 모드 지시: items와 extra(판정 객체(verdict)의 예상 산출물별 결과 목록과 초과 달성 목록), tldr 형식 |
| `prompts/types/task/deliverables.md` | Task의 완료 판단 기준, 예상 산출물 표현('개발', '적용')별 달성 근거, items와 extra 작성 방법. CI agent가 Task의 검수 모드와 정리 모드에서 함께 읽는다 |
| `schemas/agent-output.json` | CI agent의 구조화 출력 스키마(JSON Schema draft-07). 최상위 키는 `tasks`(task별 `issueKey`, 판정 객체 `verdict`, `tldr`, `comment`)와 `failed`(처리하지 못한 task와 이유)다. 판정 객체는 `schemas/verdict.json`에서 `issueKey`, `mode`를 뺀 것과 같다(`tests/jira/test_save_output.py`의 `test_schema_verdict_part_matches_verdict_json`) |
| `schemas/verdict.json` | save_output.py가 저장하는 verdict.json의 스키마 |
| `config/slack-users.json` | `SLACK_USERS_JSON`이 비어 있을 때 apply.py가 읽는 Jira accountId와 Slack 멤버 ID 대응표 파일(`scripts/apply.py`의 `load_slack_users`). Actions 실행에서 사용하는 대응표는 secret `SLACK_USERS_JSON`에 있어 이 파일에는 설명만 둠 |

- 스크립트 검사 10개는 아래와 같다(`scripts/precheck.py`의 `run_issue_dir`, `run_scan`).
  - 예상 산출물 작성 (T1), 진행 배경 작성 (T2)
  - 기본 정보와 문제(As-Is) 작성, 첨부 파일 (B1), 개선(To-Be) 작성 (B2)
  - 이슈 유형 선택 (I1), 이슈 내용 작성 (I2)
  - stop 사유 comment (R4), 완료 기준의 사후 변경 (A2)
  - 영업일 기준 5일 이상 활동 없음 (A1). 주간 점검 모드와, task 키 없이 실행한 정리 모드에서만 계산한다.
  - ready-to-done 영업일 기준 3일 이상 대기 (A3). 상태가 ready-to-done이고 마지막 ready-to-done 진입 뒤 영업일 기준 3일 이상 지난 task를 찾는다. A1과 같은 경우에만 계산하고, 주간 점검 메시지에만 올린다.
    - 정리 모드의 누락 알림 comment에는 넣지 않는다. `end` 전환은 팀장이 하므로 담당자에게 요청할 일이 아니기 때문이다(`scripts/apply.py`의 `apply_alerts`, `LEAD_ALERTS`).
- docFacts는 CI agent가 문서화 리뷰를 판단할 때 인용하는 수치다(`scripts/precheck.py`의 `doc_facts`).
  - 배경 구역(Task 진행 배경, Bug 문제(As-Is), Issue 이슈 내용)의 줄 수와 글자 수
  - 작업 기간(첫 In Progress 전환부터 마지막 request 전환까지), 그 기간의 영업일 수, 사람 comment 수
  - sub-task마다 description 줄 수, 사람 comment 수, PR(pull request) 수

## 설치

설치는 파일 배치, GitHub 설정, Jira Automation, Jira 전환, Slack 멤버 ID의 다섯 단계다.

1. "파일" 절의 파일이 이 repo의 기본 브랜치(main)에 있어야 한다.
   - F1과 F3의 repository_dispatch 실행은 기본 브랜치의 코드를 사용한다(INNO-29의 request 전환으로 생긴 실행 `36821640590`의 브랜치가 main, `gh run list --repo everex-ai/.github --workflow jira-doc.yml`).
   - 다른 브랜치의 코드는 Actions 수동 실행에서 브랜치를 골라 시험한다(`gh workflow run jira-doc.yml --ref <브랜치>`).
2. repo의 Settings > Secrets and variables > Actions에 아래 표의 값을 등록한다.
   - Actions 워크플로가 읽는 secret 7개와 variable 5개가 모두 표에 있다(Actions 워크플로의 `env`).

| 이름 | 종류 | 값 |
|---|---|---|
| `JIRA_CLOUD_ID` | secret | `https://<site>.atlassian.net/_edge/tenant_info`의 cloudId |
| `JIRA_EMAIL` | secret | `JIRA_API_TOKEN`을 발급한 계정 이메일 |
| `JIRA_API_TOKEN` | secret | Jira scoped API 토큰(`read:jira-work`, `write:jira-work`) |
| `ORG_READ_TOKEN` | secret | Organization 전체 repo의 Pull requests 읽기 권한이 있는 GitHub fine-grained PAT(권한과 대상 repo를 골라 발급하는 personal access token). 없으면 PR 수집을 건너뜀(`scripts/collect.py`의 `gh`) |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret | `claude setup-token`으로 발급한 Claude Team plan 토큰. 발급한 사람의 좌석(Team plan에서 구성원 한 명에게 배정된 사용 권한) 사용량 한도를 소모함(https://support.claude.com/en/articles/9266767-what-is-the-team-plan: "Usage limits on Team plans are per-member") |
| `SLACK_WEBHOOK_URL` | secret | 주간 점검 메시지, 검수 뒤 Slack 알림, 실패 알림을 받을 Slack 채널의 Incoming Webhook URL |
| `SLACK_USERS_JSON` | secret | Jira accountId를 키로, Slack 멤버 ID를 값으로 둔 JSON 문자열. 주간 점검 메시지, 검수 뒤 Slack 알림, 실패 알림의 멘션에 사용(`scripts/apply.py`의 `load_slack_users`) |
| `JIRA_DOC_GATE` | variable | `false`(관찰 모드). 게이트 모드에서는 `true` |
| `JIRA_PROJECT_KEY` | variable | `INNO` |
| `JIRA_LEAD_ACCOUNT_ID` | variable | 팀장의 Jira accountId |
| `JIRA_TLDR_FIELD_ID` | variable | `customfield_10650` |
| `JIRA_SITE_URL` | variable (선택) | `https://<site>.atlassian.net`. 비우면 Jira serverInfo API로 알아냄(`scripts/jira_api.py`의 `site_url`) |

3. Jira Automation F1, F2, F3을 만들고 켠다.
   - 시험 중에는 F1과 F3을 꺼 두고 Actions 수동 실행(Actions 워크플로의 `workflow_dispatch`)으로 확인할 수 있다.
4. Jira 워크플로의 전환을 아래처럼 준비한다.
   - ready-to-done에서 In Progress로 가는 전환이 있어야 하고, jira-doc이 사용하는 Jira 계정이 그 전환을 실행할 수 있어야 한다.
     - 검수 결과가 보류이면 apply.py가 이 전환을 실행한다(`scripts/apply.py`의 `review_transition`, `scripts/jira_api.py`의 `transition_to`).
   - `end` 전환에 Administrator 제한을 건다. 통과와 검토 요청 task는 팀장이 확인한 뒤 done으로 직접 전환하기 때문이다("검수 뒤 흐름" 절).
     - apply.py는 `end` 전환을 실행하지 않는다(`scripts/apply.py`의 `review_transition`).
5. 담당자들과 팀장의 Jira accountId와 Slack 멤버 ID 대응표를 secret `SLACK_USERS_JSON`에 등록한다.
   - 값은 `{"<Jira accountId>": "<Slack 멤버 ID>", …}` 꼴의 JSON 문자열이다(`scripts/apply.py`의 `load_slack_users`). 등록 명령: `gh secret set SLACK_USERS_JSON --repo everex-ai/.github < <JSON 파일>`
   - 이 repo는 public이라(https://github.com/everex-ai/.github) 대응표를 `config/slack-users.json`에 넣지 않는다. 파일에는 설명만 있고, secret이 비어 있을 때만 apply.py가 이 파일을 읽는다(`scripts/apply.py`의 `load_slack_users`).
   - 대응표에 없는 사람은 멘션되지 않는다. Slack 메시지에는 담당자의 Jira 표시 이름 또는 "팀장"이 표시된다(`scripts/apply.py`의 `slack_mention`, `apply_issue`).

## 실행 모드

주기 실행은 주간 점검 모드뿐이다(Actions 워크플로의 `schedule`에 cron 하나). 검수 모드와 정리 모드는 Jira Automation이나 Actions 수동 실행으로 시작한다.

| 실행 모드 | 시작 | 하는 일 | CI agent |
|---|---|---|---|
| 검수 모드(review) | F1, Actions 수동 실행(task 키 필수) | TL;DR, 결과 산출물 구역(Task만), 검수 comment, 문서화 리뷰 comment(관찰 모드), 상태 전환과 Slack 알림 | 실행 |
| 정리 모드(digest), task 키 지정 | F3, Actions 수동 실행 | 그 task의 TL;DR과 결과 산출물 구역을 지금까지의 기록으로 다시 작성. comment와 검수 결과 없음 | 실행 |
| 정리 모드(digest), task 키 없음 | Actions 수동 실행 | 최근 3일 안에 사람이 바꾼 In Progress, Backlog task를 정리(실행당 최대 20건). Done, Deleted가 아닌 task 전체에서 누락 항목(R4, A1)을 찾아 누락 알림 comment를 담당자 멘션과 함께 게시 | 실행 |
| 주간 점검 모드(alerts) | 매주 수요일 09:00 KST cron, Actions 수동 실행 | stop 사유 comment가 없는 task(R4), 영업일 기준 5일 이상 활동이 없는 task(A1), ready-to-done에서 영업일 기준 3일 이상 대기 중인 task(A3)를 Slack 메시지 한 건으로 보고. R4, A1은 담당자를, A3는 팀장을 멘션 | 실행하지 않음 |

- 이 표의 근거는 아래와 같다.
  - `scripts/collect.py`의 `main`
  - `scripts/apply.py`의 `apply_issue`, `apply_alerts`, `slack_weekly`
  - Actions 워크플로의 `schedule`과 3단계의 `if`
- 누락 알림 comment는 같은 알림을 한 번만 단다. 보낸 누락 알림 코드(알림 종류와 날짜로 만든 코드. 예: `STALE:2026-09-15`)를 task의 jira-doc 기록(Jira issue property `ai-doc-agent`)에 저장하기 때문이다(`scripts/apply.py`의 `apply_alerts`).

## 검수 뒤 흐름

검수 모드는 검수 결과에 따라 task 상태를 바꾸거나 유지하고, `SLACK_WEBHOOK_URL`의 Slack 채널로 Slack 알림을 보낸다(`scripts/apply.py`의 `apply_issue`, `review_slack_text`).

- 이 흐름은 관찰 모드와 게이트 모드에서 같다. 상태를 정하는 함수가 모드 값을 받지 않는다(`scripts/apply.py`의 `review_transition`).
- 검수 결과의 조건은 `prompts/rules.md`의 "검수 결과 (검수 모드)" 절과 `prompts/types/<work type>/review.md`의 "검수 결과" 절에 있다.

| 검수 결과 | 조건 | 상태 | Slack 알림 |
|---|---|---|---|
| 통과 | 예상 산출물이 모두 달성이고, 필수 검사(검사 항목 표에서 실패 시 보류나 검토 요청으로 이어지는 검사)가 모두 만족 | ready-to-done 유지 | 팀장 멘션. task 링크와 통과 이유. 팀장이 확인한 뒤 done으로 직접 전환 |
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
- 아래 경우에는 결과를 "실패"로 처리한다(`scripts/apply.py`의 `apply_issue`, `validate_verdict`).
  - verdict.json이 없거나 형식이 틀림
  - "CI agent 판정 없음": save_output.py가 채운 판정 없음 기본값이 그대로 남음. 검수 결과 코드값이 `"no_verdict"`이거나 items에 `done`이 null인 항목이 있는 경우다(`scripts/apply.py`의 `NO_VERDICT_KO`, `validate_verdict`). 구조화 출력이 없거나 구조화 출력에 그 task가 없을 때 생긴다. 예상 산출물이 없는 정리 모드 task는 기본값만으로 형식 검사를 통과하므로, `_agent_error.txt`에 내용이 있으면 모드와 관계없이 같은 이유로 실패 처리한다(`apply_issue`).
  - 검수 모드에서 구조화 출력에 TL;DR 본문(`tldr`)이나 검수 comment의 결과 요약(`comment`)이 없거나 비어 있어 tldr.wiki나 comment.wiki가 없음. 전환 전 예: INNO-43 실행 38188818467(2026-10-12)에서 CI agent가 턴 상한(`--max-turns` 40)에 걸려 verdict.json만 쓰고 멈춤(로그의 `"subtype": "error_max_turns"`, 4단계의 "TL;DR 출력 없음")
- 실패한 task의 처리는 아래와 같다(`scripts/apply.py`의 `apply_issue`, `main`).
  - Jira에는 comment를 남기지 않는다(검수 모드 포함). 상태 전환, 검수 Slack 알림, TL;DR 갱신, 결과 산출물 구역 갱신도 하지 않는다. Summary 탭에 "실패" 행만 남긴다.
  - 실패 이유 끝에 `out/<task 키>/_agent_error.txt`의 내용이 ". 원인: …"으로 붙는다.
    - save_output.py는 구조화 출력이 없을 때 실행 결과 파일이 없으면 "실행 결과 파일 없음(Claude 문서화 단계(Actions 워크플로 3단계) 결과: …)"을, 있으면 "Claude 문서화 단계 종료 종류(subtype): …"로 result 메시지의 `subtype`(종료 종류, 예: `error_max_turns`)을 작성한다(`scripts/save_output.py`의 `no_output_reason`).
    - result 메시지의 `is_error`가 true일 때만 `result` 앞 300자를 "실패 문구"로 덧붙인다. 성공한 실행의 `result`는 Jira 기록이 담길 수 있어 옮기지 않는다.
    - 구조화 출력은 있으나 그 task가 `tasks`에 없으면 `failed`의 이유("CI agent가 처리하지 못함: …") 또는 "구조화 출력에 이 Task 결과 없음"을 적는다(`scripts/save_output.py`의 `save_task`). `failed`의 이유는 앞 300자만 옮긴다(`failed_reasons`).
  - 한 실행에서 실패한 task(Jira 오류로 반영하지 못한 task 포함)를 모아 Slack 한 건으로 팀장에게 알린다(`scripts/apply.py`의 `failure_slack_text`, `main`).
    - 첫 줄은 "[jira-doc 실패] 검수 모드(또는 정리 모드) Task n건 처리 실패"와 팀장 멘션이다.
    - task마다 Jira 링크, task 키, 제목, 원인이 한 줄씩 나오고, 마지막 줄에 GitHub Actions 실행 링크가 나온다.
    - Summary 탭에는 Slack 멤버 ID 대신 "팀장(Slack 멘션 없음)"으로 적은 사본을 남긴다(`scripts/apply.py`의 `main`).
  - 실패가 하나라도 있으면 apply.py가 종료 코드 1로 끝나 Actions 실행 상태가 failure가 된다(`scripts/apply.py`의 `main`).
- 검수 모드에서 3-2 단계가 판정 없음, items의 `done` null, 빈 `tldr`, 빈 `comment` 중 하나를 확인하면 `retry=true`를 출력해 3-R 단계가 3단계를 1회 더 실행한다(`scripts/save_output.py`의 `main`, `missing_parts`). 정리 모드는 다시 실행하지 않는다.
- 정리 모드에서 tldr.wiki가 없으면 실패로 처리하지 않고 Summary 탭에 "TL;DR 출력 없음"만 남긴다.
- 보류로 In Progress가 된 task는 담당자가 요청대로 기록하거나 미달성 사유를 comment로 남긴 뒤 다시 request 전환을 한다. 그러면 F1이 다시 검수한다.
- 사유 없이 stop했다가 다시 진행한 task는 stop 사유 comment (R4)가 보완 필요가 되어 검수 결과가 보류가 된다(`scripts/precheck.py`의 `check_r4`, `scripts/apply.py`의 `apply_issue`).
  - 늦은 사유: stop 뒤 다음 상태 변경 이후에 남긴, "stop 사유:"로 시작하는 사람 comment. precheck.py는 이 comment를 stop 뒤 언제 남겼든 그 stop의 사유로 센다(`scripts/precheck.py`의 `stop_events`).
  - "stop 사유:" 머리말을 요구하는 이유는 다시 진행한 뒤에 남긴 진행 상황 comment를 stop 사유로 세지 않기 위해서다. precheck.py는 다음 상태 변경 뒤의 사람 comment 중 머리말이 있는 comment만 확인한다(`scripts/precheck.py`의 `stop_events`, `prompts/rules.md`의 "검사 항목" 표 아래 R4 사유 인정 규칙).
  - 보류의 요청에는 "stop(<날짜>)의 사유를 'stop 사유:'로 시작하는 comment로 남긴 뒤 다시 request 전환(담당자가 완료를 요청해 task를 ready-to-done 상태로 보내는 Jira 전환) 필요"가 들어간다(`scripts/apply.py`의 `apply_issue`).
  - 늦은 사유를 남기고 다시 request 전환하면 R4는 만족이 된다. 검수 comment의 검사 표에는 R4 내용 칸에 "늦게 기록", stop 날짜, 늦은 사유 comment 링크가 표시된다(`scripts/precheck.py`의 `check_r4`).
  - 이때 검수 결과는 R4가 만족인 상태에서 위 표의 조건대로 정해진다(`scripts/apply.py`의 `apply_issue`, `recheck_verdict`).
  - 통과와 검토 요청의 Slack 알림에도 R4의 "늦게 기록" 줄이 들어가 팀장이 확인할 수 있다(`scripts/apply.py`의 `review_slack_reasons`).
- "근거 부족으로 확인 불가"는 CI agent가 근거를 찾지 못했다는 표시라 담당자의 사유로 세지 않는다(`scripts/apply.py`의 `owner_reason`).
  - 초과 달성·미달성 사유 (T8) 판정도 같은 기준으로 센다(`scripts/apply.py`의 `check_t8`).
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
   - 실패 행이 있으면 Slack 실패 알림 한 건이 가고 실행 상태가 failure가 된다. 마지막 행 `| - | - | review | 전송 완료 | 실패 알림 Task n건 |`이 실패 알림 전송 결과다(`scripts/apply.py`의 `main`).
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
   - 그 아래에 문서화 리뷰 항목의 검사 표와 항목별 피드백이 나온다. 항목은 work type마다 아래와 같다(`scripts/apply.py`의 `DOC_CHECKS`).
     - Task 6개: 진행 배경 충실도 (T4), 예상 산출물 분할 단위 (T5), 진행 기록 comment (T6), sub-task 기록 (T7), 초과 달성·미달성 사유 (T8), 진행 배경부터 결과 산출물까지의 연결 (T9)
     - Bug 3개: 문제(As-Is) 재현 정보 (B5), 원인 분석 기록 (B6), 해결 확인 기록 (B7)
     - Issue 2개: 이슈 내용 충실도 (I4), 논의와 결정 기록 (I5)
   - 마지막에 담당자에게 보낼 요청 후보(게이트 모드였다면 검수 comment의 요청에 들어갈 문장)와 팀장 멘션이 나온다.
   - Summary 탭에는 문서화 리뷰 결과 제목 줄과 검사 표만 나온다(`scripts/apply.py`의 `doc_review_md`).
8. "검수 뒤 흐름" 절의 표대로 상태가 바뀌거나 유지되고 Slack 알림이 가는지 확인한다.
9. mode를 `digest`, issueKey에 task 키를 넣어 실행하면 TL;DR과 결과 산출물 구역만 갱신되고 comment는 달리지 않는다(F3과 같은 동작).
   - 결과 산출물 구역의 회색 안내 줄 끝에 `(F3 정리: Jira task 화면의 문서 정리 버튼)`이 붙는다(`scripts/apply.py`의 `apply_issue`).
10. mode를 `alerts`, issueKey를 비워 실행하면 주간 점검 메시지가 Slack 채널로 간다.
    - 메시지 사본은 Summary 탭에도 남고, 사본에는 멘션 대신 담당자의 Jira 표시 이름이 들어간다(`scripts/apply.py`의 `slack_weekly`).

## 운영 중 자주 하는 일

- 특정 task를 다시 검수: Actions에서 Run workflow(issueKey, review)를 실행한다. 또는 Jira에서 request 전환을 다시 하면 F1이 실행된다.
- 담당자의 정정 comment를 바로 반영하거나 진행 중에 중간 정리: Jira task 화면의 Automation(번개) 버튼에서 "문서 정리 지금 실행"(F3)을 누른다.
  - TL;DR과 결과 산출물 구역이 함께 다시 작성된다.
- 게이트 모드로 바꾸기: 변수 `JIRA_DOC_GATE`를 `true`로 바꾼다.
  - 문서화 리뷰(Task T4–T9, Bug B5–B7, Issue I4–I5)가 담당자용 검수 comment에 들어가고, 보완 필요 항목이 있으면 통과가 보류로 바뀐다(`scripts/apply.py`의 `apply_issue`).
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
- 주간 점검 메시지의 사본은 Summary 탭에도 남는다. apply.py는 이 사본에 Slack 멘션 대신 담당자의 Jira 표시 이름을 적고, A3 항목의 팀장 멘션은 "팀장"으로만 적는다(`scripts/apply.py`의 `slack_weekly`, `weekly_lines`).
  - 이유: public repo의 Actions 실행 화면은 GitHub에 로그인한 누구나 볼 수 있어, 멘션(`<@U…>`)을 그대로 남기면 Slack 멤버 ID가 드러난다(https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs: "You must be logged in to a GitHub account to view workflow run information, including for public repositories.").
- 주간 점검 메시지는 중복을 걸러 내지 않는다(`scripts/apply.py`의 `weekly_lines`, `slack_weekly`).
  - 사유나 활동을 남길 때까지 매주 같은 항목이 다시 올라온다. A3 항목은 팀장이 `end` 전환할 때까지 다시 올라온다.
- 미연결 PR(제목에 task 키가 없는 병합 PR) 보고는 주간 점검 메시지에서 뺐다(`scripts/apply.py`의 `ALERT_SECTIONS`).
  - AI팀의 GitHub organization을 전사 GitHub organization과 통합한 뒤, 팀장이 이 보고를 다시 넣을지 검토한다.
  - task 키 없이 실행한 정리 모드의 Summary 탭에는 미연결 PR 목록이 남는다(`scripts/apply.py`의 `main`).
- 토큰 네 개는 모두 1년 만료다.
  - `JIRA_API_TOKEN`
  - F1의 PAT: F1이 GitHub에 repository_dispatch를 보낼 때 사용하는 GitHub 토큰이다. Jira Automation 설정에 있다.
  - `ORG_READ_TOKEN`
  - `CLAUDE_CODE_OAUTH_TOKEN`
    - 2026-10-08에 Team plan 좌석으로 다시 발급했다(`gh secret list -R everex-ai/.github`의 갱신 날짜 2026-10-08). 만료는 2027-10-08이다(https://code.claude.com/docs/en/authentication.md "Generate a long-lived token": "one-year OAuth token").
    - 추정: 발급한 사람이 Team plan에서 빠지거나 좌석이 회수되면 만료 전이라도 동작하지 않는다. 그때는 팀장이 다른 좌석으로 다시 발급해, 등록된 repo의 secret을 모두 교체한다(`docs/pr-review-agent.md`의 "대상 repo에 설치" 절 2번).
- 여러 task가 동시에 request 전환되면 task마다 Actions 워크플로 실행이 따로 생겨 동시에 실행된다(Actions 워크플로의 `concurrency`).
  - 같은 task의 실행은 앞 실행이 끝날 때까지 대기한다(`group`이 task 키 기준, `cancel-in-progress: false`).
- 3-2 단계(save_output.py)는 표준 출력에 task마다 "[save] <task 키>: verdict=<값> 없음=<빠진 부분>" 한 줄만 남긴다. tldr, comment, 실행 결과의 `result` 같은 Jira 기록 내용은 출력하지 않는다(`scripts/save_output.py`의 `save_task`).
- 3-1 단계(Claude 실행 결과 요약)는 CI agent 실행 결과 파일(claude-code-action의 `execution_file` 출력)에서 아래만 로그에 남긴다(Actions 워크플로의 "3-1. Claude 실행 결과 요약" 단계).
  - `is_error`, `num_turns`(턴 수), `total_cost_usd`(claude CLI가 계산한 API 환산 비용)
  - `permission_denials`: 권한이 거부된 도구 이름. Write와 Edit는 거부된 파일 경로도 남긴다
  - `result`: 실패했을 때만 남긴다. 예: 2026-10-12 실행 38221355521의 "Failed to authenticate: OAuth token revoked."
  - 3단계의 전체 출력은 Jira 기록이 담겨 있어 public 로그에 남기지 않는다(3단계의 `show_full_output`은 기본값 false). 성공한 실행의 `result`도 같은 이유로 남기지 않는다.
- claude-code-action은 워크플로 파일이 기본 브랜치(main)의 것과 같을 때만 3단계를 실행한다.
  - 원문: "The workflow file must exist and have identical content to the version on the repository's default branch."(2026-10-12 실행 38221063946 로그)
  - 그래서 다른 브랜치로 수동 실행해 시험할 때는 그 브랜치의 `.github/workflows/jira-doc.yml`이 main과 같아야 한다. 다르면 3단계가 건너뛰어진다.
  - 전환 전에는 4단계가 verdict.json 없음 실패 알림 comment를 남겼다(실행 38221063946: 3단계 로그 "Exiting due to workflow validation skip", 4단계 로그 `[apply] INNO-47: 실패 … verdict.json 없음`). 지금은 3-2 단계가 판정 없음 기본값과 `_agent_error.txt`(3단계 결과 값 포함)를 만들고, 4단계가 "CI agent 판정 없음. 원인: …"으로 실패 처리해 Slack 실패 알림을 보낸다(`scripts/save_output.py`의 `no_output_reason`, `scripts/apply.py`의 `apply_issue`).
- 4단계가 실패를 기록하면 Actions 실행 상태가 failure가 된다. apply.py가 실패를 기록한 뒤 종료 코드 1로 끝나기 때문이다(`scripts/apply.py`의 `main`).
  - 구조화 출력 전환 전에는 종료 코드 0으로 끝나 실행 상태가 success로 보였다. 예: 실행 38221063946은 4단계가 실패를 기록했는데 실행 상태가 success였다(`gh run list -R everex-ai/.github -w jira-doc.yml`).
  - 실패 원인은 Slack 실패 알림, 4단계 로그의 `[apply]` 줄, Summary 탭의 결과 칸으로 확인한다.
- Actions 워크플로 실행을 취소해도 4단계(반영)는 실행된다. 4단계의 실행 조건이 `always() && steps.collect.outcome == 'success'`이기 때문이다(Actions 워크플로 4단계의 `if`).
  - 3-2 단계도 같은 조건(`always()`)이라 실행된다(`.github/workflows/jira-doc.yml:126`). 검수 모드에서 3단계가 끝나기 전에 취소하면 구조화 출력이 없어 4단계가 "CI agent 판정 없음"으로 실패 처리하고 Slack 실패 알림을 보낸다(`scripts/apply.py`의 `apply_issue`, `main`).
  - 전환 전에는 verdict.json이 없어 실패 알림 comment가 달렸다(예: INNO-29의 comment `57997`).
- 영업일 수는 토요일과 일요일만 뺀 날 수이고, 공휴일은 빼지 않는다(`scripts/precheck.py`의 `business_days_since`).
