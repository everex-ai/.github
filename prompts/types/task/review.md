# Task 검수 단계

work type이 Task인 ticket을 검수 모드에서 처리하는 단계를 정한다. 각 단계가 가리키는 절과 파일을 함께 적용한다.

---

## 검수 단계

1. description.wiki에서 사람 구역(진행 배경, 예상 산출물)을 읽고, `prompts/types/task/deliverables.md`의 "완료 판단 기준"으로 precheck.json의 expected(예상 산출물 항목 목록)를 확인한다.
   - expected가 비어 있으면 requests에 "예상 산출물(Task 완료 기준)에 이 ticket이 끝났을 때 나와야 하는 것의 목록 작성 필요"를 넣는다.
2. `deliverables.md`의 "예상 산출물과 결과 산출물의 대응"과 "판정 객체(verdict)의 items와 extra" 절에 따라 items와 extra를 작성한다.
3. work type 공통 파일의 "tldr 형식"으로 TL;DR을 작성한다. 결과는 가급적 정량 수치로 3–5개 항목으로 적는다. 마지막 항목의 검수 결과는 7단계에서 정한 값으로 채운다.
4. 사람 구역에 없는 유용한 링크가 있으면 work type 공통 파일의 "링크 추가 요청 문장"으로 requests에 적는다.
5. 아래 "검사 항목" 표의 검사와 `prompts/review.md`의 "공통 검사 항목"을 checks에 넣는다.
   - 계산 주체가 스크립트인 검사(T1, T2, A2)는 precheck.json의 결과를 그대로 인용한다.
   - T3, T8은 apply.py가 계산하므로 넣지 않는다(`scripts/src/jira/issue.py`의 `apply_issue`, `scripts/src/jira/verdict.py`의 `check_t8`).
6. 아래 "문서화 리뷰" 기준표로 T4, T5, T6, T7, T9를 판단해 checks에 넣고, `prompts/review.md`의 "문서화 리뷰 feedback 작성" 절에 따라 feedback을 작성한다.
7. 아래 "검수 결과" 절과 `prompts/review.md`의 "검수 결과 코드값의 공통 기준"으로 검수 결과 코드값(verdict.verdict)을 정한다.
8. 아래 "comment (결과 요약 부분만)" 절과 `prompts/review.md`의 "결과 요약(comment) 작성" 절에 따라 tasks[].comment를 작성한다.

---

## 검사 항목

| ID | 이름 | 검사 내용 | 계산 주체 | 검수 모드 영향 |
|---|---|---|---|---|
| T1 | 예상 산출물 작성 | 예상 산출물 구역에 안내문이 아닌 목록 항목이 1개 이상 있음 | 스크립트 | 실패 시 보류 |
| T2 | 진행 배경 작성 | 진행 배경 구역에 안내문 외의 내용이 2줄 이상 있음 | 스크립트 | 실패 시 보류 |
| T3 | 예상 산출물과 결과 산출물의 1:1 대응 | 판정 객체(verdict)의 items 번호가 precheck의 expected 번호와 1:1로 같음 | apply.py (CI agent 출력 검증). CI agent는 checks에 넣지 않는다 | 불일치 시 검토 요청 |
| T4 | 진행 배경 충실도 | 아래 "문서화 리뷰" 참고 | CI agent | 문서화 리뷰 정책 |
| T5 | 예상 산출물 분할 단위 | 아래 "문서화 리뷰" 참고 | CI agent | 문서화 리뷰 정책 |
| T6 | 진행 기록 comment | 아래 "문서화 리뷰" 참고 | CI agent | 문서화 리뷰 정책 |
| T7 | sub-task 기록 | 아래 "문서화 리뷰" 참고 (sub-task가 없으면 n/a) | CI agent | 문서화 리뷰 정책 |
| T8 | 초과 달성·미달성 사유 | 미달성 items와 extra마다 why가 채워져 있음. 미달성 items의 why가 "근거 부족으로 확인 불가"이면 비어 있는 것으로 셈 | apply.py. CI agent는 checks에 넣지 않는다 | 문서화 리뷰 정책 |
| T9 | 진행 배경부터 결과 산출물까지의 연결 | 아래 "문서화 리뷰" 참고 | CI agent | 문서화 리뷰 정책 |
| A2 | 완료 기준의 사후 변경 | 가장 최근 request 전환 이후에 완료 기준 구역(예상 산출물)이 바뀜 | 스크립트 | 실패 시 검토 요청 |

- 이 표 외에 `prompts/review.md`의 공통 검사 항목(R2, R3, R4)도 checks에 넣는다.

---

## 문서화 리뷰

| ID | pass 기준 | fail 예 |
|---|---|---|
| T4 진행 배경 충실도 | 왜 이 일을 하는지(문제, 요구, 동기)가 있고, 1년 뒤 처음 보는 사람이 대상(데이터, 모델, 서비스)과 맥락(선행 ticket, 관련 문서 링크)을 알 수 있음 | "test", "모델 개선"처럼 왜가 없음. 대상과 맥락을 전혀 알 수 없음 |
| T5 예상 산출물 분할 단위 | 항목마다 결과 하나이고, 무엇이 나오면 끝인지(수치, 산출물 형태, 대상) 판단할 수 있음. '<대상> 개발', '<대상> 적용'처럼 끝 기준이 없는 문구는 아래 둘 중 하나가 있으면 pass임: 하위 항목(들여쓰기)으로 무엇을 산출하면 끝인지 구체화함, comment에 그 항목의 중간 진행상황 기록(무엇을 했고 결과나 판단이 무엇인지 적힌 comment)이 있음 | "데이터 정제 및 모델 재학습"처럼 따로 달성 여부가 갈릴 결과가 한 항목에 묶임. "성능 개선", "검토"처럼 끝을 판단할 기준이 없음. 하위 항목 없는 "캐시 기능 개발"에 comment가 완료 기록("개발 완료", "적용 완료"처럼 무엇을 했고 어떻게 확인했는지 없이 완료만 적은 comment)뿐임 |
| T6 진행 기록 comment | 작업 기간 중 중간 결과, 인사이트, 방향 변경과 그 이유가 comment로 남아 있음. 중간 진행상황 기록으로 세는 것은 무엇을 했고 결과나 판단이 무엇인지 적힌 comment뿐이고, 완료 기록과 상태만 적은 기록("진행 중", "작업함"처럼 상태만 적은 comment)은 세지 않음. comment를 남긴 시점은 작업 기간이 영업일 기준 3일 미만이면 판단하지 않고, 3일 이상이면 precheck.json의 comment를 남긴 날 수(`commentDaysDuringWork`)로 판단함. 상태만 적은 기록은 작업 기간과 관계없이 판단함 | 작업 기간 중 사람 comment가 없음. 작업이 영업일 기준 3일 이상인데 comment가 request 직전에만 몰려 있음. 상태만 적은 기록이 있고, 그 단계에서 한 일과 결과가 따로 남아 있지 않거나 나중 comment 하나에 몰려 적혀 있음. 방향이나 범위가 바뀌었는데 이유가 없음 |
| T7 sub-task 기록 | sub-task마다 description에 무엇을 왜 하는지가 있고, 끝난 sub-task에는 결과 comment나 PR(sub-task의 prs 포함)이 있음 | description이 비었거나 요약만 반복함. done인데 결과 기록이 없음 |
| T9 진행 배경부터 결과 산출물까지의 연결 | 예상 산출물이 진행 배경의 문제나 요구를 해결하는 결과임. 중간 진행상황 기록이 어느 예상 산출물을 향한 작업인지 알 수 있음. 결과 산출물이 진행 배경의 문제나 요구에 어떻게 답했는지 알 수 있음 | 진행 배경은 GPU 사용 시간 절감이 목적인데 결과에는 재추론 비율만 있어 GPU 사용 시간이 어떻게 됐는지 알 수 없음. 진행 배경과 관계없는 예상 산출물이 있음. 예상 산출물에 없는 작업이 중간 진행상황 기록의 대부분인데 이유가 없음 |

- T9는 T4, T5와 검사 대상이 다르다.
  - T4는 진행 배경이 충실한지만 검사한다.
  - T5는 예상 산출물 항목 하나하나의 단위와 끝 기준만 검사한다.
  - T9는 진행 배경, 예상 산출물, 중간 진행상황 기록, 결과 산출물 사이가 이어지는지만 검사한다.

```json
"feedback": [
  { "id": "T4", "points": ["평가 지표를 바꾸게 된 계기(고객 불만 2건)가 적혀 있어 이유가 분명함"] },
  { "id": "T5", "points": ["1번 항목은 결과 하나로 잘 나뉨",
                           "3번 '데이터 정제 및 모델 재학습'은 따로 달성 여부가 갈리는 결과 두 개가 묶여 있음"],
    "request": "예상 산출물 3번을 '데이터 정제'와 '모델 재학습' 두 항목으로 나누고 각각 끝났을 때의 기준 작성 필요" },
  { "id": "T6", "points": ["작업 기간(첫 in-progress(진행 중 상태) 전환부터 마지막 request 전환(담당자의 완료 요청)까지) 영업일 기준 8일 동안 사람 comment 1건(request 전환 당일)뿐이라 중간 결과와 방향 변경 이유를 알 수 없음"],
    "request": "진행 중 나온 중간 수치와 방향을 바꾼 이유 comment 필요" },
  { "id": "T7", "points": ["INNO-18은 결과 comment([comment 2026-09-14|https://<site>/browse/INNO-18?focusedCommentId=10150])와 PR([PR #47 export 테스트 추가|https://github.com/everex-ai/repo/pull/47])이 있음", "INNO-19는 description이 비어 있어 무엇을 했는지 알 수 없음"],
    "request": "INNO-19 description에 무엇을 왜 했는지 작성 필요" },
  { "id": "T9", "points": ["진행 배경의 목적은 export 오류 재발 방지인데 결과 산출물에는 오류 재현 테스트 추가([PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42])만 있고, 재발 여부를 어떻게 확인했는지 알 수 없음"],
    "request": "결과 산출물에 재발 여부를 확인한 방법과 결과를 알 수 있는 comment 필요" }
]
```

---

## comment (결과 요약 부분만)

이 ticket에서 무엇이 어떻게 되었고 왜 이 검수 결과인지를 개조식 2–5줄로 적는다.
- 줄마다 근거 wiki 링크([PR 제목|URL] 또는 [comment 날짜|URL])를 붙인다.

```
* 재현 테스트 추가 항목은 [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]로 병합되어 달성함
* 오류율 1% 미만 항목은 모델 v2의 검증 데이터 오류율 0.7%라는 수치만 있고 계산 방법과 리포트 링크가 기록에 없어 미달성으로 둠([comment 2026-09-12|https://<site>/browse/INNO-17?focusedCommentId=10123])
* 예상에 없던 오류율 대시보드([PR #45 오류율 대시보드|https://github.com/everex-ai/repo/pull/45])는 초과 달성으로 기록함
```

---

## 검수 결과

- R3은 검수 결과에 영향이 없어 아래 조건에 넣지 않는다.
- pass(통과): items가 모두 done이고, 아래 검사가 모두 pass 또는 n/a다.
  - 예상 산출물 작성 (T1)
  - 진행 배경 작성 (T2)
  - 1년 뒤에도 이해 가능한 기록 (R2)의 링크 기준(agent 구역의 결과마다 확인 가능한 링크가 있음)
  - stop 사유 comment (R4)
  - 완료 기준의 사후 변경 (A2)
- fix(보류): 아래 중 하나에 해당한다.
  - done이 false이고 why가 빈 문자열이거나 "근거 부족으로 확인 불가"인 항목이 있음
  - T1, T2, R2의 링크 기준, R4 중 fail이 있음
- escalate(검토 요청): 아래 중 하나에 해당한다.
  - done이 false인 항목마다 why에 사람의 기록(comment, sub-task, PR)에 있는 사유가 있고, T1, T2, R2의 링크 기준, R4가 fail이 아님
  - 예상 산출물이 모호해 대응할 수 없음
  - A2가 fail
