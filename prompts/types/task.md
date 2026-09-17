# Task

## 구역
- 사람 구역: 진행 배경, 예상 산출물 (Task 완료 기준)
- agent 구역: 결과 산출물 (Task 완료 기준과 1:1 대응), TL;DR

## 완료 판단 기준
precheck.json의 expected 목록이 기준이다. 담당자가 예상 산출물 구역에 글머리표나 번호 목록으로 자유롭게 쓴 것을
스크립트가 항목별로 나누고 1부터 번호(id)를 붙인 것이므로, 형식은 제각각일 수 있다.
항목마다 comment, sub-task, PR에서 실제 결과를 찾아 의미로 대응시키고 달성(done: true) 또는 미달성(done: false)을
정한다. 근거가 없는 항목은 달성으로 쓰지 않는다.
items의 항목 수와 id는 expected와 정확히 같아야 한다(T3). 빠뜨리거나 새 번호를 만들지 않는다.
예상에 없던 결과는 extra(초과 달성)에 적는다. 초과 달성은 판정을 바꾸지 않지만 기록으로 남긴다.

## verdict.json의 items와 extra (결과 산출물 구역은 apply.py가 이것으로 만든다)
```json
"items": [
  { "id": "1", "done": true,
    "result": "export 오류 재현 테스트 3건 추가, 2026-09-11 병합",
    "evidence": "[PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]" },
  { "id": "2", "done": false,
    "result": "comment에 val set 오류율 0.7%라는 수치만 있고 리포트 링크 없음",
    "evidence": "[comment 2026-09-12|https://<site>/browse/INNO-17?focusedCommentId=10123]",
    "why": "근거 부족으로 확인 불가",
    "reason": "평가 리포트 링크를 comment로 남길 것" },
  { "id": "3", "done": false,
    "result": "ONNX 변환 스크립트 초안까지 작성",
    "why": "배포 대상 기기 사양이 확정되지 않아 변환 옵션을 정하지 못함 (comment 2026-09-13)",
    "evidence": "[comment 2026-09-13|https://<site>/browse/INNO-17?focusedCommentId=10140]",
    "reason": "기기 사양 확정 후 후속 task 키를 comment로 남길 것" }
],
"extra": [
  { "result": "export 오류율을 매일 집계하는 대시보드 추가 (예상에 없던 산출물)",
    "why": "재발 여부를 매일 확인하자는 팀 회의 결정 (comment 2026-09-10)",
    "evidence": "[PR #45|https://github.com/everex-ai/repo/pull/45]" }
]
```
- result는 개조식 한 줄로, 수치와 날짜가 있으면 넣는다. 미달성이면 지금 어디까지 되었는지를 쓴다.
- evidence는 wiki 링크 형식 [제목|URL]로 쓰고 여러 개면 쉼표로 잇는다. 없으면 키를 빼거나 빈 문자열로 둔다.
- why는 미달성 항목에서는 왜 달성하지 못했는지, extra에서는 왜 추가했는지를 쓴다. 사람의 기록에 있는 사유만 쓰고
  출처(comment 날짜 등)를 괄호로 붙인다. 기록에 없으면 빈 문자열로 둔다(apply.py가 "사유 미기재"로 표시하고 T8을 fail로 계산).
- reason은 미달성 항목에서 담당자가 무엇을 더 남기면 달성이 되는지를 쓴다. 같은 내용을 requests에도 넣는다.

apply.py가 만드는 결과 산출물 구역의 모양은 다음과 같다 (참고용. agent가 쓰지 않는다).
```
# *재현 테스트 추가* / ✅ 달성
#* 결과: export 오류 재현 테스트 3건 추가, 2026-09-11 병합
#* 근거: [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]
# *오류율 1% 미만* / ❌ 미달성
#* 결과: comment에 val set 오류율 0.7%라는 수치만 있고 리포트 링크 없음
#* 미달성 사유: 근거 부족으로 확인 불가
#* 근거: [comment 2026-09-12|...]
#* 필요한 것: 평가 리포트 링크를 comment로 남길 것
# *ONNX 변환* / ❌ 미달성
#* 결과: ONNX 변환 스크립트 초안까지 작성
#* 미달성 사유: 배포 대상 기기 사양이 확정되지 않아 변환 옵션을 정하지 못함 (comment 2026-09-13)
#* 근거: [comment 2026-09-13|...]
#* 필요한 것: 기기 사양 확정 후 후속 task 키를 comment로 남길 것

*초과 달성* (예상 산출물에 없었지만 추가로 나온 결과)
* export 오류율을 매일 집계하는 대시보드 추가
** 추가 사유: 재발 여부를 매일 확인하자는 팀 회의 결정 (comment 2026-09-10)
** 근거: [PR #45|...]
```

## verdict.json의 feedback (문서화 리뷰, 검수 모드만)
rules.md의 "문서화 리뷰" 기준으로 T4~T7을 checks에 넣고, 항목마다 feedback을 쓴다.
```json
"feedback": [
  { "id": "T4", "points": ["평가 지표를 바꾸게 된 계기(고객 불만 2건)가 적혀 있어 왜가 분명함"] },
  { "id": "T5", "points": ["1번 항목은 결과 하나로 잘 나뉨",
                           "3번 '데이터 정제 및 모델 재학습'은 따로 달성 여부가 갈리는 결과 두 개가 묶여 있음"],
    "request": "예상 산출물 3번을 '데이터 정제'와 '모델 재학습' 두 항목으로 나누고 각각 끝났을 때의 기준을 적어 주세요" },
  { "id": "T6", "points": ["작업 8영업일 동안 comment 1건(request 당일)뿐이라 중간 결과와 방향 변경 이유를 알 수 없음"],
    "request": "진행 중 나온 중간 수치와 방향을 바꾼 이유를 comment로 남겨 주세요" },
  { "id": "T7", "points": ["INNO-18은 결과 comment와 PR이 있음", "INNO-19는 description이 비어 있어 무엇을 했는지 알 수 없음"],
    "request": "INNO-19 description에 무엇을 왜 했는지 적어 주세요" }
]
```
- 문서화 리뷰의 보완 요청은 requests가 아니라 feedback[].request에만 쓴다. comment.wiki에도 넣지 않는다.

## tldr.wiki 형식
```
h2. Summary
* (무엇을 했는지 한 줄)
* (핵심 결과 1: 수치로. 예: val set PCK@0.2 0.83, baseline 대비 +0.04)
* (핵심 결과 2 또는 산출물. 초과 달성이 있으면 한 항목으로)
* {color:#6b778c}상태: <issue.json의 status>, 판정: <통과|보완 요청|보류>, 갱신: YYYY-MM-DD HH:MM{color}
```
정리 모드에서는 "판정" 부분을 빼고 "상태"와 "갱신"만 쓴다.

## comment.wiki (검수 모드, 결과 요약 부분만)
```
* 예상 산출물 2개 중 1개 달성. 재현 테스트는 PR #42로 병합됨
* 오류율 항목은 수치(0.7%)는 있으나 확인할 리포트 링크가 없어 미달성으로 둠
* 예상에 없던 오류율 대시보드(PR #45)는 초과 달성으로 기록함
```

## 판정
T4~T8(문서화 리뷰)은 판정에 넣지 않는다. apply.py가 모드에 따라 적용한다(관찰 모드: 팀장에게 공유, 게이트 모드: 보완 요청).
- pass: items가 모두 done이고 R2(링크), R4, A2 등 필수 검사가 통과
- fix: done이 false인 항목이 있거나 T1, T2, R4가 fail
- escalate: 예상 산출물이 모호해 대응할 수 없음, 담당자가 미달성 항목을 정당한 사유로 종료하려 함, A2가 fail
