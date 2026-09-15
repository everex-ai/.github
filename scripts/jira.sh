#!/usr/bin/env bash
# 수동 확인용 Jira REST v2 래퍼. 워크플로는 python 스크립트(jira_api.py)를 쓰고, 이 파일은 사람이 터미널에서 점검할 때 쓴다.
# 사용법: scripts/jira.sh <command> <args...>   (env: JIRA_CLOUD_ID, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_TLDR_FIELD_ID)
set -euo pipefail
BASE="https://api.atlassian.com/ex/jira/${JIRA_CLOUD_ID}/rest/api/2"
req() { curl -sS --fail -u "${JIRA_EMAIL}:${JIRA_API_TOKEN}" -H "Content-Type: application/json" "$@"; }   # 4xx/5xx면 종료 코드 22

case "${1:-}" in
  issue)      req "${BASE}/issue/$2?fields=summary,status,assignee,created,updated,labels,issuetype,parent,subtasks,description,${JIRA_TLDR_FIELD_ID:-}" ;;
  comments)   req "${BASE}/issue/$2/comment?orderBy=created&maxResults=100&startAt=${3:-0}" ;;
  changelog)  req "${BASE}/issue/$2/changelog?maxResults=100&startAt=${3:-0}" ;;
  search)     req -X POST "${BASE}/search/jql" -d "$(jq -n --arg q "$2" '{jql:$q, fields:["key","summary","status","updated","parent"], maxResults:100}')" ;;
  prop-get)   req "${BASE}/issue/$2/properties/ai-doc-agent" 2>/dev/null || echo '{"value":{}}' ;;
  fields)     req "${BASE}/field" | jq -r '.[] | select(.name | test("TL;?DR"; "i")) | "\(.id)\t\(.name)\t\(.schema.type)"' ;;
  transitions) req "${BASE}/issue/$2/transitions" | jq -r '.transitions[] | "\(.id)\t\(.name)\t-> \(.to.name)"' ;;
  # 아래는 쓰기 명령. 평소에는 apply.py가 하므로 수동 복구 때만 쓴다.
  set-desc)   req -X PUT "${BASE}/issue/$2" -d "$(jq -n --rawfile d "$3" '{fields:{description:$d}}')" ;;
  set-tldr)   req -X PUT "${BASE}/issue/$2" -d "$(jq -n --arg f "$JIRA_TLDR_FIELD_ID" --rawfile v "$3" '{fields:{($f):$v}}')" ;;
  comment)    req -X POST "${BASE}/issue/$2/comment" -d "$(jq -n --rawfile b "$3" '{body:$b}')" ;;
  transition) tid=$(req "${BASE}/issue/$2/transitions" | jq -r --arg n "$3" '[.transitions[] | select(.name==$n)] | first | .id // empty')
              [ -n "$tid" ] || { echo "transition '$3' not available on $2" >&2; exit 3; }
              req -X POST "${BASE}/issue/$2/transitions" -d "{\"transition\":{\"id\":\"${tid}\"}}" ;;
  prop-set)   req -X PUT "${BASE}/issue/$2/properties/ai-doc-agent" -d @"$3" ;;
  prop-del)   req -X DELETE "${BASE}/issue/$2/properties/ai-doc-agent" ;;
  *) echo "usage: $0 {issue|comments|changelog|search|prop-get|fields|transitions|set-desc|set-tldr|comment|transition|prop-set|prop-del} ..." >&2; exit 2 ;;
esac
