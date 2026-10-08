# Issue (정리 모드)

이 파일은 정리 모드에서 work type이 Issue인 task에 적용하는 지시다.

## items와 extra
- items와 extra는 빈 배열([])로 둔다.

## tldr.wiki 형식
```
h2. Summary
* (대응 결과: 수용 / 반려 / task INNO-000으로 이관, 이유 한 줄)
* (근거: [comment 링크] 또는 이관해서 만든 task의 키)
* {color:#6b778c}상태: <issue.json의 status>, 갱신: <meta.json의 collectedAt, YYYY-MM-DD HH:MM>{color}
```
- 정리 모드에서 아직 대응이 정해지지 않았으면 첫 항목에 "검토 중: (지금까지의 논의 요지)"를 적는다.

## 링크 추가 요청
사람 구역(이슈 유형, 이슈 내용)에 없는 유용한 링크(PR, 리포트, 데이터 위치)가 있으면 requests에 "이슈 내용에 [제목|URL] 추가 필요"로 적는다.
