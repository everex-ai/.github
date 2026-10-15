# 업무 효율 측정: 문서화 Agent

이 문서는 문서화 Agent(jira-doc)를 도입한 뒤 업무 효율이 얼마나 늘었는지 수치로 확인하는 방법이다. 지표의 정의와 계산 방법, 데이터 위치, 도입 이전 현황, 집계 스크립트, 측정 일정을 다룬다. 코드 리뷰 Agent(pr-review)의 측정 방법은 `docs/pr-review-agent/metrics.md`에 있다.

- 업무 효율은 "줄어든 리뷰 시간" 하나가 아니라 아래 업무 효율 지표 세 가지로 측정한다.
  1. 리뷰 범위: 리뷰를 받은 Jira task의 비율
  2. 발견·조치: 문서화 Agent가 ❌ 보완 필요로 판정한 검사 수와, 그중 done 전환 전에 ✅ 만족으로 바뀐 비율
  3. 피드백 대기 시간: request 전환(담당자가 task의 완료를 요청하는 Jira 전환)부터 문서화 Agent의 첫 검수 comment까지 걸린 시간
- 세 지표 중 리뷰 범위를 주 지표로 둔다.
- 환산 리뷰 시간(J2)은 2026-10에 지표에서 뺐다.
  - J2에는 표본 실측이 필요하다.
    - 표본 실측: 팀장이 검수 comment를 읽기 전에 직접 리뷰해 시간을 재는 측정
  - 표본 실측은 하지 않는다(2026-10-14부터).
    - 이유: 팀장의 리뷰 병목을 없애려는 도입 목적과 맞지 않는다.
  - J2라는 ID는 다른 지표에 다시 사용하지 않는다.
  - Notion 시절에는 팀장이 확인할 문서가 task 수에 비례해 늘어 follow-up 자체가 어려웠다(도입 계획 페이지의 "진행 배경").
    - 도입 계획 페이지는 'Task 단위'의 페이지 "[AI팀 운영] Jira 문서화 Agent 도입"이다("용어" 절).
  - 추정: 그래서 업무 효율 증가는 "같은 리뷰를 더 빨리 함"보다 "하지 못하던 리뷰를 모든 task에 함"으로 더 크게 나타난다.
- 수치는 팀 합계로만 집계하고, 담당자 개인 평가에 사용하지 않는다("해석 원칙" 절).

## 용어

이 문서는 아래 용어를 한 이름으로만 사용한다.

- 리뷰: 기준에 따라 task 문서를 확인하고 판정하는 일. 사람과 문서화 Agent가 모두 한다.
  - 기준은 `prompts/rules.md`의 "검사 항목" 표다.
- 검사 ID: T1, R4처럼 검사 항목에 붙인 코드. 이름과 내용은 `prompts/rules.md`의 "검사 항목" 표에 있다.
- 검수 comment: 문서화 Agent가 리뷰 결과를 Jira task에 게시하는 comment. 첫 줄이 `[ai-doc-agent]`이고 `검수 결과: *통과*` 꼴의 줄이 있다(`scripts/apply.py`의 `agent_comment`, `review_comment`).
- request 전환: 담당자가 완료를 요청해 task를 ready-to-done(완료 요청 뒤 확인을 기다리는 Jira 상태)으로 보내는 Jira 전환(`docs/jira-doc-agent/README.md`의 "용어" 절)
- 판정 값: 문서화 Agent의 판정은 통과, 검토 요청, 보류 세 가지다(`docs/jira-doc-agent/README.md`의 "검수 결과와 ticket 상태 전환" 절).
  - 세 판정 모두 task를 ready-to-done에 남긴다.
    - 팀장이 확인한 뒤 Done이나 In Progress로 직접 전환한다.
  - 2026-10 알림 체계 재구성 전에는 apply.py가 보류를 되돌렸다.
    - 근거: commit `b9a7e28`의 `scripts/apply.py`의 `review_transition`
- 팀장: 검수 comment를 확인하고 task를 직접 전환하는 사람
  - 근거: `docs/jira-doc-agent/README.md`의 "검수 결과와 ticket 상태 전환" 절
  - 이 문서에서는 집계 스크립트 실행과 보고서 작성도 맡는다.
- stop 사유: 진행 중인 task를 Backlog로 보낼 때(stop 전환) 담당자가 남기는 멈춘 이유 comment(`prompts/rules.md`의 "팀의 문서화 규칙 네 가지" 4번)
  - Notion 시절에는 Holding 상태가 stop에 해당하고, 사유를 comment에 남기는 규칙이 같았다(도입 계획 페이지의 "진행 배경").
- Notion 시절: Jira를 도입하기 전, 업무 진행사항을 Notion의 업무 공유 체계(Kanban)에 기록하던 기간
- 'Task 단위': 업무 공유 체계(Kanban)의 task 데이터베이스. Notion 페이지 경로는 "기술혁신실 > 기술혁신실 Kanban > Task 단위"다.
- 도입 계획 페이지: 'Task 단위'의 페이지 "[AI팀 운영] Jira 문서화 Agent 도입". Notion 시절 문서화 운영 방식, 문서화 Agent 도입 기준, 이전 AS-IS 분석이 적혀 있다.
- 이전 AS-IS 분석: 도입 계획 페이지의 "Agent 도입 기준 설정"에 있는 분석. 'Task 단위'의 최근 6개월 Done 비중과 "Done 문서 중 완성률"을 집계했다.
- 도입 이전 현황: Notion 시절 'Task 단위'의 페이지에 Jira 검사와 같은 판정 규칙을 적용해 구한 값(N1–N9). "도입 이전 현황" 절에 정의가 있다.
- 기준값: 조건 지표가 넘어야 하는 하한
- 비교 대상: 지표의 도입 후 값과 비교하는 값. 아래 세 가지 중 하나다.
  - 도입 이전 현황
  - Agent 첫 리뷰: 같은 task에 대한 문서화 Agent의 첫 검수 comment
  - 기준값
  - 비교할 값이 없으면 "없음"으로 적는다.
- GitHub Actions: GitHub에서 워크플로를 실행하는 기능. 문서화 Agent는 GitHub Actions 워크플로로 실행된다(`.github/workflows/jira-doc.yml`).
- 실행 모드: 문서화 Agent 워크플로의 실행 방식(`docs/jira-doc-agent/README.md`의 "실행 모드" 절). 이 문서에는 아래 세 가지가 나온다.
  - 검수 모드: request 전환 때 검수 comment를 게시하는 모드
  - 주간 점검 모드: 매주 수요일에 stop 사유 comment가 없는 task와 영업일 기준 5일 이상 활동이 없는 task를 Slack 메시지 한 건으로 보고하는 모드
  - 보고 모드: 매월 1일에 정기 보고 메시지를 팀장 채널로 보내는 모드
    - 팀장 채널: 팀장만 받는 Slack 채널(`docs/jira-doc-agent/alerts.md`의 "용어" 절)
- 관찰 모드와 게이트 모드: 변수 `JIRA_DOC_GATE`가 `false`이면 관찰 모드, `true`이면 게이트 모드다(`docs/jira-doc-agent/README.md`의 "용어" 절).
  - 관찰 모드: Task 기록의 품질 검사 5개(T4–T8)의 결과를 판정에 반영하지 않고, 팀장에게 공유하는 문서화 리뷰 comment로만 게시한다.
  - 게이트 모드: 그 결과를 검수 comment에 넣고, ❌ 보완 필요로 판정한 검사가 있으면 통과를 보류로 바꾼다.
- ready-to-done 체류 시간: task가 ready-to-done 상태에 머문 시간

## 지표의 분류

지표는 업무 효율 지표, 품질 지표, 조건 지표 세 분류로 나뉜다. 보고서 첫머리에는 업무 효율 지표 세 가지와 조건 지표를 둔다.

- 업무 효율 지표: 이 문서 첫머리의 세 가지 지표
- 품질 지표: 업무 효율 지표가 늘어난 것이 문서 품질 향상으로 이어졌는지 확인하는 지표다.
  - 문서 품질 추이: 검사 항목을 만족한 비율의 변화
  - 진행 추적: 아래 세 값으로 진행 과정이 기록되고 있는지 확인한다.
    - stop 사유가 기록된 비율
    - 영업일 기준 5일 이상 활동이 없는 task 수
    - ready-to-done 체류 시간
- 조건 지표: 업무 효율 지표를 믿을 수 있는지 판단하는 지표다.
  - 판정 일치율: 문서화 Agent 판정과 팀장이 직접 한 전환이 같은 방향인 비율
  - 오류 신호: 문서화 Agent가 작성한 내용을 담당자가 정정한 비율과 실행이 실패한 비율

## 지표

문서화 Agent의 지표는 J1, J3–J8 일곱 개다. 그중 J1 리뷰 범위가 주 지표다.
도입 이전 현황과 비교하는 지표는 J1, J5, J6(R4)이다.

| ID | 분류 | 지표 | 계산 방법 | 데이터 | 비교 대상 |
|---|---|---|---|---|---|
| J1 | 업무 효율 | 리뷰 범위 | 기간 안에 request 전환된 task 중, 그 뒤 검수 comment가 달린 task의 비율 | Jira 변경 이력(changelog), 검수 comment | 도입 이전 현황(N1 리뷰 흔적 비율) |
| J3 | 업무 효율 | 발견·조치 | 첫 검수 comment에서 ❌ 보완 필요인 검사 수(task당), 그중 마지막 검수 comment에서 ✅ 만족으로 바뀐 비율(조치율) | 검수 comment의 검사 표 | Agent 첫 리뷰 |
| J4 | 업무 효율 | 피드백 대기 시간 | request 전환마다, 다음 request 전환 전의 첫 검수 comment까지(분). 그 사이에 검수 comment가 없는 request 전환은 뺀다 | changelog와 comment의 시각 | 없음. 'Task 단위'에는 상태 변경 이력이 없어 도입 후 값만 적는다("한계" 절) |
| J5 | 품질 | 문서 품질 추이 | 검사 ID별로 마지막 검수 comment에서 만족한 비율. 주별 Agent 첫 리뷰의 통과율도 함께 적는다 | 검수 comment | 도입 이전 현황(N2–N7) |
| J6 | 품질 | 진행 추적 | stop 사유 comment (R4) 만족 비율, 영업일 기준 5일 이상 활동 없음 (A1) task 수, ready-to-done 체류 시간(시간) | 검수 comment의 R4 행, 주간 점검 메시지, changelog | R4 만족 비율만 도입 이전 현황(N8). A1과 ready-to-done 체류 시간은 없음 |
| J7 | 조건 | 판정 일치율 | 검수 comment의 판정 뒤 팀장이 직접 한 첫 전환이 판정과 같은 방향인 비율. 통과→Done, 보류→In Progress가 일치이고, 검토 요청은 뺀다 | changelog의 전환과 작성자, 검수 comment | 기준값 80% |
| J8 | 조건 | 오류 신호 | "정정:"으로 시작하는 comment가 달린 task 비율, 실행(검수 comment와 실패 알림 comment) 중 실패 알림 비율 | Jira comment | 없음 |

- R4와 A1은 스크립트로 계산하는 검사이고, A1은 주간 점검 모드에서만 계산된다(`docs/jira-doc-agent/README.md`의 "스크립트가 계산하는 값" 절).
- J3의 조치율에서 검수 comment가 하나뿐인 task는 분모에만 들어간다.
  - 이유: 문서화 Agent가 다시 리뷰하지 않아 고쳐졌는지 확인할 수 없기 때문이다.
- J5는 마지막 검수 comment의 결과로 도입 이전 현황과 비교한다.
  - 이유: 도입 이전 현황은 Done 이후 집계 시점의 페이지 상태로 구한 값이라, 첫 리뷰보다 마지막 리뷰와 조건이 가깝다.
- J6의 A1 task 수는 주간 점검 메시지(Slack, GitHub Actions의 Summary 탭)에서 확인한다(`scripts/apply.py`의 `slack_weekly`).
- J7의 기준값 80%는 도입 계획 페이지의 도입 기준과 같다.
  - 도입 기준: 문서화 Agent가 리뷰한 task 20건 중 팀장이 판정을 뒤집은 건수가 4건 이하(일치율 80% 이상)이면 도입을 확정하고 게이트 모드로 전환한다(도입 계획 페이지의 "Agent 도입 기준 설정").
- J7의 세부 규칙은 `docs/jira-doc-agent/alerts.md`의 "판정 일치율" 절에 있다.
- J8의 "정정:" comment는 문서화 Agent가 작성한 결과 산출물 구역(Jira description 안의 `h2. 결과 산출물` 구역)과 TL;DR(Jira task의 요약 필드)을 고칠 내용을 담당자가 남기는 comment다(`docs/jira-doc-agent/README.md`의 "결과 문서를 고치는 방법" 절).

## 도입 이전 현황: 업무 공유 체계(Kanban)의 'Task 단위'

도입 이전 현황은 'Task 단위'의 페이지에 Jira 검사와 같은 판정 규칙을 적용해 구한다. Notion 템플릿 구역이 Jira 검사 항목과 대응하고(아래 대응표), 업무 체계를 Notion에서 Jira로 옮기는 중이라 Notion 시절의 문서 기록은 'Task 단위'에 있기 때문이다(도입 계획 페이지의 "진행 배경").

### 데이터 원천

- 데이터베이스는 'Task 단위'다.
  - Notion URL과 data source ID는 이 문서에 적지 않고, 집계 스크립트의 인자로 받는다.
- 사용하는 속성은 아래와 같다(2026-10-02 Notion API로 데이터베이스 스키마를 조회해 확인).
  - `Name`: 제목
  - `상태`: Not started, In progress, Holding, Done 중 하나
  - `담당자`
  - `최초 생성 일자`
- 'Task 단위'에는 상태 변경 이력을 담는 속성이 없다(같은 스키마 조회).
- 페이지는 템플릿 세 개(Engineering Template, Research Template, "[] Engineering Template (James)")로 만들고, 구역이 아래처럼 Jira 검사와 대응한다(2026-10-02 Notion API로 템플릿 페이지 세 개를 조회해 확인).
  - "[] Engineering Template (James)"의 구역 이름은 Engineering Template과 같다.
  - 템플릿 페이지 세 개는 집계에서 뺀다.

| Notion 구역(Engineering Template) | Notion 구역(Research Template) | 대응하는 Jira 검사 |
|---|---|---|
| 진행 배경 | 본 task 목적 | 진행 배경 작성 (T2) |
| Task 완료 기준 | Task 완료 기준 | 예상 산출물 작성 (T1) |
| 결과 산출물 | 결과(완료 기준 달성 여부) | 예상 산출물과 결과 산출물의 1:1 대응 (T3), 초과 달성·미달성 사유 (T8) |
| 관련 링크, 참고 자료(reference) | 참고 자료 | 근거 링크 |

- 결과 구역의 안내 문구는 "Task 완료 기준과 1:1 대응해야 함, 완료는 ✅, 미완료는 사유와 함께 ❌ 표기"다(템플릿 세 개 공통).

### 대상 페이지와 기간

- 대상 페이지는 최초 생성 일자가 2026-03-01–2026-08-31(한국 시간)이고, 팀장을 뺀 팀원이 담당한 페이지다.
- 기간을 이렇게 정한 이유는 두 가지다.
  - Jira에서 검수 모드를 확인한 날(2026-09-17, 도입 계획 페이지의 "검증 결과")보다 앞서서 Jira 전환 기간과 겹치지 않는다.
  - 이전 AS-IS 분석과 같은 6개월 단위다.
- 팀장이 담당한 페이지를 빼는 이유는, 문서화 Agent가 대신하는 리뷰가 팀장이 팀원의 문서를 리뷰하던 일이기 때문이다(도입 계획 페이지의 "진행 배경").

### 현황 지표

현황 지표는 N1–N9 아홉 개이고, 대상 페이지마다 같은 판정 규칙으로 구한다. N2–N7은 J5 문서 품질 추이, N1은 J1, N8은 J6와 비교한다. N9는 비교하는 지표 없이 참고 값으로 적는다.

| 현황 지표 | 계산 대상 | 판정 규칙 | 비교하는 지표 |
|---|---|---|---|
| N1 리뷰 흔적 비율 | Done인 대상 페이지 | 담당자가 아닌 사람의 comment가 1개 이상 있는 페이지의 비율 | J1 리뷰 범위 |
| N2 구역별 작성률 | 대상 페이지 전체 | 진행 배경(본 task 목적), Task 완료 기준, 결과 구역마다, 안내 문구·빈 블록·템플릿 예시 항목 외의 블록이 1개 이상 있는 페이지의 비율 | J5 문서 품질 추이(T1, T2) |
| N3 1:1 대응률 | Done인 대상 페이지 | Task 완료 기준과 결과 구역의 최상위 목록 항목 수가 같고 1개 이상인 페이지의 비율 | J5 문서 품질 추이(T3) |
| N4 ✅/❌ 표기율 | Done인 대상 페이지 | 결과 구역의 최상위 목록 항목이 1개 이상이고 모두 ✅ 또는 ❌를 포함하는 페이지의 비율. 체크박스 블록은 체크를 ✅, 미체크를 ❌로 센다 | J5 문서 품질 추이(T8 판정의 전제) |
| N5 미달성 사유율 | ❌ 항목이 있는 Done 대상 페이지 | ❌ 항목마다 내용이 있는 하위 블록이 1개 이상 있는 페이지의 비율 | J5 문서 품질 추이(T8) |
| N6 근거 링크율 | Done인 대상 페이지 | 결과 구역에 근거(아래 "근거" 항목)가 1개 이상 있는 페이지의 비율 | J5 문서 품질 추이(근거 링크) |
| N7 문서 완성률 | Done인 대상 페이지 | N2의 세 구역, N3, N4, N6을 모두 만족하고, ❌ 항목이 있으면 N5도 만족하는 페이지의 비율 | J5 문서 품질 추이(종합) |
| N8 Holding 사유 comment율 | Holding인 대상 페이지 | comment가 1개 이상 있는 페이지의 비율 | J6 진행 추적(R4) |
| N9 월별 task 수 | 대상 페이지 전체 | 최초 생성 월별 페이지 수, 그중 Done 수, Done 비중 | 없음(참고 값) |

- 구역은 템플릿 heading의 문구로 찾고, 다음 템플릿 heading 전까지를 그 구역으로 간주한다.
  - 템플릿에 없는 heading(예: 결과 구역 안의 "실험 결과")은 그 구역 안의 블록으로 센다.
- 구역의 작성 여부를 판정할 때 아래 블록은 내용으로 세지 않는다.
  - 안내 문구만 있는 블록. 안내 문구 뒤에 덧붙인 글이나 안내 문구 아래 들여쓴 내용은 내용으로 센다.
  - 빈 블록, 템플릿 예시 항목
  - 구분선, 목차, 상위 경로 블록
  - 템플릿에 없는 heading의 제목 텍스트. 그 heading 아래 블록으로 판정한다.
  - 다른 블록을 담는 블록(열, 동기화 블록, 표)은 그 안에 내용이 있을 때만 내용으로 센다. toggle은 제목 텍스트나 그 안에 내용이 있으면 내용으로 센다.
- 최상위 목록 항목은 구역 바로 아래의 글머리표, 번호, 체크박스 항목 중 텍스트가 있는 것이다. 하위 항목과 일반 문단은 세지 않는다.
- 근거는 아래 중 하나다.
  - 텍스트의 링크
  - 페이지 mention, 데이터베이스 mention, 링크 미리보기 mention, 링크 mention
  - 페이지 링크 블록, 하위 페이지 블록
  - 파일, PDF, 이미지, 동영상, bookmark, embed, 링크 미리보기 블록
  - 날짜와 사용자 mention은 근거로 세지 않는다.
- 추정: N5의 "내용이 있는 하위 블록이 있으면 사유 있음"과 N8의 "comment가 있으면 사유 있음"은 근사 규칙이라, 실제 사유 기록과 다를 수 있다.
  - 팀장은 집계 뒤 판정 결과의 조합이 서로 다른 페이지 3개(예: 세 구역 모두 작성, 1:1 불일치, 템플릿 미사용)를 원문과 대조하고, 그 결과를 보고서에 적는다.
- N7은 이전 AS-IS 분석의 "Done 문서 중 완성률"과 직접 비교하지 않는다.
  - 이유: 이전 AS-IS 분석은 완성률의 분모와 판정 기준을 적지 않아, N7과 기준이 같은지 확인할 수 없다(도입 계획 페이지의 "Agent 도입 기준 설정").

### 한계

- 'Task 단위'에는 상태 변경 이력을 담는 속성이 없다("데이터 원천" 절). 그래서 J4 피드백 대기 시간, J6의 A1과 ready-to-done 체류 시간은 도입 이전 현황이 없다.
- 페이지는 Done 뒤에도 수정될 수 있어, 집계 시점의 상태로 판정한다.
- 템플릿을 사용하지 않은 페이지는 구역을 찾지 못해 N2–N7에서 미작성으로 센다. `notion_baseline.py`가 만드는 `summary.md`에 템플릿 미사용 페이지 수가 따로 적힌다.
- Holding인 대상 페이지가 적으면 N8은 표본이 작다. 그래서 `notion_baseline.py`는 `summary.md`에 N8의 표본 수(n)를 함께 적는다.
- comment는 Done과 Holding인 대상 페이지에서만 조회하고(N1, N8에만 사용), 페이지 수준 comment와 본문 블록에 단 inline comment를 모두 센다.
  - 집계 스크립트는 페이지 ID와 페이지 안 블록 ID마다 comment를 조회한다. `--page-comments-only`를 주면 페이지 ID로만 조회한다.
  - Notion API는 해결되지 않은 comment만 응답한다(Notion API 문서 "List comments").
  - 추정: 리뷰한 뒤 해결(resolve) 처리한 comment는 세지 않으므로, N1과 N8은 실제보다 낮게 나올 수 있다.
  - Notion API 문서 "List comments"는 page 또는 block의 comment를 응답한다고만 적고, inline comment의 조회 범위는 적지 않는다.
  - 추정: 페이지 ID로 조회하면 페이지 수준 comment만 응답하고, inline comment는 그 블록 ID로 조회해야 한다. 이 추정 때문에 블록마다 조회한다.
- 대상 페이지는 팀원의 페이지이지만, 개인별 값은 집계하지 않는다. 출력 CSV에도 담당자를 넣지 않는다.

## 집계 스크립트

집계 스크립트 두 개는 Jira와 Notion을 읽기만 하고, 게시하거나 변경하지 않는다. 파이프라인 코드와 섞지 않으려고 `scripts/dev/`에 두고, 팀장이 실행한다.

| 스크립트 | 집계하는 지표 | 읽는 곳 |
|---|---|---|
| `scripts/dev/notion_baseline.py` | 도입 이전 현황 N1–N9 | Notion REST API |
| `scripts/dev/jira_metrics.py` | J1, J3–J8(J6의 A1 제외) | Jira REST API(`scripts/jira_api.py`의 읽기 함수) |

- 정기 보고 메시지도 일부 지표를 매달 팀장 채널로 보낸다.
  - 보내는 지표는 `docs/jira-doc-agent/alerts.md`의 "정기 보고 메시지" 절에 있다.

- 실행 순서와 명령의 예는 아래와 같다. repo 루트에서 `.venv`의 Python으로 실행한다.

```bash
# 1) 도입 이전 현황 (N1–N9). NOTION_TOKEN 환경 변수 필요
#    처음에는 --limit 5로 페이지 5개만 처리해 판정을 확인한 뒤, --limit 없이 전체를 실행함
.venv/bin/python scripts/dev/notion_baseline.py --data-source <Task 단위 data source ID> \
  --assignees <팀원 Notion 사용자 ID, 쉼표 구분> --since 2026-03-01 --until 2026-08-31 \
  --out reports/agent-impact/jira-doc/notion-baseline

# 2) Jira 리뷰 기록 집계 (J1, J3–J8). JIRA_CLOUD_ID, JIRA_EMAIL, JIRA_API_TOKEN 환경 변수 필요
.venv/bin/python scripts/dev/jira_metrics.py --since 2026-09-01 --out reports/agent-impact/jira-doc/jira
```

- `notion_baseline.py`를 실행하려면 팀장이 Notion integration을 만들고 'Task 단위' 데이터베이스에 연결해야 한다.
  - 필요한 권한은 콘텐츠 읽기와 comment 읽기다. comment 읽기 권한이 없으면 comment 조회가 HTTP 403으로 실패한다(Notion API 문서 "List comments").
- `notion_baseline.py`는 일시적인 API 오류(HTTP 429, 5xx, 연결 오류)는 기다렸다가 다시 시도하고, 그래도 실패한 페이지는 `pages.csv`의 `error` 열에 남긴 뒤 다음 페이지로 넘어간다.
  - `error` 열이 채워진 페이지는 `summary.md`의 집계에서 빠지고, 뺀 페이지 수가 `summary.md`에 적힌다.
  - comment 조회가 HTTP 403이면 comment 읽기 권한이 없다는 안내를 출력하고 멈춘다.
- 두 스크립트 모두 기간(`--since`, `--until`, 두 날짜 포함)과 월 구분을 한국 시간(+09:00) 기준으로 계산한다.
- 출력 파일은 아래와 같다.
  - `notion_baseline.py`
    - `pages.csv`: 대상 페이지별 한 줄
    - `summary.md`: N1–N9 요약
  - `jira_metrics.py`
    - `tasks.csv`: task별 한 줄
    - `reviews.csv`: 검수 comment별 한 줄
    - `summary.md`: J1, J3–J8 요약
- 결과 파일은 `reports/agent-impact/jira-doc/` 아래에 두고 git에 올리지 않는다.
  - 이유: 이 repo는 public repo라서 task별 기록이 공개된다(`docs/pr-review-agent/README.md`의 "대상 repo에 설치" 절 1번).

## 측정 일정과 보고서

측정은 8주 동안 하고, 4주차와 8주차에 팀장이 보고서를 작성한다.

| 시기 | 하는 일 |
|---|---|
| 0주차 | 팀장이 `notion_baseline.py`로 도입 이전 현황을 구하고, 판정 결과의 조합이 다른 페이지 3개를 원문과 대조함 |
| 1–4주차 | 아래 설정으로 운영하며 팀장이 매주 `jira_metrics.py`를 실행함 |
| 4주차 | 1차 보고서 |
| 8주차 | 2차 보고서(추이) |

- 4주차에 1차 보고서를 두는 이유는 주별 값(J5)이 4개 쌓여 추이를 확인할 수 있기 때문이다.
- 8주차에 2차 보고서를 두는 이유는 도입 직후 값과 운영이 익숙해진 뒤의 값을 비교하기 위해서다.
  - 추정: 도입 직후에는 담당자가 새 방식에 적응하는 중이라 첫 리뷰 통과율이 낮게 나온다.
- 1–4주차에는 관찰 모드(`JIRA_DOC_GATE=false`)로 운영한다.
  - 이때 문서화 리뷰 comment는 둘째 줄이 "문서화 리뷰 결과(관찰 모드)"인 comment로 게시된다(`docs/jira-doc-agent/README.md`의 "문서화 리뷰 comment" 절).
- 보고서는 `reports/agent-impact/jira-doc/<날짜>.md`에 둔다.
  - 첫머리에는 업무 효율 지표 세 가지와 조건 지표를 둔다.
  - 수치마다 무엇의 값인지, 데이터(기간, task 범위), 표본 수(n), 계산 방법을 함께 적는다.

## 해석 원칙

- 수치는 팀 합계로만 보고서에 적고, 담당자 개인 평가에 사용하지 않는다.
  - 추정: 개인 평가에 사용하면 담당자가 검사 통과에 맞춰 기록하게 되어, 수치가 실제 품질을 나타내지 않게 된다.
  - 예외: 담당자별 수치는 팀장 전용 코칭 자료로만 사용한다.
    - 연동 채널(담당자도 받는 Slack 채널)과 보고서에는 합계만 적는다.
    - 정기 보고 메시지의 구두 피드백 대상 후보가 여기에 해당한다.
    - 근거: `docs/jira-doc-agent/alerts.md`의 "구두 피드백 대상 후보와 개선 여부" 절
    - 이 메시지는 팀장 채널로만 가고, Summary 탭에는 팀 합계만 남는다.
- 표본이 작으므로 평균 대신 중앙값, 범위, 표본 수(n)를 적고, 통계 검정은 하지 않는다.
- 판정 일치율(J7)이 80% 미만이면 업무 효율 지표 옆에 "확인 필요"를 표시한다.
  - 추정: 일치율이 낮으면 팀장이 검수 comment를 믿지 못하고 다시 리뷰해야 하므로, 리뷰 범위가 넓어져도 팀장의 일이 줄지 않는다.
- 업무 효율 지표가 좋아져도 오류 신호(J8)가 늘면, 두 값을 함께 보고서에 적는다.

## 선택 지표

선택 지표는 측정에 담당자의 시간이 필요해 이 문서의 지표(J1–J8)에서 뺀 지표다. 필요하면 팀장이 담당자에게 요청해 따로 측정한다.

- 담당자의 TL;DR과 결과 산출물 구역 작성 시간: 담당자 작성 시간 실측(담당자가 표본 task의 TL;DR과 결과 산출물 구역을 직접 작성하며 걸린 시간을 기록하는 측정)이 필요하다.
  - 추정: 문서화 Agent가 대신 작성하므로 담당자의 작성 시간이 줄어든다.

## Claude 사용량

- 문서화 Agent의 Claude 사용량(토큰 수와 API 환산 비용)은 실행마다 ticket의 jira-doc 기록 `runs`에 남는다.
  - API 환산 비용: 1회 실행의 Claude 사용량을 API 종량제 단가로 환산한 값(claude CLI의 `total_cost_usd`)
  - 기록 항목과 네 갈래 분류는 `docs/jira-doc-agent/alerts.md`의 "Claude 사용량 기록", "Claude 사용량 네 갈래" 절에 있다.
- 정기 보고 메시지가 매달 사용량과 그 원인을 팀장 채널로 보낸다.
- 3-1 단계 로그에는 실행마다 API 환산 비용이 남는다.
  - 시작: 2026-10-12(commit `d20a424`)
  - `runs` 기록은 2026-10 알림 체계 재구성 뒤 실행부터 남는다.
    - 근거: commit `b9a7e28`의 `scripts/apply.py`에는 `runs` 기록이 없다.
