"""Jira 관련 상수: property 키, comment 표식, 상태 이름, work type."""

from __future__ import annotations

import os

PROPERTY_KEY = "ai-doc-agent"
AGENT_MARK = "[ai-doc-agent]"  # agent가 쓰는 모든 comment의 첫 줄 표식
# 늦게 남긴 stop 사유 comment의 시작 표시. src/jira/precheck/history.py(R4 계산)와 src/jira/issue.py(R4 요청 문장)가 사용함
STOP_REASON_PREFIX = "stop 사유:"
# Jira 상태 이름. 비교는 is_status로 대소문자 무시
STOP_TO, WORK_STATUS, RTD = "Backlog", "In Progress", "ready-to-done"
READY_STATUS = RTD  # apply 쪽 이름. 소문자로 바꾼 상태 이름과 비교
TARGET_TYPES = [t.strip() for t in os.environ.get("TARGET_TYPES", "Task,Bug,Issue").split(",")]
SEARCH_LIMIT = 5000  # Jira.search의 결과 상한. 닿으면 정기 보고 메시지, jira_metrics의 summary.md에 알린다
TYPE_ALIAS = {"작업": "Task", "버그": "Bug", "이슈": "Issue", "task": "Task", "bug": "Bug", "issue": "Issue"}
# 시험 ticket 제목 머리말. 정기 보고에서 뺀다
TEST_TITLE_PREFIXES = ("[jira-doc 테스트]", "[jira-doc 시험]", "[automation 테스트]")
