#!/usr/bin/env bash
# Check that this machine can run the benchmark, and that an executor is configured.
#   bin/doctor.sh                       machine, task cache, and every custom executor in matrix.yaml
#   bin/doctor.sh <executor-id> ...     machine, task cache, and these executors
#   bin/doctor.sh --smoke <executor-id> plus a short live session in the sandbox (a few cents at most)
#   bin/doctor.sh --base                machine only (used by bin/setup.sh)
set -uo pipefail
BENCH="$(cd "$(dirname "$0")/.." && pwd)"
. "$BENCH/bin/lib.sh"
bench_load_env
BASE=0 SMOKE=0 EXS=()
for a in "$@"; do
  case "$a" in
    --base) BASE=1 ;;
    --smoke) SMOKE=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) EXS+=("$a") ;;
  esac
done
BAD=0
ok()   { echo "  ✓ $*"; }
bad()  { echo "  ✗ $*"; BAD=1; }
hint() { echo "      → $*"; }
ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }

echo "== Machine"
[ "$(uname -s)" = Linux ] && ok "Linux$(grep -qi microsoft /proc/version 2>/dev/null && echo ' (WSL2)')" \
  || { bad "Linux required (bubblewrap sandbox); on Windows use WSL2"; }
for c in git python3 timeout; do command -v $c >/dev/null && ok "$c" || bad "$c not found"; done
if command -v go >/dev/null; then
  v="$(go env GOVERSION)"; v="${v#go}"
  ver_ge "$v" 1.26 && ok "go $v" || { bad "go $v is too old (tasks need 1.26+)"; hint "https://go.dev/dl/"; }
else bad "go not found (tasks need Go 1.26+)"; hint "https://go.dev/dl/"; fi
if command -v node >/dev/null; then
  v="$(node --version)"; v="${v#v}"
  ver_ge "$v" 20 && ok "node $v" || bad "node $v is too old (need 20+)"
  command -v npm >/dev/null && ok "npm" || bad "npm not found"
else bad "node not found (need Node.js 20+)"; hint "https://nodejs.org or nvm"; fi
if ! command -v bwrap >/dev/null; then
  bad "bwrap not found"; hint "sudo apt install bubblewrap   (Fedora: dnf, Arch: pacman -S bubblewrap)"
elif err="$(bwrap --ro-bind / / --dev /dev --proc /proc --unshare-pid --unshare-ipc --unshare-net true 2>&1)"; then
  ok "bwrap sandbox works"
else
  bad "bwrap cannot create a sandbox: $err"
  [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" = 1 ] &&
    hint "Ubuntu 24.04+ blocks it: sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0"
fi
free_gb=$(( $(df -Pk "$BENCH" | awk 'NR==2{print $4}') / 1048576 ))
[ "$free_gb" -ge 3 ] && ok "${free_gb} GB free disk" || bad "only ${free_gb} GB free disk (need ~3 GB, plus ~0.5 GB per run)"
[ $BASE = 1 ] && exit $BAD

echo; echo "== Tasks"
READY=0
for t in "${BENCH_TASKS_DIR:-$BENCH/tasks}"/*/; do
  [ -f "$t/task.env" ] || continue
  id="$(basename "$t")"
  if git -C "$BENCH/.cache/templates/$id" rev-parse -q --verify baseline >/dev/null 2>&1; then ok "$id"; READY=1
  else echo "  · $id not prepared (bin/setup.sh $id)"; fi
done
[ $READY = 1 ] || { bad "no task prepared"; hint "bin/setup.sh"; }

if [ ${#EXS[@]} -eq 0 ]; then
  mapfile -t EXS < <(grep -E '^\s*-\s*\{id:.*provider:\s*custom' "$BENCH/matrix.yaml" | sed -E 's/.*\{id:\s*([^,]+),.*/\1/')
fi
for EX in "${EXS[@]}"; do
  echo; echo "== Executor $EX"
  if ! read -r PROVIDER MODEL HARNESS EFFORT < <(executor_fields "$EX"); then
    bad "not in matrix.yaml"; hint "add a line like: - {id: $EX, provider: custom, model: <model>, harness: claude, effort: high}"; continue
  fi
  ok "$HARNESS · $PROVIDER · $MODEL · effort $EFFORT"
  p="$(command -v "$HARNESS" || true)"; [ -n "$p" ] || { [ -x "$HOME/.local/bin/$HARNESS" ] && p="$HOME/.local/bin/$HARNESS"; }
  if [ -n "$p" ]; then ok "$HARNESS CLI: $("$p" --version 2>/dev/null | head -1)"
  else
    bad "$HARNESS CLI not found"
    case "$HARNESS" in
      claude) hint "curl -fsSL https://claude.ai/install.sh | bash" ;;
      codex)  hint "npm install -g @openai/codex" ;;
      pi)     hint "npm install -g @earendil-works/pi-coding-agent" ;;
    esac
  fi
  case "$PROVIDER" in
    custom)
      if [ -z "${CUSTOM_BASE_URL:-}" ]; then bad "CUSTOM_BASE_URL not set"; hint "cp .env.example .env and edit it"
      else
        u="$CUSTOM_BASE_URL"; [ "$HARNESS" = claude ] && u="$(custom_anthropic_url)"
        ok "endpoint $u"
        models="$(python3 -I - "${CUSTOM_BASE_URL%/}/models" "${CUSTOM_API_KEY:-}" <<'EOF'
import json,sys,urllib.request
req=urllib.request.Request(sys.argv[1],headers={"Authorization":f"Bearer {sys.argv[2]}"} if sys.argv[2] else {})
try: print(" ".join(m["id"] for m in json.load(urllib.request.urlopen(req,timeout=20))["data"]))
except Exception as e: print(f"ERROR {e}")
EOF
)"
        case "$models" in
          ERROR*) echo "  ? could not list models at ${CUSTOM_BASE_URL%/}/models (${models#ERROR }); the smoke test will tell" ;;
          *) [[ " $models " == *" $MODEL "* ]] && ok "model $MODEL is served" || { bad "model $MODEL not served; available: $models"; } ;;
        esac
        [ "$HARNESS" = codex ] && echo "  ! codex needs the Responses API (/v1/responses) at this endpoint"
      fi ;;
    anthropic) [ -f "$HOME/.claude/.credentials.json" ] || [ -n "${ANTHROPIC_API_KEY:-}" ] && ok "Anthropic credentials" \
                 || { bad "no Anthropic login"; hint "run claude and /login, or set ANTHROPIC_API_KEY"; } ;;
    openai)    [ -f "$HOME/.codex/auth.json" ] || [ -n "${OPENAI_API_KEY:-}" ] && ok "OpenAI credentials" \
                 || { bad "no OpenAI login"; hint "codex login, or set OPENAI_API_KEY"; } ;;
    zai)       [ -n "${ZAI_API_KEY:-}" ] && ok ZAI_API_KEY || { bad "ZAI_API_KEY not set"; hint "add it to .env"; } ;;
    alibaba)   [ -n "${ALI_CP_API_KEY:-}" ] && ok ALI_CP_API_KEY || { bad "ALI_CP_API_KEY not set"; hint "add it to .env"; } ;;
    azure)     [ -n "${AZURE_OPENAI_API_KEY:-}" ] && [ -n "${AZURE_OPENAI_ENDPOINT:-}" ] && ok "Azure key and endpoint" \
                 || { bad "AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT not set"; hint "add them to .env"; } ;;
  esac
  [ "$PROVIDER" != custom ] && [ "$HARNESS" = pi ] && [ ! -f "$HOME/.pi/agent/models.json" ] &&
    { bad "pi provider $PROVIDER needs ~/.pi/agent/models.json"; hint "or use provider custom"; }

  if [ $SMOKE = 1 ] && [ $BAD = 0 ]; then
    R="$BENCH/runs/_smoke/$EX-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$R/work"; git -C "$R/work" init -q
    harness_args "$HARNESS" 'Run `go version` with your shell tool, then reply with only its output.'
    echo "  … live session in the sandbox (up to 3 min): $R"
    timeout --kill-after=10 180 "$BENCH/bin/sandbox.sh" --run "$R" --harness "$HARNESS" --provider "$PROVIDER" \
      --model "$MODEL" --effort "$EFFORT" -- "${ARGS[@]}" < /dev/null > "$R/agent.out" 2> "$R/agent.err"
    rc=$?
    if grep -q 'go1\.[0-9]' "$R/agent.out"; then ok "smoke test: the model ran a tool in the sandbox"
    else
      bad "smoke test failed (exit $rc)"
      grep -ohE '(API Error|error)[^"]{0,200}' "$R/agent.out" "$R/agent.err" 2>/dev/null | head -3 | sed 's/^/      /'
      hint "details: $R/agent.out and agent.err"
    fi
  fi
done

echo
[ $BAD = 0 ] && echo "All good." || echo "Some checks failed (✗); see the hints (→)."
exit $BAD
