# Bug

## 구역
- 사람 구역: 현황(AS-IS), 개선(To-be), 첨부 자료. 제보자가 쓴다. agent는 채워졌는지만 검사한다(B1, B2는 precheck에 있음)
- agent 구역: TL;DR (원인, 해결). description은 쓰지 않으므로 deliverables.wiki를 만들지 않는다

## 완료 판단 기준
1. 원인이 근거 링크(PR 또는 comment)와 함께 있음 (B3)
2. 해결이 근거 링크(PR, 배포 버전 또는 날짜)와 함께 있음 (B3)
3. To-be 동작을 확인했다는 사람의 comment가 해결 이후에 있음 (B4). "확인함", "정상 동작 확인" 같은
   문장이 담긴 kind가 human인 comment를 찾는다. 없으면 fix로 가고 requests에 확인 comment를 요청한다

## tldr.wiki 형식
```
h2. Summary
원인
* (발생 원인을 구체적으로. 근거: [PR 또는 comment 링크])
해결
* (해결 내용을 구체적으로. 근거: [PR 링크], 배포: 버전 또는 날짜)
* (To-be 동작 확인: YYYY-MM-DD 누가 확인, [comment 링크])
* {color:#6b778c}상태: <status>, 판정: <통과|보완 요청|보류>, 갱신: YYYY-MM-DD HH:MM{color}
```
원인이나 해결을 아직 모르면 그 항목에 "(미확인)"이라고 쓰고 정리 모드에서는 지금까지 시도한 것을 한 줄 덧붙인다.
정리 모드에서는 "판정" 부분을 빼고 "상태"와 "갱신"만 쓴다.

## verdict.json의 acStatus
빈 배열([])로 둔다. checks에 B1, B2(precheck 인용), B3, B4(agent 판단), R2, R4, A2를 넣는다.

## 판정
- pass: B1, B2, B3, B4, R4, A2가 모두 pass 또는 n/a
- fix: 위 항목 중 fail이 있음 (B1, B2 fail은 제보자가 채우면 되므로 requests에 무엇이 비었는지 적는다)
- escalate: 원인과 해결이 서로 맞지 않음, 재현 불가로 종료하려 함, A2가 fail
