# Bug (정리 모드)

이 파일은 정리 모드에서 work type이 Bug인 task에 적용하는 지시다.

## items와 extra
- items와 extra는 빈 배열([])로 둔다.

## tldr.wiki 형식
```
h2. Summary
h3. 원인
* (발생 원인을 구체적으로. 근거: [PR 또는 comment 링크]. 기록이나 코드를 해석해 추론한 원인이면 앞에 "추정:")
h3. 해결
* (해결 내용을 구체적으로. 근거: [PR 링크], 배포: 버전 또는 날짜)
* (To-Be 동작 확인: YYYY-MM-DD 누가 확인, [comment 링크])
* {color:#6b778c}상태: <issue.json의 status>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```
- 원인이나 해결을 아직 모르면 그 항목에 "(미확인)"이라고 적는다.
  - 정리 모드(digest 실행 모드)에서는 지금까지 시도한 것을 한 줄 추가한다.

## 링크 추가 요청
사람 구역(기본 정보, 문제(As-Is), 개선(To-Be), 첨부 자료(필수))에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에 "문제(As-Is)에 [제목|URL] 추가 필요"로 적는다.
