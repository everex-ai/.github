# Bug (검수 모드)

이 파일은 work type이 Bug인 task의 구역, 완료 판단 기준, 출력 파일의 작성 형식, 검수 결과 기준을 정한다.
출력 파일은 verdict.json, tldr.wiki, comment.wiki다.

## 구역
- 사람 구역(제보자가 작성하는 description 구역)은 아래 네 구역이다. agent는 채워졌는지만 검사한다.
  - 기본 정보
  - 문제(As-Is)
  - 개선(To-Be)
  - 첨부 자료(필수)
- 기본 정보와 문제(As-Is) 작성, 첨부 파일 (B1)과 개선(To-Be) 작성 (B2)은 `scripts/precheck.py`의 `check_bug_template`가 계산해 precheck.json(`scripts/precheck.py`가 계산한 검사 결과 파일)에 적는다.
- agent 구역(이 agent의 출력으로 채우는 구역)은 TL;DR(Jira task의 요약 필드)의 원인과 해결이다. description에는 작성하지 않는다.

## 완료 판단 기준
1. 원인이 근거 링크(PR 또는 comment)와 함께 있음: 원인과 해결의 근거 링크 (B3)
2. 해결이 근거 링크(PR, 배포 버전 또는 날짜)와 함께 있음: B3
3. To-Be 동작을 확인했다는 사람의 comment가 해결 이후에 있음: To-Be 동작 확인 comment (B4)
   - "확인함", "정상 동작 확인" 같은 문장이 담긴 comment 중 comments.json의 kind가 "human"인 것(사람이 작성한 comment)을 찾는다.
   - 없으면 fix(보류)로 가고 requests(검수 comment의 요청 절에 들어가는 요청 문장 목록)에 확인 comment를 요청한다.

## 검사 항목
| ID | 이름 | 검사 내용 | 계산 주체 | 검수 모드 영향 |
|---|---|---|---|---|
| B1 | 기본 정보와 문제(As-Is) 작성, 첨부 파일 | 기본 정보의 발생 기기/서비스, 발생 일자, 발생 장비, 발생 계정이 채워져 있고, 문제(As-Is)에 오류 동작 설명이 있고, Jira 첨부 파일이 1개 이상 있음 | 스크립트 | 실패 시 보류 |
| B2 | 개선(To-Be) 작성 | 개선(To-Be)에 정상 동작 설명이 있음 | 스크립트 | 실패 시 보류 |
| B3 | 원인과 해결의 근거 링크 | TL;DR의 원인과 해결에 각각 근거 링크(PR 또는 comment)가 있음 | agent | 실패 시 보류 |
| B4 | To-Be 동작 확인 comment | To-Be 동작을 확인했다는 사람의 comment가 해결 이후에 있음 | agent | 없으면 보류 |
| B5 | 문제(As-Is) 재현 정보 | 아래 "문서화 리뷰" 참고 | agent | 문서화 리뷰 정책 |
| B6 | 원인 분석 기록 | 아래 "문서화 리뷰" 참고 | agent | 문서화 리뷰 정책 |
| B7 | 해결 확인 기록 | 아래 "문서화 리뷰" 참고 | agent | 문서화 리뷰 정책 |
| A2 | 완료 기준의 사후 변경 | 가장 최근 request 전환 이후에 완료 기준 구역(개선(To-Be))이 바뀜 | 스크립트 | 실패 시 검토 요청 |

## 할 일
- 링크 추가 요청: 사람 구역에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에
  "문제(As-Is)에 [제목|URL] 추가 필요"로 적는다.

## verdict.json의 items와 extra
- items와 extra는 둘 다 빈 배열([])로 둔다.
- checks(verdict.json의 검사별 결과 목록)에는 아래 검사를 넣는다.
  - B1, B2: precheck.json의 결과를 인용한다
  - B3, B4: agent가 판단한다
  - B5–B7: 문서화 리뷰(검수 모드). agent가 판단한다
  - 1년 뒤에도 이해 가능한 기록 (R2)
  - 개조식 작성 (R3)
  - stop 사유 comment (R4)
  - 완료 기준의 사후 변경 (A2)

## 문서화 리뷰 (검수 모드)
검수 모드에서는 rules.md의 "문서화 리뷰" 규칙으로 아래 세 검사를 checks에 넣고, 항목마다 feedback을 작성한다.

| ID | pass 기준 | fail 예 |
|---|---|---|
| B5 문제(As-Is) 재현 정보 | 다른 사람이 재현할 수 있는 절차, 조건, 빈도가 문제(As-Is)에 있음 | "export 안 됨"처럼 현상만 있음 |
| B6 원인 분석 기록 | 원인을 찾은 과정(로그, 재현 결과)이 comment나 PR에 있음 | 원인 한 줄만 있고 확인 과정이 없음 |
| B7 해결 확인 기록 | 반영 버전이나 환경과 To-Be 확인 방법이 comment나 PR에 있음 | "확인함"만 있음 |

To-Be 동작 확인 comment (B4)는 확인 comment가 있는지를, B7은 그 기록이 1년 뒤에도 이해될 만큼 충실한지를 판단한다.
```json
"feedback": [
  { "id": "B5", "points": ["기본 정보에 발생 기기/서비스, 발생 일자, 발생 장비, 발생 계정이 모두 채워져 있음",
                           "문제(As-Is)에 'CSV export 안 됨'이라는 현상만 있고 어느 화면에서 무엇을 눌렀는지, 매번 일어나는지가 없음"],
    "request": "문제(As-Is)에 재현 절차(화면과 조작 순서), 발생 조건, 발생 빈도 작성 필요" },
  { "id": "B6", "points": ["원인 분석 comment([comment 2026-09-15|https://<site>/browse/INNO-31?focusedCommentId=10210])에 오류 로그와 재현 결과가 함께 있어 원인을 찾은 과정이 분명함"] },
  { "id": "B7", "points": ["해결 PR([PR #51 CSV export 인코딩 수정|https://github.com/everex-ai/repo/pull/51]) 병합 뒤 사람 comment([comment 2026-09-17|https://<site>/browse/INNO-31?focusedCommentId=10220])는 '확인함' 한 줄뿐이라 어느 버전, 어느 환경에서 To-Be 동작을 어떻게 확인했는지 알 수 없음"],
    "request": "To-Be 동작을 확인한 반영 버전, 확인 환경, 확인 방법 comment 필요" }
]
```
- points는 comment 검사 표의 내용 칸에 들어가므로 항목당 1–3개로 짧게 적는다(`scripts/apply.py`의 `check_table`).
- points의 수치(영업일 수, comment 수)는 셈 기준(기간의 시작과 끝, 무엇을 셌는지)을 함께 적는다.
  - 영업일 수는 "영업일 기준 N일"로 적는다("N영업일"로 적지 않는다).
  - precheck.json의 docFacts(문서화 리뷰용 수치)의 키 이름은 적지 않는다.
- feedback[].request는 "필요"로 맺는다.
- 문서화 리뷰의 요청 문장은 requests가 아니라 feedback[].request에만 적는다. comment.wiki에도 넣지 않는다.

## tldr.wiki 형식
```
h2. Summary

h3. 원인
* (발생 원인을 구체적으로. 근거: [PR 또는 comment 링크]. 기록이나 코드를 해석해 추론한 원인이면 앞에 "추정:")

h3. 해결
* (해결 내용을 구체적으로. 근거: [PR 링크], 배포: 버전 또는 날짜)
* (To-Be 동작 확인: YYYY-MM-DD 누가 확인, [comment 링크])
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|검토 요청|보류>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```
- 원인이나 해결을 아직 모르면 그 항목에 "(미확인)"이라고 적는다.
- h3. 소제목 앞에는 빈 줄을 둔다. 빈 줄이 없으면 Jira가 소제목을 앞 목록 항목에 붙여 표시한다(INNO-34 시험 실행의 TL;DR, 2026-10-08).

## comment.wiki (검수 모드, 결과 요약 부분만)
검수 모드(review 실행 모드)에서는 원인과 해결이 무엇이었고 To-Be 확인이 있었는지를 개조식 2–4줄로 적는다.
- 줄마다 근거 wiki 링크([PR 제목|URL] 또는 [comment 날짜|URL])를 붙인다.

## 검수 결과
verdict(verdict.json의 검수 결과 키)에는 코드값을 적고, 괄호 안은 검수 결과 이름이다.
- 문서화 리뷰(B5–B7)는 검수 결과에 넣지 않는다. apply.py가 모드에 따라 적용한다(`scripts/apply.py`의 `apply_issue`, `recheck_verdict`).
  - 관찰 모드(Actions 변수 `JIRA_DOC_GATE`가 `false`)에서는 apply.py가 문서화 리뷰 결과를 팀장용 comment와 Actions Summary로 공유한다(`scripts/apply.py`의 `doc_review_comment`).
  - 게이트 모드(`JIRA_DOC_GATE`가 `true`)에서는 fail이 있으면 통과를 보류로 바꾼다.
- pass(통과): 아래 검사가 모두 pass 또는 n/a다.
  - B1
  - B2
  - B3
  - B4
  - R2의 링크 기준(agent 구역의 결과마다 확인 가능한 링크가 있음)
  - R4
  - A2
- escalate(검토 요청): 아래 중 하나에 해당한다.
  - 원인과 해결이 서로 맞지 않음
  - 재현 불가로 종료하려 함
  - A2가 fail
- fix(보류): 아래 검사 중 fail이 있다. B1, B2의 fail은 제보자가 채우면 되므로 requests에 무엇이 비었는지 적는다.
  - B1
  - B2
  - B3
  - B4
  - R2의 링크 기준
  - R4
