# 검수 모드 절차

검수 모드는 request 전환(담당자가 완료를 요청해 ticket을 ready-to-done 상태로 보내는 Jira 전환)된 ticket 하나를 검수한다.
CI agent는 그 ticket의 TL;DR, 판정 객체, 검수 comment의 결과 요약을 작성하고 검수 결과(통과, 보류, 검토 요청 중 하나. 아래 "검수 결과 코드값의 공통 기준" 절)를 정한다.

---

## 검수 순서

1. `prompts/rules.md`의 "공통 처리 순서"를 수행한다.
2. 공통 처리 순서의 마지막 단계에서 work type 검수 파일(`prompts/types/<work type>/review.md`)의 단계를 수행할 때, 그 단계가 가리키는 이 파일의 아래 절을 함께 적용한다.
3. `prompts/rules.md`의 "출력" 절 형식으로 결과를 낸다. 검수 모드에서는 tasks[].comment도 낸다.

---

## 결과 요약(comment) 작성

- tasks[].comment에는 검수 comment의 "결과 요약" 부분만 작성한다.
  - 이 ticket에서 무엇이 어떻게 되었고 왜 이 검수 결과인지를 work type 검수 파일의 "comment (결과 요약 부분만)" 절에 적힌 줄 수로 적고, 줄마다 근거 wiki 링크를 붙인다.
- 아래는 apply.py가 붙이므로 넣지 않는다(`scripts/apply.py`의 `review_comment`).
  - 검수 결과 줄("검수 결과: ...")
  - Task의 달성 요약 줄("예상 산출물 n개 중 m개 달성")
  - 검사 표, requests 목록, 담당자 멘션
- ticket에서 나온 결과와, 그 결과로 정한 검수 결과의 이유만 작성한다. 문서화 리뷰 항목에 대한 지적은 넣지 않고 feedback에만 작성한다.

---

## 공통 검사 항목 (R2, R3, R4)

"계산 주체"가 스크립트인 항목은 precheck.json에 결과가 이미 들어 있다. 다시 계산하지 말고 그대로 인용한다.
CI agent가 계산하는 항목만 직접 판단한다. "검수 모드 영향"은 검수에서 실패(검사 결과 fail)했을 때 검수 결과가 어떻게 되는지다.
"이름"은 검수 comment의 검사 표에 나가는 표시 이름이며 apply.py가 붙인다(`scripts/apply.py`의 `CHECK_NAMES`). CI agent는 판정 객체(verdict)에 ID만 적는다.

| ID | 이름 | 검사 내용 | 계산 주체 | 검수 모드 영향 |
|---|---|---|---|---|
| R2 | 1년 뒤에도 이해 가능한 기록 | 배경(Bug는 문제(As-Is), Issue는 이슈 내용. Task의 진행 배경은 T4에서 확인한다)에 "왜"가 있고, agent 구역의 결과마다 확인 가능한 링크가 있으며, 결정 사항의 이유가 comment에 남아 있음 | CI agent | 링크 부재는 보류, 나머지는 검수 comment로 권고 |
| R3 | 개조식 작성 | agent 구역은 개조식으로 적음. 사람 구역이 서술형이면 검수 comment로 권고만 함 | CI agent | 검수 comment로 권고 |
| R4 | stop 사유 comment | `stop`으로 Backlog에 들어간 뒤 사람이 작성한 사유 comment가 있음(rules.md의 팀 문서화 규칙 3). Jira Automation의 안내 comment는 제외 | 스크립트 | stop 이력이 있는데 실패면 보류 |

work type별 검사 항목은 work type 검수 파일의 "검사 항목" 절에 있다.

---

## 문서화 리뷰 feedback 작성

CI agent는 문서화 리뷰 항목을 판단하고, 항목마다 판정 객체(verdict)의 `feedback`에 피드백을 적는다.
- 문서화 리뷰는 팀장이 request 전환된 ticket을 리뷰할 때 확인하는 것 중 담당자의 기록 습관에 관한 항목이다.
- 항목과 기준표는 work type 검수 파일의 "문서화 리뷰" 절에 있다.
- 판단에 사용하는 수치는 precheck.json의 `docFacts`에 있다.

- `feedback`의 `points`에는 잘한 점 한 줄과 부족한 점을 적는다. 부족한 점은 어디가(예상 산출물 번호, sub-task 키, comment 날짜)
  어떻게 부족한지 구체적으로 적는다. fail이면 `feedback[].request`에 담당자에게 보낼 요청 한 문장을 적는다.
- `points`에 수치(영업일 수, comment 수, 줄 수)를 적을 때는 셈 기준(기간의 시작과 끝, 무엇을 셌는지)을 함께 적는다.
  영업일 수는 "영업일 기준 N일"로 적는다("N영업일"로 적지 않는다).
  docFacts의 키 이름은 적지 않는다. 예: "작업 기간(첫 in-progress 전환부터 마지막 request 전환까지) 영업일 기준 8일 동안 사람 comment 1건"
- `points`는 검수 comment 검사 표의 내용 칸에 그대로 들어가므로 항목당 1–3개로 짧게 적는다(`scripts/apply.py`의 `check_table`).
  같은 항목의 checks `detail`은 points가 없을 때만 표시되는 한 줄 요약이다.
- 문서화 리뷰의 요청 문장은 `requests`에 넣지 않는다(`feedback[].request`에만 적는다). 결과 요약(`tasks[].comment`)에도 넣지 않는다.

---

## 검수 결과 코드값의 공통 기준

검수 결과 코드값(`verdict.verdict`)에는 pass, fix, escalate 중 하나를 적는다. 괄호 안은 검수 comment와 TL;DR에 나가는 검수 결과 이름이다.
검수 결과 코드값은 문서화 리뷰 항목을 빼고 정한다. 문서화 리뷰 결과의 반영은 apply.py가 한다(`scripts/apply.py`의 `recheck_verdict`).

- pass(통과): 완료 판단 기준(work type 검수 파일, Task는 `prompts/types/task/deliverables.md`)을 충족하고, 필수 검사(위 "공통 검사 항목" 표와 work type 검수 파일의 검사 항목 표에서 "보류" 또는 "검토 요청"으로 이어지는 항목)가 모두 통과한 경우다.
- fix(보류): 담당자가 고칠 수 있는 경우다. 무엇을 어디에 남기면 되는지 requests에 적는다.
- escalate(검토 요청): 팀장 판단이 필요한 경우다. 근거가 서로 모순되면 work type과 관계없이 escalate로 정한다.
- 세 코드값의 work type별 조건은 work type 검수 파일의 "검수 결과" 절에 있다.
- R4가 fail이고 검수 결과가 보류이면 apply.py가 R4 요청 문장(stop의 사유를 "stop 사유:"로 시작하는 comment로 남긴 뒤 다시 request 전환을 하라는 문장)을 requests에 넣으므로(`scripts/apply.py`의 `apply_issue`), R4 요청 문장은 requests에 적지 않는다
