"""사용자 대응표 경로."""

from __future__ import annotations

from pathlib import Path

# Jira accountId -> Slack member ID 대응표. repo 루트의 config/에 있다
SLACK_USERS = Path(__file__).parents[3] / "config" / "slack-users.json"
