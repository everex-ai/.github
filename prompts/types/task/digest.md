# Task 정리 단계

work type이 Task인 ticket을 정리 모드에서 처리하는 단계를 정한다. 각 단계가 가리키는 절과 파일을 함께 적용한다.

---

## 정리 단계

1. description.wiki에서 사람 구역(진행 배경, 예상 산출물)을 읽는다.
2. `prompts/types/task/deliverables.md`의 "예상 산출물과 결과 산출물의 대응"과 "판정 객체(verdict)의 items와 extra" 절에 따라 items와 extra를 작성한다. 정리 모드에서는 아래를 함께 적용한다.
   - 지금까지의 기록으로 항목마다 달성(done: true) 또는 미달성(done: false)을 정한다. "진행 중" 같은 다른 상태는 없다.
   - 아직 끝나지 않은 항목은 done: false로 두고, result에 지금까지 된 것을, reason에 달성하려면 담당자가 무엇을 더 남겨야 하는지를 적는다.
   - why는 기록에 사유가 있을 때만 적는다(중단, 범위 변경 등). 아직 진행 중이라 사유가 없으면 빈 문자열로 둔다.
3. work type 공통 파일의 "tldr 형식"으로 TL;DR을 작성한다.
4. 사람 구역에 없는 유용한 링크가 있으면 work type 공통 파일의 "링크 추가 요청 문장"으로 requests에 적는다.
5. 판정 객체의 검수 결과 코드값(verdict.verdict)과 checks는 `prompts/digest.md`의 "정리 모드 규칙"대로 채운다.

---

## 출력 예 (구조화 출력, 진행 중인 Task)

```json
{
  "tasks": [
    {
      "issueKey": "INNO-29",
      "verdict": {
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
      },
      "tldr": "h2. Summary\n* (무엇을 했는지 한 줄. 근거: [PR 또는 comment 링크])\n* {color:#6b778c}상태: In Progress, 갱신: 2026-09-29 09:00{color}"
    }
  ],
  "failed": []
}
```
