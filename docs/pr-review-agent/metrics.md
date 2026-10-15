# 업무 효율 측정: 코드 리뷰 Agent

이 문서는 코드 리뷰 Agent(pr-review)를 도입한 뒤 업무 효율이 얼마나 늘었는지 수치로 확인하는 방법이다. 지표의 정의와 계산 방법, 데이터 위치, 기준선, 표본 실측 기록표, 집계 스크립트, 측정 일정을 다룬다. 문서화 Agent(jira-doc)의 측정 방법은 `docs/jira-doc-agent/metrics.md`에 있다.

- 업무 효율은 "줄어든 리뷰 시간" 하나가 아니라 아래 업무 효율 지표 네 가지로 측정한다.
  1. 리뷰 범위: merge 전에 리뷰를 받은 PR(pull request)의 비율
  2. 환산 리뷰 시간: 코드 리뷰 Agent가 리뷰한 PR을 팀장이 같은 기준으로 리뷰했다면 필요했을 시간에서, 팀장이 PR 검수 comment를 확인한 시간을 뺀 값
  3. 발견·조치: 코드 리뷰 Agent가 반려한 PR 중 merge 전에 고쳐진 비율
  4. 피드백 대기 시간: PR 생성부터 PR 검수 comment가 처음 게시될 때까지 걸린 시간
- 네 지표 중 리뷰 범위와 환산 리뷰 시간을 주 지표로 둔다.
  - 추정: 도입 전에는 PR을 모든 건에 기준대로 리뷰하지 못했다.
  - 이 추정은 0주차 집계의 P1 기준선으로 확인한다.
  - 추정: 그래서 업무 효율 증가는 "같은 리뷰를 더 빨리 함"보다 "하지 못하던 리뷰를 모든 PR에 함"으로 더 크게 나타난다.
- 수치는 팀 합계로만 집계하고, PR 작성자 개인 평가에 사용하지 않는다("해석 원칙" 절).

## 용어

이 문서는 아래 용어를 한 이름으로만 사용한다.

- 리뷰: 기준에 따라 코드를 확인하고 판정하는 일. 사람과 코드 리뷰 Agent가 모두 한다.
  - 기준은 검수 절차 7단계다(`docs/pr-review-agent/README.md`의 "검수 절차와 분담" 절).
- 검사 번호: 검수 절차 7단계의 단계 번호. 단계는 아래와 같다(`docs/pr-review-agent/README.md`의 "검수 절차와 분담" 표).
  - 1번: 기존 코드에서 수정된 부분 확인
  - 2번: 추가된 코드와 기존 코드 수정 구분
  - 3-1번: 추가된 코드의 형식, 타입 힌트, docstring 규칙 확인
  - 3-2번: 추가된 코드에 별도 테스트가 필요한지 판단
  - 3-3번: 필요한 테스트가 코드 베이스에 있는지 확인
  - 4-1번: 수정된 코드에 별도 테스트가 필요한지 판단
  - 4-2번: 필요한 테스트가 코드 베이스에 있는지 확인
  - 5번: 테스트 실행
  - 6번: 사용되지 않는 함수, 클래스, 변수 찾기
  - 7번: 비효율과 확장성 의견
- PR 검수 comment: 코드 리뷰 Agent가 리뷰 결과를 PR에 게시하는 comment. 첫 줄이 `<!-- everex-review -->`이고, 다시 실행되면 같은 comment를 수정한다(`scripts/review/common.py`의 `COMMENT_MARKER`, `scripts/review/apply.py`의 `upsert_comment`).
- GitHub review: GitHub PR 화면에서 남기는 리뷰 기록(승인, 변경 요청, comment)
- 팀장: 이 문서에서 표본 실측, 집계 스크립트 실행, 보고서 작성을 맡는 사람
- 결과 확인 시간: 팀장이 PR 검수 comment를 읽고 확인하는 데 걸린 시간
- 표본 실측: 팀장이 PR 검수 comment를 읽기 전에 같은 기준으로 직접 리뷰하면서, PR마다 리뷰 시간, 판정, 결과 확인 시간을 기록하는 측정
- 기준선: 도입 전 값, 또는 코드 리뷰 Agent 없이 팀장이 리뷰했을 때의 값
- 기준값: 조건 지표가 넘어야 하는 하한
- 비교 대상: 지표의 도입 후 값과 비교하는 값. 아래 세 가지 중 하나이고, "비교 대상을 얻는 방법" 절에서 얻는 방법을 설명한다.
  - 기준선
  - Agent 첫 리뷰: 같은 PR에 대한 코드 리뷰 Agent의 첫 판정
  - 기준값
- 스크립트 검사 단계: 코드 리뷰 Agent의 첫 단계. `collect.py`, `classify.py`, `precheck.py`가 Claude 없이 검사 1, 2, 3-1, 5번을 판정한다(`docs/pr-review-agent/README.md`의 첫 절).
- precheck 판정: 스크립트 검사 단계의 판정. 반려이면 Claude 단계를 실행하지 않는다(`docs/pr-review-agent/README.md`의 "용어" 절, "검수 절차와 분담" 절).
- Claude 단계: 코드 리뷰 Agent의 둘째 단계. Claude가 검사 3-2, 3-3, 4-1, 4-2, 6, 7번을 판단한다(`docs/pr-review-agent/README.md`의 첫 절, `.github/workflows/pr-review.yml`의 "2. Claude 검수" 스텝).
- 반려: 코드 리뷰 Agent의 판정 중 merge 전에 고쳐야 함을 뜻하는 값. PR에 `review/reject` 라벨이 붙고, 통과면 `review/pass`가 붙는다(`docs/pr-review-agent/README.md`의 "검수 절차와 분담" 절, `scripts/review/apply.py`의 `set_labels`).
- 등급: precheck가 정하는 Claude 단계의 실행 방식(0, 1, 2). 정의는 P7 설명에 있다(`docs/pr-review-agent/README.md`의 "Claude 단계 실행 등급" 절).
- Python 변경 줄: PR에서 테스트 외 Python 파일의 추가·삭제 줄 합. 등급을 정할 때 세는 줄 수와 같은 정의다(`scripts/review/precheck.py`의 `compute_tier`).
- 소급 판정: 도입 전에 merge된 PR에 스크립트 검사 단계를 다시 실행해 판정하는 것. `backfill_precheck.py`가 한다("집계 스크립트" 절).
- SHA: git commit을 가리키는 해시 값
- GitHub Actions: GitHub에서 워크플로를 실행하는 기능. 코드 리뷰 Agent는 GitHub Actions 워크플로로 실행된다(`.github/workflows/pr-review.yml`).
- artifact: GitHub Actions 실행이 남기는 결과 파일 묶음. 코드 리뷰 Agent는 `everex-review-pr<번호>`로 30일 보관한다(`docs/pr-review-agent/README.md`의 "워크플로" 절).

## 지표의 분류

지표는 업무 효율 지표, 품질 지표, 조건 지표 세 분류로 나뉜다. 보고서 첫머리에는 업무 효율 지표 네 가지와 조건 지표를 둔다.

- 업무 효율 지표: 이 문서 첫머리의 네 가지 지표
- 품질 지표: 업무 효율 지표가 늘어난 것이 코드 품질 향상으로 이어졌는지 확인하는 지표다.
  - merge 시점 품질: merge된 PR에 스크립트 검사 단계의 기준 위반이 남은 비율
- 조건 지표: 업무 효율 지표를 믿을 수 있는지 판단하는 지표다.
  - 판정 일치율: 팀장의 판정과 코드 리뷰 Agent 판정이 얼마나 같은지 나타내는 세 값(반려 정밀도, 반려 재현율, override 비율). 정의는 P6 설명에 있다.
  - API 환산 비용: 코드 리뷰 Agent 1회 실행의 Claude 사용량을 API 종량제 단가로 환산한 값

## 지표

코드 리뷰 Agent의 지표는 P1–P7 일곱 개이고, 그중 P1 리뷰 범위와 P2 환산 리뷰 시간이 주 지표다. 도입 전 기록으로 기준선을 구할 수 있는 지표는 P1, P4, P5다.

| ID | 분류 | 지표 | 계산 방법 | 데이터 | 비교 대상 |
|---|---|---|---|---|---|
| P1 | 업무 효율 | 리뷰 범위 | merge된 PR 중 merge 전에 리뷰를 받은 PR의 비율. 사람 리뷰는 작성자 외 GitHub review, Agent 리뷰는 PR 검수 comment 기준 | GitHub PR 기록 | 기준선(도입 전 merge PR의 사람 리뷰 비율) |
| P2 | 업무 효율 | 환산 리뷰 시간(월) | 그 달 merge된 PR 중 merge 전에 PR 검수 comment가 달린 PR의 Python 변경 줄 합 × 줄당 팀장 리뷰 시간 − 그 PR 수 × 건당 결과 확인 시간 | 소급 판정 결과, 표본 실측 기록표 | 기준선(표본 실측) |
| P3 | 업무 효율 | 발견·조치 | `review/reject` 라벨을 받은 적이 있는 PR 중 merge 시점의 라벨이 `review/pass`인 비율 | PR timeline의 라벨 이벤트 | Agent 첫 리뷰 |
| P4 | 업무 효율 | 피드백 대기 시간 | PR 생성부터 PR 검수 comment 게시까지(분) | GitHub PR 기록 | 기준선(도입 전 PR 생성부터 작성자 외 첫 GitHub review까지 시간) |
| P5 | 품질 | merge 시점 품질 | merge된 PR 중 스크립트 검사 단계의 기준 위반이 남은 PR의 비율 | 소급 판정 결과 | 기준선(도입 전 merge PR의 소급 판정) |
| P6 | 조건 | 판정 일치율 | 반려 정밀도, 반려 재현율, override 비율 | 표본 실측 기록표, PR 검수 comment | 기준값: 반려 정밀도 80% |
| P7 | 조건 | API 환산 비용 | PR당 Claude 단계 비용, 등급별 PR 수 | artifact의 `claude-execution.json` | 없음 |

- P2의 두 시간은 표본 실측 pr 행에서 아래처럼 구한다.
  - 줄당 팀장 리뷰 시간: `review_minutes` 합을 `size`(Python 변경 줄) 합으로 나눈 값
  - 건당 결과 확인 시간: `check_minutes`의 중앙값
- P3은 라벨로만 계산한다.
  - 검사 6번(사용되지 않는 함수, 클래스, 변수 찾기)과 7번(비효율과 확장성 의견)의 보완 필요 수는 넣지 않았다. PR 검수 comment가 마지막 실행의 결과만 남겨 Agent 첫 리뷰와 비교할 수 없기 때문이다.
- P1의 사람 리뷰와 P4의 기준선에서 bot 계정의 GitHub review와 제출하지 않은 review는 뺀다.
  - 이유: 코드 리뷰 Agent는 수정 제안을 bot 계정(`github.token`)의 PR review로 달기 때문이다(`scripts/review/apply.py`의 `post_suggestions`, `.github/workflows/pr-review.yml`의 "3. PR 반영 (comment, 라벨, gate)" 스텝).
- P1에서 merge 뒤에 달린 GitHub review와 PR 검수 comment는 리뷰를 받은 것으로 세지 않는다. merge를 막거나 고치게 할 수 없었던 리뷰이기 때문이다.
  - `prs.csv`에는 merge 뒤에 달린 경우를 따로 표시한다.
- P4는 merge 전후와 관계없이 PR 검수 comment가 있는 PR 전체로 계산하고, PR 검수 comment의 생성 시각(`created_at`)을 사용한다.
  - `upsert_comment`는 기존 PR 검수 comment를 PATCH로 수정하고 새로 달지 않는다(`scripts/review/apply.py`의 `upsert_comment`).
  - 추정: GitHub API는 comment를 수정할 때 `updated_at`만 바꾸므로, `created_at`은 첫 게시 시각으로 남는다.
- P5는 PR 하나에 아래 중 하나라도 있으면 위반이 남은 PR로 센다. 보고서에는 세 기준별 PR 수도 함께 적는다.
  - 검사 3-1번 위반: 변경 줄의 ruff 위반이나 format 위반이 1개 이상
  - 테스트 파일에 이름이 나오지 않는 판단 대상 심볼이 1개 이상. `review-input.json`의 `test_candidates`에서 그 심볼의 `name_hits`가 0인 경우다.
  - 검사 6번 미사용 후보가 1개 이상
- P5에서 ruff나 vulture가 실행되지 않은 PR은 빠지지만, Python 변경 줄은 계산되므로 P2에는 들어간다.
- 추정: 미사용 후보 수는 별칭 import(예: `import json as js`)에서 ruff와 vulture가 내는 이름이 달라 두 번 세어질 수 있다.
  - P5는 "1개 이상" 기준이라, 두 번 세어져도 판정은 바뀌지 않는다.
- 판단 대상 심볼과 테스트 후보의 뜻은 `docs/pr-review-agent/README.md`의 "용어" 절과 "각 검사가 결정하는 것" 절에 있다.
  - 판단 대상 심볼: Claude가 테스트 필요 여부를 판단하는 심볼. 테스트 심볼, 삭제된 심볼, docstring만 바뀐 심볼은 빠진다.
  - 테스트 후보: 테스트 파일에서 그 심볼의 이름이나 모듈 import가 나오는 테스트
- P5의 둘째 기준을 이름 기준(`name_hits`)으로 정한 이유는, 테스트가 없는 심볼도 모듈 import 기준(`import_hits`)으로는 후보가 잡히기 때문이다.
  - 근거: 아래 순서로 실행한 결과(2026-10-02), 테스트가 없는 `scale`의 `name_hits`는 0, `import_hits`는 1이었다.
    1. `scripts/review/dev/make_fixture.py <폴더>/fx`(옵션 없음)로 fixture(검증용 대상 repo)를 만듦
    2. `prs.csv`에 `repo,number,base_sha,head_sha` 헤더와 `fx,1,<main SHA>,<feature SHA>` 한 줄을 적음
    3. `backfill_precheck.py --repo <폴더>/fx --repo-name fx --prs prs.csv --out backfill.csv --work-root <폴더>/work`를 실행함
    4. `<폴더>/work` 아래 `review-input.json`의 `test_candidates`에서 `calc/ops.py::scale` 항목을 확인함
  - `tests/review/test_precheck.py`도 `name_hits`로 테스트 후보 유무를 확인한다.
- 추정: 이름이 나오는 테스트가 있어도 그 심볼을 실제로 검증하지 않는 테스트일 수 있으므로, 둘째 기준의 심볼 수는 테스트가 없는 심볼 수의 하한에 가깝다.
- P6의 세 값은 아래와 같다.
  - 반려 정밀도: 표본 실측에서 코드 리뷰 Agent가 반려한 PR 중 팀장도 반려로 판정한 비율
  - 반려 재현율: 표본 실측에서 팀장이 반려로 판정한 PR 중 코드 리뷰 Agent도 반려한 비율
  - 두 값 모두 `user_verdict`와 `agent_verdict`가 모두 적힌 행만 세고, 뺀 행 수는 `pr_metrics.py`가 만드는 `summary.md`에 적힌다.
  - override 비율: merge 전에 PR 검수 comment가 달린 PR 중, 지정 리뷰어가 `review/override` 라벨로 판정을 통과로 바꾼 PR의 비율. PR 검수 comment 맨 위의 override 문구로 센다(`docs/pr-review-agent/README.md`의 "판정에 이의가 있을 때 (override)" 절).
    - PR 검수 comment는 마지막 실행의 결과만 남기므로, 마지막 PR 검수 comment 맨 위에 override 문구가 있는 PR만 세어진다.
- 반려 정밀도 80%는 게이트 모드(`gate: true`. 반려나 오류일 때 PR의 check를 실패시키는 설정)로 바꾸는 기준과 같은 값이다(`docs/pr-review-agent/README.md`의 "다음 단계" 절).
- P7의 비용은 `claude-execution.json`의 `total_cost_usd`다.
  - 이 값은 같은 사용량을 API 종량제로 사용했을 때의 환산값이고, `scripts/review/claude_summary.py`가 로그에 `api_equiv_usd`로 출력한다(`docs/pr-review-agent/README.md`의 "대상 repo에 설치" 절).
  - 등급은 Claude 단계의 실행 방식이다(`docs/pr-review-agent/README.md`의 "Claude 단계 실행 등급" 절).
    - 0: Claude 단계를 실행하지 않음
    - 1: Claude 하나가 혼자 판단함
    - 2: 검사를 하위 Claude(test-necessity, test-coverage, design-review, 미사용 후보가 있으면 dead-code)에 나눠 맡김(`docs/pr-review-agent/README.md`의 "파일" 절)
  - 등급 0이거나 precheck 판정이 반려인 PR은 Claude 단계를 실행하지 않으므로 비용이 0이다.
  - artifact는 30일만 보관되므로, 팀장이 매주 집계 때 `gh run download`로 받아 `reports/agent-impact/pr-review/artifacts/`에 둔다.
  - P7은 집계 스크립트가 계산하지 않는다. 팀장이 받은 파일마다 `scripts/review/claude_summary.py`를 실행해 값을 확인한다.
- PR 생성부터 merge까지 시간은 지표로 사용하지 않는다.
  - 추정: 리뷰 없이 바로 merge하는 repo에서는 이 시간이 이미 0에 가깝고, 리뷰 단계가 생기면 늘어나기만 해서 업무 효율을 나타내지 않는다.
  - 이 시간은 0주차 집계의 `prs.csv`(`created_at`, `merged_at`)로 확인한다.

## 비교 대상을 얻는 방법

비교 대상은 기록의 유무에 따라 아래 세 방법으로 얻는다. 첫째와 둘째는 기준선이고, 셋째는 Agent 첫 리뷰다.

1. 기록 소급: 도입 전 기록이 있는 지표(P1, P4, P5)에 사용한다.
   - 팀장이 `pr_metrics.py`로 도입 전 GitHub PR 기록을 집계하고, `backfill_precheck.py`로 도입 전 merge PR을 소급 판정한다("집계 스크립트" 절).
2. 표본 실측: 기록에 없는 팀장의 리뷰 시간과 판정(P2, P6)에 사용한다("표본 실측 기록표" 절).
   - 팀장의 리뷰 시간은 도입 전후와 관계없이 같은 방법으로 재므로, 1–2주차에 측정한 값도 기준선으로 사용한다.
3. 건 안의 전후 비교: 도입 후에만 있는 지표(P3)에 사용한다.
   - 같은 PR의 Agent 첫 리뷰와 merge 시점의 판정을 비교하므로 도입 전 기록이 필요 없다.

- 기준선 값은 팀장이 `reports/agent-impact/pr-review/baseline/`에 두고, 결과 파일은 git에 올리지 않는다.
  - 이유: 이 repo는 public repo라서 repo별, PR별 기록이 공개된다(`docs/pr-review-agent/README.md`의 "대상 repo에 설치" 절 1번).

## 표본 실측 기록표

표본 실측은 팀장이 PR 검수 comment를 읽기 전에 기준대로 리뷰하고, PR마다 한 줄을 CSV(쉼표로 구분한 표 파일)로 기록하는 측정이다.

- 표본은 Python 변경 줄 기준으로 작은 PR 2개, 중간 PR 2개, 큰 PR 2개다.
  - 작은 PR은 50줄 이하다. 50줄은 등급 1의 줄 수 경계다(`scripts/review/precheck.py`의 `TIER1_MAX_LINES`).
  - 중간 PR과 큰 PR의 경계는 0주차 소급 판정에서 구한, 51줄 이상 PR의 Python 변경 줄 중앙값이다.
  - 크기 구간별로 고르는 이유는 리뷰 시간이 크기에 따라 어떻게 늘어나는지 확인해 줄당 팀장 리뷰 시간을 정하기 위해서다.
  - 크기 구간마다 2개로 정한 이유는 한 PR의 값에만 기대지 않게 하는 최소 수이기 때문이다.
  - 추정: PR 6개에 건당 20–40분이 들어 2–4시간이 걸린다.
- 첫 줄은 아래 헤더다. 문서화 Agent의 기록표와 같은 열을 사용하고, 이 문서에서는 `kind`가 `pr`인 행만 사용한다.

```text
kind,target,size,review_minutes,issues_found,user_verdict,check_minutes,agent_verdict
```

| 열 | 값 |
|---|---|
| `kind` | `pr` |
| `target` | `<repo>#<번호>`(예: `pose-griffin#3`) |
| `size` | Python 변경 줄(정수) |
| `review_minutes` | PR 검수 comment를 읽기 전에 기준대로 리뷰한 시간(분) |
| `issues_found` | 리뷰에서 찾은 보완 필요 항목 수 |
| `user_verdict` | 팀장의 판정. 통과 또는 반려 |
| `check_minutes` | 결과 확인 시간(분) |
| `agent_verdict` | 같은 PR에 대한 코드 리뷰 Agent 판정. 통과 또는 반려 |

- 열 이름은 스크립트가 읽는 식별자다. `user_verdict`의 user는 팀장을 가리킨다.
- 기록 순서는 아래와 같다.
  1. 대상과 크기를 적는다.
  2. PR 검수 comment를 열지 않은 채 기준대로 리뷰하고, 리뷰 시간, 찾은 항목 수, 판정을 적는다.
  3. PR 검수 comment를 열어 확인하고, 결과 확인 시간과 코드 리뷰 Agent 판정을 적는다.
- 파일은 `reports/agent-impact/pr-review/time-study.csv`에 둔다.

## 집계 스크립트

집계 스크립트 두 개는 GitHub를 읽기만 하고, 게시하거나 변경하지 않는다. 파이프라인 코드와 섞지 않으려고 `scripts/review/dev/`에 두고, 팀장이 실행한다.

| 스크립트 | 집계하는 지표 | 읽는 곳 |
|---|---|---|
| `scripts/review/dev/pr_metrics.py` | P1–P6 | GitHub REST API(`gh api`), `backfill_precheck.py`의 출력 |
| `scripts/review/dev/backfill_precheck.py` | P5와 P2에 필요한 PR별 값(검사 위반 수, Python 변경 줄) | 대상 repo의 로컬 clone |

- 실행 순서와 명령의 예는 아래와 같다. repo 루트에서 `.venv`의 Python으로 실행한다.

```bash
# 1) GitHub PR 기록 집계 (P1, P3, P4)
.venv/bin/python scripts/review/dev/pr_metrics.py --repos pose-ai-verifier,pose-griffin \
  --since 2026-04-01 --out reports/agent-impact/pr-review/baseline

# 2) 과거 merge PR 소급 판정 (P5, Python 변경 줄). repo마다 실행
.venv/bin/python scripts/review/dev/backfill_precheck.py --repo ~/work/pose-griffin \
  --prs reports/agent-impact/pr-review/baseline/prs.csv \
  --out reports/agent-impact/pr-review/baseline/backfill-pose-griffin.csv --work-root /tmp/backfill

# 3) 소급 판정 결과와 표본 실측을 넣어 다시 집계 (P2, P5, P6). --backfill은 쉼표로 여러 파일을 받음
.venv/bin/python scripts/review/dev/pr_metrics.py --repos pose-ai-verifier,pose-griffin \
  --since 2026-04-01 --out reports/agent-impact/pr-review/baseline \
  --backfill reports/agent-impact/pr-review/baseline/backfill-pose-ai-verifier.csv,reports/agent-impact/pr-review/baseline/backfill-pose-griffin.csv \
  --time-study reports/agent-impact/pr-review/time-study.csv
```

- `pr_metrics.py`는 기간(`--since`, `--until`, 두 날짜 포함)과 월 구분을 한국 시간(+09:00) 기준으로 계산한다.
- `pr_metrics.py`는 GitHub REST API를 사용한다.
  - 이유: gh 2.4.0의 `gh pr list --json`에는 head·base SHA 필드가 없어, 소급 판정에 넘길 SHA를 얻을 수 없다(`gh pr list --json`의 필드 목록, 2026-10-02 확인).
- `backfill_precheck.py`는 PR마다 head commit의 git worktree를 만들고, 스크립트 검사 단계를 실행한 뒤 worktree를 지운다.
  - 기본으로 검사 5번(pytest)을 건너뛴다. pytest는 대상 repo의 코드를 실행하기 때문이다.
  - 추정: 과거 commit의 pytest는 의존성이나 GPU 조건이 맞지 않아 실패할 수 있다.
  - 실패한 PR은 출력 CSV의 오류 열에 이유를 남기고 다음 PR로 넘어간다. ruff나 vulture가 실행되지 않은 PR도 오류로 남겨 P5에서 뺀다.
  - 대상 clone에 head·base commit이 없으면 `git fetch`로 받으므로 clone의 `.git`에 객체가 추가된다.
  - `--work-root` 아래 PR별 작업 디렉터리(`review-input.json` 포함)는 지우지 않고 남긴다. 필요 없으면 팀장이 지운다.
- 출력 파일은 아래와 같다.
  - `pr_metrics.py`
    - `prs.csv`: PR별 한 줄
    - `summary.md`: P1–P6 요약
  - `backfill_precheck.py`
    - `--out`의 CSV: PR별 한 줄

## 측정 일정과 보고서

측정은 8주 동안 하고, 4주차와 8주차에 팀장이 보고서를 작성한다.

| 시기 | 하는 일 |
|---|---|
| 0주차 | 팀장이 기록 소급 집계로 P1, P4, P5의 기준선을 구함 |
| 1–2주차 | 팀장이 표본 실측을 함 |
| 1–4주차 | 아래 설정으로 운영하며 팀장이 매주 집계 스크립트를 실행함 |
| 4주차 | 1차 보고서 |
| 8주차 | 2차 보고서(추이) |

- 4주차에 1차 보고서를 두는 이유는 매주 실행한 집계(P1, P3, P4)가 4번 쌓여 추이를 확인할 수 있기 때문이다.
- 8주차에 2차 보고서를 두는 이유는 도입 직후 값과 운영이 익숙해진 뒤의 값을 비교하기 위해서다.
  - 추정: 도입 직후에는 PR 작성자가 새 방식에 적응하는 중이라 반려 비율이 높게 나온다.
- 1–4주차에는 대상 repo의 `.github/workflows/pr-review.yml`(코드 리뷰 Agent 워크플로를 부르는 호출 yml)에서 `gate: false`로 운영한다. 반려여도 PR의 check를 실패시키지 않는다(`docs/pr-review-agent/README.md`의 "용어" 절).
- 보고서는 `reports/agent-impact/pr-review/<날짜>.md`에 둔다.
  - 첫머리에는 업무 효율 지표 네 가지와 조건 지표를 둔다.
  - 수치마다 무엇의 값인지, 데이터(기간, repo), 표본 수(n), 계산 방법을 함께 적는다.

## 해석 원칙

- 수치는 팀 합계로만 보고서에 적고, PR 작성자 개인 평가에 사용하지 않는다.
  - 추정: 개인 평가에 사용하면 PR 작성자가 검사 통과에 맞춰 코드를 작성하게 되어, 수치가 실제 품질을 나타내지 않게 된다.
- 표본이 작으므로 평균 대신 중앙값, 범위, 표본 수(n)를 적고, 통계 검정은 하지 않는다.
- 판정 일치율 중 반려 정밀도(P6)가 80% 미만이면 환산 리뷰 시간을 업무 효율로 집계하지 않고 "확인 필요"로 표시한다.
  - 추정: 반려 정밀도가 낮으면 팀장이 PR 검수 comment를 믿지 못하고 다시 리뷰해야 하므로, 환산 리뷰 시간만큼 시간이 줄지 않는다.
