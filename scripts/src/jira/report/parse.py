"""Jira comment와 변경 이력, 실행 기록(property)을 정기 보고용 값으로 읽는다(검수 comment 문구 파싱 포함)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from src.constant.check import RESULT_KO
from src.constant.time import KST
from src.jira.report.model import TOKEN_KO, Run, StatusChange
from src.jira.wiki import find_section

# scripts/src/constant/check.py의 VERDICT_KO(정리 모드 None 제외)와 같은 값
VERDICT_KO = {"pass": "통과", "fix": "보류", "escalate": "검토 요청"}
RESULT_CODE = {label: code for code, label in RESULT_KO.items()}  # 검사 표 결과 칸 -> pass, fail, n/a
VERDICT_PREFIX, VERDICT_SUFFIX = (
    "검수 결과: *",
    "*",
)  # scripts/src/jira/comment.py review_comment의 첫 줄 "검수 결과: *통과*"
VERDICT_RE = re.compile(
    rf"^{re.escape(VERDICT_PREFIX)}({'|'.join(map(re.escape, VERDICT_KO.values()))}){re.escape(VERDICT_SUFFIX)}\s*$",
    re.M,
)
# scripts/src/jira/comment.py doc_review_comment의 첫 줄 "문서화 리뷰 결과(관찰 모드): *통과*"
DOC_REVIEW_RE = re.compile(r"^문서화 리뷰 결과\(관찰 모드\): \*[^*\n]+\*\s*$", re.M)
ROW_RE = re.compile(  # 검사 표의 행 "| 이름 (ID) | 결과 | 내용 |". 내용 칸은 wiki 링크의 "|"를 품을 수 있어 읽지 않는다
    rf"^\|(?!\|)\s*(?P<label>[^|]*?)\s*\|\s*(?P<result>{'|'.join(map(re.escape, RESULT_CODE))})\s*\|", re.M
)
LABEL_ID_RE = re.compile(r"\(([^()\s]+)\)$")  # check_label의 끝 "(ID)"
# jira-doc(scripts/src/jira/wiki.py의 set_section)이 고치는 description 구역. 이 구역만 바뀐 수정은 업데이트로 세지 않는다
DELIVERABLES_SECTION = "결과 산출물"


def parse_ts(s: str | None) -> dt.datetime | None:
    """Jira 시각 문자열(예: 2026-09-02T10:31:00.000+0900)을 시간대가 있는 datetime으로 바꾼다.

    Args:
        s: Jira 시각 문자열 또는 ISO 8601 문자열. 시간대가 없으면 한국 시간으로 본다.

    Returns:
        datetime. 비었거나 읽을 수 없으면 None.
    """
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=KST)


def is_status(name: str, target: str) -> bool:
    """Jira 상태 이름이 target과 같은지 대소문자를 무시하고 비교한다.

    Args:
        name: 상태 이름.
        target: 비교할 상태 이름.

    Returns:
        같으면 True.
    """
    return name.strip().lower() == target.strip().lower()


def table_rows(body: str) -> list[tuple[str, str, str]]:
    """comment 본문의 검사 표에서 행을 읽는다.

    Args:
        body: comment 본문(wiki markup).

    Returns:
        (검사 ID, 검사 칸 문구 "검사 이름 (ID)", 결과 코드값(pass, fail, n/a)) 목록. 표 순서.
    """
    out: list[tuple[str, str, str]] = []
    for row in ROW_RE.finditer(body):
        idm = LABEL_ID_RE.search(row["label"])
        if idm:
            out.append((idm.group(1), row["label"], RESULT_CODE[row["result"]]))
    return out


def parse_review(body: str) -> tuple[str, dict[str, str]] | None:
    """검수 comment 본문에서 판정과 검사 표의 결과를 읽는다.

    Args:
        body: comment 본문(wiki markup).

    Returns:
        (판정 문구, {검사 ID: 결과 코드값(pass, fail, n/a)}). 판정 줄이 없으면 None.
    """
    m = VERDICT_RE.search(body)
    if not m:
        return None
    return m.group(1), {cid: code for cid, _, code in table_rows(body)}


def parse_doc_review(body: str) -> dict[str, str] | None:
    """문서화 리뷰 comment 본문에서 검사 표의 결과를 읽는다.

    Args:
        body: comment 본문(wiki markup).

    Returns:
        {검사 ID: 결과 코드값(pass, fail, n/a)}. "문서화 리뷰 결과(관찰 모드): …" 줄이 없으면 None.
    """
    if not DOC_REVIEW_RE.search(body):
        return None
    return {cid: code for cid, _, code in table_rows(body)}


def status_changes(changelog: list[dict]) -> list[StatusChange]:
    """Jira changelog 응답(Jira.changelog)에서 status 변경만 시각 순서로 뽑는다.

    Args:
        changelog: Jira.changelog의 결과. 항목마다 created, author, items(field, fromString, toString).

    Returns:
        status 변경 목록. 전환 전 상태(src)와 전환한 사람의 accountId(author)도 담는다.
    """
    out: list[StatusChange] = []
    for history in changelog:
        at = parse_ts(history.get("created"))
        if at is None:
            continue
        author = (history.get("author") or {}).get("accountId") or ""
        for item in history.get("items") or []:
            if item.get("field") == "status":
                out.append(StatusChange(at, item.get("toString") or "", item.get("fromString") or "", author))
    return sorted(out, key=lambda c: c.at)


def without_deliverables(desc: str) -> str:
    """description에서 결과 산출물 구역(제목 줄 포함)을 빼고 공백을 한 칸으로 줄인 문자열.

    구역은 scripts/src/jira/wiki.py의 find_section으로 찾는다. set_section이 구역을 맨 뒤에 새로 붙이며 앞 본문 끝의
    공백을 지우므로 공백 차이는 무시한다.

    Args:
        desc: description(wiki markup).

    Returns:
        비교용 문자열.
    """
    desc = desc or ""
    hit = find_section(desc, DELIVERABLES_SECTION)
    if hit:
        desc = desc[: hit[1]] + desc[hit[3] :]
    return " ".join(desc.split())


def description_edits(changelog: list[dict]) -> list[dt.datetime]:
    """Jira changelog 응답(Jira.changelog)에서 결과 산출물 구역 밖이 바뀐 description 수정 시각을 시각 순서로 뽑는다.

    jira-doc이 결과 산출물 구역만 고친 수정은 사람의 업데이트가 아니므로 뺀다. jira-doc은 팀장 계정으로 기록되어
    작성자로는 구분할 수 없어, 수정 전후(fromString, toString)에서 그 구역을 뺀 나머지를 비교한다.

    Args:
        changelog: Jira.changelog의 결과. 항목마다 created, items(field, fromString, toString).

    Returns:
        결과 산출물 구역 밖이 바뀐 description 항목이 있는 변경 이력의 시각 목록.
    """
    out: list[dt.datetime] = []
    for history in changelog:
        at = parse_ts(history.get("created"))
        items = [i for i in history.get("items") or [] if i.get("field") == "description"]
        changed = any(
            without_deliverables(i.get("fromString") or "") != without_deliverables(i.get("toString") or "")
            for i in items
        )
        if at is not None and changed:
            out.append(at)
    return sorted(out)


def run_branch(entry: dict) -> str:
    """runs 항목 하나를 Claude 사용량 네 갈래 중 하나로 나눈다.

    verdict가 failed이거나 retry가 true면 재실행·실패, mode가 digest면 정리 모드, 그 밖에는 검수다.
    검수는 requestNo가 2 이상이면 반려 cycle 재검수, 1 이하이거나 없으면 첫 검수다.
    같은 request 전환에 다시 실행한 검수는 ticket의 실행 기록을 함께 봐야 하므로 mark_reruns가 바꾼다.

    Args:
        entry: runs 항목.

    Returns:
        BRANCHES의 키(first, cycle, retry, digest).
    """
    if entry.get("verdict") == "failed" or entry.get("retry") is True:
        return "retry"
    if entry.get("mode") == "digest":
        return "digest"
    no = entry.get("requestNo")
    return "cycle" if isinstance(no, int) and not isinstance(no, bool) and no >= 2 else "first"


def parse_run(entry: Any) -> Run | None:
    """runs 항목 하나를 Run으로 바꾼다.

    Args:
        entry: runs 항목.

    Returns:
        Run. dict가 아니거나 at을 읽을 수 없으면 None.
    """
    if not isinstance(entry, dict):
        return None
    at = parse_ts(str(entry.get("at") or ""))
    if at is None:
        return None
    cost = entry.get("costUsd")
    no = entry.get("requestNo")
    tokens = entry.get("tokens") if isinstance(entry.get("tokens"), dict) else {}
    return Run(
        at=at,
        branch=run_branch(entry),
        mode=str(entry.get("mode") or ""),
        request_no=no if isinstance(no, int) and not isinstance(no, bool) else None,
        cost=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        tokens={k: int(tokens.get(k) or 0) for k in TOKEN_KO},
        failed=entry.get("verdict") == "failed",
    )


def mark_reruns(runs: list[Run]) -> list[Run]:
    """같은 request 전환에 다시 실행한 검수를 재실행·실패 갈래로 바꾼다.

    한 ticket의 review 실행 중 requestNo가 같고 반영에 성공한 실행이 앞서 있으면(실행 시각 순) 그 뒤 실행은
    재실행·실패다. 앞선 실행이 모두 반영에 실패했으면 첫 성공 실행은 원래 갈래(첫 검수, 반려 cycle 재검수)로 둔다.

    Args:
        runs: 한 ticket의 실행 기록.

    Returns:
        실행 시각 순으로 정렬한 실행 기록. 같은 객체의 branch를 바꾼다.
    """
    seen: set[int] = set()
    out = sorted(runs, key=lambda r: r.at)
    for r in out:
        if r.mode != "review" or r.request_no is None:
            continue
        if r.request_no in seen:
            r.branch = "retry"
        if not r.failed:
            seen.add(r.request_no)
    return out
