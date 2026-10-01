# jira-doc: AI팀 업무 문서화 agent 설치와 운영

설계 문서는 Claude 프로젝트 "Jira 기반 AI팀 업무 프로세스 수립"의 `task-doc-agent/AI팀_업무문서화_agent_도입계획_v0.1.md`다.
이 문서는 `.github` repo에 들어 있는 파일이 무엇이고, 어떻게 설치하고 첫 실행을 확인하는지만 다룬다.

## 파일

| 경로 | 역할 |
|---|---|
| `.github/workflows/jira-doc.yml` | Actions 워크플로. 수집, 검사, Claude, 반영 네 단계. 모드는 review, digest, alerts |
| `scripts/jira_api.py` | Jira REST v2 클라이언트와 구역 처리 공통 함수 |
| `scripts/collect.py` | 1단계. Jira와 GitHub에서 입력을 모아 `ctx/`에 저장 |
| `scripts/precheck.py` | 2단계. 기계적 검사(T1, T2, B1, B2, I1, I2, R4, A1, A2)와 문서화 리뷰용 수치(docFacts) |
| `scripts/apply.py` | 4단계. Claude 출력(verdict.json)을 검증하고, 결과 산출물 구역과 검수 comment를 정해진 형식으로 조립해 Jira에 반영. Jira에 쓰는 유일한 곳. alerts 모드에서는 Jira에 쓰지 않고 Slack으로 주간 점검을 보낸다 |
| `scripts/jira.sh` | 사람이 터미널에서 점검할 때 쓰는 curl 래퍼 |
| `prompts/rules.md` | 문서화 규칙과 검사 항목 정의 |
| `prompts/review.md`, `prompts/digest.md` | 검수 모드, 정리 모드 프롬프트 |
| `prompts/types/{task,bug,issue}.md` | work type별 구역, 출력 형식, 검수 결과 기준 |
| `schemas/verdict.json` | Claude 출력(verdict.json)의 스키마 |
| `config/slack-users.json` | Jira accountId와 Slack 멤버 ID 짝. 주간 점검 메시지와 Slack 알림의 멘션에 사용한다 |

## 설치

1. 이 묶음을 `.github` repo의 기본 브랜치에 그대로 넣는다. 기존 스텁 `jira-doc.yml`은 덮어쓴다.
2. repo의 Settings > Secrets and variables > Actions에 다음을 등록한다.

| 이름 | 종류 | 값 |
|---|---|---|
| `JIRA_CLOUD_ID` | secret | `https://<site>.atlassian.net/_edge/tenant_info`의 cloudId |
| `JIRA_EMAIL` | secret | Jira API 토큰을 발급한 계정 이메일 |
| `JIRA_API_TOKEN` | secret | scoped API 토큰 (`read:jira-work`, `write:jira-work`) |
| `ORG_READ_TOKEN` | secret | Organization 전체 repo에 Pull requests Read 권한이 있는 fine-grained PAT |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret | `claude setup-token`으로 만든 구독 토큰 |
| `SLACK_WEBHOOK_URL` | secret | 주간 점검과 Slack 알림을 받을 Slack 채널의 Incoming Webhook URL |
| `JIRA_DOC_GATE` | variable | `false` (게이트 모드에서 `true`) |
| `JIRA_PROJECT_KEY` | variable | `INNO` |
| `JIRA_LEAD_ACCOUNT_ID` | variable | 팀장의 Atlassian accountId |
| `JIRA_TLDR_FIELD_ID` | variable | `customfield_10650` |
| `JIRA_SITE_URL` | variable (선택) | `https://<site>.atlassian.net`. 비우면 serverInfo API로 알아낸다 |

3. Jira Automation flow F1(request 전환), F2(stop 전환), F3(수동 정리)이 만들어져 있어야 한다. F1과 F3은 시험이 끝날 때까지 꺼 둔다.
4. "검수 뒤 흐름"을 사용하려면 아래 세 가지를 준비한다.
   - F1을 켠다. 담당자가 request 전환으로 task를 ready-to-done에 두면 F1이 검수를 실행한다.
   - Jira 워크플로에 ready-to-done에서 in-progress(Jira 상태 이름 In Progress)로 가는 전환이 있어야 하고, agent가 사용하는 Jira 계정이 그 전환을 실행할 수 있어야 한다. 검수 결과가 보류이면 apply.py가 이 전환을 실행한다(`scripts/apply.py`의 `review_transition`, `scripts/jira_api.py`의 `transition_to`).
   - `config/slack-users.json`에 담당자들과 팀장의 Jira accountId와 Slack 멤버 ID를 넣는다. 없는 사람은 멘션되지 않고, 담당자는 Jira 표시 이름으로, 팀장은 "팀장"으로 나간다(`scripts/apply.py`의 `slack_mention`, `apply_issue`).

## 실행 모드

| 모드 | 언제 | 하는 일 | Claude |
|---|---|---|---|
| review | Jira F1(request 전환), Actions 수동 실행 | 결과 산출물 구역, TL;DR, 검수 comment, 문서화 리뷰 | 사용 |
| digest | Jira F3(번개 버튼), Actions 수동 실행 | 그 task의 TL;DR과 결과 산출물 구역을 지금까지의 기록으로 다시 씀. comment와 검수 결과 없음 | 사용 |
| alerts | 매주 수요일 09:00 KST cron, Actions 수동 실행 | stop 사유 미기재와 5영업일 무활동을 Slack 한 건으로 보고 | 미사용 |

정리(digest)는 담당자가 필요할 때 F3으로 돌린다. 주기 실행은 alerts뿐이다.

## 검수 뒤 흐름

검수 모드(review)는 검수 결과에 따라 task 상태를 바꾸거나 유지하고, `SLACK_WEBHOOK_URL`의 Slack 채널로 Slack 알림을 보낸다(`scripts/apply.py`의 `apply_issue`, `review_slack_text`). 게이트 모드(`JIRA_DOC_GATE`가 `true`)는 문서화 리뷰를 검수 결과에 넣을지만 정하고, 아래 흐름은 모드와 관계없다. 검수 결과의 조건은 `prompts/rules.md`의 "검수 결과 (검수 모드)" 절과 같고, CI agent(워크플로 3단계(Claude)에서 TL;DR과 verdict.json을 작성하는 agent. 4단계의 apply.py와 다름)가 정한 검수 결과를 apply.py가 아래 규칙으로 다시 확인한다(`scripts/apply.py`의 `recheck_verdict`).

| 검수 결과 | 조건 | 상태 | Slack 알림 |
|---|---|---|---|
| 통과 | 예상 산출물이 모두 달성이고, 필수 검사(`prompts/rules.md` 검사 항목 표에서 실패 시 보류나 검토 요청으로 이어지는 검사)가 모두 만족 | ready-to-done 유지 | 팀장을 멘션해 task 링크와 통과 이유를 보냄. 팀장이 task를 확인한 뒤 done으로 직접 전환한다 |
| 검토 요청 | 미달성 항목마다 담당자가 comment로 남긴 사유가 있음, 또는 팀장 판단이 필요함(예상 산출물이 모호함, ready-to-done 뒤 완료 기준이 바뀜, CI agent 출력 오류, 근거끼리 모순) | ready-to-done 유지 | 팀장을 멘션해 task 링크와 검토 요청 이유를 보냄. 팀장이 task를 확인한 뒤 in-progress 또는 done으로 직접 전환한다 |
| 보류 | 사유 없는 미달성 항목이 있음, 또는 담당자가 고칠 수 있는 누락이 있음(예상 산출물·진행 배경·Bug 현황과 개선·Issue 유형과 내용의 템플릿 누락, stop 사유 comment 없음, 근거 링크 없음, Bug의 To-be 동작 확인 comment 없음) | in-progress로 자동 전환 | 담당자를 멘션해 task 링크와 보류 이유, 요청을 보냄 |

- 보류로 in-progress가 된 task는 담당자가 요청대로 기록하거나 미달성 사유를 comment로 남긴 뒤 다시 request 전환을 한다. 그러면 F1이 다시 검수한다.
- "근거 부족으로 확인 불가"는 CI agent가 근거를 찾지 못했다는 표시라 담당자의 사유로 세지 않는다(`scripts/apply.py`의 `owner_reason`).
- 지금 상태가 ready-to-done이 아니면(예: Actions 수동 실행) 상태를 바꾸지 않고, 그 사실을 검수 comment와 Summary 탭에 남긴다(`scripts/apply.py`의 `review_transition`).
- 상태 전환이나 Slack 전송에 실패하면 실패 내용을 Summary 탭에 남긴다(`scripts/apply.py`의 `apply_issue`, `slack_post`).

## 첫 실행 (INNO-29로 시험)

1. Actions 탭에서 `jira-doc` 워크플로를 고르고 Run workflow를 누른다. issueKey에 `INNO-29`, mode에 `review`를 넣는다.
2. 실행 로그의 1단계에서 `[collect] INNO-29: 수집 완료`가, 2단계에서 `[precheck] INNO-29 (Task): 예상 산출물 n개`가 보이면 Jira 읽기가 된 것이다.
3. 3단계(Claude)가 끝난 뒤 4단계 요약(Summary 탭)에 `INNO-29 | Task | review | 통과|검토 요청|보류`가 나오면 Jira에 반영된 것이다.
4. INNO-29를 열어 TL;DR 필드와 description의 결과 산출물 구역이 바뀌었고, 진행 배경과 예상 산출물은 그대로이며, `[ai-doc-agent]`로 시작하는 검수 comment가 달렸는지 확인한다.
   - 검수 comment는 검수 결과 줄, apply.py가 CI agent의 검수 결과를 바꾼 이유와 상태 처리, 달성 요약, 결과 요약, 요청 다음에 검사 표가 나온다.
   - 검사 표는 "예상 산출물 작성 (T1)"처럼 이름과 ID가 함께 나오고, 순서는 T, B, I, R, A 순으로 고정이다. 결과 칸은 모든 검사 항목에서 만족, 보완 필요, 해당 없음으로 표시된다.
   - apply.py가 검수 결과를 바꾼 이유에 나오는 검사도 "완료 기준의 사후 변경 (A2)"처럼 이름과 ID가 함께 나온다.
   - 결과 산출물 구역은 맨 앞에 달성 요약("예상 산출물 3개 중 1개 달성") 한 줄이 나오고, 예상 산출물 항목 순서대로 달성 여부가 붙는다.
   - 미달성 항목에는 "미달성 사유"와 "요청"이 붙는다. 예상에 없던 결과는 아래 "초과 달성"에 "추가 사유"와 함께 따로 나온다(기록에 사유가 없으면 "사유 미기재").
   - 관찰 모드(`JIRA_DOC_GATE`가 `false`)에서는 `문서화 리뷰 결과(관찰 모드): 통과` 또는 `…: 보완 필요`로 시작하는 comment가 하나 더 달리고, 같은 내용이 Summary 탭에도 나온다. 이 comment에 들어가는 것은 아래와 같다.
     - 문서화 리뷰 결과 한 줄
     - 문서화 리뷰 항목(진행 배경 충실도 (T4)부터 초과 달성·미달성 사유 (T8)까지 5개)의 검사 표와 항목별 피드백
     - 담당자에게 보낼 요청 후보(게이트 모드였다면 검수 comment의 요청에 들어갈 문장)
   - "검수 뒤 흐름"의 표대로 상태가 바뀌거나 유지되고 Slack 알림이 간다. 수동 실행이라 상태가 ready-to-done이 아니면 상태는 바뀌지 않는다.
5. 같은 방법으로 mode를 `digest`, issueKey에 task 키를 넣어 실행하면 TL;DR과 결과 산출물 구역만 갱신되고 comment는 달리지 않는지 확인한다(F3 버튼과 같은 동작). 결과 산출물 구역의 회색 안내 줄 끝에 `(F3 정리: Jira task 화면의 문서 정리 버튼)`이 붙는다.
6. mode를 `alerts`, issueKey를 비워 실행하면 주간 점검이 Slack 채널로 간다. 메시지 본문은 Summary 탭에도 그대로 남는다.

## 운영 중 자주 쓰는 것

- 특정 task를 다시 검수: Actions에서 Run workflow (issueKey, review). 또는 Jira에서 request 전환을 다시 하면 F1이 실행한다.
- 담당자가 정정 comment를 남긴 뒤 바로 반영, 또는 진행 중 중간 정리: Jira task 화면의 Automation(번개) 버튼에서 "F3 문서 정리 지금 실행". TL;DR과 결과 산출물 구역이 함께 다시 쓰인다.
- 게이트 모드 전환: 변수 `JIRA_DOC_GATE`를 `true`로 바꾸고, Jira 워크플로의 `end` 전환(done으로 가는 전환)에 Administrator 제한을 건다.
  - agent가 사용하는 계정이 space Administrator여야 한다.
  - 게이트 모드에서는 문서화 리뷰(T4–T8)가 담당자용 검수 comment에 들어가고, 보완 필요 항목이 있으면 통과가 보류로 바뀐다(in-progress 전환, `scripts/apply.py`의 `apply_issue`).
  - 팀장용 문서화 리뷰 comment는 달리지 않는다.
- agent 상태 초기화(다시 처음부터 정리하게 하려면): `scripts/jira.sh prop-del INNO-26`.
- 주간 점검을 미리 보기: Actions에서 Run workflow (mode `alerts`). 수요일을 기다리지 않아도 된다.

## 알아 둘 것

- agent가 사용하는 Jira 계정이 팀장 개인 계정이므로 agent의 comment도 팀장 이름으로 달린다. 그래서 agent의 모든 comment는 첫 줄이 `[ai-doc-agent]`이고, 스크립트는 이 표식과 Automation 작성자를 보고 사람의 comment와 구분한다. 팀장이 직접 작성하는 comment에는 이 표식을 넣지 않는다.
- description의 agent 구역과 TL;DR은 실행 때마다 통째로 다시 쓰인다. 고치고 싶은 내용은 "정정:"으로 시작하는 comment로 남긴다.
- F3로 정리한 결과 산출물에서는 사유가 기록에 없는 미달성·초과 달성 항목에 "사유 미기재"를 쓰지 않는다(진행 중이라 아직 사유가 없을 수 있으므로). "사유 미기재" 표시와 T8 판정은 검수 때만 한다.
- F3나 task를 지정한 수동 실행은 항상 돈다. task 키 없이 전체 정리를 돌릴 때만, 사람의 입력(사람 구역, 사람 comment, sub-task, PR, 상태 전환)이 지난 실행과 같은 task를 건너뛴다.
- 팀장용 comment의 팀장 멘션은 agent가 팀장 계정으로 쓰므로 알림이 가지 않을 수 있다. 관찰 기간에는 Summary 탭이나 JQL `project = INNO AND comment ~ "문서화 리뷰"`로 모아 본다.
- 주간 점검 메시지와 Slack 알림의 멘션은 `config/slack-users.json`에 Slack 멤버 ID가 있는 사람만 붙는다. 없으면 담당자는 Jira 표시 이름으로, 팀장은 "팀장"으로 나간다(`scripts/apply.py`의 `slack_mention`, `apply_issue`).
- 주간 점검은 중복을 걸러 내지 않는다. 사유를 남기거나 활동을 남길 때까지 매주 같은 항목이 다시 올라온다.
- 미연결 PR(제목에 task 키가 없는 병합 PR) 보고는 주간 점검에서 뺐다. GitHub 조직이 통합된 뒤 다시 검토한다.
- 토큰 세 개(Jira 토큰, F1의 PAT, ORG_READ_TOKEN)와 Claude 구독 토큰은 모두 1년 만료다.
