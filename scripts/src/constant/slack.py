"""Slack 메시지의 들여쓰기, 맺음 문구, 주간 점검 절, 토큰(인증 키) 갱신 안내."""

from __future__ import annotations

SLACK_INDENT = "    "  # Slack 메시지의 묶음 항목 들여쓰기(공백 4칸). 검수 알림과 주간 점검에 사용
SLACK_CLOSING = {
    "escalate": "팀장이 ticket을 확인한 뒤 In Progress 또는 done으로 직접 전환 필요",
    "pass": "팀장이 ticket을 확인한 뒤 done으로 직접 전환 필요",
    "fix": "팀장이 ticket을 확인한 뒤 In Progress로 직접 전환 필요",
}
# (검사 ID, 절 제목, 절 제목 뒤의 조치 문구). 절은 이 순서로 나간다
ALERT_SECTIONS = [
    ("R4", "stop 사유 미기재", "담당자: stop 사유 comment 필요"),
    ("A1", "영업일 기준 5일 이상 활동 없음", "담당자: 진행 상황 comment, 또는 멈춘 ticket이면 stop 전환 필요"),
    ("A3", "ready-to-done 영업일 기준 3일 이상 대기", "팀장: done 전환, 또는 보완이 필요하면 In Progress 전환 필요"),
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
