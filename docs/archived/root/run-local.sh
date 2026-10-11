#!/bin/bash
# run-local.sh — Run ai-discovery locally without pip install -e .
#
# Usage:
#   ./run-local.sh discover scan /path/to/repo -p myproject
#   ./run-local.sh discover --help
#   ./run-local.sh pytest tests/
#   ./run-local.sh pytest tests/test_call_graph.py -v

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Set PYTHONPATH to include src/
export PYTHONPATH="${SCRIPT_DIR}/src:${PYTHONPATH}"

# Determine command
COMMAND="${1:-help}"

case "$COMMAND" in
  discover)
    # Run discover CLI
    shift
    python -m ai_discovery.cli "$@"
    ;;
  pytest)
    # Run pytest
    shift
    pytest "$@"
    ;;
  python)
    # Run Python directly (with PYTHONPATH set)
    shift
    python "$@"
    ;;
  help|--help|-h)
    cat <<EOF
run-local.sh — Run ai-discovery locally without pip install

Usage:
  ./run-local.sh discover [ARGS...]    Run discover CLI
  ./run-local.sh pytest [ARGS...]      Run pytest
  ./run-local.sh python [ARGS...]      Run Python directly
  ./run-local.sh help                  Show this help

Examples:
  ./run-local.sh discover scan /path/to/repo -p myproject
  ./run-local.sh discover --help
  ./run-local.sh pytest tests/
  ./run-local.sh pytest tests/test_call_graph.py -v
  ./run-local.sh python -c "from ai_discovery.cli import app; print('OK')"

Note: PYTHONPATH is automatically set to ./src
EOF
    ;;
  *)
    echo "Unknown command: $COMMAND"
    echo "Run './run-local.sh help' for usage"
    exit 1
    ;;
esac
