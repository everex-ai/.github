# Task 결과 산출물(검수 모드, 정리 모드 공통)

이 파일은 work type이 Task인 task의 완료 판단 기준과, 예상 산출물에 결과를 대응시켜 verdict.json의 items와 extra를 작성하는 방법을 정한다. CI agent가 검수 모드와 정리 모드에서 함께 읽는다.

## 완료 판단 기준
precheck.json(`scripts/precheck.py`가 계산한 검사 결과 파일)의 expected 목록(예상 산출물 항목 목록)이 기준이다.
- expected는 담당자가 예상 산출물 구역에 글머리표나 번호 목록으로 자유롭게 작성한 것을 항목별로 나누고 1부터 번호(id)를 붙인 것이다(`scripts/precheck.py`의 `expected_items`).
  - 그래서 항목의 형식은 제각각일 수 있다.
- 항목마다 comment, sub-task, PR에서 실제 결과를 찾아 의미로 대응시키고, 달성(done: true) 또는 미달성(done: false)을 정한다.
  - 근거가 없는 항목은 달성으로 판정하지 않는다.
- items의 항목 수와 id는 expected와 정확히 같아야 한다(예상 산출물과 결과 산출물의 1:1 대응 (T3)). 빠뜨리거나 새 번호를 만들지 않는다.
- 예상에 없던 결과는 extra(초과 달성)에 적는다.
  - 초과 달성은 검수 결과를 바꾸지 않지만 기록으로 남긴다.

## 예상 산출물과 결과 산출물의 대응 (Task)

예상 산출물은 담당자가 글머리표 또는 번호 목록으로 자유롭게 쓴다. 정해진 형식은 없다.
precheck.json의 `expected`가 그 목록을 항목별로 나누고 1부터 번호(id)를 붙인 것이다. agent는 이 번호를 기준으로
항목마다 comment, sub-task, PR에서 실제 결과를 찾아 대응시키고 달성 여부를 판단한다.

- 대응은 의미로 한다. 예상 산출물이 "오류율 1% 미만"이고 comment에 "오류율 0.7% 달성, 리포트 링크"가 있으면 달성이다.
  문구가 같은지가 아니라 그 항목이 뜻하는 결과가 나왔는지를 본다.
- 근거 없이 달성으로 쓰지 않는다. 결과가 있다는 말만 있고 확인할 링크나 수치가 없으면 미달성으로 두고 requests에 무엇을 남기면 되는지 "필요"로 맺는 문장으로 적는다.
- 미달성 항목은 `why`에 왜 달성하지 못했는지를 구체적으로 쓴다(예: "라벨 재수집이 다음 분기로 밀림").
  사람의 기록(comment, sub-task, PR)에 있는 사유만 쓰고, 없으면 빈 문자열로 둔다. 결과는 있으나 근거가 없어
  확인하지 못한 경우는 "근거 부족으로 확인 불가"로 쓴다.
- 예상 산출물 어느 항목에도 해당하지 않는 결과(예: 예상에 없던 도구, 추가 분석, 다른 팀에 넘긴 산출물)는 버리지 않고
  `extra`(초과 달성)에 따로 적고, `why`에 왜 추가했는지를 쓴다. 사유가 기록에 없으면 빈 문자열로 둔다(지어내지 않는다).
  초과 달성 자체는 검수 결과에 영향을 주지 않는다. 사유 누락은 T8로 따로 본다.
- 한 예상 항목에 결과가 여러 개면 한 항목의 result에 모아 쓴다. 결과 하나가 예상 항목 여러 개에 걸치면 각 항목에 나눠 쓴다.
- 결과 산출물 구역과 검수 comment의 검사 표는 apply.py가 verdict.json으로 조립한다. agent는 형식이 아니라 내용(대응, 판단, 근거)에 집중한다.

## verdict.json의 items와 extra
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
  - 수치에는 무엇의 값인지(모델, 대상), 데이터, 계산 방법을 함께 적고, 기록에 없으면 없다고 적는다.
  - 기록을 해석해서 추론한 내용은 앞에 "추정:"을 붙인다.
- evidence는 wiki 링크 형식 [제목|URL]로 적고, 여러 개면 쉼표로 잇는다.
  - 근거가 없으면 키를 빼거나 빈 문자열로 둔다.
- why는 미달성 항목에서는 왜 달성하지 못했는지, extra에서는 왜 추가했는지를 적는다.
  - 사람의 기록(comment, sub-task, PR)에 있는 사유만 적고, 출처를 wiki 링크로 괄호 안에 붙인다(예: "([comment 2026-09-13|URL])").
  - 기록에 사유가 없으면 빈 문자열로 둔다. 이때 apply.py는 "사유 미기재"로 표시하고 초과 달성·미달성 사유 (T8)를 fail로 계산한다(`scripts/apply.py`의 `deliverables_wiki`, `check_t8`).
- reason은 미달성 항목에서 담당자가 무엇을 더 남기면 달성이 되는지를 "필요"로 맺는 한 문장으로 적는다.
  - 같은 내용을 requests(검수 comment의 요청 절에 들어가는 요청 문장 목록)에도 넣는다.
  - 결과 산출물 구역에는 "요청:" 줄로 나간다(`scripts/apply.py`의 `deliverables_wiki`).

`scripts/apply.py`의 `deliverables_wiki`가 만드는 결과 산출물 구역의 모양은 다음과 같다. 참고용이며 agent는 이 구역을 직접 작성하지 않는다.
```
예상 산출물 3개 중 1개 달성, 초과 달성 1건

# *재현 테스트 추가* / ✅ 달성
#* 결과: export 오류 재현 테스트 3건 추가, 2026-09-11 병합
#* 근거: [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]
# *오류율 1% 미만* / ❌ 미달성
#* 결과: comment에 모델 v2의 검증 데이터(val set) export 오류율 0.7%라는 수치만 있고 데이터 규모, 계산 방법, 리포트 링크 없음
#* 미달성 사유: 근거 부족으로 확인 불가
#* 근거: [comment 2026-09-12|...]
#* 요청: 평가 리포트 링크, 데이터 규모, 오류율 계산 방법 comment 필요
# *ONNX 변환* / ❌ 미달성
#* 결과: ONNX(신경망 모델 교환 형식) 변환 스크립트 초안까지 작성
#* 미달성 사유: 배포 대상 기기 사양이 확정되지 않아 변환 옵션을 정하지 못함([comment 2026-09-13|...])
#* 근거: [comment 2026-09-13|...]
#* 요청: 기기 사양 확정 뒤 후속 task 키 comment 필요

*초과 달성* (예상 산출물에 없었지만 추가로 나온 결과)
* export 오류율을 매일 집계하는 대시보드 추가
** 추가 사유: 재발 여부를 매일 확인하자는 팀 회의 결정([comment 2026-09-10|...])
** 근거: [PR #45 오류율 대시보드|...]
```
