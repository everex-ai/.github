# Issue (검수 모드)

이 파일은 work type이 Issue인 task의 구역, 완료 판단 기준, 출력 파일의 작성 형식, 검수 결과 기준을 정한다.
출력 파일은 verdict.json, tldr.wiki, comment.wiki다.

## 구역
- 사람 구역(제기자가 작성하는 description 구역): 이슈 유형(제안, 이슈, 기타 중 고르는 체크박스), 이슈 내용
- agent 구역(이 agent의 출력으로 채우는 구역)은 TL;DR(Jira task의 요약 필드)의 대응 결과다. description에는 작성하지 않는다.

## 완료 판단 기준
대응 결과가 근거와 함께 있어야 한다(대응 결과의 근거 (I3)).
- 대응 결과는 수용, 반려, 다른 task로 이관 중 하나이며 이유 한 줄이 있어야 한다.
- 근거는 결정을 적은 comment의 링크, 또는 이관해서 만든 task의 키다.
- precheck.json(`scripts/precheck.py`가 계산한 검사 결과 파일)의 이슈 유형 선택 (I1) 결과가 unknown이면, description.wiki의 이슈 유형 구역을 직접 읽어 선택된 항목이 있는지 판단한다.
  - unknown은 스크립트가 체크박스 상태를 텍스트로 확인할 수 없을 때의 결과다(`scripts/precheck.py`의 `check_issue_template`).
  - 이슈 유형 체크박스에서 선택한 항목은 description.wiki에 취소선(예: `-제안-`)으로 나온다(INNO-36 시험 실행, 2026-10-08). 취소선 항목이 있으면 선택된 것으로 판단한다.

## 검사 항목
| ID | 이름 | 검사 내용 | 계산 주체 | 검수 모드 영향 |
|---|---|---|---|---|
| I1 | 이슈 유형 선택 | 이슈 유형 체크박스가 1개 이상 선택됨 (precheck가 unknown이면 agent가 description 원문으로 판단) | 스크립트, 보조로 agent | 실패 시 보류 |
| I2 | 이슈 내용 작성 | 이슈 내용이 있음 | 스크립트 | 실패 시 보류 |
| I3 | 대응 결과의 근거 | TL;DR의 대응 결과에 근거(comment 링크 또는 만들어진 task 키)가 있음 | agent | 실패 시 보류 |
| I4 | 이슈 내용 충실도 | 아래 "문서화 리뷰" 참고 | agent | 문서화 리뷰 정책 |
| I5 | 논의와 결정 기록 | 아래 "문서화 리뷰" 참고 | agent | 문서화 리뷰 정책 |

## 할 일
- 링크 추가 요청: 사람 구역에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에
  "이슈 내용에 [제목|URL] 추가 필요"로 적는다.

## verdict.json의 items와 extra
- items와 extra는 둘 다 빈 배열([])로 둔다.
- checks(verdict.json의 검사별 결과 목록)에는 아래 검사를 넣는다.
  - I1과 이슈 내용 작성 (I2): precheck.json의 결과를 인용한다
  - I3: agent가 판단한다
  - I4, I5: 문서화 리뷰(검수 모드). agent가 판단한다
  - 1년 뒤에도 이해 가능한 기록 (R2)
  - 개조식 작성 (R3)
  - stop 사유 comment (R4)

## 문서화 리뷰 (검수 모드)
검수 모드에서는 rules.md의 "문서화 리뷰" 규칙으로 아래 두 검사를 checks에 넣고, 항목마다 feedback을 작성한다.

| ID | pass 기준 | fail 예 |
|---|---|---|
| I4 이슈 내용 충실도 | 왜 제기했는지(문제, 근거 수치)와 결정이 필요한 것이 이슈 내용에 있음 | 요구만 있고 이유가 없음 |
| I5 논의와 결정 기록 | 검토한 대안이나 의견과 결정 이유가 comment에 있음 | 결정만 있고 이유가 없음 |

대응 결과의 근거 (I3)는 결정을 적은 근거가 있는지를, I5는 결정에 이른 논의와 이유가 충실한지를 판단한다.
```json
"feedback": [
  { "id": "I4", "points": ["추론 서버의 GPU 메모리 부족으로 요청이 실패한 사례와 결정이 필요한 것(서버 증설 또는 모델 경량화)이 이슈 내용에 있어 제기한 이유가 분명함"] },
  { "id": "I5", "points": ["이관 결정 comment([comment 2026-09-20|https://<site>/browse/INNO-40?focusedCommentId=10300])에 결정만 있고 검토한 대안(서버 증설, 모델 경량화)과 이관을 고른 이유가 없음"],
    "request": "검토한 대안과 이관을 결정한 이유 comment 필요" }
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
* (대응 결과: 수용 / 반려 / task INNO-000으로 이관, 이유 한 줄)
* (근거: [comment 링크] 또는 이관해서 만든 task의 키)
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|검토 요청|보류>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```

## comment.wiki (검수 모드, 결과 요약 부분만)
검수 모드(review 실행 모드)에서는 대응 결과가 무엇이고 어디에 근거가 있는지를 개조식 1–3줄로 적는다.
- 줄마다 근거 wiki 링크([comment 날짜|URL]) 또는 이관해서 만든 task의 키를 붙인다.

## 검수 결과
verdict(verdict.json의 검수 결과 키)에는 코드값을 적고, 괄호 안은 검수 결과 이름이다.
- 문서화 리뷰(I4, I5)는 검수 결과에 넣지 않는다. apply.py가 모드에 따라 적용한다(`scripts/apply.py`의 `apply_issue`, `recheck_verdict`).
  - 관찰 모드(Actions 변수 `JIRA_DOC_GATE`가 `false`)에서는 apply.py가 문서화 리뷰 결과를 팀장용 comment와 Actions Summary로 공유한다(`scripts/apply.py`의 `doc_review_comment`).
  - 게이트 모드(`JIRA_DOC_GATE`가 `true`)에서는 fail이 있으면 통과를 보류로 바꾼다.
- pass(통과): 아래 검사가 모두 pass 또는 n/a다.
  - I1
  - I2
  - I3
  - R2의 링크 기준(agent 구역의 결과마다 확인 가능한 링크가 있음)
  - R4
- escalate(검토 요청): 대응 결과가 comment마다 다르게 적혀 있어 무엇이 최종인지 알 수 없다.
- fix(보류): 아래 검사 중 fail이 있다.
  - I1
  - I2
  - I3
  - R2의 링크 기준
  - R4
