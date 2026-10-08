# Task (검수 모드)

이 파일은 검수 모드에서 work type이 Task인 task의 구역, 완료 판단 기준, 출력 파일의 작성 형식, 검수 결과 기준을 정한다.
출력 파일은 verdict.json, tldr.wiki, comment.wiki다.

## 구역
- 사람 구역(담당자가 작성하는 description 구역): 진행 배경, 예상 산출물 (Task 완료 기준)
- agent 구역(이 agent의 출력으로 채우는 구역): 결과 산출물 (Task 완료 기준과 1:1 대응), TL;DR(Jira task의 요약 필드)

완료 판단 기준과 items, extra 작성 방법은 `prompts/types/task/deliverables.md`를 읽는다.

## 검사 항목
| ID | 이름 | 검사 내용 | 계산 주체 | 검수 모드 영향 |
|---|---|---|---|---|
| T1 | 예상 산출물 작성 | 예상 산출물 구역에 안내문이 아닌 목록 항목이 1개 이상 있음 | 스크립트 | 실패 시 보류 |
| T2 | 진행 배경 작성 | 진행 배경 구역에 안내문 외의 내용이 2줄 이상 있음 | 스크립트 | 실패 시 보류 |
| T3 | 예상 산출물과 결과 산출물의 1:1 대응 | verdict.json의 items 번호가 precheck의 expected 번호와 1:1로 같음 | apply.py (agent 출력 검증). agent는 checks에 넣지 않는다 | 불일치 시 검토 요청 |
| T4 | 진행 배경 충실도 | 아래 "문서화 리뷰" 참고 | agent | 문서화 리뷰 정책 |
| T5 | 예상 산출물 분할 단위 | 아래 "문서화 리뷰" 참고 | agent | 문서화 리뷰 정책 |
| T6 | 진행 기록 comment | 아래 "문서화 리뷰" 참고 | agent | 문서화 리뷰 정책 |
| T7 | sub-task 기록 | 아래 "문서화 리뷰" 참고 (sub-task가 없으면 n/a) | agent | 문서화 리뷰 정책 |
| T8 | 초과 달성·미달성 사유 | 미달성 items와 extra마다 why가 채워져 있음 | apply.py. agent는 checks에 넣지 않는다 | 문서화 리뷰 정책 |
| A2 | 완료 기준의 사후 변경 | 가장 최근 request 전환 이후에 완료 기준 구역(예상 산출물)이 바뀜 | 스크립트 | 실패 시 검토 요청 |

## 할 일
- 완료 판단 기준: deliverables.md의 "완료 판단 기준"에 따라 expected가 비어 있으면 fix로 가고 requests에
  "예상 산출물(Task 완료 기준)에 이 task가 끝났을 때 나와야 하는 것의 목록 작성 필요"를 넣는다.
  항목은 있으나 무엇이 나와야 완료인지 알 수 없을 만큼 모호하면 escalate로 간다.
- 대응: expected 항목마다 실제 결과를 대응시킨다 (deliverables.md의 "예상 산출물과 결과 산출물의 대응").
  항목마다 done, result(실제 결과 한 줄), evidence(근거 링크)를 정하고, 미달성이면 why(왜 달성하지 못했는지)와
  reason(달성하려면 무엇을 더 남겨야 하는지)을 쓴다.
  어느 항목에도 해당하지 않는 결과는 extra(초과 달성)에 result, why(왜 추가했는지), evidence로 적는다.
  why는 사람의 기록에 있는 사유만 쓰고, 없으면 빈 문자열로 둔다.
  모든 결과에는 근거 링크가 있어야 한다. 근거는 PR url, 문서 url, 또는 그 사실이 적힌 comment의 url이다.
- 링크 추가 요청: 사람 구역에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에
  "진행 배경에 [제목|URL] 추가 필요"로 적는다.
- 문서화 리뷰: 진행 배경, 예상 산출물, 사람 comment, sub-task의 description과 comment를 팀장이 리뷰하듯 확인한다.

## 문서화 리뷰 (검수 모드)
검수 모드(review 실행 모드)에서는 rules.md의 "문서화 리뷰" 기준으로 아래 네 검사를 checks(verdict.json의 검사별 결과 목록)에 넣고, 항목마다 feedback을 작성한다.
- 진행 배경 충실도 (T4)
- 예상 산출물 분할 단위 (T5)
- 진행 기록 comment (T6)
- sub-task 기록 (T7)

| ID | pass 기준 | fail 예 |
|---|---|---|
| T4 진행 배경 충실도 | 왜 이 일을 하는지(문제, 요구, 동기)가 있고, 1년 뒤 처음 보는 사람이 대상(데이터, 모델, 서비스)과 맥락(선행 task, 관련 문서 링크)을 알 수 있음 | "test", "모델 개선"처럼 왜가 없음. 대상과 맥락을 전혀 알 수 없음 |
| T5 예상 산출물 분할 단위 | 항목마다 결과 하나이고, 무엇이 나오면 끝인지(수치, 산출물 형태, 대상) 판단할 수 있음 | "데이터 정제 및 모델 재학습"처럼 따로 달성 여부가 갈릴 결과가 한 항목에 묶임. "성능 개선", "검토"처럼 끝을 판단할 기준이 없음 |
| T6 진행 기록 comment | 작업 기간 중 중간 결과, 인사이트, 방향 변경과 그 이유가 comment로 남아 있음 | 작업 기간 중 사람 comment가 없음. 작업이 영업일 기준 3일 이상인데 comment가 request 직전에만 몰려 있음. 방향이나 범위가 바뀌었는데 이유가 없음 |
| T7 sub-task 기록 | sub-task마다 description에 무엇을 왜 하는지가 있고, 끝난 sub-task에는 결과 comment나 PR(sub-task의 prs 포함)이 있음 | description이 비었거나 요약만 반복함. done인데 결과 기록이 없음 |

```json
"feedback": [
  { "id": "T4", "points": ["평가 지표를 바꾸게 된 계기(고객 불만 2건)가 적혀 있어 이유가 분명함"] },
  { "id": "T5", "points": ["1번 항목은 결과 하나로 잘 나뉨",
                           "3번 '데이터 정제 및 모델 재학습'은 따로 달성 여부가 갈리는 결과 두 개가 묶여 있음"],
    "request": "예상 산출물 3번을 '데이터 정제'와 '모델 재학습' 두 항목으로 나누고 각각 끝났을 때의 기준 작성 필요" },
  { "id": "T6", "points": ["작업 기간(첫 in-progress(진행 중 상태) 전환부터 마지막 request 전환(담당자의 완료 요청)까지) 영업일 기준 8일 동안 사람 comment 1건(request 전환 당일)뿐이라 중간 결과와 방향 변경 이유를 알 수 없음"],
    "request": "진행 중 나온 중간 수치와 방향을 바꾼 이유 comment 필요" },
  { "id": "T7", "points": ["INNO-18은 결과 comment([comment 2026-09-14|https://<site>/browse/INNO-18?focusedCommentId=10150])와 PR([PR #47 export 테스트 추가|https://github.com/everex-ai/repo/pull/47])이 있음", "INNO-19는 description이 비어 있어 무엇을 했는지 알 수 없음"],
    "request": "INNO-19 description에 무엇을 왜 했는지 작성 필요" }
]
```
- points는 comment 검사 표의 내용 칸에 들어가므로 항목당 1–3개로 짧게 적는다(`scripts/apply.py`의 `check_table`).
- points의 수치(영업일 수, comment 수)는 셈 기준(기간의 시작과 끝, 무엇을 셌는지)을 함께 적는다.
  - 영업일 수는 "영업일 기준 N일"로 적는다("N영업일"로 적지 않는다).
  - precheck.json의 docFacts(문서화 리뷰용 수치)의 키 이름은 적지 않는다.
- feedback[].request는 "필요"로 맺는다.
- 문서화 리뷰의 요청 문장은 requests가 아니라 feedback[].request에만 적는다. comment.wiki에도 넣지 않는다.

## tldr.wiki 형식
`h2. Summary` 아래에 결과를 가급적 정량 수치로 3–5개 항목으로 적는다.
```
h2. Summary
* (무엇을 했는지 한 줄. 근거: [PR 또는 comment 링크])
* (핵심 결과 1: 수치로. 모델, 데이터, 계산 방법을 함께. 예: pose-v3의 검증 데이터(val set, 1,200장) PCK@0.2(관절 위치 오차가 몸통 크기의 0.2배 이내인 비율) 0.83, 기준 모델 pose-v2 대비 +0.04. 근거: [리포트 링크])
* (핵심 결과 2 또는 산출물. 초과 달성이 있으면 한 항목으로. 근거: [PR 또는 comment 링크])
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|검토 요청|보류>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```

## comment.wiki (검수 모드, 결과 요약 부분만)
검수 모드(review 실행 모드)에서는 이 task에서 무엇이 어떻게 되었고 왜 이 검수 결과인지를 개조식 2–5줄로 적는다.
- 줄마다 근거 wiki 링크([PR 제목|URL] 또는 [comment 날짜|URL])를 붙인다.
```
* 재현 테스트 추가 항목은 [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]로 병합되어 달성함
* 오류율 1% 미만 항목은 모델 v2의 검증 데이터 오류율 0.7%라는 수치만 있고 계산 방법과 리포트 링크가 기록에 없어 미달성으로 둠([comment 2026-09-12|https://<site>/browse/INNO-17?focusedCommentId=10123])
* 예상에 없던 오류율 대시보드([PR #45 오류율 대시보드|https://github.com/everex-ai/repo/pull/45])는 초과 달성으로 기록함
```

## 검수 결과
verdict(verdict.json의 검수 결과 키)에는 코드값을 적고, 괄호 안은 검수 결과 이름이다.
- 문서화 리뷰(T4–T8)는 검수 결과에 넣지 않는다. apply.py가 모드에 따라 적용한다(`scripts/apply.py`의 `apply_issue`, `recheck_verdict`).
  - 관찰 모드(Actions 변수 `JIRA_DOC_GATE`가 `false`)에서는 apply.py가 문서화 리뷰 결과를 팀장용 comment와 Actions Summary로 공유한다(`scripts/apply.py`의 `doc_review_comment`).
  - 게이트 모드(`JIRA_DOC_GATE`가 `true`)에서는 fail이 있으면 통과를 보류로 바꾼다.
- pass(통과): items가 모두 done이고, 아래 검사가 모두 pass 또는 n/a다.
  - 예상 산출물 작성 (T1)
  - 진행 배경 작성 (T2)
  - 1년 뒤에도 이해 가능한 기록 (R2)의 링크 기준(agent 구역의 결과마다 확인 가능한 링크가 있음)
  - stop 사유 comment (R4)
  - 완료 기준의 사후 변경 (A2)
- escalate(검토 요청): 아래 중 하나에 해당한다.
  - done이 false인 항목마다 why에 사람의 기록(comment, sub-task, PR)에 있는 사유가 있고, T1, T2, R2의 링크 기준, R4가 fail이 아님
  - 예상 산출물이 모호해 대응할 수 없음
  - A2가 fail
  - 근거가 서로 모순됨
- fix(보류): 아래 중 하나에 해당한다.
  - done이 false이고 why가 빈 문자열이거나 "근거 부족으로 확인 불가"인 항목이 있음
  - T1, T2, R2의 링크 기준, R4 중 fail이 있음
