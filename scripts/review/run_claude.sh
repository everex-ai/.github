#!/usr/bin/env bash
# 2단(Claude) 실행. CI(pr-review.yml)와 로컬이 같은 스크립트를 쓴다.
#   scripts/review/run_claude.sh <대상 repo> [orchestrator 모델]
# 전제: precheck.py 까지 돌아 <repo>/.everex-review/ctx/review-input.json 이 있고,
#       claude CLI가 로그인되어 있거나(로컬) CLAUDE_CODE_OAUTH_TOKEN 환경 변수가 있다(CI).
# 환경 변수: MAX_TURNS (기본 60)
#
# 도구 권한: orchestrator는 Read,Grep,Glob,Agent,Write. Bash가 없으므로 명령 실행과 네트워크 접근을 못 한다.
# claude 2.1.272 의 -p 모드에서는 Write(.everex-review/out/**) 처럼 경로를 한정한 규칙이 동작하지 않아(allow, deny 모두)
# Write 를 통째로 허용하고, apply.py --require-clean 이 repo 파일이 바뀌지 않았는지 확인한다.
set -euo pipefail
REPO=$(cd "${1:?대상 repo 경로}" && pwd)
MODEL=${2:-claude-opus-5}
TOOLS=$(cd "$(dirname "$0")/../.." && pwd)
PLUGIN="$TOOLS/plugins/everex-review"
OUT="$REPO/.everex-review/out"
[ -f "$REPO/.everex-review/ctx/review-input.json" ] || { echo "[claude] review-input.json 이 없음. precheck.py 를 먼저 실행할 것" >&2; exit 2; }
mkdir -p "$OUT"
rm -f "$OUT/verdict.json"
cd "$REPO"
set +e
claude -p "$(cat "$PLUGIN/orchestrator.md")" \
  --plugin-dir "$PLUGIN" \
  --model "$MODEL" \
  --max-turns "${MAX_TURNS:-60}" \
  --allowedTools "Read,Grep,Glob,Agent,Write" \
  --output-format json \
  < /dev/null > "$OUT/claude-result.json"
CODE=$?
set -e
python3 - "$OUT/claude-result.json" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:  # noqa: BLE001
    print(f"[claude] 결과 JSON을 읽지 못함: {e}", file=sys.stderr); sys.exit(0)
print(f"[claude] {d.get('result')}", file=sys.stderr)
print(f"[claude] turns={d.get('num_turns')} cost_usd={round(d.get('total_cost_usd') or 0, 2)} "
      f"duration_s={round((d.get('duration_ms') or 0) / 1000)} denials={len(d.get('permission_denials') or [])} "
      f"subagents={(d.get('subagent_stats') or {}).get('by_type')}", file=sys.stderr)
PY
if [ ! -f "$OUT/verdict.json" ]; then
  echo "[claude] verdict.json 이 만들어지지 않음 (claude exit $CODE)" >&2
  exit 1
fi
echo "[claude] -> $OUT/verdict.json" >&2
