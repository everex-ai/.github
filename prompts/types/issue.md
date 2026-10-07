# Issue

이 파일은 work type이 Issue인 task의 구역, 완료 판단 기준, 출력 파일의 작성 형식, 검수 결과 기준을 정한다.
출력 파일은 verdict.json, tldr.wiki, comment.wiki다.

## 구역
- 사람 구역(제기자가 작성하는 description 구역): 이슈 유형(제안, 이슈, 기타 중 고르는 체크박스), 이슈 내용
- agent 구역(이 agent의 출력으로 채우는 구역)은 TL;DR(Jira task의 요약 필드)의 대응 결과다. description에는 작성하지 않는다.

## 완료 판단 기준
대응 결과가 근거와 함께 있어야 한다(대응 결과의 근거 (I3)).
- 대응 결과는 수용, 반려, 다른 task로 이관 중 하나이며 이유 한 줄이 있어야 한다.
- 근거는 결정을 적은 comment의 링크, 또는 이관해서 만든 task의 키다.
- precheck.json(`scripts/precheck.py`가 계산한 검사 결과 파일)의 이슈 유형 선택 (I1) 결과가 unknown이면, description.wiki의 이슈 유형 구역을 직접 읽어 선택된 항목이 있는지 판단한다.
  - unknown은 스크립트가 체크박스 상태를 텍스트로 확인할 수 없을 때의 결과다(`scripts/precheck.py`의 `check_issue_template`).

## tldr.wiki 형식
```
h2. Summary
* (대응 결과: 수용 / 반려 / task INNO-000으로 이관, 이유 한 줄)
* (근거: [comment 링크] 또는 이관해서 만든 task의 키)
* {color:#6b778c}상태: <issue.json의 status>, 검수 결과: <통과|검토 요청|보류>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```
- 정리 모드(digest 실행 모드. 지시는 `prompts/digest.md`에 있다)에서는 "검수 결과" 부분을 빼고 "상태"와 "갱신"만 적는다.
- 정리 모드에서 아직 대응이 정해지지 않았으면 첫 항목에 "검토 중: (지금까지의 논의 요지)"를 적는다.

## verdict.json의 items와 extra
- items와 extra는 둘 다 빈 배열([])로 둔다.
- checks(verdict.json의 검사별 결과 목록)에는 아래 검사를 넣는다.
  - I1과 이슈 내용 작성 (I2): precheck.json의 결과를 인용한다
  - I3: agent가 판단한다
  - 1년 뒤에도 이해 가능한 기록 (R2)
  - 개조식 작성 (R3)
  - stop 사유 comment (R4)

## comment.wiki (검수 모드, 결과 요약 부분만)
검수 모드(review 실행 모드)에서는 대응 결과가 무엇이고 어디에 근거가 있는지를 개조식 1–3줄로 적는다.
- 줄마다 근거 wiki 링크([comment 날짜|URL]) 또는 이관해서 만든 task의 키를 붙인다.

## 검수 결과
verdict(verdict.json의 검수 결과 키)에는 코드값을 적고, 괄호 안은 검수 결과 이름이다.
- pass(통과): 아래 검사가 모두 pass 또는 n/a다.
  - I1
  - I2
  - I3
  - R2의 링크 기준(agent 구역의 결과마다 확인 가능한 링크가 있음)
  - R4
- escalate(검토 요청): 대응 결과가 comment마다 다르게 적혀 있어 무엇이 최종인지 알 수 없다.
- fix(보류): 아래 검사 중 fail이 있다.
  - I1
  - I2
  - I3
  - R2의 링크 기준
  - R4
