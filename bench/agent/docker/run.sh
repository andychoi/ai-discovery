#!/usr/bin/env bash
# Run the agent-eval harness inside its disposable container (M5.7.b).
#
#   bench/agent/docker/run.sh build                      # build the image
#   bench/agent/docker/run.sh check                      # toolchain versions + `bench.agent validate`
#   bench/agent/docker/run.sh preflight                  # build/test each task's unmodified base
#   bench/agent/docker/run.sh dev-run --limit 1 …        # harness development against the local gateway
#   bench/agent/docker/run.sh run --confirm-spend …      # the eval itself (needs ANTHROPIC_API_KEY in the env file)
#
# Secrets come from an env file that only docker reads (default ~/.config/ai-discovery/agent-eval.env, mode 600):
#   AIGW_DEV_KEY=sk-...          gateway key for dev-run
#   ANTHROPIC_API_KEY=sk-ant-... for the eval run
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"          # ai-discovery checkout
PARENT="$(cd "$REPO/.." && pwd)"              # holds ../sample-apps (task sources, mounted read-only)
IMAGE="${BENCH_AGENT_IMAGE:-ai-discovery-agent-eval:latest}"
ENV_FILE="${BENCH_AGENT_ENV:-$HOME/.config/ai-discovery/agent-eval.env}"
GATEWAY="${BENCH_GATEWAY_URL:-http://host.docker.internal:11433/anthropic}"

if [[ "${1:-}" == "build" ]]; then
  exec docker build -f "$HERE/Dockerfile" -t "$IMAGE" "$REPO"
fi

env_args=()
if [[ -f "$ENV_FILE" ]]; then env_args=(--env-file "$ENV_FILE"); fi
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
exec docker run --rm -i $([[ -t 0 ]] && echo -t) --add-host host.docker.internal:host-gateway \
  ${env_args[@]+"${env_args[@]}"} "${mounts[@]}" "$IMAGE" \
  bash -c "uv pip install -q --no-deps -e /code-intel/ai-discovery >/dev/null && $inner"
