# Issue 공통

work type이 Issue인 ticket의 구조와, 검수 모드와 정리 모드에 공통인 형식을 정한다.

---

## ticket 구조

| 위치 | 이름 | 작성 주체 | 내용 |
|---|---|---|---|
| description | 이슈 유형 | 제기자(사람 구역) | 제안, 이슈, 기타 중 고르는 체크박스(Jira 체크 목록) |
| description | 이슈 내용 | 제기자(사람 구역) | 왜 제기했는지와 결정이 필요한 것 |
| TL;DR 필드 | Summary(대응 결과) | CI agent(agent 구역) | 아래 "tldr 형식" |
| comment, sub-task, PR | 사람의 기록 | 담당자, 제기자 | 논의, 결정, 이관의 근거 |

- description에는 agent 구역이 없다. apply.py는 Task에만 결과 산출물 구역을 만든다(`scripts/apply.py`의 `apply_issue`). 그래서 판정 객체의 items와 extra는 둘 다 빈 배열([])로 둔다.

---

## tldr 형식

```
h2. Summary
* (대응 결과: 수용 / 반려 / ticket INNO-000으로 이관, 이유 한 줄)
* (근거: [comment 링크] 또는 이관해서 만든 ticket의 키)
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|보류|검토 요청>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```

- 정리 모드에서는 마지막 항목에서 "검수 결과: …, " 부분을 뺀다.

---

## 링크 추가 요청 문장

사람 구역에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에 "이슈 내용에 [제목|URL] 추가 필요"로 적는다.
