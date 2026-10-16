"""검사 항목 이름과 표시 순서, 검사 결과와 판정의 표시 문구."""

from __future__ import annotations

VERDICT_KO = {"pass": "통과", "fix": "보류", "escalate": "검토 요청", None: "정리"}
# 검수 결과를 다시 정하는 규칙(recheck_verdict). 검사 이름은 CHECK_NAMES, prompts/review.md와 prompts/types/<work type>/review.md의 검사 항목 표와 같다
OWNER_FIXABLE_CHECKS = (
    "T1",
    "T2",
    "R2",
    "R4",
    "B1",
    "B2",
    "B3",
    "B4",
    "I1",
    "I2",
    "I3",
)  # 담당자가 고칠 수 있는 검사. 실패하면 보류
SENTENCE_DETAIL_CHECKS = ("R4",)  # detail이 이유 문장이라 Slack 보류 이유에 검사 이름 없이 적는 검사
NOT_A_REASON = "근거 부족으로 확인 불가"  # CI agent가 why에 넣는 문구. 담당자가 남긴 사유로 세지 않는다
DECISION_KO = {
    "done": "통과",
    "in progress": "보류",
}  # 팀장 확인 결과: ready-to-done에서 전환한 도착 상태(소문자) -> 결과 이름
SECTION_NOTE = {
    "Task": 'agent가 comment, sub-task, PR 기록을 근거로 작성함. 고칠 내용은 직접 수정하지 않고 "정정:"으로 시작하는 comment로 요청 필요.'
}
SOURCE_KO = {
    "review": "검수",
    "digest": "F3 정리: Jira ticket 화면의 문서 정리 버튼",
}  # 결과 산출물 구역의 안내 줄 끝에 붙여 출처를 보인다
# 검사 항목의 표시 순서와 이름. Jira comment의 검사 표는 항상 이 순서로, 이 이름으로 나간다 (ID는 괄호로 덧붙인다).
# 정의는 prompts/review.md와 prompts/types/<work type>/review.md의 검사 항목 표와 같다(A1, A3는 docs/jira-doc-agent/README.md의 "스크립트가 계산하는 값" 절).
CHECK_NAMES = {
    "T1": "예상 산출물 작성",
    "T2": "진행 배경 작성",
    "T3": "예상 산출물과 결과 산출물의 1:1 대응",
    "T4": "진행 배경 충실도",
    "T5": "예상 산출물 분할 단위",
    "T6": "진행 기록 comment",
    "T7": "sub-task 기록",
    "T8": "초과 달성·미달성 사유",
    "T9": "진행 배경부터 결과 산출물까지의 연결",
    "B1": "기본 정보와 문제(As-Is) 작성, 첨부 파일",
    "B2": "개선(To-Be) 작성",
    "B3": "원인과 해결의 근거 링크",
    "B4": "To-Be 동작 확인 comment",
    "B5": "문제(As-Is) 재현 정보",
    "B6": "원인 분석 기록",
    "B7": "해결 확인 기록",
    "I1": "이슈 유형 선택",
    "I2": "이슈 내용 작성",
    "I3": "대응 결과의 근거",
    "I4": "이슈 내용 충실도",
    "I5": "논의와 결정 기록",
    "R2": "1년 뒤에도 이해 가능한 기록",
    "R3": "개조식 작성",
    "R4": "stop 사유 comment",
    "A1": "영업일 기준 5일 이상 활동 없음",
    "A2": "완료 기준의 사후 변경",
    "A3": "ready-to-done 영업일 기준 3일 이상 대기",
}
CHECK_ORDER = {cid: i for i, cid in enumerate(CHECK_NAMES)}
RESULT_KO = {
    "pass": "✅ 만족",
    "fail": "❌ 보완 필요",
    "n/a": "➖ 해당 없음",
}  # 검사 표 결과 칸. 모든 검사 항목에 같은 표시
# 문서화 리뷰(검수 모드)의 work type별 항목. 관찰 모드에서는 검수 결과를 바꾸지 않고 팀장용 comment로, 게이트 모드에서는 보류로 이어진다
DOC_CHECKS = {"Task": ("T4", "T5", "T6", "T7", "T8", "T9"), "Bug": ("B5", "B6", "B7"), "Issue": ("I4", "I5")}
ALL_DOC_CHECKS = tuple(cid for ids in DOC_CHECKS.values() for cid in ids)  # 세 work type의 문서화 리뷰 항목 전체
FEEDBACK_DOC_CHECKS = tuple(
    cid for cid in ALL_DOC_CHECKS if cid != "T8"
)  # CI agent가 feedback에 적는 항목. T8은 이 스크립트가 계산(check_t8)
NO_REASON = "사유 미기재"
VERDICT_KEY_KO = {
    "issueKey": "ticket 키",
    "mode": "실행 모드",
    "verdict": "판정",
    "checks": "검사 결과 목록",
    "items": "예상 산출물별 결과 목록",
    "extra": "초과 달성 목록",
    "requests": "담당자 요청 목록",
}
VERDICT_FILE_KO = (
    "CI agent(GitHub Actions에서 실행되는 문서화 Agent)의 판정을 저장한 파일(verdict.json)"  # 실패 알림에 나가는 이름
)
REVIEW_OUTPUT_FILES_KO = {
    "tldr.wiki": "TL;DR 본문",
    "comment.wiki": "검수 comment의 결과 요약",
}  # 검수 모드에서 save_output.py가 verdict.json과 함께 쓰는 출력 파일과 실패 알림에 나가는 뜻
NO_VERDICT_KO = "CI agent 판정 없음"  # save_output.py의 판정 없음 기본값(verdict "no_verdict", items의 done null)이 그대로 남았을 때의 실패 이유
STATE_NOTE = (
    "상태를 바꾸지 않고 팀장 확인을 요청함",
    "상태 변경 없음",
)  # 어떤 검수 결과에서도 Jira 상태를 바꾸지 않는다. (검수 comment와 Actions 요약 문구, Slack 첫 줄 문구)
