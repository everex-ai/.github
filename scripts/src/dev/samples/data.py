"""샘플 입력 문구. prompts/types/task/deliverables.md와 prompts/types/*/review.md의 출력 예시에서 가져온다."""

from __future__ import annotations

from src.dev.samples.fake_jira import comment_url

ASSIGNEE, LEAD = "acc-assignee", "acc-lead"
TASK_DESC = """h2. 진행 배경
* export 오류로 고객 불만 2건이 접수되어 원인 확인과 재발 방지가 필요함
* 관련 task: INNO-12

h2. 예상 산출물
* 재현 테스트 추가
* 오류율 1% 미만
* ONNX 변환
"""
TASK_ITEMS = [
    {
        "id": "1",
        "done": True,
        "result": "export 오류 재현 테스트 3건 추가, 2026-09-11 병합",
        "evidence": "[PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]",
    },
    {
        "id": "2",
        "done": False,
        "result": "comment에 모델 v2의 검증 데이터(val set) export 오류율 0.7%라는 수치만 있고 데이터 규모, 계산 방법, 리포트 링크 없음",
        "evidence": f"[comment 2026-09-12|{comment_url('INNO-17', 10123)}]",
        "why": "근거 부족으로 확인 불가",
        "reason": "평가 리포트 링크, 데이터 규모, 오류율 계산 방법 comment 필요",
    },
    {
        "id": "3",
        "done": False,
        "result": "ONNX(신경망 모델 교환 형식) 변환 스크립트 초안까지 작성",
        "why": f"배포 대상 기기 사양이 확정되지 않아 변환 옵션을 정하지 못함([comment 2026-09-13|{comment_url('INNO-17', 10140)}])",
        "evidence": f"[comment 2026-09-13|{comment_url('INNO-17', 10140)}]",
        "reason": "기기 사양 확정 뒤 후속 task 키 comment 필요",
    },
]
TASK_EXTRA = [
    {
        "result": "export 오류율을 매일 집계하는 대시보드 추가",
        "why": f"재발 여부를 매일 확인하자는 팀 회의 결정([comment 2026-09-10|{comment_url('INNO-17', 10101)}])",
        "evidence": "[PR #45 오류율 대시보드|https://github.com/everex-ai/repo/pull/45]",
    },
    {
        "result": "오류 로그 수집 스크립트 추가",
        "why": "",
        "evidence": "[PR #46 오류 로그 수집|https://github.com/everex-ai/repo/pull/46]",
    },
]
TASK_FEEDBACK = [
    {"id": "T4", "points": ["평가 지표를 바꾸게 된 계기(고객 불만 2건)가 적혀 있어 이유가 분명함"]},
    {
        "id": "T5",
        "points": [
            "1번 항목은 결과 하나로 나뉨",
            "3번 'ONNX 변환'은 끝났다고 판단할 기준(대상 기기, 허용 지연)이 없음",
        ],
        "request": "예상 산출물 3번에 끝났다고 판단할 기준(대상 기기, 허용 지연) 추가 필요",
    },
    {
        "id": "T6",
        "points": [
            "작업 기간(첫 in-progress(진행 중 상태) 전환부터 마지막 request 전환(담당자의 완료 요청)까지) 영업일 기준 8일 동안 "
            "사람 comment 1건(request 전환 당일)뿐이라 중간 결과와 방향 변경 이유를 알 수 없음"
        ],
        "request": "진행 중 나온 중간 수치와 방향을 바꾼 이유 comment 필요",
    },
    {
        "id": "T7",
        "points": [
            f"INNO-18은 결과 comment([comment 2026-09-14|{comment_url('INNO-18', 10150)}])와 "
            "PR([PR #47 export 테스트 추가|https://github.com/everex-ai/repo/pull/47])이 있음"
        ],
    },
    {
        "id": "T9",
        "points": [
            "진행 배경의 export 오류 재발 방지 요구에 예상 산출물 1번(재현 테스트 추가)이 대응하고, 결과도 "
            "[PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]의 테스트 병합으로 이어짐"
        ],
    },
]
TASK_CHECKS = [
    {"id": "R2", "result": "pass", "detail": "결과마다 PR 또는 comment 링크 있음"},
    {"id": "R3", "result": "pass", "detail": "agent 구역(결과 산출물 구역과 TL;DR)을 개조식으로 작성함"},
    {"id": "T4", "result": "pass", "detail": "진행 배경에 이유와 선행 task 있음"},
    {"id": "T5", "result": "fail", "detail": "3번 항목의 완료 기준 없음"},
    {"id": "T6", "result": "fail", "detail": "작업 기간 중 사람 comment 1건"},
    {"id": "T7", "result": "pass", "detail": "sub-task 기록 있음"},
    {"id": "T9", "result": "pass", "detail": "진행 배경, 예상 산출물, 결과 산출물이 이어짐"},
]
TASK_REQUESTS = [
    "평가 리포트 링크, 데이터 규모, 오류율 계산 방법 comment 필요",
    "기기 사양 확정 뒤 후속 task 키 comment 필요",
]
TASK_SUMMARY = f"""* 재현 테스트 추가 항목은 [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]로 병합되어 달성함
* 오류율 1% 미만 항목은 모델 v2의 검증 데이터 오류율 0.7%라는 수치만 있고 계산 방법과 리포트 링크가 기록에 없어 미달성으로 둠([comment 2026-09-12|{comment_url("INNO-17", 10123)}])
* 예상에 없던 오류율 대시보드([PR #45 오류율 대시보드|https://github.com/everex-ai/repo/pull/45])는 초과 달성으로 기록함"""
TASK_TITLE = "export 오류 원인 확인과 재발 방지"
# verdict.slack 샘플. prompts/review.md의 "Slack 요점(verdict.slack) 작성" 절의 예시 꼴(보류는 기록에서 빠진 것)
TASK_SLACK = ["오류율 평가 리포트 링크 없음", "기기 사양 확정 기록 없음"]
# 검토 요청 샘플(INNO-21): INNO-17과 같은 예상 산출물에서, 미달성 항목마다 담당자가 comment로 사유를 남긴 경우
REQUEST_ITEMS = [
    TASK_ITEMS[0],
    {
        "id": "2",
        "done": False,
        "result": "comment에 고객사 검증 데이터로 export 오류율을 측정할 계획만 있고 측정 결과 없음",
        "evidence": f"[comment 2026-09-15|{comment_url('INNO-21', 10160)}]",
        "why": f"고객사 검증 데이터 제공이 10월로 밀려 오류율을 측정하지 못함([comment 2026-09-15|{comment_url('INNO-21', 10160)}])",
        "reason": "고객사 검증 데이터로 측정한 오류율과 리포트 링크 comment 필요",
    },
    {
        "id": "3",
        "done": False,
        "result": "ONNX(신경망 모델 교환 형식) 변환 스크립트 초안까지 작성",
        "why": f"배포 대상 기기 사양이 확정되지 않아 변환 옵션을 정하지 못함([comment 2026-09-13|{comment_url('INNO-21', 10140)}])",
        "evidence": f"[comment 2026-09-13|{comment_url('INNO-21', 10140)}]",
        "reason": "기기 사양 확정 뒤 후속 task 키 comment 필요",
    },
]
# 검토 요청은 팀장이 판단할 쟁점. 통과는 팀장이 알아야 할 특이점이 없어 빈 목록(PASS_SLACK)
REQUEST_SLACK = ["고객사 데이터 제공이 늦어 오류율을 측정하지 못함", "기기 사양 미확정으로 ONNX 변환 옵션 못 정함"]
REQUEST_SUMMARY = f"""* 재현 테스트 추가 항목은 [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]로 병합되어 달성함
* 오류율 1% 미만 항목은 고객사 검증 데이터 제공이 10월로 밀렸다는 담당자 comment가 있어 미달성으로 둠([comment 2026-09-15|{comment_url("INNO-21", 10160)}])
* ONNX 변환 항목은 배포 대상 기기 사양이 확정되지 않았다는 담당자 comment가 있어 미달성으로 둠([comment 2026-09-13|{comment_url("INNO-21", 10140)}])"""
# 통과 샘플(INNO-22): INNO-17과 같은 예상 산출물이 모두 달성인 경우
REPORT_LINK = "[export 오류율 평가 리포트|https://github.com/everex-ai/repo/blob/main/reports/export-error-rate.md]"
PASS_ITEMS = [
    TASK_ITEMS[0],
    {
        "id": "2",
        "done": True,
        "result": "모델 v2의 검증 데이터(val set, export 요청 12,000건) export 오류율 0.7%(오류 건수를 export 요청 건수로 나눔)",
        "evidence": REPORT_LINK,
    },
    {
        "id": "3",
        "done": True,
        "result": "Galaxy S24용 ONNX(신경망 모델 교환 형식) 변환 스크립트 추가, 2026-09-16 병합",
        "evidence": "[PR #48 ONNX 변환|https://github.com/everex-ai/repo/pull/48]",
    },
]
PASS_SUMMARY = f"""* 재현 테스트 추가 항목은 [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]로 병합되어 달성함
* 오류율 1% 미만 항목은 모델 v2의 검증 데이터(val set, export 요청 12,000건) 오류율 0.7%(오류 건수를 export 요청 건수로 나눔)가 {REPORT_LINK}에 있어 달성함
* ONNX 변환 항목은 [PR #48 ONNX 변환|https://github.com/everex-ai/repo/pull/48]로 병합되어 달성함"""
PASS_SLACK: list[str] = []
# stop 이력: 2026-09-08에 stop하고 다음 상태 변경(2026-09-10 In Progress)까지 사람 comment가 없음
# 보류 샘플(INNO-17)은 사유가 없어 R4가 보완 필요이고, 통과 샘플(INNO-22)은 늦게 남긴 사유(LATE_REASON)가 있음
STOP_CHANGES = [
    {"created": "2026-09-02T10:00:00.000+0900", "field": "status", "to": "In Progress"},
    {"created": "2026-09-08T18:00:00.000+0900", "field": "status", "to": "Backlog"},
    {"created": "2026-09-10T09:30:00.000+0900", "field": "status", "to": "In Progress"},
    {"created": "2026-09-25T15:00:00.000+0900", "field": "status", "to": "ready-to-done"},
]
LATE_REASON = {
    "created": "2026-09-25T11:00:00.000+0900",
    "url": comment_url("INNO-22", 10210),
    "body": "stop 사유: 고객사 검증 데이터 전달을 기다리느라 작업을 멈춤",
}
BUG_CHECKS = [
    {"id": "B3", "result": "pass", "detail": "원인과 해결에 PR 링크 있음"},
    {"id": "B4", "result": "fail", "detail": "해결 뒤 To-Be 동작 확인 comment 없음"},
    {"id": "B5", "result": "pass", "detail": "문제(As-Is)와 기본 정보로 재현 조건을 알 수 있음"},
    {"id": "B6", "result": "pass", "detail": "원인을 찾은 과정이 PR 본문에 있음"},
    {"id": "B7", "result": "fail", "detail": "해결 뒤 To-Be 확인 기록 없음"},
    {"id": "R2", "result": "pass", "detail": "원인과 해결마다 링크 있음"},
    {"id": "R3", "result": "pass", "detail": "agent 구역(TL;DR)을 개조식으로 작성함"},
]
BUG_DESC_OLD = """h2. 기본 정보
* 발생 기기/서비스: 포즈 추정 앱
* 발생 일자: 2026-09-15
* 발생 장비: Galaxy S24
* 발생 계정: qa01

h2. 문제(As-Is)
* 결과 파일 export가 오류로 실패함

h2. 개선(To-Be)
* export가 오류 없이 끝남

h2. 첨부 자료(필수)
* export-error.mp4
"""
BUG_DESC = BUG_DESC_OLD.replace(
    "* export가 오류 없이 끝남\n", "* export가 오류 없이 끝남\n* 결과 파일이 1분 안에 생성됨\n"
)
BUG_CHANGELOG = [
    {"field": "status", "to": "Ready-to-Done", "created": "2026-09-19T17:40:00.000+0900"},
    {"field": "description", "from": BUG_DESC_OLD, "to": BUG_DESC, "created": "2026-09-20T10:12:00.000+0900"},
]
# Bug 문서화 리뷰 샘플(INNO-33): 통과 조건은 모두 만족하고, 문서화 리뷰의 B5와 B7이 보완 필요인 경우.
# feedback 문장은 prompts/types/bug/review.md의 feedback 예시에서 가져온다
BUG_DOC_DESC = """h2. 기본 정보
* 발생 기기/서비스: 포즈 추정 웹 콘솔
* 발생 일자: 2026-09-12
* 발생 장비: Windows 11 PC
* 발생 계정: qa02

h2. 문제(As-Is)
* CSV export 안 됨

h2. 개선(To-Be)
* CSV export가 오류 없이 끝남

h2. 첨부 자료(필수)
* csv-export-error.png
"""
BUG_DOC_CHECKS = [
    {"id": "B3", "result": "pass", "detail": "원인과 해결에 PR 링크 있음"},
    {"id": "B4", "result": "pass", "detail": "해결 뒤 To-Be 동작을 확인한 사람 comment 있음"},
    {"id": "B5", "result": "fail", "detail": "문제(As-Is)에 재현 절차, 조건, 빈도 없음"},
    {"id": "B6", "result": "pass", "detail": "원인을 찾은 과정 comment 있음"},
    {"id": "B7", "result": "fail", "detail": "To-Be 확인 comment에 반영 버전, 환경, 확인 방법 없음"},
    {"id": "R2", "result": "pass", "detail": "원인과 해결마다 링크 있음"},
    {"id": "R3", "result": "pass", "detail": "agent 구역(TL;DR)을 개조식으로 작성함"},
]
BUG_FEEDBACK = [
    {
        "id": "B5",
        "points": [
            "기본 정보에 발생 기기/서비스, 발생 일자, 발생 장비, 발생 계정이 모두 채워져 있음",
            "문제(As-Is)에 'CSV export 안 됨'이라는 현상만 있고 어느 화면에서 무엇을 눌렀는지, 매번 일어나는지가 없음",
        ],
        "request": "문제(As-Is)에 재현 절차(화면과 조작 순서), 발생 조건, 발생 빈도 작성 필요",
    },
    {
        "id": "B6",
        "points": [
            f"원인 분석 comment([comment 2026-09-15|{comment_url('INNO-33', 10210)}])에 오류 로그와 재현 결과가 함께 있어 "
            "원인을 찾은 과정이 분명함"
        ],
    },
    {
        "id": "B7",
        "points": [
            "해결 PR([PR #51 CSV export 인코딩 수정|https://github.com/everex-ai/repo/pull/51]) 병합 뒤 사람 comment"
            f"([comment 2026-09-17|{comment_url('INNO-33', 10220)}])는 '확인함' 한 줄뿐이라 어느 버전, 어느 환경에서 "
            "To-Be 동작을 어떻게 확인했는지 알 수 없음"
        ],
        "request": "To-Be 동작을 확인한 반영 버전, 확인 환경, 확인 방법 comment 필요",
    },
]
BUG_DOC_SUMMARY = f"""* 원인은 CSV 인코딩 처리 누락, 해결은 인코딩 지정으로 2026-09-16 병합됨([PR #51 CSV export 인코딩 수정|https://github.com/everex-ai/repo/pull/51])
* 해결 뒤 To-Be 동작을 확인했다는 사람 comment 있음([comment 2026-09-17|{comment_url("INNO-33", 10220)}])"""
# Issue 검수 샘플(INNO-35): 대응 결과의 근거(I3)가 보완 필요라 보류이고, 문서화 리뷰의 I5가 보완 필요인 경우.
# feedback 문장은 prompts/types/issue/review.md의 feedback 예시에서 가져온다
ISSUE_DESC = """h2. 이슈 유형
* [x] 제안
* [ ] 이슈
* [ ] 기타

h2. 이슈 내용
* 추론 서버의 GPU 메모리 부족으로 배치 크기 16 요청이 실패함
* 서버 증설과 모델 경량화 중 무엇으로 대응할지 결정 필요
"""
ISSUE_CHECKS = [
    {"id": "I3", "result": "fail", "detail": "대응 결과(다른 task로 이관)에 이관해서 만든 task 키 없음"},
    {"id": "I4", "result": "pass", "detail": "제기한 이유와 결정이 필요한 것 있음"},
    {"id": "I5", "result": "fail", "detail": "검토한 대안과 결정 이유 없음"},
    {"id": "R2", "result": "pass", "detail": "대응 결과에 결정 comment 링크 있음"},
    {"id": "R3", "result": "pass", "detail": "agent 구역(TL;DR)을 개조식으로 작성함"},
]
ISSUE_FEEDBACK = [
    {
        "id": "I4",
        "points": [
            "추론 서버의 GPU 메모리 부족으로 요청이 실패한 사례와 결정이 필요한 것(서버 증설 또는 모델 경량화)이 이슈 내용에 "
            "있어 제기한 이유가 분명함"
        ],
    },
    {
        "id": "I5",
        "points": [
            f"이관 결정 comment([comment 2026-09-20|{comment_url('INNO-35', 10300)}])에 결정만 있고 검토한 대안"
            "(서버 증설, 모델 경량화)과 이관을 고른 이유가 없음"
        ],
        "request": "검토한 대안과 이관을 결정한 이유 comment 필요",
    },
]
ISSUE_SUMMARY = (
    "* 대응 결과는 다른 task로 이관하기로 한 결정만 있고 이관해서 만든 task 키가 기록에 없어 보류함"
    f"([comment 2026-09-20|{comment_url('INNO-35', 10300)}])"
)
