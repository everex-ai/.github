# Issue

## 구역
- 사람 구역: 이슈 유형(체크박스: 제안, 이슈, 기타), 이슈 내용. 제기자가 쓴다
- agent 구역: TL;DR (대응 결과). description은 쓰지 않는다

## 완료 판단 기준
대응 결과가 근거와 함께 있음 (I3). 대응 결과는 수용, 반려, 다른 task로 이관 중 하나이며 이유 한 줄이 있어야 한다.
근거는 결정을 적은 comment의 링크, 또는 이관해서 만든 task 키다.
precheck의 I1이 unknown이면 description.wiki의 이슈 유형 구역을 직접 읽어 선택된 항목이 있는지 판단한다.

## tldr.wiki 형식
```
h2. Summary
* (대응 결과: 수용 / 반려 / task INNO-000으로 이관, 이유 한 줄)
* (근거: [comment 링크] 또는 만들어진 task 키)
* {color:#6b778c}상태: <status>, 검수 결과: <통과|검토 요청|보류>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```
정리 모드에서는 "검수 결과" 부분을 빼고 "상태"와 "갱신"만 쓰며, 아직 대응이 정해지지 않았으면 첫 항목에
"검토 중: (지금까지의 논의 요지)"를 쓴다.

## verdict.json의 items와 extra
둘 다 빈 배열([])로 둔다. checks에 I1, I2(precheck 인용), I3(agent 판단), R2, R3, R4를 넣는다.

## comment.wiki (검수 모드, 결과 요약 부분만)
대응 결과가 무엇이고 어디에 근거가 있는지를 개조식 1–3줄로 쓰고, 줄마다 근거 wiki 링크([comment 날짜|URL] 또는 만들어진 task 키)를 붙인다.

## 검수 결과
verdict에는 코드값을 쓰고, 괄호 안은 검수 결과 이름이다.
- pass(통과): I1, I2, I3, R2(링크), R4가 모두 pass 또는 n/a
- escalate(검토 요청): 대응 결과가 comment마다 다르게 적혀 있어 무엇이 최종인지 알 수 없음
- fix(보류): I1, I2, I3, R2(링크 부재), R4 중 fail이 있음
