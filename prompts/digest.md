# 정리 모드 절차

정리 모드는 ticket의 지금까지의 기록으로 TL;DR과 결과 산출물 구역(Task만)을 다시 작성한다.
진행 중인 ticket을 중간 정리하고 "정정:" comment(이전 agent 구역을 고치라는 사람의 comment)를 반영하며, 검수 결과는 정하지 않는다.

---

## 정리 순서

1. `ctx/` 아래 ticket 폴더를 폴더 이름 순서대로 하나씩 처리한다. 한 ticket의 출력이 끝나면 다음 ticket으로 넘어간다.
2. ticket마다 tldr.txt(현재 TL;DR)를 먼저 읽는다. CI agent가 지난 실행에서 작성한 것이면 그것을 바탕으로 새 기록만 반영해 갱신한다.
3. `prompts/rules.md`의 "공통 처리 순서"를 수행한다. 마지막 단계에서 work type 정리 파일(`prompts/types/<work type>/digest.md`)의 단계를 수행할 때 아래 "정리 모드 규칙"을 함께 적용한다.
4. `prompts/rules.md`의 "출력" 절 형식으로 결과를 낸다. 처리하지 못한 ticket은 failed[]에 issueKey와 이유(reason)로 적는다.

---

## 정리 모드 규칙

- 판정 객체
  - 검수 결과 코드값(verdict.verdict)은 null로 둔다.
  - checks에는 precheck.json의 checks를 그대로 옮긴다.
  - 문서화 리뷰는 하지 않으며 feedback은 작성하지 않는다.
  - items와 extra는 work type 정리 파일대로 채운다.
- tasks[].comment는 내지 않는다.
- requests에는 "정정:" comment를 반영했는지와 사람 구역에 추가를 권장할 링크만 적는다.
  - "정정:" comment 반영은 요청이 아니므로 "반영함"으로 맺는다.
  - 링크 추가는 work type 공통 파일(`prompts/types/<work type>/common.md`)의 링크 추가 요청 문장대로 "필요"로 맺는다.
- TL;DR에는 지금까지의 진행 결과를 적는다. 완료되지 않은 ticket이므로 결과 수치가 없으면 무엇을 시도했고 어디까지 왔는지를 적고, 다음에 할 작업이나 막힌 것을 한 줄 넣는다.
