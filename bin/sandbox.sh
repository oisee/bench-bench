#!/usr/bin/env bash
# Launch one coding-agent session in an isolated bwrap sandbox.
#
#   bin/sandbox.sh --run RUN_DIR --harness claude|codex|pi --provider anthropic|openai|zai|alibaba|azure|custom \
#                  --model MODEL [--effort low|medium|high] [-- extra harness args]
#
# RUN_DIR must contain work/ (the prepared task repo). The sandbox:
#   - sees only RUN_DIR/work (mounted at /work) and a fresh fake HOME (RUN_DIR/home)
#   - has no access to ~/dev, /mnt, real harness histories, gh auth or ssh keys
#   - has GitHub hosts blackholed via /etc/hosts; the rest of the internet works
# Session transcripts stay in RUN_DIR/home and are collected/audited afterwards.
set -euo pipefail

BENCH="$(cd "$(dirname "$0")/.." && pwd)"
. "$BENCH/bin/lib.sh"
REAL="$HOME"
RUN="" HARNESS="" PROVIDER="" MODEL="" EFFORT="medium"
while [ $# -gt 0 ]; do
  case "$1" in
    --run) RUN="$(realpath "$2")"; shift 2 ;;
    --harness) HARNESS="$2"; shift 2 ;;
    --provider) PROVIDER="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --effort) EFFORT="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$RUN" ] && [ -n "$HARNESS" ] && [ -n "$PROVIDER" ] && [ -n "$MODEL" ] || { sed -n '2,13p' "$0"; exit 2; }
[ -d "$RUN/work" ] || { echo "$RUN/work missing" >&2; exit 1; }

# provider keys live in the user's shell rc files; read just the one we need
key_from_rc() { bash --norc --noprofile -c 'for f in "$2/.bashrc" "$2/.profile"; do [ -f "$f" ] && eval "$(grep -E "^export $1=" "$f" | tail -1)"; done; printf %s "${!1}"' _ "$1" "$REAL"; }
ENVV=()
case "$PROVIDER" in
  alibaba)
    if [ "$HARNESS" = codex ]; then
      [ -n "${ALI_CODEX_BASE_URL:-}" ] && [ -n "${ALI_CODEX_MASTER_KEY:-}" ] || {
        echo 'ALI_CODEX_BASE_URL and ALI_CODEX_MASTER_KEY are required for Alibaba Codex' >&2; exit 2;
      }
      [[ "$ALI_CODEX_BASE_URL" =~ ^http://127\.0\.0\.1:[0-9]+/v1$ ]] || {
        echo 'Alibaba Codex requires a local LiteLLM tunnel ending in /v1' >&2; exit 2;
      }
      ENVV+=(--setenv LITELLM_MASTER_KEY "$ALI_CODEX_MASTER_KEY")
    else
      K="${ALI_CP_API_KEY:-$(key_from_rc ALI_CP_API_KEY)}"; [ -n "$K" ] || { echo "ALI_CP_API_KEY not set" >&2; exit 1; }
      ENVV+=(--setenv ALI_CP_API_KEY "$K")
    fi ;;
  zai)     K="${ZAI_API_KEY:-$(key_from_rc ZAI_API_KEY)}";     [ -n "$K" ] || { echo "ZAI_API_KEY not set" >&2; exit 1; };     ENVV+=(--setenv ZAI_API_KEY "$K") ;;
  azure)   K="${AZURE_OPENAI_API_KEY:-$(key_from_rc AZURE_OPENAI_API_KEY)}"; [ -n "$K" ] || { echo "AZURE_OPENAI_API_KEY not set" >&2; exit 1; }; ENVV+=(--setenv AZURE_OPENAI_API_KEY "$K") ;;
  custom)  [ -n "${CUSTOM_BASE_URL:-}" ] || { echo "CUSTOM_BASE_URL not set (see .env.example)" >&2; exit 1; }
           ENVV+=(--setenv CUSTOM_API_KEY "${CUSTOM_API_KEY:-none}") ;;
  anthropic|openai) ;;
  *) echo "unknown provider $PROVIDER" >&2; exit 2 ;;
esac

H="$RUN/home"
mkdir -p "$H"/{.claude,.codex,.pi/agent,.local/bin,.local/share,.cache,go/pkg/mod}
toolchain_setup "$REAL" || exit 2
cat > "$H/.gitconfig" <<'EOF'
[user]
	name = bench
	email = bench@localhost
[init]
	defaultBranch = main
EOF

# --- harness config inside the fake HOME -------------------------------------
BINDS=()
harness_bin "$HARNESS" "$REAL" "$H" || exit 2
case "$HARNESS" in
  claude)
    python3 -I - "$H/.claude.json" <<'EOF'
import json,sys
json.dump({"hasCompletedOnboarding":True,"theme":"dark","autoUpdates":False,
           "projects":{"/work":{"hasTrustDialogAccepted":True}}},open(sys.argv[1],"w"))
EOF
    echo '{"theme":"dark","autoUpdates":false}' > "$H/.claude/settings.json"
    case "$PROVIDER" in
      anthropic)
        if [ -f "$REAL/.claude/.credentials.json" ]; then
          # bind the real credentials file so OAuth refreshes are written back, not forked
          touch "$H/.claude/.credentials.json"
          BINDS+=(--bind "$REAL/.claude/.credentials.json" "$REAL/.claude/.credentials.json")
        elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
          ENVV+=(--setenv ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY")
        else echo 'Anthropic: run `claude` once to log in, or set ANTHROPIC_API_KEY' >&2; exit 1; fi ;;
      alibaba)
        cp "$REAL/.claude/alibaba-settings.json" "$H/.claude/settings.json"
        ENVV+=(--setenv ANTHROPIC_BASE_URL "https://coding-intl.dashscope.aliyuncs.com/apps/anthropic"
               --setenv ANTHROPIC_AUTH_TOKEN "$K" --setenv CLAUDE_CODE_MAX_OUTPUT_TOKENS 32768
               --setenv ANTHROPIC_DEFAULT_HAIKU_MODEL "$MODEL" --setenv CLAUDE_CODE_SUBAGENT_MODEL "$MODEL")
        case "$MODEL" in qwen*) ENVV+=(--setenv CLAUDE_CODE_MAX_CONTEXT_TOKENS 1000000) ;;
                         kimi*) ENVV+=(--setenv CLAUDE_CODE_MAX_CONTEXT_TOKENS 262144) ;;
                         *)     ENVV+=(--setenv CLAUDE_CODE_MAX_CONTEXT_TOKENS 200000) ;; esac ;;
      zai)
        ENVV+=(--setenv ANTHROPIC_BASE_URL "https://api.z.ai/api/anthropic" --setenv ANTHROPIC_AUTH_TOKEN "$K"
               --setenv ANTHROPIC_DEFAULT_HAIKU_MODEL "$MODEL" --setenv CLAUDE_CODE_SUBAGENT_MODEL "$MODEL") ;;
      custom)
        ENVV+=(--setenv ANTHROPIC_BASE_URL "$(custom_anthropic_url)" --setenv ANTHROPIC_AUTH_TOKEN "${CUSTOM_API_KEY:-none}"
               --setenv ANTHROPIC_DEFAULT_HAIKU_MODEL "$MODEL" --setenv CLAUDE_CODE_SUBAGENT_MODEL "$MODEL"
               --setenv CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC 1)
        [ -n "${CUSTOM_CONTEXT_WINDOW:-}" ] && ENVV+=(--setenv CLAUDE_CODE_MAX_CONTEXT_TOKENS "$CUSTOM_CONTEXT_WINDOW") ;;
      *) echo "claude harness does not support provider $PROVIDER" >&2; exit 2 ;;
    esac
    CMD=(claude --model "$MODEL" --effort "$EFFORT") ;;
  codex)
    {
      echo "model = \"$MODEL\""
      echo "model_reasoning_effort = \"$EFFORT\""
      echo 'check_for_update_on_startup = false'
      case "$PROVIDER" in
        zai) printf '%s\n' 'model_provider = "zai"' '' '[model_providers.zai]' 'name = "Z.ai"' \
               'base_url = "https://api.z.ai/api/v1"' 'env_key = "ZAI_API_KEY"' 'wire_api = "responses"' 'requires_openai_auth = false' ;;
        azure) printf '%s\n' 'model_provider = "azure"' '' '[model_providers.azure]' 'name = "Azure"' \
               "base_url = \"${AZURE_OPENAI_ENDPOINT:?AZURE_OPENAI_ENDPOINT not set}/openai\"" 'env_key = "AZURE_OPENAI_API_KEY"' \
               'query_params = { api-version = "2025-04-01-preview" }' 'wire_api = "responses"' ;;
        alibaba)
          cp "$BENCH/gateway/codex-models.json" "$H/.codex/codex-models.json"
          case "$MODEL" in
            qwen3.7-plus) CONTEXT_WINDOW=1000000 ;;
            kimi-k2.5) CONTEXT_WINDOW=262144 ;;
            MiniMax-M2.5) CONTEXT_WINDOW=196608 ;;
            glm-5) CONTEXT_WINDOW=202752 ;;
            *) echo "Unsupported Alibaba Codex model: $MODEL" >&2; exit 2 ;;
          esac
          printf '%s\n' 'model_provider = "ali_bridge"' "model_catalog_json = \"$REAL/.codex/codex-models.json\"" \
            "model_context_window = $CONTEXT_WINDOW" '' '[model_providers.ali_bridge]' \
            'name = "Alibaba Coding Plan via LiteLLM"' "base_url = \"$ALI_CODEX_BASE_URL\"" \
            'env_key = "LITELLM_MASTER_KEY"' 'wire_api = "responses"' \
            'requires_openai_auth = false' 'supports_websockets = false' ;;
        custom) # Codex speaks only the Responses API (/v1/responses)
          printf '%s\n' 'model_provider = "custom"' '' '[model_providers.custom]' 'name = "Custom endpoint"' \
            "base_url = \"${CUSTOM_BASE_URL%/}\"" 'env_key = "CUSTOM_API_KEY"' 'wire_api = "responses"' \
            'requires_openai_auth = false' 'supports_websockets = false'
          [ -n "${CUSTOM_CONTEXT_WINDOW:-}" ] && echo "model_context_window = $CUSTOM_CONTEXT_WINDOW" ;;
        openai) ;;
        *) echo "codex harness does not support provider $PROVIDER" >&2; exit 2 ;;
      esac
      printf '\n[projects."/work"]\ntrust_level = "trusted"\n'
    } > "$H/.codex/config.toml"
    if [ "$PROVIDER" = openai ]; then
      if [ -f "$REAL/.codex/auth.json" ]; then
        # a private copy: a token refresh inside the sandbox must never rotate the user's own login
        install -m 600 "$REAL/.codex/auth.json" "$H/.codex/auth.json"
      elif [ -n "${OPENAI_API_KEY:-}" ]; then
        ENVV+=(--setenv CODEX_API_KEY "$OPENAI_API_KEY")
      else echo 'OpenAI: run `codex login` once, or set OPENAI_API_KEY' >&2; exit 1; fi
    fi
    CMD=(codex) ;;
  pi)
    python3 -I - "$REAL/.pi/agent/models.json" "$H/.pi/agent" "$PROVIDER" "$MODEL" \
      "${CUSTOM_BASE_URL:-}" "${CUSTOM_PI_API:-openai-completions}" "${CUSTOM_CONTEXT_WINDOW:-131072}" "${CUSTOM_MAX_TOKENS:-32768}" <<'EOF'
import json,sys,os
src,dst,prov,model,url,api,ctx,maxtok=sys.argv[1:]
if prov=="custom":  # generated from .env; reasoning levels pass through unchanged
    p={"name":"Custom endpoint","baseUrl":url.rstrip("/"),"apiKey":"$CUSTOM_API_KEY","api":api,
       "models":[{"id":model,"name":model,"api":api,"reasoning":True,"input":["text"],
                  "contextWindow":int(ctx),"maxTokens":int(maxtok),
                  "thinkingLevelMap":{"xhigh":"xhigh"}}]}
else:
    if not os.path.exists(src): sys.exit(f"{src} missing: configure provider {prov} in pi first")
    d=json.load(open(src))["providers"]
    if prov not in d: sys.exit(f"provider {prov} not in pi models.json")
    p=d[prov]
    assert str(p.get("apiKey","$")).startswith("$"), "refusing to copy a literal key"
json.dump({"providers":{prov:p}},open(os.path.join(dst,"models.json"),"w"),indent=2)
json.dump({"defaultProvider":prov,"defaultModel":model,"enabledModels":[f"{prov}/{model}"]},
          open(os.path.join(dst,"settings.json"),"w"),indent=2)
EOF
    CMD=(pi --model "$PROVIDER/$MODEL" --thinking "$EFFORT") ;;
  *) echo "unknown harness $HARNESS" >&2; exit 2 ;;
esac

# BENCH_SHELL=1 drops into a shell inside the same sandbox (for debugging / smoke tests)
[ -n "${BENCH_SHELL:-}" ] && CMD=(bash)

# --- GitHub blackhole ---------------------------------------------------------
HOSTS="$RUN/hosts"
{ cat /etc/hosts; for h in github.com www.github.com api.github.com gist.github.com raw.githubusercontent.com \
    codeload.github.com objects.githubusercontent.com githubusercontent.com github.io oisee.github.io \
    uploads.github.com; do echo "0.0.0.0 $h"; echo ":: $h"; done; } > "$HOSTS"

# /etc/resolv.conf may point into /mnt (WSL); expose just that file
RESOLV="$(readlink -f /etc/resolv.conf)"
PATH_IN="$REAL/.local/bin:$TOOL_PATH:$REAL/go/bin:/usr/local/go/bin:/usr/lib/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

meta() { python3 -I - "$RUN/meta.json" "$@" <<'EOF'
import json,sys,os
p=sys.argv[1]; d=json.load(open(p)) if os.path.exists(p) else {}
for kv in sys.argv[2:]:
    k,v=kv.split("=",1); d[k]=v
json.dump(d,open(p,"w"),indent=2)
EOF
}
meta harness="$HARNESS" provider="$PROVIDER" model="$MODEL" effort="$EFFORT" round="${BENCH_ROUND:-1}" \
     harness_version="$("${CMD[0]}" --version 2>/dev/null | head -1)" started_at="$(date -Is)"

set +e
bwrap --die-with-parent --unshare-pid --unshare-ipc \
  --ro-bind /usr /usr --ro-bind /etc /etc --ro-bind "$HOSTS" /etc/hosts \
  --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
  --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run --tmpfs /mnt \
  --ro-bind "$RESOLV" "$RESOLV" \
  --bind "$H" "$REAL" \
  "${TOOL_BINDS[@]+"${TOOL_BINDS[@]}"}" "${GO_BINDS[@]+"${GO_BINDS[@]}"}" \
  "${BINDS[@]+"${BINDS[@]}"}" \
  --bind "$RUN/work" /work --chdir /work \
  --clearenv --setenv HOME "$REAL" --setenv USER "$USER" --setenv PATH "$PATH_IN" \
  --setenv TERM "${TERM:-xterm-256color}" --setenv LANG "${LANG:-C.UTF-8}" --setenv COLORTERM "${COLORTERM:-truecolor}" \
  --setenv GOFLAGS "-mod=mod" --setenv GH_CONFIG_DIR /nonexistent "${TOOL_ENV[@]+"${TOOL_ENV[@]}"}" \
  "${ENVV[@]+"${ENVV[@]}"}" \
  "${CMD[@]}" "$@"
RC=$?
set -e
meta finished_at="$(date -Is)" exit_code="$RC"
exit $RC
