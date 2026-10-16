# Bug 공통

work type이 Bug인 ticket의 구조와, 검수 모드와 정리 모드에 공통인 형식을 정한다.

---

## ticket 구조

| 위치 | 이름 | 작성 주체 | 내용 |
|---|---|---|---|
| description | 기본 정보 | 제보자(사람 구역) | 발생 기기/서비스, 발생 일자, 발생 장비, 발생 계정(`scripts/src/jira/precheck/template.py`의 `BUG_INFO_FIELDS`) |
| description | 문제(As-Is) | 제보자(사람 구역) | 오류 동작 설명 |
| description | 개선(To-Be) | 제보자(사람 구역) | 정상 동작 설명. Bug의 완료 기준 구역이다 |
| description | 첨부 자료(필수) | 제보자(사람 구역) | Jira 첨부 파일. 목록은 issue.json의 첨부 목록에 있다 |
| TL;DR 필드 | Summary(원인, 해결) | CI agent(agent 구역) | 아래 "tldr 형식" |
| comment, sub-task, PR | 사람의 기록 | 담당자, 제보자 | 원인 분석, 해결, To-Be 동작 확인의 근거 |

- description에는 agent 구역이 없다. apply.py는 Task에만 결과 산출물 구역을 만든다(`scripts/src/jira/issue.py`의 `apply_issue`). 그래서 판정 객체의 items와 extra는 둘 다 빈 배열([])로 둔다.

---

## tldr 형식

```
h2. Summary

h3. 원인
* (발생 원인을 구체적으로. 근거: [PR 또는 comment 링크]. 기록이나 코드를 해석해 추론한 원인이면 앞에 "추정:")

h3. 해결
* (해결 내용을 구체적으로. 근거: [PR 링크], 배포: 버전 또는 날짜)
* (To-Be 동작 확인: YYYY-MM-DD 누가 확인, [comment 링크])
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|보류|검토 요청>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```

- 정리 모드에서는 마지막 항목에서 "검수 결과: …, " 부분을 뺀다.
- 원인이나 해결을 아직 모르면 그 항목에 "(미확인)"이라고 적는다.
- h3. 소제목 앞에는 빈 줄을 둔다. 빈 줄이 없으면 Jira가 소제목을 앞 목록 항목에 붙여 표시한다(INNO-34 시험 실행의 TL;DR, 2026-10-08).

---

## 링크 추가 요청 문장

사람 구역에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에 "문제(As-Is)에 [제목|URL] 추가 필요"로 적는다.
