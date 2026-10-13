# Issue 정리 단계

work type이 Issue인 ticket을 정리 모드에서 처리하는 단계를 정한다. 각 단계가 가리키는 절과 파일을 함께 적용한다.

---

## 정리 단계

1. description.wiki에서 사람 구역(이슈 유형, 이슈 내용)을 읽는다.
2. 사람의 기록에서 지금까지의 논의와 대응 결과를 찾는다.
3. work type 공통 파일의 "tldr 형식"으로 TL;DR을 작성한다. 아직 대응이 정해지지 않았으면 첫 항목에 "검토 중: (지금까지의 논의 요지)"를 적는다.
4. 사람 구역에 없는 유용한 링크가 있으면 work type 공통 파일의 "링크 추가 요청 문장"으로 requests에 적는다.
5. 판정 객체의 items와 extra는 빈 배열로 두고, 검수 결과 코드값(verdict.verdict)과 checks는 `prompts/digest.md`의 "정리 모드 규칙"대로 채운다.
