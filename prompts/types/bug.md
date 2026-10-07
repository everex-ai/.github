# Bug

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

## tldr.wiki 형식
```
h2. Summary
원인
* (발생 원인을 구체적으로. 근거: [PR 또는 comment 링크]. 기록이나 코드를 해석해 추론한 원인이면 앞에 "추정:")
해결
* (해결 내용을 구체적으로. 근거: [PR 링크], 배포: 버전 또는 날짜)
* (To-Be 동작 확인: YYYY-MM-DD 누가 확인, [comment 링크])
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|검토 요청|보류>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```
- 원인이나 해결을 아직 모르면 그 항목에 "(미확인)"이라고 적는다.
  - 정리 모드(digest 실행 모드. 지시는 `prompts/digest.md`에 있다)에서는 지금까지 시도한 것을 한 줄 추가한다.
- 정리 모드에서는 "검수 결과" 부분을 빼고 "상태"와 "갱신"만 적는다.

## verdict.json의 items와 extra
- items와 extra는 둘 다 빈 배열([])로 둔다.
- checks(verdict.json의 검사별 결과 목록)에는 아래 검사를 넣는다.
  - B1, B2: precheck.json의 결과를 인용한다
  - B3, B4: agent가 판단한다
  - 1년 뒤에도 이해 가능한 기록 (R2)
  - 개조식 작성 (R3)
  - stop 사유 comment (R4)
  - 완료 기준의 사후 변경 (A2)

## comment.wiki (검수 모드, 결과 요약 부분만)
검수 모드(review 실행 모드)에서는 원인과 해결이 무엇이었고 To-Be 확인이 있었는지를 개조식 2–4줄로 적는다.
- 줄마다 근거 wiki 링크([PR 제목|URL] 또는 [comment 날짜|URL])를 붙인다.

## 검수 결과
verdict(verdict.json의 검수 결과 키)에는 코드값을 적고, 괄호 안은 검수 결과 이름이다.
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
