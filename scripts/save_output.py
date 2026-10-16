#!/usr/bin/env python3
"""3-2단계: CI agent의 구조화 출력(JSON)을 out/<KEY>/ 파일로 저장한다. 표준 라이브러리만 쓴다.

구조화 출력은 claude-code-action의 실행 결과 파일(execution_file)에서 읽는다. 그 파일은 메시지 JSON 배열이고, 마지막
type이 "result"인 메시지의 structured_output(객체)이 구조화 출력이다. 형식은 schemas/agent-output.json에 있다.

ctx/<KEY>/마다 판정 없음 기본값을 만들고, 구조화 출력에 있는 키만 덮어쓴 뒤 아래 파일을 쓴다. 기본값과 덮어쓰는 범위는 build_verdict에 있다.
- out/<KEY>/verdict.json: 항상 쓴다.
- out/<KEY>/tldr.wiki: 내용이 있을 때만 쓴다.
- out/<KEY>/comment.wiki: 검수 모드이고 내용이 있을 때만 쓴다.
- out/<KEY>/_agent_error.txt: 구조화 출력이 없거나 그 Task가 구조화 출력에 없을 때 한 줄 원인을 쓴다. apply.py가 실패 이유 끝에 붙인다.
- out/_failed.txt: 구조화 출력의 failed 목록을 KEY와 이유를 탭으로 이은 줄로 쓴다. 없으면 이전 파일을 지운다.

검수 모드에서 어느 Task든 판정(verdict)이 "no_verdict"이거나 items에 done이 null인 항목이 있거나 tldr, comment가 비어 있으면
--github-output 파일에 retry=true를 덧붙인다(워크플로의 3-R단계가 3단계를 1회 다시 실행함).

실행 결과 파일의 Claude 사용량(total_cost_usd, modelUsage 또는 usage)은 out/_usage.json(목록)에 실행마다 항목 하나로 추가한다.
항목은 {"retry", "costUsd", "tokens": {"input", "output", "cacheWrite", "cacheRead"}, "models"}이다. apply.py가 이 파일을 합산해 jira-doc 기록에 남긴다.
`--exec-summary <경로>`만 주면 저장 없이 실행 결과 요약 줄(exec_summary)만 출력한다(워크플로 3-1단계).

표준 출력에는 Jira 기록이 담길 수 있는 내용(tldr, comment, 실행 결과의 result 문구)을 남기지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.jira.output.result import append_usage, exec_summary, read_result, structured_output  # noqa: E402
from src.jira.output.save import failed_reasons, no_output_reason, save_task  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """명령줄 인자 파서를 만든다. 워크플로(.github/workflows/jira-doc.yml)의 3-2단계와 3-R단계가 이 인자로 부른다.

    Returns:
        인자 파서.
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ctx", default="ctx")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mode", choices=["review", "digest"], help="--exec-summary가 없으면 필수")
    ap.add_argument("--exec-file", default="", help="claude-code-action의 execution_file. 비었거나 없는 경로일 수 있음")
    ap.add_argument(
        "--claude-outcome",
        default="",
        help="Claude 문서화 단계(Actions 워크플로 3단계)의 결과(success, failure, skipped 등)",
    )
    ap.add_argument("--retry", action="store_true", help="3-R단계의 다시 실행이면 _usage.json 항목에 retry=true를 적음")
    ap.add_argument(
        "--exec-summary",
        default="",
        metavar="PATH",
        help="이 실행 결과 파일의 요약 줄만 출력하고 끝냄(워크플로 3-1단계). 파일이 없거나 비면 아무것도 출력하지 않음",
    )
    ap.add_argument("--github-output", default="", help="GITHUB_OUTPUT 파일 경로. 비어 있으면 retry를 쓰지 않음")
    return ap


def main(argv: list[str] | None = None) -> None:
    """구조화 출력을 읽어 out/ 파일을 쓰고, 검수 모드에서 다시 실행이 필요하면 retry=true를 출력한다.

    Args:
        argv: 명령줄 인자. None이면 sys.argv를 쓴다.
    """
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.exec_summary:  # 저장하지 않고 요약 줄만 출력한다. 파일이 없거나 비면 아무것도 출력하지 않는다
        for line in exec_summary(args.exec_summary):
            print(line)
        return
    if not args.mode:
        ap.error("--mode가 필요하다(--exec-summary를 쓰지 않을 때)")
    ctx, out = Path(args.ctx), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    append_usage(out, args.exec_file, args.retry)
    read_ok, result = read_result(args.exec_file)
    structured = structured_output(result)
    error = "" if structured is not None else no_output_reason(read_ok, result, args.claude_outcome)
    failed = failed_reasons(structured) if structured is not None else {}

    retry = False
    keys: list[str] = []
    for d in sorted(ctx.glob("[A-Z]*-[0-9]*/")):
        if (d / "issue.json").exists():
            keys.append(d.name)
            retry = bool(save_task(d, out, args.mode, structured, error, failed)) or retry
    tasks = structured.get("tasks") if structured else None
    for t in tasks if isinstance(tasks, list) else []:  # ctx에 없는 issueKey는 키만 남긴다(내용은 남기지 않음)
        if isinstance(t, dict) and isinstance(t.get("issueKey"), str) and t["issueKey"] not in keys:
            print(f"[save] ctx에 없는 issueKey: {t['issueKey']}")

    failed_file = out / "_failed.txt"
    if failed:
        failed_file.write_text("".join(f"{k}\t{r}\n" for k, r in failed.items()), encoding="utf-8")
    else:
        failed_file.unlink(missing_ok=True)

    if args.mode == "review" and retry and args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write("retry=true\n")
        print("[save] retry=true")


if __name__ == "__main__":
    main()
