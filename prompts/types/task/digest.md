# Task (정리 모드)

이 파일은 정리 모드에서 work type이 Task인 task에 적용하는 지시다.

완료 판단 기준과 items, extra 작성 방법은 `prompts/types/task/deliverables.md`를 읽는다.

## items와 extra
- items와 extra를 검수 모드와 같은 규칙으로 채운다(deliverables.md의 "예상 산출물과 결과 산출물의 대응",
  "verdict.json의 items와 extra"). apply.py가 이것으로 결과 산출물 구역을 다시 작성한다.
  - 지금까지의 기록으로 항목마다 달성(done: true) 또는 미달성(done: false)을 정한다. "진행 중" 같은 다른 상태는 없다.
  - 아직 끝나지 않은 항목은 done: false로 두고, result에 지금까지 된 것을, reason에 남은 일을 쓴다.
  - why는 기록에 사유가 있을 때만 쓴다(중단, 범위 변경 등). 아직 진행 중이라 사유가 없으면 빈 문자열로 둔다.
- precheck.json의 expected 항목마다 실제 결과를 대응시켜 items를 만들고, 예상에 없던 결과는 extra에 적는다.
- items와 extra는 deliverables.md의 "verdict.json의 items와 extra" 규칙대로 채운다.

## tldr.wiki 형식
```
h2. Summary
* (무엇을 했는지 한 줄. 근거: [PR 또는 comment 링크])
* (핵심 결과 1: 수치로. 모델, 데이터, 계산 방법을 함께. 예: pose-v3의 검증 데이터(val set, 1,200장) PCK@0.2(관절 위치 오차가 몸통 크기의 0.2배 이내인 비율) 0.83, 기준 모델 pose-v2 대비 +0.04. 근거: [리포트 링크])
* (핵심 결과 2 또는 산출물. 초과 달성이 있으면 한 항목으로. 근거: [PR 또는 comment 링크])
* {color:#6b778c}상태: <issue.json의 status>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```

## 링크 추가 요청
사람 구역(진행 배경, 예상 산출물 (Task 완료 기준))에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에 "진행 배경에 [제목|URL] 추가 필요"로 적는다.

## 출력 예 (verdict.json, 정리 모드, 진행 중인 Task)
```json
{
  "issueKey": "INNO-29",
  "mode": "digest",
  "verdict": null,
  "checks": [ { "id": "T1", "result": "pass", "detail": "예상 산출물 4개" } ],
  "items": [
    { "id": "1", "done": true,
      "result": "엣지 장비(Jetson Orin) 지연 측정 스크립트 작성, 기준 모델 pose-v3의 추론 지연 중앙값(p50: 요청 1,000회 중 50번째 백분위수) 121ms 재현",
      "evidence": "[측정 결과|https://example.com/reports/edge-latency-baseline]" },
    { "id": "2", "done": false,
      "result": "pose-v3 프루닝(가중치 제거) 20%, 30% 완료, 40%는 학습 중. 30% 모델은 Jetson Orin에서 p50 84ms(요청 1,000회), 검증 데이터(val set, 1,200장) PCK@0.2(관절 위치 오차가 몸통 크기의 0.2배 이내인 비율) 0.824",
      "evidence": "[comment 2026-09-17|https://<site>/browse/INNO-29?focusedCommentId=56987]",
      "why": "",
      "reason": "40% 결과를 포함한 비교표 comment 필요" }
  ],
  "extra": [],
  "requests": [ "[정정 comment 2026-09-17|https://<site>/browse/INNO-29?focusedCommentId=56990]대로 프루닝 30% 모델의 PCK@0.2를 0.819에서 0.824로 수정해 반영함" ]
}
```
