# Task 결과 산출물

work type이 Task인 ticket의 완료 판단 기준과, 예상 산출물에 결과를 대응시켜 판정 객체(verdict)의 items와 extra를 작성하는 방법을 정한다. CI agent가 검수 모드와 정리 모드에서 함께 읽는다.

---

## 완료 판단 기준

- precheck.json(`scripts/precheck.py`가 계산한 검사 결과 파일)의 expected 목록(예상 산출물 항목 목록)이 기준이다.
  - expected는 담당자가 예상 산출물 구역에 글머리표나 번호 목록으로 자유롭게 작성한 것을 항목별로 나누고 1부터 번호(id)를 붙인 것이다(`scripts/precheck.py`의 `expected_items`).
  - 그래서 항목의 형식은 제각각일 수 있다.
- items의 항목 수와 id는 expected와 정확히 같아야 한다(예상 산출물과 결과 산출물의 1:1 대응 (T3)). 빠뜨리거나 새 번호를 만들지 않는다.

---

## 예상 산출물과 결과 산출물의 대응

- 예상 산출물 항목마다 comment, sub-task, PR에서 실제 결과를 찾아 대응시키고, 달성(done: true) 또는 미달성(done: false)을 정한다.
- 대응은 의미로 한다.
  - 문구가 같은지가 아니라 그 예상 산출물 항목이 뜻하는 결과가 나왔는지를 확인한다.
  - 예: 예상 산출물이 "오류율 1% 미만"이고 comment에 "오류율 0.7% 달성, 리포트 링크"가 있으면 달성이다.
- 근거 없이 달성으로 판정하지 않는다. 결과가 있다는 말만 있고 확인할 링크나 수치가 없으면 미달성으로 두고, requests(검수 comment의 요청 절에 들어가는 요청 문장 목록)에 무엇을 남기면 되는지 "필요"로 맺는 문장으로 적는다.
- 예상 산출물 문구에 '개발' 또는 '적용'이 들어간 항목은 아래 "예상 산출물 표현별 달성 근거" 절의 기준으로 판정한다.
- 한 예상 산출물 항목에 결과가 여러 개면 그 항목의 result에 모아 적는다. 결과 하나가 예상 산출물 항목 여러 개에 걸치면 각 항목에 나눠 적는다.
- 예상 산출물 어느 항목에도 해당하지 않는 결과(예: 예상에 없던 도구, 추가 분석, 다른 팀에 넘긴 산출물)는 버리지 않고 extra(초과 달성)에 적는다.
  - 초과 달성 자체는 검수 결과에 영향을 주지 않는다(`scripts/apply.py`의 `recheck_verdict`). 사유 누락은 초과 달성·미달성 사유 (T8)로 따로 검사한다.
- 결과 산출물 구역과 검수 comment의 검사 표는 apply.py가 판정 객체(verdict)로 조립한다(`scripts/apply.py`의 `deliverables_wiki`, `check_table`). CI agent는 형식이 아니라 내용(대응, 판단, 근거)에 집중한다.

---

## 예상 산출물 표현별 달성 근거

예상 산출물 문구에 '개발' 또는 '적용'이 들어간 항목은 아래 표의 기록이 모두 있어야 달성(done: true)으로 판정한다. 검수 모드와 정리 모드에서 같다.
- 근거로 세는 기록은 무엇을 했는지와 어떻게 확인했는지가 적힌 comment, sub-task, PR 본문(prs.json)이다.
- 완료 기록("개발 완료", "적용 완료"처럼 끝났다는 말만 있는 기록)은 링크가 붙어 있어도 근거로 세지 않는다.
  - CI agent는 링크를 열 수 없으므로 링크가 가리키는 내용을 근거로 삼지 않는다. 허용 도구에 웹 조회 도구가 없기 때문이다(`.github/workflows/jira-doc.yml:98`의 `--allowedTools`).

| 표현 | 달성으로 판정하는 데 필요한 기록 |
|---|---|
| 개발 | 무엇을 만들었는지(기능, 입력과 출력, 범위). 테스트로 검증한 방법과 결과. 결과를 확인할 위치(PR, 코드 위치) |
| 적용 | 적용하며 고려한 점. 모의 적용(운영에 반영하기 전에 스테이징 환경이나 일부 데이터로 시험 적용함) 방법과 결과. 실제 적용을 마친 기록(대상, 날짜, 적용 뒤 확인 결과) |

- 표의 기록 중 하나라도 없으면 아래처럼 적는다.
  - done은 false로 두고, why는 "근거 부족으로 확인 불가"로 적는다.
  - reason에는 빠진 기록을 "필요"로 맺는 한 문장으로 적는다. 예: "캐시 기능을 어떤 테스트로 검증했는지와 그 결과 comment 필요"
  - 같은 문장을 requests에도 넣는다.
    - 이 항목 때문에 apply.py가 검수 결과를 보류로 정한다(`scripts/apply.py`의 `recheck_verdict`).
    - 문서화 리뷰를 검수 결과에 넣지 않는 관찰 모드에서도 보류가 된다.
- 다른 표현의 항목은 위 표를 적용하지 않고 앞 절 "예상 산출물과 결과 산출물의 대응"의 규칙대로 판정한다.
  - 무엇을 했는지와 결과가 기록에 있으면 달성으로 판정한다. 확인 방법이 빠졌으면 requests에 요청 문장만 적는다.

---

## 판정 객체(verdict)의 items와 extra

결과 산출물 구역은 `scripts/apply.py`의 `deliverables_wiki`가 items와 extra로 만든다.

```json
"items": [
  { "id": "1", "done": true,
    "result": "export 오류 재현 테스트 3건 추가, 2026-09-11 병합",
    "evidence": "[PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]" },
  { "id": "2", "done": false,
    "result": "comment에 모델 v2의 검증 데이터(val set) export 오류율 0.7%라는 수치만 있고 데이터 규모, 계산 방법, 리포트 링크 없음",
    "evidence": "[comment 2026-09-12|https://<site>/browse/INNO-17?focusedCommentId=10123]",
    "why": "근거 부족으로 확인 불가",
    "reason": "평가 리포트 링크, 데이터 규모, 오류율 계산 방법 comment 필요" },
  { "id": "3", "done": false,
    "result": "ONNX(신경망 모델 교환 형식) 변환 스크립트 초안까지 작성",
    "why": "배포 대상 기기 사양이 확정되지 않아 변환 옵션을 정하지 못함([comment 2026-09-13|https://<site>/browse/INNO-17?focusedCommentId=10140])",
    "evidence": "[comment 2026-09-13|https://<site>/browse/INNO-17?focusedCommentId=10140]",
    "reason": "기기 사양 확정 뒤 후속 task 키 comment 필요" }
],
"extra": [
  { "result": "export 오류율을 매일 집계하는 대시보드 추가",
    "why": "재발 여부를 매일 확인하자는 팀 회의 결정([comment 2026-09-10|https://<site>/browse/INNO-17?focusedCommentId=10101])",
    "evidence": "[PR #45 오류율 대시보드|https://github.com/everex-ai/repo/pull/45]" }
]
```

- result는 개조식 한 줄로 적고, 수치와 날짜가 있으면 넣는다. 미달성이면 지금 어디까지 되었는지를 적는다.
  - 수치와 추정은 `prompts/rules.md`의 "수치와 추정" 절에 따라 적는다. 수치의 데이터나 계산 방법이 기록에 없으면 없다고 적는다.
- evidence는 wiki 링크 형식 [제목|URL]로 적고, 여러 개면 쉼표로 잇는다.
  - 근거가 없으면 키를 빼거나 빈 문자열로 둔다.
- why는 미달성 항목에서는 왜 달성하지 못했는지(예: "라벨 재수집이 다음 분기로 밀림"), extra에서는 담당자가 예상에 없던 결과를 낸 이유를 구체적으로 적는다.
  - 사람의 기록(comment, sub-task, PR)에 있는 사유만 적고, 출처를 wiki 링크로 괄호 안에 붙인다(예: "([comment 2026-09-13|URL])"). 지어내지 않는다.
  - 결과는 있으나 근거가 없어 확인하지 못한 미달성 항목은 "근거 부족으로 확인 불가"로 적는다.
  - 기록에 사유가 없으면 빈 문자열로 둔다. 이때 apply.py는 "사유 미기재"로 표시하고 T8을 fail로 계산한다(`scripts/apply.py`의 `deliverables_wiki`, `check_t8`).
- reason은 미달성 항목에서 담당자가 무엇을 더 남기면 달성이 되는지를 "필요"로 맺는 한 문장으로 적는다.
  - 같은 내용을 requests에도 넣는다.
  - 결과 산출물 구역에는 "요청:" 줄로 나간다(`scripts/apply.py`의 `deliverables_wiki`).
