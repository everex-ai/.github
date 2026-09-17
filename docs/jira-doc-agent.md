# jira-doc: AI팀 업무 문서화 agent 설치와 운영

설계 문서는 Claude 프로젝트 "Jira 기반 AI팀 업무 프로세스 수립"의 `task-doc-agent/AI팀_업무문서화_agent_도입계획_v0.1.md`다.
이 문서는 `.github` repo에 들어 있는 파일이 무엇이고, 어떻게 설치하고 첫 실행을 확인하는지만 다룬다.

## 파일

| 경로 | 역할 |
|---|---|
| `.github/workflows/jira-doc.yml` | Actions 워크플로. 수집, 검사, Claude, 반영 네 단계 |
| `scripts/jira_api.py` | Jira REST v2 클라이언트와 구역 처리 공통 함수 |
| `scripts/collect.py` | 1단계. Jira와 GitHub에서 입력을 모아 `ctx/`에 저장 |
| `scripts/precheck.py` | 2단계. 기계적 검사(T1, T2, B1, B2, I1, I2, R4, A1, A2)와 문서화 리뷰용 수치(docFacts) |
| `scripts/apply.py` | 4단계. Claude 출력(verdict.json)을 검증하고, 결과 산출물 구역과 검수 comment를 정해진 형식으로 조립해 Jira에 반영. Jira에 쓰는 유일한 곳 |
| `scripts/jira.sh` | 사람이 터미널에서 점검할 때 쓰는 curl 래퍼 |
| `prompts/rules.md` | 문서화 규칙과 검사 항목 정의 |
| `prompts/review.md`, `prompts/digest.md` | 검수 모드, 정리 모드 프롬프트 |
| `prompts/types/{task,bug,issue}.md` | work type별 구역, 출력 형식, 판정 기준 |
| `schemas/verdict.json` | Claude 출력(verdict.json)의 스키마 |

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
| `JIRA_DOC_GATE` | variable | `false` (게이트 모드에서 `true`) |
| `JIRA_PROJECT_KEY` | variable | `INNO` |
| `JIRA_LEAD_ACCOUNT_ID` | variable | 팀장의 Atlassian accountId |
| `JIRA_TLDR_FIELD_ID` | variable | `customfield_10650` |
| `JIRA_SITE_URL` | variable (선택) | `https://<site>.atlassian.net`. 비우면 serverInfo API로 알아낸다 |

3. Jira Automation flow F1(request 전환), F2(stop 전환), F3(수동 정리)이 만들어져 있어야 한다. F1과 F3은 시험이 끝날 때까지 꺼 둔다.

## 첫 실행 (INNO-17로 시험)

1. Actions 탭에서 `jira-doc` 워크플로를 고르고 Run workflow를 누른다. issueKey에 `INNO-17`, mode에 `review`를 넣는다.
2. 실행 로그의 1단계에서 `[collect] INNO-17: 수집 완료`가, 2단계에서 `[precheck] INNO-17 (Task): 예상 산출물 n개`가 보이면 Jira 읽기가 된 것이다.
3. 3단계(Claude)가 끝난 뒤 4단계 요약(Summary 탭)에 `INNO-17 | Task | review | 통과|보완 요청|보류`가 나오면 Jira에 반영된 것이다.
4. INNO-17을 열어 TL;DR 필드와 description의 결과 산출물 구역이 바뀌었고, 진행 배경과 예상 산출물은 그대로이며, `[ai-doc-agent]`로 시작하는 검수 comment가 달렸는지 확인한다. 검수 comment의 검사 표는 "예상 산출물 작성 (T1)"처럼 이름과 ID가 함께 나오고 순서는 T, B, I, R, A 순으로 고정이다. 결과 산출물 구역은 예상 산출물 항목 순서대로 달성 여부가 붙고, 미달성 항목에는 "미달성 사유"가, 예상에 없던 결과는 아래 "초과 달성"에 "추가 사유"와 함께 따로 나온다(기록에 사유가 없으면 "사유 미기재").
   관찰 모드에서는 `문서화 리뷰 (관찰 모드, 팀장 확인용)`으로 시작하는 comment가 하나 더 달리고(T4~T8 표와 항목별 피드백, 결과는 만족/보완 필요로 표시, 보완 요청 후보, 게이트 모드였다면의 판정), 같은 내용이 Summary 탭에도 나온다.
5. 같은 방법으로 mode를 `digest`, issueKey를 비워 실행하면 정리 모드 전체(변경 task의 TL;DR 갱신, 누락 알림, 미연결 PR 목록)를 볼 수 있다.

## 운영 중 자주 쓰는 것

- 특정 task를 다시 검수: Actions에서 Run workflow (issueKey, review). 또는 Jira에서 request 전환을 다시 하면 F1이 실행한다.
- 담당자가 정정 comment를 남긴 뒤 바로 반영: Jira task 화면의 Automation(번개) 버튼에서 "F3 문서 정리 지금 실행".
- 게이트 모드 전환: 변수 `JIRA_DOC_GATE`를 `true`로 바꾸고, Jira 워크플로의 `end` 전환에 Administrator 제한을 건다. agent가 쓰는 계정이 space Administrator여야 한다.
  게이트 모드에서는 문서화 리뷰(T4~T8)가 담당자용 검수 comment에 들어가고, 미달이면 통과가 보완 요청(`rejected` 전환)으로 바뀐다. 팀장용 comment는 달리지 않는다.
- agent 상태 초기화(다시 처음부터 정리하게 하려면): `scripts/jira.sh prop-del INNO-17`.

## 알아 둘 것

- agent가 쓰는 Jira 계정이 팀장 개인 계정이므로 agent의 comment도 팀장 이름으로 달린다. 그래서 agent의 모든 comment는 첫 줄이 `[ai-doc-agent]`이고, 스크립트는 이 표식과 Automation 작성자를 보고 사람의 comment와 구분한다. 팀장이 직접 쓰는 comment에는 이 표식을 넣지 않는다.
- description의 agent 구역과 TL;DR은 실행 때마다 통째로 다시 쓰인다. 고치고 싶은 내용은 "정정:"으로 시작하는 comment로 남긴다.
- 정리 모드는 사람의 입력(사람 구역, 사람 comment, sub-task, PR, 상태 전환)이 지난 실행과 같으면 그 task를 건너뛴다. 억지로 다시 정리하려면 F3을 누르거나 property를 지운다.
- 팀장용 comment의 팀장 멘션은 agent가 팀장 계정으로 쓰므로 알림이 가지 않을 수 있다. 관찰 기간에는 Summary 탭이나 JQL `project = INNO AND comment ~ "문서화 리뷰"`로 모아 본다.
- 토큰 세 개(Jira 토큰, F1의 PAT, ORG_READ_TOKEN)와 Claude 구독 토큰은 모두 1년 만료다.
