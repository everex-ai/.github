"""샘플 생성에 쓰는 가짜 Jira와 ctx/(수집 결과 폴더), out/(CI agent 출력 폴더)의 입력 파일 쓰기."""

from __future__ import annotations

import json
from pathlib import Path

ASSIGNEE, LEAD = "acc-assignee", "acc-lead"
SITE = "https://example.atlassian.net"


class FakeJira:
    """apply.py가 부르는 Jira 메서드를 흉내 내고, 쓰려던 값을 모아 둔다."""

    tldr_field = "customfield_10650"

    def __init__(self, descriptions: dict[str, str], lead_name: str = "Kai") -> None:
        self.descriptions = descriptions
        self.lead_name = lead_name
        self.comments: list[tuple[str, str]] = []
        self.fields: dict[str, dict] = {}
        self.props_set: list[tuple[str, dict]] = []

    def issue(self, key: str, fields: list[str]) -> dict:
        """description 필드만 돌려준다.

        Args:
            key: task 키.
            fields: 요청한 필드 이름 목록(사용하지 않음).

        Returns:
            Jira issue 응답 모양의 dict.
        """
        return {"fields": {"description": self.descriptions.get(key, "")}}

    def user_name(self, account_id: str) -> str:
        """팀장 표시 이름을 돌려준다.

        Args:
            account_id: 사용자 accountId.

        Returns:
            account_id가 비어 있으면 빈 문자열, 아니면 lead_name.
        """
        return self.lead_name if account_id else ""

    def set_fields(self, key: str, fields: dict) -> None:
        """필드 값을 모아 둔다.

        Args:
            key: task 키.
            fields: 필드 ID와 값.
        """
        self.fields.setdefault(key, {}).update(fields)

    def add_comment(self, key: str, body: str) -> dict:
        """comment 본문을 모아 두고, Jira 응답처럼 comment ID를 돌려준다.

        Args:
            key: task 키.
            body: comment 본문(wiki markup).

        Returns:
            Jira comment 응답 모양의 dict. id는 10200부터 comment마다 1씩 늘어나는 숫자 문자열.
        """
        self.comments.append((key, body))
        return {"id": str(10199 + len(self.comments))}

    def prop_get(self, key: str) -> dict:
        """빈 property를 돌려준다.

        Args:
            key: task 키.

        Returns:
            빈 dict.
        """
        return {}

    def prop_set(self, key: str, prop: dict) -> None:
        """property 쓰기 요청을 모아 둔다.

        Args:
            key: task 키.
            prop: property 값.
        """
        self.props_set.append((key, prop))


def write_json(path: Path, data: object) -> None:
    """JSON을 UTF-8로 쓴다. 상위 폴더는 만든다.

    Args:
        path: 파일 경로.
        data: 저장할 값.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def comment_url(key: str, cid: int) -> str:
    """Jira comment 링크를 만든다.

    Args:
        key: task 키.
        cid: comment ID.

    Returns:
        comment URL.
    """
    return f"{SITE}/browse/{key}?focusedCommentId={cid}"


def write_issue(
    ctx: Path,
    key: str,
    itype: str,
    expected: list[dict],
    checks: dict,
    status: str = "ready-to-done",
    summary: str = "",
    stops: list[dict] | None = None,
) -> None:
    """ctx/<KEY>/에 apply.py가 읽는 입력(issue.json, precheck.json, meta.json)을 쓴다.

    Args:
        ctx: ctx 폴더.
        key: task 키.
        itype: work type(Task, Bug, Issue).
        expected: 예상 산출물 목록.
        checks: precheck가 계산한 검사 결과.
        status: 지금 Jira 상태 이름.
        summary: task 제목.
        stops: precheck.stop_events의 결과. 주면 precheck.json의 stops에 쓴다.
    """
    d = ctx / key
    issue = {"key": key, "type": itype, "summary": summary, "status": status, "url": f"{SITE}/browse/{key}"}
    write_json(d / "issue.json", issue | {"assignee": {"accountId": ASSIGNEE}})
    pre = {"expected": expected, "checks": checks}
    if stops is not None:
        pre["stops"] = stops
    write_json(d / "precheck.json", pre)
    write_json(d / "meta.json", {"inputHash": "sample"})


def write_out(out: Path, key: str, verdict: dict | None, summary: str = "") -> None:
    """out/<KEY>/에 CI agent 출력(verdict.json, tldr.wiki, comment.wiki)을 쓴다.

    Args:
        out: out 폴더.
        key: task 키.
        verdict: verdict.json 내용. None이면 쓰지 않는다(Claude 단계 실패).
        summary: comment.wiki 내용.
    """
    o = out / key
    o.mkdir(parents=True, exist_ok=True)
    if verdict is not None:
        write_json(o / "verdict.json", verdict)
    (o / "tldr.wiki").write_text("h2. Summary\n* (CI agent가 작성하는 부분. 샘플에서 다루지 않음)\n", encoding="utf-8")
    if summary:
        (o / "comment.wiki").write_text(summary, encoding="utf-8")


def agent_section(desc: str) -> str:
    """description에서 결과 산출물 구역(h2. 결과 산출물부터 끝까지)만 떼어 낸다.

    Args:
        desc: description 전체.

    Returns:
        결과 산출물 구역.
    """
    i = desc.find("h2. 결과 산출물")
    return desc[i:].strip() + "\n" if i >= 0 else ""
