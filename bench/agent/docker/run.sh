#!/usr/bin/env bash
# Run the agent-eval harness inside its disposable container (M5.7.b).
#
#   bench/agent/docker/run.sh build                      # build the image
#   bench/agent/docker/run.sh check                      # toolchain versions + `bench.agent validate`
#   bench/agent/docker/run.sh preflight                  # build/test each task's unmodified base
#   bench/agent/docker/run.sh dev-run --limit 1 …        # harness development against the local gateway
#   bench/agent/docker/run.sh run --confirm-spend …      # the eval itself (needs ANTHROPIC_API_KEY in the env file)
#
# Secrets: only these lines reach the container (agents there run with bypassPermissions, so nothing else from
# your env files does):
#   AIGW_API_KEY=sk-...          gateway key for dev-run (AIGW_DEV_KEY is still accepted)
#   AIGW_API_MODEL=glm-...       default --model for dev-run (not a secret; passed the same way)
#   ANTHROPIC_API_KEY=sk-ant-... for the eval run
# They are read from $BENCH_AGENT_ENV, else ~/.config/ai-discovery/agent-eval.env, else the checkout's .env.
# The dev gateway URL is BENCH_GATEWAY_URL, else AIGW_API_URL from that file with localhost → host.docker.internal
# and a trailing /v1 → /anthropic (Claude Code speaks the Anthropic protocol).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"          # ai-discovery checkout
PARENT="$(cd "$REPO/.." && pwd)"              # holds ../sample-apps (task sources, mounted read-only)
IMAGE="${BENCH_AGENT_IMAGE:-ai-discovery-agent-eval:latest}"
ENV_FILE="${BENCH_AGENT_ENV:-$HOME/.config/ai-discovery/agent-eval.env}"
[[ -f "$ENV_FILE" ]] || ENV_FILE="$REPO/.env"
SECRET_KEYS='^(AIGW_API_KEY|AIGW_DEV_KEY|AIGW_API_MODEL|ANTHROPIC_API_KEY)='

gateway_url() {
  local url=""
  [[ -f "$ENV_FILE" ]] && url="$(grep -E '^AIGW_API_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d "\"'" || true)"
  url="${url:-http://localhost:11433/v1}"
  echo "$url" | sed -E 's#^(https?://)(localhost|127\.0\.0\.1)#\1host.docker.internal#; s#/v1/?$#/anthropic#'
}
GATEWAY="${BENCH_GATEWAY_URL:-$(gateway_url)}"

if [[ "${1:-}" == "build" ]]; then
  exec docker build -f "$HERE/Dockerfile" -t "$IMAGE" "$REPO"
fi

env_args=()
SECRETS=""
if [[ -f "$ENV_FILE" ]]; then
  SECRETS="$(mktemp)"; chmod 600 "$SECRETS"
  # only the key lines, quotes stripped (docker --env-file keeps them literally)
  { grep -E "$SECRET_KEYS" "$ENV_FILE" || true; } | sed -E "s/^([A-Z_]+)=[\"'](.*)[\"']\$/\1=\2/" > "$SECRETS"
  env_args=(--env-file "$SECRETS")
fi
cleanup() { [[ -n "$SECRETS" ]] && rm -f "$SECRETS"; }
trap cleanup EXIT
mounts=(-v "$REPO:/code-intel/ai-discovery" -v "uv-cache-agent-eval:/home/bench/.cache/uv")
[[ -d "$PARENT/sample-apps" ]] && mounts+=(-v "$PARENT/sample-apps:/code-intel/sample-apps:ro")

cmd="${1:-check}"; shift || true
args=""
if [[ $# -gt 0 ]]; then args="$(printf '%q ' "$@")"; fi
case "$cmd" in
  check)     inner='java -version 2>&1 | head -1; dotnet --version; node --version; claude --version; git --version; python -m bench.agent validate' ;;
  dev-run)   inner="python -m bench.agent dev-run --base-url $GATEWAY $args" ;;
  shell)     inner='bash' ;;
  *)         inner="python -m bench.agent $cmd $args" ;;
esac
docker run --rm -i $([[ -t 0 ]] && echo -t) --add-host host.docker.internal:host-gateway \
  ${env_args[@]+"${env_args[@]}"} "${mounts[@]}" "$IMAGE" \
  bash -c "uv pip install -q --no-deps -e /code-intel/ai-discovery >/dev/null && $inner"
