# Shared helpers, sourced by the bin/*.sh scripts (expects $BENCH to be set).

# Load $BENCH/.env (KEY=value lines, see .env.example) into the environment.
# Variables already set in the environment win, so one run can point elsewhere:
#   CUSTOM_BASE_URL=http://host:8081/v1 bin/bench.sh <executor>
bench_load_env() {
  [ -f "$BENCH/.env" ] || return 0
  local preset; preset="$(export -p | sed 's/^declare -x /declare -gx /')"
  set -a; . "$BENCH/.env"; set +a
  eval "$preset"
}

# executor id -> "provider model harness effort" (from matrix.yaml); exit 1 if unknown
executor_fields() {
  python3 -I - "$BENCH/matrix.yaml" "$1" <<'EOF'
import re,sys
for line in open(sys.argv[1]):
    m=re.search(r'\{id:\s*([^,]+),\s*provider:\s*([^,]+),\s*model:\s*("?[^,"]+"?),\s*harness:\s*([^,]+),\s*effort:\s*([^,}]+)',line)
    if m and not line.lstrip().startswith('#') and m.group(1).strip()==sys.argv[2]:
        print(m.group(2).strip(), m.group(3).strip().strip('"'), m.group(4).strip(), m.group(5).strip()); break
else: sys.exit(1)
EOF
}

# The Anthropic-style base URL for a custom endpoint: CUSTOM_ANTHROPIC_BASE_URL, or
# CUSTOM_BASE_URL without its trailing /v1 (Claude Code appends /v1/messages itself).
# API endpoints; key_proxy_setup points these at a local proxy before sandboxing.
: "${ZAI_ORIGIN:=https://api.z.ai}"; ZAI_ANTHROPIC_PATH=/api/anthropic; ZAI_CODEX_PATH=/api/v1
: "${ALI_ANTHROPIC_BASE_URL:=https://coding-intl.dashscope.aliyuncs.com/apps/anthropic}"

# A provider key exported in ~/.bashrc or ~/.profile (non-interactive shells skip them)
key_from_rc() { bash --norc --noprofile -c 'for f in "$2/.bashrc" "$2/.profile"; do [ -f "$f" ] && eval "$(grep -E "^export $1=" "$f" | tail -1)"; done; printf %s "${!1}"' _ "$1" "$HOME"; }

custom_anthropic_url() {
  local u="${CUSTOM_ANTHROPIC_BASE_URL:-${CUSTOM_BASE_URL:-}}"
  [ -n "${CUSTOM_ANTHROPIC_BASE_URL:-}" ] || { u="${u%/}"; u="${u%/v1}"; }
  printf %s "${u%/}"
}

# Harness CLI arguments for one non-interactive session on prompt $2.
harness_args() {
  case "$1" in
    pi)     ARGS=(-p "$2") ;;
    claude) ARGS=(-p "$2" --dangerously-skip-permissions --output-format stream-json --verbose) ;;
    codex)  ARGS=(exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json "$2") ;;
    *) echo "unknown harness $1" >&2; return 2 ;;
  esac
}

# Mount a host path read-only into a sandbox unless it is already visible under /usr.
_bind_ro() { case "$1" in /usr|/usr/*) ;; *) TOOL_BINDS+=(--ro-bind "$1" "$1") ;; esac; }

# Node and Go for the sandboxes, wherever they are installed (apt, nvm, fnm, go.dev tarball, ...).
# Sets TOOL_BINDS (read-only bwrap args), GO_BINDS (writable Go caches), TOOL_PATH (PATH prefix),
# TOOL_ENV (bwrap --setenv args), GOMODCACHE_DIR and GOCACHE_DIR. $1 is the real HOME.
toolchain_setup() {
  local node go_root
  TOOL_BINDS=(); TOOL_PATH=""; TOOL_ENV=(); GO_BINDS=(); GOMODCACHE_DIR=""; GOCACHE_DIR=""
  node="$(command -v node || true)"
  [ -n "$node" ] || node="$(find "$1/.nvm/versions/node" -type f -path '*/bin/node' 2>/dev/null | sort -V | tail -1)"
  [ -n "$node" ] && [ -x "$node" ] || { echo 'node executable not found (install Node.js 20+)' >&2; return 2; }
  NODE_BIN="$(dirname "$(readlink -f "$node")")"
  _bind_ro "$(dirname "$NODE_BIN")"
  [ -d "$1/.nvm" ] && TOOL_BINDS+=(--ro-bind "$1/.nvm" "$1/.nvm")
  TOOL_PATH="$NODE_BIN"
  if command -v go >/dev/null; then
    go_root="$(go env GOROOT)"; GOMODCACHE_DIR="$(go env GOMODCACHE)"; GOCACHE_DIR="$(go env GOCACHE)"
    mkdir -p "$GOMODCACHE_DIR" "$GOCACHE_DIR"
    _bind_ro "$go_root"
    TOOL_PATH="$TOOL_PATH:$go_root/bin"
    TOOL_ENV=(--setenv GOMODCACHE "$GOMODCACHE_DIR" --setenv GOCACHE "$GOCACHE_DIR")
    GO_BINDS=(--bind "$GOMODCACHE_DIR" "$GOMODCACHE_DIR" --bind "$GOCACHE_DIR" "$GOCACHE_DIR")
  fi
}

# Make harness CLI $1 runnable inside a sandbox whose fake HOME $3 is mounted over the real HOME $2:
# bind its install directory and link it into $3/.local/bin. Appends to TOOL_BINDS.
harness_bin() {
  local name="$1" real="$2" fake="$3" p rest pkg root roots=()
  p="$(command -v "$name" || true)"
  [ -n "$p" ] || { [ -x "$real/.local/bin/$name" ] && p="$real/.local/bin/$name"; }
  [ -n "$p" ] || { echo "$name CLI not found; install it first (README: Prerequisites)" >&2; return 2; }
  p="$(readlink -f "$p")"
  case "$p" in
    "$real"/.local/share/claude/*) roots=("$real/.local/share/claude") ;;
    "$real"/.codex/packages/*)     roots=("$real/.codex/packages") ;;
    "$real"/.pi/agent/*)           roots=("$real/.pi/agent/install" "$real/.pi/agent/bin") ;;
    */node_modules/*)              # npm install: bind the package directory
      rest="${p#*/node_modules/}"; pkg="${rest%%/*}"
      [[ "$pkg" == @* ]] && { rest="${rest#*/}"; pkg="$pkg/${rest%%/*}"; }
      roots=("${p%%/node_modules/*}/node_modules/$pkg") ;;
    *) roots=("$(dirname "$p")") ;;
  esac
  for root in "${roots[@]}"; do
    [ -e "$root" ] || continue
    case "$root" in "$real"/*) mkdir -p "$fake${root#"$real"}" ;; esac
    _bind_ro "$root"
  done
  mkdir -p "$fake/.local/bin"
  ln -sfn "$p" "$fake/.local/bin/$name"
}

# Documentation access for the egress proxy; strict mode ignores extra hosts too.
egress_docs_policy() {
  NET_POLICY=egress-allowlist
  ALLOW="help.sap.com,rules.abaplint.org,abaplint.app${BENCH_EGRESS_EXTRA:+,$BENCH_EGRESS_EXTRA}"
  if [ "${BENCH_EGRESS_DOCS:-1}" = 0 ]; then
    NET_POLICY=egress-endpoint-only
    ALLOW=""
  fi
}

# Start outside bwrap. Only the proxy receives the real key; callers inherit dummy credentials.
# $1: record (0/1). Expects PROVIDER, HARNESS, RUN; sets REAL_KEY and PROXY_PID.
key_proxy_setup() {
  local record="$1" key="" up="" origin path port
  REAL_KEY=""
  case "$PROVIDER" in
    custom)
      key="${CUSTOM_API_KEY:-}"
      if [ "$HARNESS" = claude ]; then up="$(custom_anthropic_url)"; else up="${CUSTOM_BASE_URL:-}"; fi ;;
    zai)
      key="${ZAI_API_KEY:-$(key_from_rc ZAI_API_KEY)}"
      up="$ZAI_ORIGIN$([ "$HARNESS" = claude ] && echo "$ZAI_ANTHROPIC_PATH" || echo "$ZAI_CODEX_PATH")" ;;
    alibaba)
      if [ "$HARNESS" = codex ]; then
        key="${ALI_CODEX_MASTER_KEY:-}"; up="${ALI_CODEX_BASE_URL:-}"
        [[ "$up" =~ ^http://127\.0\.0\.1:[0-9]+/v1$ ]] || { echo 'Alibaba Codex requires a local LiteLLM tunnel ending in /v1' >&2; return 2; }
      else key="${ALI_CP_API_KEY:-$(key_from_rc ALI_CP_API_KEY)}"; up="$ALI_ANTHROPIC_BASE_URL"; fi ;;
    azure) key="${AZURE_OPENAI_API_KEY:-$(key_from_rc AZURE_OPENAI_API_KEY)}"; up="${AZURE_OPENAI_ENDPOINT:-}" ;;
    *) [ "$record" = 0 ] && return 0
       echo 'recording requires an API-key provider' >&2; return 2 ;;
  esac
  [ "$key" != bench-sandbox-dummy-key ] || return 0   # already proxied by run.sh
  if [ -z "$key" ] || [ "$key" = none ]; then
    [ "$PROVIDER" = custom ] || { echo "$PROVIDER API key not set" >&2; return 1; }
    key=""; [ "$record" = 1 ] || return 0
  fi
  [[ "$up" =~ ^(https?://[^/]+)(.*)$ ]] || { echo 'cannot parse endpoint URL' >&2; return 2; }
  origin="${BASH_REMATCH[1]}"; path="${BASH_REMATCH[2]}"
  REAL_KEY="$key"
  coproc PROXY { export BENCH_PROXY_RECORD="$record" BENCH_UPSTREAM_KEY="$key"
                 [ "$record" = 1 ] || unset BENCH_SNAPSHOT_RUN
                 exec python3 -I "$BENCH/bin/record-proxy.py" "$origin" "$RUN/traffic.jsonl" 2>> "$RUN/proxy.err"; }
  read -r -t 10 port <&"${PROXY[0]}" || { echo 'key proxy did not start (see proxy.err)' >&2; return 2; }
  trap 'kill "${PROXY_PID:-}" 2>/dev/null || true' EXIT
  case "$PROVIDER" in
    custom)
      if [ "$HARNESS" = claude ]; then export CUSTOM_ANTHROPIC_BASE_URL="http://127.0.0.1:$port$path"
      else export CUSTOM_BASE_URL="http://127.0.0.1:$port$path"; fi
      export CUSTOM_API_KEY=bench-sandbox-dummy-key ;;
    zai) export ZAI_ORIGIN="http://127.0.0.1:$port" ZAI_API_KEY=bench-sandbox-dummy-key ;;
    alibaba)
      if [ "$HARNESS" = codex ]; then export ALI_CODEX_BASE_URL="http://127.0.0.1:$port$path" ALI_CODEX_MASTER_KEY=bench-sandbox-dummy-key
      else export ALI_ANTHROPIC_BASE_URL="http://127.0.0.1:$port$path" ALI_CP_API_KEY=bench-sandbox-dummy-key; fi ;;
    azure) export AZURE_OPENAI_ENDPOINT="http://127.0.0.1:$port$path" AZURE_OPENAI_API_KEY=bench-sandbox-dummy-key ;;
  esac
}
