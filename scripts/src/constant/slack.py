"""Slack 메시지의 들여쓰기, 검수 결과 메시지의 요점 문구, 주간 점검 절, 토큰(인증 키) 갱신 안내."""

from __future__ import annotations

SLACK_INDENT = "    "  # Slack 메시지의 묶음 항목 들여쓰기(공백 4칸). 주간 점검과 정기 보고에 사용
SLACK_MAX_POINTS = 4  # 검수 결과 메시지의 요점 최대 줄 수
SLACK_AGENT_POINTS = 3  # 요점 중 CI agent가 verdict.slack으로 적는 줄의 최대 개수
# 검수 결과 메시지 요점의 문구. 스크립트가 검수 결과를 바꾼 이유는 짧은 꼴로 적는다
SLACK_A2_CHANGED = "request 전환 뒤 완료 기준 바뀜"
SLACK_CHECK_FIX = "{name} 보완 필요"  # name: 검사 이름(CHECK_NAMES)
SLACK_MISSED_REASONED = "미달성 {n}건, 담당자 사유 있음"
SLACK_MISSED_HOLD = "예상 산출물 미달성 {n}건"
SLACK_T3_CHANGED = "예상 산출물 번호가 CI agent 출력과 다름"
SLACK_LATE_STOP = "stop 사유 뒤늦게 작성"
LEAD_NAME_DEFAULT = "팀장"  # Jira에서 팀장 표시 이름을 읽지 못했을 때 메시지에 적는 이름
# (검사 ID, 절 제목, 절 제목 뒤의 조치 문구). 절은 이 순서로 나가고, 조치 문구가 비면 절 제목 뒤에 적지 않는다
ALERT_SECTIONS = [
    ("R4", "stop 사유 미기재", ""),
    ("A1", "영업일 기준 5일 이상 활동 없음", "업데이트 요망"),
    ("A3", "ready-to-done 영업일 기준 3일 이상 대기", "팀장: done 또는 In Progress 전환"),
]
# 실행 모드 이름 -> 실패 알림에 적는 한국어 이름
MODE_KO = {
    "review": "검수 모드",
    "digest": "정리 모드",
    "alerts": "주간 점검 모드",
    "report": "보고 모드",
    "decision": "확인 결과 알림 모드",
}
LEAD_ALERTS = ("A3",)  # 팀장이 할 조치의 알림. 주간 점검 Slack(weekly_lines)에서 담당자 대신 팀장을 멘션한다
LEAD_WEBHOOK_ENV = "SLACK_LEAD_WEBHOOK_URL"  # 팀장 채널 webhook 환경 변수 이름
TOKEN_EXPIRY_DAYS = 30  # 만료까지 남은 날이 이 값 이하인 토큰(인증 키)을 알린다
# 토큰(인증 키) 이름 -> (팀장이 발급하는 곳, 교체하는 곳)
TOKEN_RENEWAL = {
    "JIRA_API_TOKEN": ("Atlassian 계정의 API 토큰 화면에서 발급", "secret JIRA_API_TOKEN"),
    "ORG_READ_TOKEN": ("GitHub Settings의 Personal access tokens 발급", "secret ORG_READ_TOKEN"),
    "F1 PAT": ("GitHub Settings의 Personal access tokens 발급", "Jira Automation F1 설정"),
    "CLAUDE_CODE_OAUTH_TOKEN": ("`claude setup-token`으로 발급", "secret CLAUDE_CODE_OAUTH_TOKEN"),
}
ALERT_LIMIT = 15
