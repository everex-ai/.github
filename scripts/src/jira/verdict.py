"""CI agent 판정(verdict.json)의 형식 검증과 검수 결과 재결정."""

from __future__ import annotations

from src.constant.check import (
    CHECK_NAMES,
    FEEDBACK_DOC_CHECKS,
    NO_VERDICT_KO,
    NOT_A_REASON,
    OWNER_FIXABLE_CHECKS,
    VERDICT_KEY_KO,
    VERDICT_KO,
)
from src.jira.wiki import short_name


def check_label(cid: str) -> str:
    """검사 ID를 검사 표와 같은 꼴 "검사 이름 (검사 ID)"로 적는다. 검사 표보다 먼저 나오는 문장에서 ID만 쓰지 않기 위함."""
    return f"{CHECK_NAMES.get(cid, cid)} ({cid})"


def validate_verdict(v: object, mode: str, key: str) -> str | None:
    """schemas/verdict.json의 핵심 제약만 검사한다.

    feedback의 id는 CI agent가 적는 문서화 리뷰 항목(FEEDBACK_DOC_CHECKS: T4–T7, T9, B5–B7, I4–I5) 중 하나여야 한다.

    Args:
        v: verdict.json을 JSON으로 읽은 값.
        mode: 실행 모드(review: 검수, digest: F3 정리).
        key: task 키(ctx 폴더 이름).

    Returns:
        문제가 있으면 그 이유, 없으면 None. verdict가 "no_verdict"이거나 items에 done이 null인 항목이 있으면(save_output.py의
        판정 없음 기본값) NO_VERDICT_KO를 돌려준다.
    """
    if not isinstance(v, dict):
        return "verdict.json이 객체가 아님"
    for k in ("issueKey", "mode", "verdict", "checks", "items", "extra", "requests"):
        if k not in v:
            return f"필수 키 없음: {k}({VERDICT_KEY_KO[k]})"
    if v["issueKey"] != key:
        return f"issueKey(ticket 키) 불일치: {v['issueKey']}"
    if v["mode"] != mode:
        return f"mode(실행 모드) 불일치: {v['mode']}"
    items = v["items"]
    if v["verdict"] == "no_verdict" or (
        isinstance(items, list) and any(isinstance(a, dict) and "done" in a and a["done"] is None for a in items)
    ):
        return NO_VERDICT_KO
    if v["verdict"] not in ("pass", "fix", "escalate", None):
        return f"verdict(판정) 값 오류: {v['verdict']}"
    if mode == "review" and v["verdict"] is None:
        return "검수 모드인데 verdict(판정)가 비어 있음(null)"
    if not isinstance(v["checks"], list) or not all(
        isinstance(c, dict) and isinstance(c.get("id"), str) and c.get("result") in ("pass", "fail", "n/a")
        for c in v["checks"]
    ):
        return "checks(검사 결과 목록) 형식 오류"
    if not isinstance(v["items"], list) or not all(
        isinstance(a, dict) and "id" in a and isinstance(a.get("done"), bool) and isinstance(a.get("result"), str)
        for a in v["items"]
    ):
        return "items(예상 산출물별 결과 목록) 형식 오류 (항목마다 id(예상 산출물 번호), done(달성 여부), result(결과) 필요)"
    if not isinstance(v["extra"], list) or not all(
        isinstance(a, dict) and isinstance(a.get("result"), str) for a in v["extra"]
    ):
        return "extra(초과 달성 목록) 형식 오류 (항목마다 result(결과) 필요)"
    if not isinstance(v["requests"], list) or not all(isinstance(r, str) for r in v["requests"]):
        return "requests(담당자 요청 목록) 형식 오류"
    fb = v.get("feedback", [])
    if not isinstance(fb, list) or not all(
        isinstance(f, dict) and f.get("id") in FEEDBACK_DOC_CHECKS and isinstance(f.get("points"), list) for f in fb
    ):
        return (
            f"feedback(문서화 리뷰 항목별 피드백) 형식 오류 (항목마다 id(검사 ID)는 {', '.join(check_label(c) for c in FEEDBACK_DOC_CHECKS)} "
            "중 하나, points(피드백 문장 목록) 필요)"
        )
    return None


def check_t8(expected: list[dict], items: list[dict], extra: list[dict]) -> dict:
    """초과 달성마다 추가 사유, 미달성 항목마다 담당자가 남긴 사유가 있는지 계산한다 (Task 검수만).

    미달성 항목의 why가 비었거나 CI agent가 넣는 NOT_A_REASON이면 담당자의 사유가 없는 것으로 센다(owner_reason).

    Args:
        expected: precheck.json의 예상 산출물 목록.
        items: verdict.json의 items.
        extra: verdict.json의 extra.

    Returns:
        T8 검사 결과(id, result, detail, 보완 필요이면 request).
    """
    if not expected:
        return {"id": "T8", "result": "n/a", "detail": "예상 산출물이 없어 대응할 수 없음"}
    no_why_miss = [str(it["id"]) for it in items if not it.get("done") and not owner_reason(it)]
    no_why_extra = [short_name(x["result"]) for x in extra if not (x.get("why") or "").strip()]
    if not no_why_miss and not no_why_extra:
        return {"id": "T8", "result": "pass", "detail": "미달성·초과 달성 항목 모두 사유 있음"}
    parts, asks = [], []
    if no_why_miss:
        parts.append(f"사유 없는 미달성 항목 {', '.join(no_why_miss)}번")
        asks.append(f"미달성 항목 {', '.join(no_why_miss)}번의 달성 근거 또는 미달성 사유")
    if no_why_extra:
        names = ", ".join(f"'{r}'" for r in no_why_extra)
        parts.append(f"사유 없는 초과 달성 {len(no_why_extra)}건({names})")
        asks.append(f"초과 달성 {names}의 추가 사유")
    return {"id": "T8", "result": "fail", "detail": ", ".join(parts), "request": f"{', '.join(asks)} comment 필요"}


def doc_requests(doc_checks: list[dict], feedback: list[dict]) -> list[str]:
    """fail인 문서화 리뷰 항목의 요청 문장."""
    by_id = {f["id"]: f for f in feedback}
    out = []
    for c in doc_checks:
        if c["result"] != "fail":
            continue
        req = c.get("request") if c["id"] == "T8" else (by_id.get(c["id"], {}).get("request") or "").strip()
        if req:
            out.append(req)
    return out


def owner_reason(item: dict) -> str:
    """미달성 항목에 담당자가 남긴 사유. why가 비었거나 CI agent가 넣는 NOT_A_REASON이면 빈 문자열.

    Args:
        item: verdict.json items의 항목 하나.

    Returns:
        사유 문장(wiki markup) 또는 빈 문자열.
    """
    why = (item.get("why") or "").strip()
    return "" if why == NOT_A_REASON else why


def owner_fails(checks: list[dict]) -> list[dict]:
    """실패한 검사 중 담당자가 고칠 수 있는 검사(OWNER_FIXABLE_CHECKS).

    Args:
        checks: 검사 결과 목록.

    Returns:
        실패한 담당자 검사 목록. checks의 순서를 따른다.
    """
    return [c for c in checks if c["result"] == "fail" and c["id"] in OWNER_FIXABLE_CHECKS]


def last_check(checks: list[dict], cid: str) -> dict:
    """검사 결과 목록에서 ID가 cid인 검사를 찾는다. 검사 표(check_table)처럼 같은 ID가 여러 번 있으면 마지막 것을 쓴다.

    Args:
        checks: 검사 결과 목록.
        cid: 검사 ID.

    Returns:
        찾은 검사 결과. 없으면 빈 dict.
    """
    return next((c for c in reversed(checks) if c["id"] == cid), {})


def recheck_verdict(
    v: str, itype: str, items: list[dict], checks: list[dict], gate: bool, doc_fails: list[str]
) -> tuple[str, str | None]:
    """Task의 미달성 항목과 사유로 검수 결과를 다시 정한다.

    미달성 항목마다 담당자가 남긴 사유가 있고, 담당자가 고칠 수 있는 검사가 실패하지 않았고, 게이트 모드에서
    문서화 리뷰에 보완 필요가 없으면 검토 요청으로 바꾼다. 그렇지 않은데 통과이면 보류로 바꾼다.
    Task가 아니거나, 이미 검토 요청이거나, 미달성 항목이 없으면 바꾸지 않는다.

    Args:
        v: 지금의 검수 결과 코드값(pass, fix, escalate).
        itype: work type(Task, Bug, Issue).
        items: verdict.json의 items.
        checks: 검수 comment의 검사 표에 들어가는 검사 결과 목록.
        gate: 게이트 모드 여부.
        doc_fails: 보완 필요인 문서화 리뷰 항목 ID 목록.

    Returns:
        (새 검수 결과 코드값, 바꾼 이유). 바꾸지 않았으면 이유는 None.
    """
    if itype != "Task" or v == "escalate":
        return v, None
    missed = [it for it in items if not it.get("done")]
    if not missed:
        return v, None
    unreasoned = [it for it in missed if not owner_reason(it)]
    if not unreasoned and not owner_fails(checks) and not (gate and doc_fails):
        return "escalate", f"미달성 항목마다 담당자가 남긴 사유가 있어 {VERDICT_KO[v]}에서 검토 요청으로 바꿈"
    if v == "pass":
        return "fix", "미달성 항목이 있어 통과에서 보류로 바꿈"
    return v, None


def doc_review_result(doc_checks: list[dict]) -> str:
    """문서화 리뷰 결과 값. 문서화 리뷰 항목에 보완 필요가 하나라도 있으면 "보완 필요", 없으면 "통과".

    Args:
        doc_checks: 문서화 리뷰 항목(Task T4–T9, Bug B5–B7, Issue I4–I5)의 검사 결과 목록.

    Returns:
        "보완 필요" 또는 "통과".
    """
    return "보완 필요" if any(c["result"] == "fail" for c in doc_checks) else "통과"
