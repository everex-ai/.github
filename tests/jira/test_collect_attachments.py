"""scripts/src/jira/collect.py의 첨부 수집. Jira와 subprocess(ffmpeg, ffprobe)를 가짜로 바꿔 네트워크와 ffmpeg 없이 확인한다."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
KEY = "INNO-9"


@pytest.fixture(scope="module")
def jci():
    """scripts/src/jira/collect.py (모듈 이름 src.jira.collect)."""
    if str(ROOT / "scripts") not in sys.path:
        sys.path.append(str(ROOT / "scripts"))  # scripts/src 패키지를 불러오기 위함
    return importlib.import_module("src.jira.collect")


@pytest.fixture
def ctx(jci, monkeypatch, tmp_path):
    """CTX를 임시 폴더로 바꾸고 ticket 폴더의 첨부 폴더 경로를 돌려준다."""
    monkeypatch.setattr(jci, "CTX", tmp_path / "ctx")
    return tmp_path / "ctx" / KEY / "attachments"


class FakeJira:
    """attachment_content만 있는 가짜 Jira. fail에 있는 id는 JiraError를 낸다.

    실제 클라이언트처럼 max_bytes를 받으면 max_bytes + 1 bytes까지만 돌려준다.
    """

    tldr_field = ""

    def __init__(self, contents: dict | None = None, fail: tuple = ()) -> None:
        self.contents = contents or {}
        self.fail = set(fail)
        self.calls: list[str] = []
        self.max_bytes: dict[str, int | None] = {}

    def attachment_content(self, att_id: str, max_bytes: int | None = None) -> bytes:
        self.calls.append(att_id)
        self.max_bytes[att_id] = max_bytes
        if att_id in self.fail:
            from src.jira.client import JiraError

            raise JiraError(f"GET /attachment/content/{att_id} -> 500: boom")
        data = self.contents.get(att_id, b"data-" + att_id.encode())
        return data if max_bytes is None else data[: max_bytes + 1]


def att(aid, filename, mime, size=100, created="2026-11-03T11:56:29.233+0900", account="u1"):
    """Jira 첨부 응답 항목 하나."""
    return {
        "id": aid,
        "filename": filename,
        "mimeType": mime,
        "size": size,
        "created": created,
        "author": {"accountId": account, "displayName": f"이름-{account}"},
        "content": f"https://example/{aid}",
    }


def comment(cid, body="", created="2026-11-03T11:56:29.824+0900", account="u1", kind="human"):
    """norm_comments 결과 항목 하나."""
    return {"id": cid, "created": created, "author": {"accountId": account}, "kind": kind, "body": body}


def by_id(records):
    return {r["id"]: r for r in records}


# ---------------------------------------------------------------------- 형식 분류와 크기 기준


@pytest.mark.parametrize(
    ("mime", "name", "kind"),
    [
        ("text/plain", "결과.md", "text"),
        ("application/json", "a", "text"),
        ("application/octet-stream", "config.yml", "text"),
        ("text/csv; charset=utf-8", "a.csv", "text"),
        ("image/png", "a.png", "image"),
        ("image/jpeg", "a.jpg", "image"),
        ("application/octet-stream", "a.webp", "image"),
        ("image/heic", "a.heic", "other"),
        ("application/pdf", "a.pdf", "pdf"),
        ("video/mp4", "a.mp4", "video"),
        ("video/quicktime", "a.mov", "video"),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "a.xlsx", "other"),
        ("application/zip", "a.zip", "other"),
    ],
)
def test_attachment_kind(jci, mime, name, kind):
    assert jci.attachment_kind(mime, name) == kind


def test_saves_within_limits_and_skips_over_limit_or_other(jci, ctx):
    limits = jci.ATT_MAX_BYTES
    atts = [
        att("1", "note.md", "text/plain", size=limits["text"]),
        att("2", "big.md", "text/plain", size=limits["text"] + 1),
        att("3", "shot.png", "image/png", size=384_342),
        att("4", "huge.png", "image/png", size=limits["image"] + 1),
        att("5", "plan.pdf", "application/pdf", size=3_683_050),
        att("6", "big.pdf", "application/pdf", size=limits["pdf"] + 1),
        att("7", "long.mp4", "video/mp4", size=limits["video"] + 1),
        att("8", "table.xlsx", "application/vnd.ms-excel", size=10),
    ]
    j = FakeJira()
    recs = by_id(jci.collect_attachments(j, KEY, [(KEY, atts, [])]))

    assert sorted(j.calls) == ["1", "3", "5"]  # 기준 안의 첨부만 받는다
    for aid, name in (("1", "note.md"), ("3", "shot.png"), ("5", "plan.pdf")):
        path = ctx / f"{aid}-{name}"
        assert recs[aid]["status"] == "saved" and recs[aid]["path"] == path.as_posix()
        assert path.read_bytes() == b"data-" + aid.encode()
    for aid in ("2", "4", "6", "7"):
        assert recs[aid]["status"] == "skipped" and "기준" in recs[aid]["reason"] and "path" not in recs[aid]
    assert recs["8"]["status"] == "skipped" and recs["8"]["kind"] == "other"
    assert "형식" in recs["8"]["reason"]
    assert {p.name for p in ctx.iterdir()} == {"1-note.md", "3-shot.png", "5-plan.pdf"}


def test_record_fields(jci, ctx):
    rec = jci.collect_attachments(FakeJira(), KEY, [(KEY, [att("1", "a.png", "image/png")], [])])[0]
    assert set(rec) == {
        "id",
        "source",
        "filename",
        "mimeType",
        "size",
        "author",
        "created",
        "kind",
        "status",
        "path",
        "commentIds",
        "matchedBy",
    }
    assert rec["source"] == KEY and rec["author"] == {"accountId": "u1", "displayName": "이름-u1"}


@pytest.mark.parametrize("size", [None, "12", 10], ids=["size 없음", "size가 문자열", "size가 실제보다 작음"])
def test_download_is_capped_by_kind_limit(jci, ctx, size):
    """Jira size가 없거나 틀려도 형식별 상한 + 1 bytes까지만 읽고, 넘으면 저장하지 않는다."""
    limit = jci.ATT_MAX_BYTES["text"]
    j = FakeJira({"1": b"x" * (limit * 3)})
    rec = jci.collect_attachments(j, KEY, [(KEY, [att("1", "a.md", "text/plain", size=size)], [])])[0]
    assert j.max_bytes == {"1": limit}
    assert rec["status"] == "skipped" and rec["reason"] == f"받는 중 text 기준 {limit} bytes를 넘어 저장하지 않음"
    assert not (ctx / "1-a.md").exists()


def test_download_at_limit_is_saved(jci, ctx):
    limit = jci.ATT_MAX_BYTES["text"]
    rec = jci.collect_attachments(
        FakeJira({"1": b"x" * limit}), KEY, [(KEY, [att("1", "a.md", "text/plain", size=None)], [])]
    )[0]
    assert rec["status"] == "saved" and (ctx / "1-a.md").stat().st_size == limit


def test_count_limit_counts_only_stored_and_records_reason(jci, ctx, monkeypatch):
    """개수 상한에는 saved, frames만 센다. other, 받기 실패, ffmpeg 없는 영상의 skipped는 세지 않는다."""
    monkeypatch.setattr(jci.shutil, "which", lambda name: None)
    n = jci.ATT_MAX_FILES + 3
    pngs = [att(str(i), f"{i}.png", "image/png", created=f"2026-11-03T10:00:{i:02d}.000+0900") for i in range(n)]
    newest_other = att("x", "a.xlsx", "application/zip", created="2026-11-03T11:00:00.000+0900")
    newest_video = att("v", "a.mp4", "video/mp4", created="2026-11-03T11:00:01.000+0900")
    sub = [att("s", "s.png", "image/png", created="2026-11-03T09:00:00.000+0900")]  # sub-task 첨부도 합산된다
    j = FakeJira(fail=(str(n - 1),))  # 가장 최신 png는 받기 실패
    recs = jci.collect_attachments(j, KEY, [(KEY, [*pngs, newest_other, newest_video], []), ("INNO-10", sub, [])])

    assert [r["id"] for r in recs][:3] == ["v", "x", str(n - 1)]  # 생성 시각 최신순
    assert "받기 실패" in by_id(recs)[str(n - 1)]["reason"]
    saved = [r["id"] for r in recs if r["status"] == "saved"]
    assert saved == [str(i) for i in range(n - 2, 1, -1)] and len(saved) == jci.ATT_MAX_FILES
    capped = [r for r in recs if r["status"] == "skipped" and "상한" in r.get("reason", "")]
    assert [r["id"] for r in capped] == ["1", "0", "s"]
    assert capped[0]["reason"] == f"첨부 수 상한 {jci.ATT_MAX_FILES}개를 넘어 받지 않음(생성 시각 최신순)"
    assert by_id(recs)["s"]["source"] == "INNO-10"
    assert len(j.calls) == jci.ATT_MAX_FILES + 1


# ---------------------------------------------------------------------- comment 짝짓기


def test_match_by_body_notation(jci):
    a = att("1", "image-20261104-090408.png", "image/png")
    comments = [
        comment("10", "결과 화면\n!image-20261104-090408.png|width=1372,height=703!", account="u2"),
        comment("11", "에이전트 인용 !image-20261104-090408.png!", kind="agent", account="bot"),
        comment("12", "!image-20261104-090408.png.bak|width=1!"),  # 다른 파일 이름
        comment("13", "같은 작성자, 같은 시각이지만 본문 표기가 있으므로 시각 방식은 쓰지 않음"),
    ]
    assert jci.match_comments(a, comments) == (["10", "11"], "body")


@pytest.mark.parametrize("body", ["[^정량평가셋 확장 계획.pdf]", "[계획서|^정량평가셋 확장 계획.pdf]"])
def test_match_by_link_notation(jci, body):
    a = att("1", "정량평가셋 확장 계획.pdf", "application/pdf")
    assert jci.match_comments(a, [comment("20", body, account="u2", created="2026-11-05T00:00:00.000+0900")]) == (
        ["20"],
        "body",
    )


def test_match_by_time_and_author(jci):
    a = att("46482", "x.png", "image/png", created="2026-11-03T11:56:29.233+0900", account="u1")
    comments = [
        comment("60022", "", created="2026-11-03T11:56:29.824+0900", account="u1"),
        comment("60023", "", created="2026-11-03T11:56:39.233+0900", account="u1"),  # 정확히 10초
        comment("60024", "", created="2026-11-03T11:56:40.233+0900", account="u1"),  # 11초
        comment("60025", "", created="2026-11-03T11:56:29.900+0900", account="u2"),  # 다른 작성자
    ]
    assert jci.match_comments(a, comments) == (["60022", "60023"], "time")


def test_no_match(jci):
    a = att("1", "x.png", "image/png", created="2026-11-03T11:56:29.233+0900", account="u1")
    comments = [comment("1", "본문", created="2026-11-03T12:00:00.000+0900", account="u1")]
    assert jci.match_comments(a, comments) == ([], None)


def test_subtask_attachment_matches_subtask_comment(jci, ctx):
    parent_c = [comment("100", "!s.png|width=1!")]
    sub_c = [comment("200", "", account="u1")]
    recs = by_id(
        jci.collect_attachments(
            FakeJira(), KEY, [(KEY, [], parent_c), ("INNO-10", [att("9", "s.png", "image/png")], sub_c)]
        )
    )
    assert recs["9"]["commentIds"] == ["200"] and recs["9"]["matchedBy"] == "time"


def test_same_name_attachments_match_nearest_body_comment(jci, ctx):
    """같은 source에 같은 이름 첨부가 여럿이면 본문 표기 comment 중 생성 시각이 가장 가까운 하나만 짝짓는다."""
    first = att("1", "image.png", "image/png", created="2026-11-03T10:00:00.000+0900")
    second = att("2", "image.png", "image/png", created="2026-11-03T10:05:00.000+0900")
    comments = [
        comment("c1", "첫 화면 !image.png|width=10!", created="2026-11-03T10:00:01.000+0900", account="u2"),
        comment("c2", "둘째 화면 !image.png|width=10!", created="2026-11-03T10:05:02.000+0900", account="u2"),
    ]
    recs = by_id(jci.collect_attachments(FakeJira(), KEY, [(KEY, [first, second], comments)]))
    assert (recs["1"]["commentIds"], recs["1"]["matchedBy"]) == (["c1"], "body")
    assert (recs["2"]["commentIds"], recs["2"]["matchedBy"]) == (["c2"], "body")


def test_same_name_in_other_source_keeps_all_body_matches(jci, ctx):
    """이름이 같은 첨부가 다른 source(sub-task)에 있으면 같은 이름으로 세지 않는다."""
    parent = att("1", "image.png", "image/png", created="2026-11-03T10:00:00.000+0900")
    sub = att("2", "image.png", "image/png", created="2026-11-03T10:05:00.000+0900")
    comments = [
        comment("c1", "!image.png|width=10!", created="2026-11-03T10:00:01.000+0900", account="u2"),
        comment("c2", "!image.png|width=10!", created="2026-11-03T12:00:00.000+0900", account="u2"),
    ]
    recs = by_id(jci.collect_attachments(FakeJira(), KEY, [(KEY, [parent], comments), ("INNO-10", [sub], [])]))
    assert (recs["1"]["commentIds"], recs["1"]["matchedBy"]) == (["c1", "c2"], "body")


@pytest.mark.parametrize("value", [None, 12345, "", "2026-11-03", {"x": 1}])
def test_parse_time_returns_none_for_unreadable(jci, value):
    assert jci.parse_time(value) is None


def test_unreadable_created_does_not_stop_collection(jci, ctx):
    a = att("1", "a.png", "image/png", created=12345)
    rec = jci.collect_attachments(FakeJira(), KEY, [(KEY, [a], [comment("c1", "")])])[0]
    assert rec["status"] == "saved" and rec["matchedBy"] is None


# ---------------------------------------------------------------------- 영상 프레임


class FakeRun:
    """subprocess.run 대신 쓰는 가짜. ffprobe에는 길이를, ffmpeg에는 마지막 인자 경로에 파일을 만들어 준다."""

    def __init__(self, duration="16.0", ffmpeg_code=0, ffprobe_code=0) -> None:
        self.duration = duration
        self.ffmpeg_code = ffmpeg_code
        self.ffprobe_code = ffprobe_code
        self.fail_frames: set[int] = set()  # 실패시킬 ffmpeg 호출 순번(0부터)
        self.calls: list[list[str]] = []
        self.timeouts: list[int] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        self.timeouts.append(kwargs.get("timeout"))
        if args[0] == "ffprobe":
            return subprocess.CompletedProcess(args, self.ffprobe_code, stdout=self.duration + "\n", stderr="probe err")
        n = sum(c[0] == "ffmpeg" for c in self.calls) - 1
        code = 1 if n in self.fail_frames else self.ffmpeg_code
        if code == 0:
            Path(args[-1]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(args, code, stdout="", stderr=f"decode err {n}")


@pytest.fixture
def tools(jci, monkeypatch):
    """ffmpeg와 ffprobe가 있는 것으로 하고 가짜 subprocess.run을 끼운다."""
    monkeypatch.setattr(jci.shutil, "which", lambda name: f"/usr/bin/{name}")
    fake = FakeRun()
    monkeypatch.setattr(jci.subprocess, "run", fake)
    return fake


def test_ffmpeg_arguments_and_frames(jci, ctx, tools):
    a = att("7", "SKEL viewer 데모 영상.mov", "video/quicktime", size=95_481_724)
    rec = jci.collect_attachments(FakeJira(), KEY, [(KEY, [a], [])])[0]

    ffmpeg = [c for c in tools.calls if c[0] == "ffmpeg"]
    assert len(ffmpeg) == jci.FRAME_COUNT == 8
    assert [t for c, t in zip(tools.calls, tools.timeouts) if c[0] == "ffmpeg"] == [jci.FRAME_TIMEOUT] * 8
    assert jci.FRAME_TIMEOUT == 60
    assert [c[c.index("-ss") + 1] for c in ffmpeg] == [f"{t:.3f}" for t in (1, 3, 5, 7, 9, 11, 13, 15)]
    for c in ffmpeg:
        assert c[c.index("-frames:v") + 1] == "1"
        vf = c[c.index("-vf") + 1]
        assert "min(1280,iw)" in vf and "min(1280,ih)" in vf and "force_original_aspect_ratio=decrease" in vf
    assert rec["status"] == "frames" and "path" not in rec
    frames_dir = ctx / "7-frames"
    assert rec["frames"] == [
        {"path": (frames_dir / f"frame-{i:02d}.jpg").as_posix(), "seconds": float(t)}
        for i, t in enumerate((1, 3, 5, 7, 9, 11, 13, 15), 1)
    ]
    assert [p.name for p in ctx.iterdir()] == ["7-frames"]  # 원본 영상은 ctx/에 남기지 않는다
    assert "reason" not in rec


def test_partial_frame_failure_keeps_frames_and_records_reason(jci, ctx, tools):
    tools.fail_frames = {2, 5}
    rec = jci.collect_attachments(FakeJira(), KEY, [(KEY, [att("7", "a.mp4", "video/mp4")], [])])[0]
    assert rec["status"] == "frames"
    assert [Path(f["path"]).name for f in rec["frames"]] == [f"frame-0{i}.jpg" for i in (1, 2, 4, 5, 7, 8)]
    assert rec["reason"] == "프레임 8장 중 2장 추출 실패(마지막 원인: exit 1: decode err 5)"


def test_video_time_budget_skips_remaining_videos(jci, ctx, tools, monkeypatch):
    """영상 처리 시간 합계가 VIDEO_TIME_BUDGET을 넘으면 남은 영상은 받지 않는다. 다른 형식은 계속 받는다."""
    ticks = iter([0.0, jci.VIDEO_TIME_BUDGET + 1.0])
    monkeypatch.setattr(jci, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    atts = [
        att("1", "a.mp4", "video/mp4", created="2026-11-03T10:00:03.000+0900"),
        att("2", "b.mp4", "video/mp4", created="2026-11-03T10:00:02.000+0900"),
        att("3", "c.png", "image/png", created="2026-11-03T10:00:01.000+0900"),
    ]
    j = FakeJira()
    recs = by_id(jci.collect_attachments(j, KEY, [(KEY, atts, [])]))
    assert recs["1"]["status"] == "frames"
    assert recs["2"]["status"] == "skipped"
    assert recs["2"]["reason"] == f"영상 처리 시간 예산 {jci.VIDEO_TIME_BUDGET}초를 넘어 받지 않음"
    assert recs["3"]["status"] == "saved" and j.calls == ["1", "3"]
    assert jci.VIDEO_TIME_BUDGET == 600


def test_no_ffmpeg_skips_video_without_download(jci, ctx, monkeypatch):
    monkeypatch.setattr(jci.shutil, "which", lambda name: None)
    j = FakeJira()
    atts = [att("1", "a.mp4", "video/mp4"), att("2", "a.png", "image/png")]
    recs = by_id(jci.collect_attachments(j, KEY, [(KEY, atts, [])]))
    assert recs["1"]["status"] == "skipped" and recs["1"]["reason"] == "ffmpeg 또는 ffprobe가 없어 영상을 받지 않음"
    assert recs["2"]["status"] == "saved" and j.calls == ["2"]


def test_ffmpeg_failure_records_reason_and_continues(jci, ctx, tools):
    tools.ffmpeg_code = 1
    atts = [att("1", "a.mp4", "video/mp4"), att("2", "a.md", "text/plain")]
    recs = by_id(jci.collect_attachments(FakeJira(), KEY, [(KEY, atts, [])]))
    assert recs["1"]["status"] == "skipped" and "ffmpeg" in recs["1"]["reason"] and "decode err" in recs["1"]["reason"]
    assert not (ctx / "1-frames").exists()
    assert recs["2"]["status"] == "saved"


def test_ffprobe_failure_records_reason(jci, ctx, tools):
    tools.ffprobe_code = 1
    rec = jci.collect_attachments(FakeJira(), KEY, [(KEY, [att("1", "a.mp4", "video/mp4")], [])])[0]
    assert rec["status"] == "skipped" and "ffprobe" in rec["reason"]
    assert not [c for c in tools.calls if c[0] == "ffmpeg"]


def test_ffmpeg_missing_binary_error_records_reason(jci, ctx, monkeypatch):
    monkeypatch.setattr(jci.shutil, "which", lambda name: f"/usr/bin/{name}")

    def broken(args, **kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(jci.subprocess, "run", broken)
    rec = jci.collect_attachments(FakeJira(), KEY, [(KEY, [att("1", "a.mp4", "video/mp4")], [])])[0]
    assert rec["status"] == "skipped" and "FileNotFoundError" in rec["reason"]


def test_download_failure_records_reason_and_continues(jci, ctx, capsys):
    atts = [att("1", "a.png", "image/png"), att("2", "b.png", "image/png")]
    recs = by_id(jci.collect_attachments(FakeJira(fail=("1",)), KEY, [(KEY, atts, [])]))
    assert (
        recs["1"]["status"] == "skipped" and "받기 실패" in recs["1"]["reason"] and "JiraError" in recs["1"]["reason"]
    )
    assert recs["2"]["status"] == "saved"
    assert "첨부 1 건너뜀" in capsys.readouterr().err


# ---------------------------------------------------------------------- 파일 이름


@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "..\\..\\x.md", "/abs/path.md", "..", ".hidden.md", "a/../../b.png", ""]
)
def test_safe_filename_blocks_path_escape(jci, name):
    safe = jci.safe_filename(name)
    assert safe and "/" not in safe and "\\" not in safe and not safe.startswith(".")


def test_safe_filename_keeps_korean_and_extension(jci):
    assert jci.safe_filename("정량평가셋 확장 계획.pdf") == "정량평가셋_확장_계획.pdf"
    assert (
        jci.safe_filename("가" * 300 + ".png").endswith(".png") and len(jci.safe_filename("가" * 300 + ".png")) == 120
    )


def test_saved_path_stays_inside_attachment_folder(jci, ctx):
    atts = [att("1", "../../../escape.md", "text/plain"), att("../2", "x.md", "text/plain")]
    recs = jci.collect_attachments(FakeJira(), KEY, [(KEY, atts, [])])
    for r in recs:
        assert r["status"] == "saved"
        assert Path(r["path"]).resolve().parent == ctx.resolve()
    assert not (ctx.parent.parent / "escape.md").exists()


# ---------------------------------------------------------------------- collect_issue 연결


class IssueJira(FakeJira):
    """collect_issue가 부르는 메서드를 모두 가진 가짜 Jira."""

    def issue(self, key: str, fields: list[str]) -> dict:
        if key == KEY:
            return {
                "fields": {
                    "issuetype": {"name": "Task"},
                    "summary": "제목",
                    "status": {"name": "In Progress"},
                    "subtasks": [{"key": "INNO-10"}],
                    "description": "",
                    "attachment": [att("1", "a.png", "image/png"), att("2", "a.zip", "application/zip")],
                }
            }
        assert "attachment" in fields
        return {"fields": {"summary": "하위", "attachment": [att("3", "s.md", "text/plain")]}}

    def comments(self, key: str) -> list[dict]:
        return []

    def changelog(self, key: str) -> list[dict]:
        return []

    def prop_get(self, key: str) -> dict:
        return {}


def test_collect_issue_writes_attachments(jci, ctx, capsys):
    assert jci.collect_issue(IssueJira(), KEY, "", "", "review") == {"key": KEY}
    d = ctx.parent
    issue = json.loads((d / "issue.json").read_text(encoding="utf-8"))
    assert issue["attachments"] == [
        {"filename": "a.png", "created": "2026-11-03T11:56:29.233+0900", "mimeType": "image/png"},
        {"filename": "a.zip", "created": "2026-11-03T11:56:29.233+0900", "mimeType": "application/zip"},
    ]
    sub = json.loads((d / "subtasks" / "INNO-10.json").read_text(encoding="utf-8"))
    assert sub["attachments"] == [
        {"filename": "s.md", "created": "2026-11-03T11:56:29.233+0900", "mimeType": "text/plain"}
    ]
    recs = by_id(json.loads((d / "attachments.json").read_text(encoding="utf-8")))
    assert {k: r["status"] for k, r in recs.items()} == {"1": "saved", "2": "skipped", "3": "saved"}
    assert recs["3"]["source"] == "INNO-10" and (ctx / "3-s.md").exists()
    assert "첨부 저장 2, 프레임 0, 건너뜀 1" in capsys.readouterr().out


def test_collect_issue_continues_when_attachment_processing_fails(jci, ctx, monkeypatch, capsys):
    def broken(*args):
        raise RuntimeError("예상하지 못한 오류")

    monkeypatch.setattr(jci, "collect_attachments", broken)
    assert jci.collect_issue(IssueJira(), KEY, "", "", "review") == {"key": KEY}
    assert json.loads((ctx.parent / "attachments.json").read_text(encoding="utf-8")) == []
    assert (ctx.parent / "meta.json").exists()
    out = capsys.readouterr()
    assert "첨부 처리 실패" in out.err and "예상하지 못한 오류" in out.err
    assert "첨부 저장 0, 프레임 0, 건너뜀 0" in out.out
