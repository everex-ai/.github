# Task

이 파일은 work type이 Task인 task의 구역, 완료 판단 기준, 출력 파일의 작성 형식, 검수 결과 기준을 정한다.
출력 파일은 verdict.json, tldr.wiki, comment.wiki다.

## 구역
- 사람 구역(담당자가 작성하는 description 구역): 진행 배경, 예상 산출물 (Task 완료 기준)
- agent 구역(이 agent의 출력으로 채우는 구역): 결과 산출물 (Task 완료 기준과 1:1 대응), TL;DR(Jira task의 요약 필드)

## 완료 판단 기준
precheck.json(`scripts/precheck.py`가 계산한 검사 결과 파일)의 expected 목록(예상 산출물 항목 목록)이 기준이다.
- expected는 담당자가 예상 산출물 구역에 글머리표나 번호 목록으로 자유롭게 작성한 것을 항목별로 나누고 1부터 번호(id)를 붙인 것이다(`scripts/precheck.py`의 `expected_items`).
  - 그래서 항목의 형식은 제각각일 수 있다.
- 항목마다 comment, sub-task, PR에서 실제 결과를 찾아 의미로 대응시키고, 달성(done: true) 또는 미달성(done: false)을 정한다.
  - 근거가 없는 항목은 달성으로 판정하지 않는다.
- items의 항목 수와 id는 expected와 정확히 같아야 한다(예상 산출물과 결과 산출물의 1:1 대응 (T3)). 빠뜨리거나 새 번호를 만들지 않는다.
- 예상에 없던 결과는 extra(초과 달성)에 적는다.
  - 초과 달성은 검수 결과를 바꾸지 않지만 기록으로 남긴다.

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
- 정리 모드(digest 실행 모드. 지시는 `prompts/digest.md`에 있다)에서도 items와 extra를 이 규칙대로 채운다.
  - 아직 끝나지 않은 항목은 done: false로 두고, result에 지금까지 된 것, reason에 남은 일을 적는다.
  - why는 기록에 사유가 있을 때만 적는다. 없으면 apply.py가 사유 줄을 생략한다(`scripts/apply.py`의 `deliverables_wiki`).

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

## verdict.json의 feedback (문서화 리뷰, 검수 모드만)
검수 모드(review 실행 모드)에서는 rules.md의 "문서화 리뷰" 기준으로 아래 네 검사를 checks(verdict.json의 검사별 결과 목록)에 넣고, 항목마다 feedback을 작성한다.
- 진행 배경 충실도 (T4)
- 예상 산출물 분할 단위 (T5)
- 진행 기록 comment (T6)
- sub-task 기록 (T7)
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
```
h2. Summary
* (무엇을 했는지 한 줄. 근거: [PR 또는 comment 링크])
* (핵심 결과 1: 수치로. 모델, 데이터, 계산 방법을 함께. 예: pose-v3의 검증 데이터(val set, 1,200장) PCK@0.2(관절 위치 오차가 몸통 크기의 0.2배 이내인 비율) 0.83, 기준 모델 pose-v2 대비 +0.04. 근거: [리포트 링크])
* (핵심 결과 2 또는 산출물. 초과 달성이 있으면 한 항목으로. 근거: [PR 또는 comment 링크])
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|검토 요청|보류>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```
정리 모드에서는 "검수 결과" 부분을 빼고 "상태"와 "갱신"만 적는다.

## comment.wiki (검수 모드, 결과 요약 부분만)
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
