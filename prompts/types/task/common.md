# Task 공통

work type이 Task인 ticket의 구조와, 검수 모드와 정리 모드에 공통인 형식을 정한다.
완료 판단 기준과 items, extra 작성 방법은 `prompts/types/task/deliverables.md`에 있다. CI agent는 두 실행 모드 모두 이 common.md와 함께 deliverables.md를 읽는다.

---

## ticket 구조

| 위치 | 이름 | 작성 주체 | 내용 |
|---|---|---|---|
| description | 진행 배경 | 담당자(사람 구역) | 왜 이 일을 하는지와 맥락 |
| description | 예상 산출물 (Task 완료 기준) | 담당자(사람 구역) | ticket이 끝났을 때 나와야 하는 것의 목록. precheck.json의 expected가 이 목록을 항목별로 나눈 것이다(`scripts/precheck.py`의 `expected_items`) |
| description | 결과 산출물 (Task 완료 기준과 1:1 대응) | CI agent(agent 구역) | apply.py가 판정 객체의 items와 extra로 만든다(`scripts/apply.py`의 `deliverables_wiki`) |
| TL;DR 필드 | Summary | CI agent(agent 구역) | 아래 "tldr 형식" |
| comment, sub-task, PR | 사람의 기록 | 담당자 | 결과의 근거 |

---

## tldr 형식

```
h2. Summary
* (무엇을 했는지 한 줄. 근거: [PR 또는 comment 링크])
* (핵심 결과 1: 수치로. 모델, 데이터, 계산 방법을 함께. 예: pose-v3의 검증 데이터(val set, 1,200장) PCK@0.2(관절 위치 오차가 몸통 크기의 0.2배 이내인 비율) 0.83, 기준 모델 pose-v2 대비 +0.04. 근거: [리포트 링크])
* (핵심 결과 2 또는 산출물. 초과 달성이 있으면 한 항목으로. 근거: [PR 또는 comment 링크])
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|보류|검토 요청>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```

- 정리 모드에서는 마지막 항목에서 "검수 결과: …, " 부분을 뺀다.

---

## 링크 추가 요청 문장

사람 구역에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에 "진행 배경에 [제목|URL] 추가 필요"로 적는다.
