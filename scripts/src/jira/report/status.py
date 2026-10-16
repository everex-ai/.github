"""정기 보고가 상태별로 세는 Jira 상태 이름과 표시 순서(ready-to-done은 src/constant/jira.py의 READY_STATUS)."""

from __future__ import annotations

from src.constant.jira import READY_STATUS

IN_PROGRESS, DONE_STATUS = "In Progress", "done"  # Jira 상태 이름. 대소문자 무시로 비교
TODO_STATUS, STOP_STATUS, DELETED_STATUS = "To Do", "Backlog", "Deleted"
CLOSED_STATUSES = (DONE_STATUS, STOP_STATUS, DELETED_STATUS)  # 진행 상태가 아닌 상태
# 새 ticket의 상태별 수를 적는 순서. 목록에 없는 상태는 뒤에 이름 순으로 적는다
STATUS_ORDER = (TODO_STATUS, IN_PROGRESS, READY_STATUS, DONE_STATUS, STOP_STATUS, DELETED_STATUS)
