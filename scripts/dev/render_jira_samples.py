#!/usr/bin/env python3
"""jira-doc이 결과로 산출하는 문서 중 코드가 조립하는 부분의 샘플을 만든다. Jira, Slack에 쓰지 않는다.

    python scripts/dev/render_jira_samples.py --out reports/result-doc-style/after

scripts/apply.py의 apply_issue, apply_alerts, slack_weekly를 가짜 Jira 객체와 샘플 입력으로 실행하고,
Jira에 쓰려던 comment와 필드 값, Slack에 보내려던 본문을 파일로 저장한다.
샘플 입력의 문구는 prompts/types/task.md의 출력 예시에서 가져온다.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
FIXED_NOW = dt.datetime(2026, 9, 29, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
ASSIGNEE, LEAD = "acc-assignee", "acc-lead"
SITE = "https://example.atlassian.net"


def load_module(name: str, path: Path) -> types.ModuleType:
    """스크립트 파일을 지정한 모듈 이름으로 불러온다.

    scripts/apply.py와 scripts/review/apply.py처럼 파일 이름이 같은 스크립트가 섞이지 않게 하려고,
    불러오는 동안 스크립트가 바꾼 sys.path를 원래대로 되돌린다.

    Args:
        name: sys.modules에 등록할 모듈 이름.
        path: 스크립트 파일 경로.

    Returns:
        불러온 모듈.
    """
    if name in sys.modules:
        return sys.modules[name]
    saved = list(sys.path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        sys.path[:] = saved
    return mod


def load_modules() -> tuple[types.ModuleType, types.ModuleType]:
    """scripts/apply.py와 scripts/precheck.py를 jira_apply, jira_precheck라는 이름으로 불러온다.

    Returns:
        (jira_apply, jira_precheck) 모듈.
    """
    return load_module("jira_apply", SCRIPTS / "apply.py"), load_module("jira_precheck", SCRIPTS / "precheck.py")


class FixedDatetime(dt.datetime):
    """now()가 항상 FIXED_NOW를 돌려주는 datetime. 샘플의 날짜를 고정한다."""

    @classmethod
    def now(cls, tz: dt.tzinfo | None = None) -> FixedDatetime:
        """고정 시각을 돌려준다.

        Args:
            tz: 시간대. 주면 그 시간대로 바꾼다.

        Returns:
            고정 시각.
        """
        t = cls.fromtimestamp(FIXED_NOW.timestamp(), FIXED_NOW.tzinfo)
        return t.astimezone(tz) if tz else t.replace(tzinfo=None)


class FakeJira:
    """apply.py가 부르는 Jira 메서드를 흉내 내고, 쓰려던 값을 모아 둔다."""

    tldr_field = "customfield_10650"

    def __init__(self, descriptions: dict[str, str]) -> None:
        self.descriptions = descriptions
        self.comments: list[tuple[str, str]] = []
        self.fields: dict[str, dict] = {}
        self.transitioned_to: list[tuple[str, str]] = []

    def issue(self, key: str, fields: list[str]) -> dict:
        """description 필드만 돌려준다.

        Args:
            key: task 키.
            fields: 요청한 필드 이름 목록(사용하지 않음).

        Returns:
            Jira issue 응답 모양의 dict.
        """
        return {"fields": {"description": self.descriptions.get(key, "")}}

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
        """property 쓰기를 무시한다.

        Args:
            key: task 키.
            prop: property 값.
        """

    def transition_to(self, key: str, status: str) -> None:
        """도착 상태 이름으로 한 상태 전환 요청을 모아 둔다.

        Args:
            key: task 키.
            status: 도착 상태 이름.
        """
        self.transitioned_to.append((key, status))


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


TASK_DESC = """h2. 진행 배경
* export 오류로 고객 불만 2건이 접수되어 원인 확인과 재발 방지가 필요함
* 관련 task: INNO-12

h2. 예상 산출물
* 재현 테스트 추가
* 오류율 1% 미만
* ONNX 변환
"""
TASK_ITEMS = [
    {
        "id": "1",
        "done": True,
        "result": "export 오류 재현 테스트 3건 추가, 2026-09-11 병합",
        "evidence": "[PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]",
    },
    {
        "id": "2",
        "done": False,
        "result": "comment에 모델 v2의 검증 데이터(val set) export 오류율 0.7%라는 수치만 있고 데이터 규모, 계산 방법, 리포트 링크 없음",
        "evidence": f"[comment 2026-09-12|{comment_url('INNO-17', 10123)}]",
        "why": "근거 부족으로 확인 불가",
        "reason": "평가 리포트 링크, 데이터 규모, 오류율 계산 방법 comment 필요",
    },
    {
        "id": "3",
        "done": False,
        "result": "ONNX 변환 스크립트 초안까지 작성",
        "why": f"배포 대상 기기 사양이 확정되지 않아 변환 옵션을 정하지 못함([comment 2026-09-13|{comment_url('INNO-17', 10140)}])",
        "evidence": f"[comment 2026-09-13|{comment_url('INNO-17', 10140)}]",
        "reason": "기기 사양 확정 뒤 후속 task 키 comment 필요",
    },
]
TASK_EXTRA = [
    {
        "result": "export 오류율을 매일 집계하는 대시보드 추가",
        "why": f"재발 여부를 매일 확인하자는 팀 회의 결정([comment 2026-09-10|{comment_url('INNO-17', 10101)}])",
        "evidence": "[PR #45 오류율 대시보드|https://github.com/everex-ai/repo/pull/45]",
    },
    {
        "result": "오류 로그 수집 스크립트 추가",
        "why": "",
        "evidence": "[PR #46 오류 로그 수집|https://github.com/everex-ai/repo/pull/46]",
    },
]
TASK_FEEDBACK = [
    {"id": "T4", "points": ["평가 지표를 바꾸게 된 계기(고객 불만 2건)가 적혀 있어 이유가 분명함"]},
    {
        "id": "T5",
        "points": [
            "1번 항목은 결과 하나로 나뉨",
            "3번 'ONNX 변환'은 끝났다고 판단할 기준(대상 기기, 허용 지연)이 없음",
        ],
        "request": "예상 산출물 3번에 끝났다고 판단할 기준(대상 기기, 허용 지연) 추가 필요",
    },
    {
        "id": "T6",
        "points": [
            "작업 기간(첫 in-progress 전환부터 마지막 request 전환(담당자의 완료 요청)까지) 영업일 기준 8일 동안 "
            "사람 comment 1건(request 전환 당일)뿐이라 중간 결과와 방향 변경 이유를 알 수 없음"
        ],
        "request": "진행 중 나온 중간 수치와 방향을 바꾼 이유 comment 필요",
    },
    {"id": "T7", "points": ["INNO-18은 결과 comment와 PR이 있음"]},
]
TASK_CHECKS = [
    {"id": "R2", "result": "pass", "detail": "결과마다 PR 또는 comment 링크 있음"},
    {"id": "R3", "result": "pass", "detail": "agent 구역(결과 산출물 구역과 TL;DR)을 개조식으로 작성함"},
    {"id": "T4", "result": "pass", "detail": "진행 배경에 이유와 선행 task 있음"},
    {"id": "T5", "result": "fail", "detail": "3번 항목의 완료 기준 없음"},
    {"id": "T6", "result": "fail", "detail": "작업 기간 중 사람 comment 1건"},
    {"id": "T7", "result": "pass", "detail": "sub-task 기록 있음"},
]
TASK_REQUESTS = [
    "평가 리포트 링크, 데이터 규모, 오류율 계산 방법 comment 필요",
    "기기 사양 확정 뒤 후속 task 키 comment 필요",
]
TASK_SUMMARY = f"""* 재현 테스트 추가 항목은 [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]로 병합되어 달성함
* 오류율 1% 미만 항목은 모델 v2의 검증 데이터 오류율 0.7%라는 수치만 있고 계산 방법과 리포트 링크가 기록에 없어 미달성으로 둠([comment 2026-09-12|{comment_url("INNO-17", 10123)}])
* 예상에 없던 오류율 대시보드([PR #45 오류율 대시보드|https://github.com/everex-ai/repo/pull/45])는 초과 달성으로 기록함"""
TASK_TITLE = "export 오류 원인 확인과 재발 방지"
# 검토 요청 샘플(INNO-21): INNO-17과 같은 예상 산출물에서, 미달성 항목마다 담당자가 comment로 사유를 남긴 경우
REQUEST_ITEMS = [
    TASK_ITEMS[0],
    {
        "id": "2",
        "done": False,
        "result": "comment에 고객사 검증 데이터로 export 오류율을 측정할 계획만 있고 측정 결과 없음",
        "evidence": f"[comment 2026-09-15|{comment_url('INNO-21', 10160)}]",
        "why": f"고객사 검증 데이터 제공이 10월로 밀려 오류율을 측정하지 못함([comment 2026-09-15|{comment_url('INNO-21', 10160)}])",
        "reason": "고객사 검증 데이터로 측정한 오류율과 리포트 링크 comment 필요",
    },
    {
        "id": "3",
        "done": False,
        "result": "ONNX 변환 스크립트 초안까지 작성",
        "why": f"배포 대상 기기 사양이 확정되지 않아 변환 옵션을 정하지 못함([comment 2026-09-13|{comment_url('INNO-21', 10140)}])",
        "evidence": f"[comment 2026-09-13|{comment_url('INNO-21', 10140)}]",
        "reason": "기기 사양 확정 뒤 후속 task 키 comment 필요",
    },
]
REQUEST_SUMMARY = f"""* 재현 테스트 추가 항목은 [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]로 병합되어 달성함
* 오류율 1% 미만 항목은 고객사 검증 데이터 제공이 10월로 밀렸다는 담당자 comment가 있어 미달성으로 둠([comment 2026-09-15|{comment_url("INNO-21", 10160)}])
* ONNX 변환 항목은 배포 대상 기기 사양이 확정되지 않았다는 담당자 comment가 있어 미달성으로 둠([comment 2026-09-13|{comment_url("INNO-21", 10140)}])"""
# 통과 샘플(INNO-22): INNO-17과 같은 예상 산출물이 모두 달성인 경우
REPORT_LINK = "[export 오류율 평가 리포트|https://github.com/everex-ai/repo/blob/main/reports/export-error-rate.md]"
PASS_ITEMS = [
    TASK_ITEMS[0],
    {
        "id": "2",
        "done": True,
        "result": "모델 v2의 검증 데이터(val set, export 요청 12,000건) export 오류율 0.7%(오류 건수를 export 요청 건수로 나눔)",
        "evidence": REPORT_LINK,
    },
    {
        "id": "3",
        "done": True,
        "result": "Galaxy S24용 ONNX 변환 스크립트 추가, 2026-09-16 병합",
        "evidence": "[PR #48 ONNX 변환|https://github.com/everex-ai/repo/pull/48]",
    },
]
PASS_SUMMARY = f"""* 재현 테스트 추가 항목은 [PR #42 export 오류 수정|https://github.com/everex-ai/repo/pull/42]로 병합되어 달성함
* 오류율 1% 미만 항목은 모델 v2의 검증 데이터(val set, export 요청 12,000건) 오류율 0.7%(오류 건수를 export 요청 건수로 나눔)가 {REPORT_LINK}에 있어 달성함
* ONNX 변환 항목은 [PR #48 ONNX 변환|https://github.com/everex-ai/repo/pull/48]로 병합되어 달성함"""
# stop 이력: 2026-09-08에 stop하고 다음 상태 변경(2026-09-10 In Progress)까지 사람 comment가 없음
# 보류 샘플(INNO-17)은 사유가 없어 R4가 보완 필요이고, 통과 샘플(INNO-22)은 늦게 남긴 사유(LATE_REASON)가 있음
STOP_CHANGES = [
    {"created": "2026-09-02T10:00:00.000+0900", "field": "status", "to": "In Progress"},
    {"created": "2026-09-08T18:00:00.000+0900", "field": "status", "to": "Backlog"},
    {"created": "2026-09-10T09:30:00.000+0900", "field": "status", "to": "In Progress"},
    {"created": "2026-09-25T15:00:00.000+0900", "field": "status", "to": "ready-to-done"},
]
LATE_REASON = {
    "created": "2026-09-25T11:00:00.000+0900",
    "url": comment_url("INNO-22", 10210),
    "body": "stop 사유: 고객사 검증 데이터 전달을 기다리느라 작업을 멈춤",
}
BUG_CHECKS = [
    {"id": "B3", "result": "pass", "detail": "원인과 해결에 PR 링크 있음"},
    {"id": "B4", "result": "fail", "detail": "해결 뒤 To-be 동작 확인 comment 없음"},
    {"id": "R2", "result": "pass", "detail": "원인과 해결마다 링크 있음"},
    {"id": "R3", "result": "pass", "detail": "agent 구역(TL;DR)을 개조식으로 작성함"},
]
BUG_DESC_OLD = """h2. 현황
* 발생 기기/서비스: 포즈 추정 앱
* 발생 일자: 2026-09-15
* 발생 장비: Galaxy S24
* 발생 계정: qa01
* 발생 내용: export 실패

h2. 개선
* export가 오류 없이 끝남
"""
BUG_DESC = BUG_DESC_OLD + "* 결과 파일이 1분 안에 생성됨\n"
BUG_CHANGELOG = [
    {"field": "status", "to": "Ready-to-Done", "created": "2026-09-19T17:40:00.000+0900"},
    {"field": "description", "from": BUG_DESC_OLD, "to": BUG_DESC, "created": "2026-09-20T10:12:00.000+0900"},
]


def precheck_checks(jp: types.ModuleType, itype: str, stops: list[dict] | None = None) -> dict:
    """샘플 task의 스크립트 검사 결과를 scripts/precheck.py의 검사 함수로 계산한다.

    Args:
        jp: jira_precheck 모듈.
        itype: Task 또는 Bug.
        stops: precheck.stop_events의 결과. 없으면 stop 이력이 없는 것으로 R4를 계산한다.

    Returns:
        precheck.json의 checks.
    """
    if itype == "Task":
        checks = jp.check_task_template(TASK_DESC)
        checks.pop("expected")
        checks["A2"] = jp.check_a2("Task", [], "예상 산출물")
    else:
        checks = jp.check_bug_template(BUG_DESC, 2)
        checks["A2"] = jp.check_a2("Bug", BUG_CHANGELOG, "개선")
    checks["R4"] = jp.check_r4(stops or [])
    return checks


def task_verdict(key: str, mode: str, verdict: str | None) -> dict:
    """샘플 Task의 verdict.json을 만든다.

    Args:
        key: task 키.
        mode: review 또는 digest.
        verdict: 판정 값. 정리 모드는 None.

    Returns:
        verdict.json 내용.
    """
    v = {
        "issueKey": key,
        "mode": mode,
        "verdict": verdict,
        "checks": TASK_CHECKS if mode == "review" else [],
        "items": TASK_ITEMS,
        "extra": TASK_EXTRA,
        "requests": TASK_REQUESTS,
    }
    if mode == "review":
        v["feedback"] = TASK_FEEDBACK
    return v


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


def run_issue(ja: types.ModuleType, work: Path, key: str, mode: str, gate: bool) -> FakeJira:
    """apply_issue를 가짜 Jira로 실행한다.

    Args:
        ja: jira_apply 모듈.
        work: ctx, out 폴더가 있는 작업 폴더.
        key: task 키.
        mode: review 또는 digest.
        gate: 게이트 모드 여부.

    Returns:
        쓰려던 값을 모은 FakeJira.
    """
    j = FakeJira({key: TASK_DESC})
    with contextlib.redirect_stdout(io.StringIO()):
        ja.apply_issue(j, work / "ctx" / key, work / "out", mode, gate, LEAD, ja.Summary(str(work / "summary.md")))
    return j


def render(out_dir: Path) -> list[Path]:
    """샘플을 만들어 out_dir에 저장한다.

    Args:
        out_dir: 샘플을 저장할 폴더.

    Returns:
        저장한 파일 목록.
    """
    ja, jp = load_modules()
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    sent: list[str] = []

    def save(name: str, text: str) -> None:
        p = out_dir / name
        p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        saved.append(p)

    # 시각, 실행 로그 주소, Slack 전송을 고정한다. 끝나면 모듈과 환경 변수를 원래대로 되돌린다
    with contextlib.ExitStack() as stack:
        work = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        ctx, out = work / "ctx", work / "out"
        users = work / "slack-users.json"
        write_json(users, {ASSIGNEE: "U000SAMPLE", LEAD: "U000LEAD"})
        fake_dt = types.SimpleNamespace(**{k: getattr(dt, k) for k in dir(dt) if not k.startswith("_")})
        fake_dt.datetime = FixedDatetime
        run_env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "everex-ai/.github",
            "GITHUB_RUN_ID": "123456",
        }
        stack.enter_context(mock.patch.dict(os.environ, run_env))
        stack.enter_context(mock.patch.object(ja, "now_stamp", lambda: FIXED_NOW.strftime("%Y-%m-%d %H:%M")))
        stack.enter_context(mock.patch.object(ja, "dt", fake_dt))
        stack.enter_context(mock.patch.object(ja, "SLACK_USERS", users))
        stack.enter_context(mock.patch.object(ja, "slack_post", lambda text: sent.append(text) or "전송 완료"))

        # 검수 모드, 관찰 모드: 결과 산출물 구역, 검수 comment, 문서화 리뷰 comment, 보류 Slack 알림
        # (미달성 2번의 why가 "근거 부족으로 확인 불가"라 보류. 사유 없는 stop이 있어 요청 끝에 R4 요청 문장이 붙음)
        expected, task_checks = jp.expected_items(TASK_DESC), precheck_checks(jp, "Task")
        hold_stops = jp.stop_events(STOP_CHANGES, [], FIXED_NOW)
        hold_checks = precheck_checks(jp, "Task", hold_stops)
        write_issue(ctx, "INNO-17", "Task", expected, hold_checks, summary=TASK_TITLE, stops=hold_stops)
        write_out(out, "INNO-17", task_verdict("INNO-17", "review", "fix"), TASK_SUMMARY)
        j = run_issue(ja, work, "INNO-17", "review", gate=False)
        save("deliverables-review.wiki", agent_section(j.fields["INNO-17"]["description"]))
        save("review-comment-task-observe.wiki", j.comments[0][1])
        save("doc-review-comment.wiki", j.comments[1][1])
        save("slack-review-hold.txt", sent[-1])

        # 검토 요청 Slack 알림: 미달성 항목마다 담당자가 남긴 사유가 있음
        write_issue(ctx, "INNO-21", "Task", expected, task_checks, summary=TASK_TITLE)
        reasons = [it["reason"] for it in REQUEST_ITEMS if it.get("reason")]
        request = task_verdict("INNO-21", "review", "escalate")
        request |= {"items": REQUEST_ITEMS, "extra": [], "requests": reasons}
        write_out(out, "INNO-21", request, REQUEST_SUMMARY)
        run_issue(ja, work, "INNO-21", "review", gate=False)
        save("slack-review-request.txt", sent[-1])

        # 통과 검수 comment와 Slack 알림: 예상 산출물이 모두 달성이고, stop 사유를 늦게 남겨 R4가 "늦게 기록"임
        pass_stops = jp.stop_events(STOP_CHANGES, [LATE_REASON], FIXED_NOW)
        pass_checks = precheck_checks(jp, "Task", pass_stops)
        write_issue(ctx, "INNO-22", "Task", expected, pass_checks, summary=TASK_TITLE, stops=pass_stops)
        passed = task_verdict("INNO-22", "review", "pass") | {"items": PASS_ITEMS, "extra": [], "requests": []}
        write_out(out, "INNO-22", passed, PASS_SUMMARY)
        j = run_issue(ja, work, "INNO-22", "review", gate=False)
        save("review-comment-task-pass.wiki", j.comments[0][1])
        save("slack-review-pass.txt", sent[-1])

        # 검수 모드, 게이트 모드: 문서화 리뷰가 검수 comment에 들어간다
        j = run_issue(ja, work, "INNO-17", "review", gate=True)
        save("review-comment-task-gate.wiki", j.comments[0][1])

        # 정리 모드(F3): 사유가 기록에 없으면 사유 줄을 생략한다
        write_out(out, "INNO-17", task_verdict("INNO-17", "digest", None))
        j = run_issue(ja, work, "INNO-17", "digest", gate=False)
        save("deliverables-digest.wiki", agent_section(j.fields["INNO-17"]["description"]))

        # Bug 검수: A2 실패로 보류
        write_issue(ctx, "INNO-30", "Bug", [], precheck_checks(jp, "Bug"))
        bug = {"issueKey": "INNO-30", "mode": "review", "verdict": "fix", "checks": BUG_CHECKS}
        bug |= {"items": [], "extra": [], "requests": ["해결 뒤 To-be 동작을 확인한 comment 필요"]}
        write_out(
            out,
            "INNO-30",
            bug,
            "* 원인은 입력 해상도 검사 누락, 해결은 검사 추가로 2026-09-18 병합됨"
            "([PR #51 해상도 검사 추가|https://github.com/everex-ai/repo/pull/51])\n"
            "* 해결 뒤 To-be 동작을 확인한 사람 comment 없음",
        )
        j = FakeJira({"INNO-30": BUG_DESC})
        with contextlib.redirect_stdout(io.StringIO()):
            ja.apply_issue(j, ctx / "INNO-30", out, "review", False, LEAD, ja.Summary(str(work / "summary.md")))
        save("review-comment-bug-escalate.wiki", j.comments[0][1])

        # 실패 알림: verdict.json이 없음
        write_issue(ctx, "INNO-31", "Task", jp.expected_items(TASK_DESC), precheck_checks(jp, "Task"))
        write_out(out, "INNO-31", None)
        j = FakeJira({"INNO-31": TASK_DESC})
        with contextlib.redirect_stdout(io.StringIO()):
            ja.apply_issue(j, ctx / "INNO-31", out, "review", False, LEAD, ja.Summary(str(work / "summary.md")))
        save("failure-comment.wiki", j.comments[0][1])

        # 실패 알림: verdict.json이 형식에 맞지 않음(문서화 리뷰가 아닌 검사 ID로 feedback을 씀)
        bad = task_verdict("INNO-32", "review", "fix")
        bad["feedback"] = [{"id": "T8", "points": ["사유 누락"]}]
        write_issue(ctx, "INNO-32", "Task", jp.expected_items(TASK_DESC), precheck_checks(jp, "Task"))
        write_out(out, "INNO-32", bad)
        j = FakeJira({"INNO-32": TASK_DESC})
        with contextlib.redirect_stdout(io.StringIO()):
            ja.apply_issue(j, ctx / "INNO-32", out, "review", False, LEAD, ja.Summary(str(work / "summary.md")))
        save("failure-comment-invalid.wiki", j.comments[0][1])

        # 누락 알림(정리 모드)과 주간 점검(Slack): precheck.run_scan이 만든 알림을 쓴다
        scan = ctx / "_scan"
        write_json(
            scan / "INNO-40.json",
            {
                "key": "INNO-40",
                "type": "Task",
                "status": "Backlog",
                "summary": "라벨 재수집",
                "url": f"{SITE}/browse/INNO-40",
                "created": "2026-09-01T10:00:00.000+0900",
                "assignee": {"accountId": ASSIGNEE, "displayName": "담당자A"},
                "humanComments": [],
                "statusChanges": [
                    {"created": "2026-09-02T10:00:00.000+0900", "field": "status", "to": "In Progress"},
                    {"created": "2026-09-22T10:00:00.000+0900", "field": "status", "to": "Backlog"},
                ],
            },
        )
        write_json(
            scan / "INNO-41.json",
            {
                "key": "INNO-41",
                "type": "Task",
                "status": "In Progress",
                "summary": "모델 경량화",
                "url": f"{SITE}/browse/INNO-41",
                "created": "2026-09-01T10:00:00.000+0900",
                "assignee": {"accountId": "acc-other", "displayName": "담당자B"},
                "humanComments": [{"created": "2026-09-15T10:00:00.000+0900", "url": comment_url("INNO-41", 1)}],
                "statusChanges": [{"created": "2026-09-02T10:00:00.000+0900", "field": "status", "to": "In Progress"}],
            },
        )
        for p in sorted(scan.glob("INNO-*.json")):
            if not p.name.endswith(".alerts.json"):
                with contextlib.redirect_stdout(io.StringIO()):
                    jp.run_scan(p, FIXED_NOW)
        j = FakeJira({})
        with contextlib.redirect_stdout(io.StringIO()):
            ja.apply_alerts(j, ctx, LEAD, ja.Summary(str(work / "summary.md")))
        save("missing-alert-comment.wiki", "\n\n".join(body for _, body in j.comments))

        with contextlib.redirect_stdout(io.StringIO()):
            ja.slack_weekly(ctx, ja.Summary(str(work / "summary.md")))
        save("weekly-slack.txt", sent[-1])  # 검수 알림도 sent에 쌓이므로 마지막에 보낸 주간 점검 본문을 쓴다
    return saved


def main() -> None:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="샘플을 저장할 폴더")
    a = ap.parse_args()
    for p in render(Path(a.out)):
        print(p)


if __name__ == "__main__":
    main()
