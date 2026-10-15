# Bug 검수 단계

work type이 Bug인 ticket을 검수 모드에서 처리하는 단계를 정한다. 각 단계가 가리키는 절과 파일을 함께 적용한다.

---

## 검수 단계

1. description.wiki에서 사람 구역(기본 정보, 문제(As-Is), 개선(To-Be), 첨부 자료(필수))을 읽는다.
2. 아래 "완료 판단 기준"으로 사람의 기록에서 원인, 해결, To-Be 동작 확인을 찾는다.
3. work type 공통 파일의 "tldr 형식"으로 TL;DR의 원인과 해결을 작성한다. 마지막 항목의 검수 결과는 7단계에서 정한 값으로 채운다.
4. 사람 구역에 없는 유용한 링크가 있으면 work type 공통 파일의 "링크 추가 요청 문장"으로 requests에 적는다.
5. 아래 "검사 항목" 표의 검사와 `prompts/review.md`의 "공통 검사 항목"을 모두 checks에 넣는다.
   - 계산 주체가 스크립트인 검사(B1, B2, A2)는 precheck.json의 결과를 그대로 인용한다.
   - B3, B4는 CI agent가 판단한다. B3은 3단계에서 작성한 TL;DR로 판단한다.
6. 아래 "문서화 리뷰" 기준표로 B5, B6, B7을 판단해 checks에 넣고, `prompts/review.md`의 "문서화 리뷰 feedback 작성" 절에 따라 feedback을 작성한다.
7. 아래 "검수 결과" 절과 `prompts/review.md`의 "검수 결과 코드값의 공통 기준"으로 검수 결과 코드값(verdict.verdict)을 정한다.
8. 아래 "comment (결과 요약 부분만)" 절과 `prompts/review.md`의 "결과 요약(comment) 작성" 절에 따라 tasks[].comment를 작성한다.

---

## 완료 판단 기준

1. 원인이 근거 링크(PR 또는 comment)와 함께 있음: 원인과 해결의 근거 링크 (B3)
2. 해결이 근거 링크(PR, 배포 버전 또는 날짜)와 함께 있음: B3
3. To-Be 동작을 확인했다는 사람의 comment가 해결 이후에 있음: To-Be 동작 확인 comment (B4)
   - "확인함", "정상 동작 확인" 같은 문장이 담긴 comment 중 comments.json의 kind가 "human"인 것(사람이 작성한 comment)을 찾는다.
   - 없으면 fix(보류)로 가고 requests(검수 comment의 요청 절에 들어가는 요청 문장 목록)에 확인 comment를 요청한다.

---

## 검사 항목

| ID | 이름 | 검사 내용 | 계산 주체 | 검수 모드 영향 |
|---|---|---|---|---|
| B1 | 기본 정보와 문제(As-Is) 작성, 첨부 파일 | 기본 정보의 발생 기기/서비스, 발생 일자, 발생 장비, 발생 계정이 채워져 있고, 문제(As-Is)에 오류 동작 설명이 있고, Jira 첨부 파일이 1개 이상 있음 | 스크립트 | 실패 시 보류 |
| B2 | 개선(To-Be) 작성 | 개선(To-Be)에 정상 동작 설명이 있음 | 스크립트 | 실패 시 보류 |
| B3 | 원인과 해결의 근거 링크 | TL;DR의 원인과 해결에 각각 근거 링크(PR 또는 comment)가 있음 | CI agent | 실패 시 보류 |
| B4 | To-Be 동작 확인 comment | To-Be 동작을 확인했다는 사람의 comment가 해결 이후에 있음 | CI agent | 없으면 보류 |
| B5 | 문제(As-Is) 재현 정보 | 아래 "문서화 리뷰" 참고 | CI agent | 문서화 리뷰 정책 |
| B6 | 원인 분석 기록 | 아래 "문서화 리뷰" 참고 | CI agent | 문서화 리뷰 정책 |
| B7 | 해결 확인 기록 | 아래 "문서화 리뷰" 참고 | CI agent | 문서화 리뷰 정책 |
| A2 | 완료 기준의 사후 변경 | 가장 최근 request 전환 이후에 완료 기준 구역(개선(To-Be))이 바뀜 | 스크립트 | 실패 시 검토 요청 |

- 이 표 외에 `prompts/review.md`의 공통 검사 항목(R2, R3, R4)도 checks에 넣는다.

---

## 문서화 리뷰

| ID | pass 기준 | fail 예 |
|---|---|---|
| B5 문제(As-Is) 재현 정보 | 다른 사람이 재현할 수 있는 절차, 조건, 빈도가 문제(As-Is)에 있음 | "export 안 됨"처럼 현상만 있음 |
| B6 원인 분석 기록 | 원인을 찾은 과정(로그, 재현 결과)이 comment나 PR에 있음 | 원인 한 줄만 있고 확인 과정이 없음 |
| B7 해결 확인 기록 | 반영 버전이나 환경과 To-Be 확인 방법이 comment나 PR에 있음 | "확인함"만 있음 |

To-Be 동작 확인 comment (B4)는 확인 comment가 있는지를, B7은 그 기록이 1년 뒤에도 이해될 만큼 충실한지를 판단한다.

```json
"feedback": [
  { "id": "B5", "points": ["기본 정보에 발생 기기/서비스, 발생 일자, 발생 장비, 발생 계정이 모두 채워져 있음",
                           "문제(As-Is)에 'CSV export 안 됨'이라는 현상만 있고 어느 화면에서 무엇을 눌렀는지, 매번 일어나는지가 없음"],
    "request": "문제(As-Is)에 재현 절차(화면과 조작 순서), 발생 조건, 발생 빈도 작성 필요" },
  { "id": "B6", "points": ["원인 분석 comment([comment 2026-09-15|https://<site>/browse/INNO-31?focusedCommentId=10210])에 오류 로그와 재현 결과가 함께 있어 원인을 찾은 과정이 분명함"] },
  { "id": "B7", "points": ["해결 PR([PR #51 CSV export 인코딩 수정|https://github.com/everex-ai/repo/pull/51]) 병합 뒤 사람 comment([comment 2026-09-17|https://<site>/browse/INNO-31?focusedCommentId=10220])는 '확인함' 한 줄뿐이라 어느 버전, 어느 환경에서 To-Be 동작을 어떻게 확인했는지 알 수 없음"],
    "request": "To-Be 동작을 확인한 반영 버전, 확인 환경, 확인 방법 comment 필요" }
]
```

---

## comment (결과 요약 부분만)

원인과 해결이 무엇이었고 To-Be 동작 확인이 있었는지를 개조식 2–4줄로 적는다.
- 줄마다 근거 wiki 링크([PR 제목|URL] 또는 [comment 날짜|URL])를 붙인다.

---

## 검수 결과

- R3은 검수 결과에 영향이 없어 아래 조건에 넣지 않는다.
- pass(통과): 아래 검사가 모두 pass 또는 n/a다.
  - B1
  - B2
  - B3
  - B4
  - R2의 링크 기준(agent 구역의 결과마다 확인 가능한 링크가 있음)
  - R4
  - A2
- fix(보류): 아래 검사 중 fail이 있다. B1, B2의 fail은 제보자가 채우면 되므로 requests에 무엇이 비었는지 적는다.
  - B1
  - B2
  - B3
  - B4
  - R2의 링크 기준
  - R4
- escalate(검토 요청): 아래 중 하나에 해당한다.
  - 원인과 해결이 서로 맞지 않음
  - 재현 불가로 종료하려 함
  - A2가 fail
