#!/bin/bash
#
# demo.sh — AI-Discovery Demo: TodoApp (davidfowl/TodoApp)
#
# Discovers a real C#/.NET application using MLX Qwen3.5 models via mlc-server
#
# Usage:
#   ./demo.sh                    # Full discovery with all features
#   ./demo.sh --no-mining        # Skip process mining (faster)
#   ./demo.sh --quick            # Skip self-review (faster)
#   ./demo.sh --help             # Show options
#
# Requirements:
#   - mlc-server running on localhost:11435 with Qwen3.5 models
#   - Python 3.10+
#   - ai-discovery package installed (pip install -e .)
#   - ~2-3 hours runtime (full discovery with mining + self-review)
#
# Models used:
#   - Tier 1 (fast):     Qwen3.5-2B-4bit    (summarization)
#   - Tier 2 (standard): Qwen3.5-4B-MLX-4bit (flow analysis)
#   - Tier 3 (dev):      Qwen3.5-9B-MLX-4bit (doc generation)
#
# Author: Claude
# Date: 2026-04-17

set -e  # Exit on error

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

DEMO_NAME="TodoApp"
DEMO_PROJECT="todoapp"
REPO_URL="https://github.com/davidfowl/TodoApp"
REPO_DIR="${HOME}/tmp/TodoApp"
OUTPUT_DIR="./data/${DEMO_PROJECT}"
DOCS_DIR="./data/${DEMO_PROJECT}"
LOG_FILE="${OUTPUT_DIR}/discovery.log"

# MLC Server configuration
MLC_SERVER_URL="http://localhost:11435"
MLC_PROVIDER="bedrock"  #mlx-qwen"

# Default options
ENABLE_MINING=true
ENABLE_SELF_REVIEW=true
RESCAN=false

# ──────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────────────────────────────────────

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log_section() {
    echo "" | tee -a "${LOG_FILE}"
    echo "╔════════════════════════════════════════════════════════════════╗" | tee -a "${LOG_FILE}"
    echo "║ $* " | tee -a "${LOG_FILE}"
    echo "╚════════════════════════════════════════════════════════════════╝" | tee -a "${LOG_FILE}"
    echo "" | tee -a "${LOG_FILE}"
}

show_help() {
    cat << 'EOF'
╔════════════════════════════════════════════════════════════════════════════╗
║                  AI-Discovery Demo: TodoApp (C# .NET)                     ║
╚════════════════════════════════════════════════════════════════════════════╝

Usage:
  ./demo.sh                    # Full discovery (2-3 hours)
  ./demo.sh --no-mining        # Skip process mining (1.5-2 hours)
  ./demo.sh --quick            # Skip self-review (30-45 min)
  ./demo.sh --rescan           # Force rescan (ignore cache)
  ./demo.sh --help             # Show this message

Options:
  --no-mining       Disable Stage 10.5 (process mining)
  --quick           Skip Stage 14 (self-review for speed)
  --rescan          Force full rescan (ignore previous run)
  --help            Show this help message

Configuration:
  Repository:       https://github.com/davidfowl/TodoApp
  Provider:         mlx-qwen (MLX Qwen server on localhost:11435)
  Tier 1 (fast):    Qwen3.5-2B-4bit
  Tier 2 (std):     Qwen3.5-4B-MLX-4bit
  Tier 3 (dev):     Qwen3.5-9B-MLX-4bit
  Output:           ./data/todoapp/

Pipeline Stages:
  Stages 1-9:       Parse, graph, chunking, embedding (10-15 min)
  Stage 11:         Tier 1 summarization (10-20 min)
  Stage 12:         Tier 2 flow analysis (15-30 min)
  Stage 12.5:       Scenario flow inference (5-10 min)
  Stage 13.5:       BPMN/Mermaid/PlantUML artifacts (5-10 min)
  Stage 10.5:       Process mining [OPTIONAL] (10-20 min)
  Stage 15:         Markdown rendering (5 min)
  Stage 14:         Self-review [OPTIONAL] (30-60 min)

Output Artifacts:
  docs/ASIS/        As-is domain documentation
  docs/ASD/         As-is detailed specs
  docs/PF/          Process flows (BPMN, Mermaid, PlantUML)
  mining_reports/   Mining analysis (conformance, bottlenecks)

Requirements:
  • mlc-server running: ollama serve mlx-community/Qwen3.5-2B-4bit
  • Python 3.10+
  • 8GB+ GPU memory recommended
  • 2-3 hours for full discovery

Start mlc-server:
  # Terminal 1
  ollama serve mlx-community/Qwen3.5-2B-4bit

  # Terminal 2
  ./demo.sh

EOF
}

check_mlc_server() {
    log_section "Checking MLC Server"

    if ! command -v curl &> /dev/null; then
        log "⚠️  curl not found, skipping MLC server check"
        return 0
    fi

    if curl -s "${MLC_SERVER_URL}/v1/models" &> /dev/null; then
        log "✅ MLC Server is running on ${MLC_SERVER_URL}"
    else
        log "❌ ERROR: MLC Server is not responding on ${MLC_SERVER_URL}"
        log "   Start it with: ollama serve mlx-community/Qwen3.5-2B-4bit"
        exit 1
    fi
}

prepare_repo() {
    log_section "Preparing Repository"

    if [ -d "${REPO_DIR}" ]; then
        log "Repository already cloned at ${REPO_DIR}"
        log "Updating to latest..."
        cd "${REPO_DIR}"
        git pull origin main 2>/dev/null || git pull origin master 2>/dev/null || true
        cd - > /dev/null
    else
        log "Cloning repository from ${REPO_URL}..."
        git clone "${REPO_URL}" "${REPO_DIR}"
    fi

    REPO_COMMIT=$(cd "${REPO_DIR}" && git rev-parse --short HEAD)
    log "✅ Repository ready: ${REPO_COMMIT}"
}

create_config() {
    log_section "Creating Configuration"

    mkdir -p "${OUTPUT_DIR}"

    cat > "${OUTPUT_DIR}/discovery.yaml" << 'YAML'
# AI-Discovery Configuration for TodoApp Demo
# Using MLX Qwen3.5 models on mlc-server

provider: mlx-qwen

# LLM cost tracking (local, so no actual cost)
budget_limit_usd: 50.0

# Concurrent processing
max_concurrent: 4

# Production mode: use tier3p instead of tier3d for doc generation
prod: false

# MLX Qwen configuration
mlx_qwen:
  base_url: http://localhost:11435
  api_key: ""
  tier1: mlx-community/Qwen3.5-2B-4bit        # Fast (2B)
  tier2: mlx-community/Qwen3.5-4B-MLX-4bit   # Standard (4B)
  tier3d: mlx-community/Qwen3.5-9B-MLX-4bit  # Dev (9B)
  tier3p: mlx-community/Qwen3.5-27B-4bit     # Prod (27B, if available)
  tier1_num_ctx: 4096

# RAG configuration
rag:
  embedding_provider: mlx-qwen
  ollama_model: nomic-embed-text
  chunk_size: 1500
  chunk_overlap: 200
  top_k: 5

# Process Mining Configuration (Stage 10.5) — OPTIONAL
process_mining:
  enabled: false  # Change to true to enable conformance analysis
  miner_variant: inductive
  fitness_threshold: 0.90
  precision_threshold: 0.85
  generalization_threshold: 0.80
  max_traces: 10000
  output_reports: true
YAML

    log "✅ Configuration created at ${OUTPUT_DIR}/discovery.yaml"
}

run_discovery() {
    log_section "Running AI-Discovery"

    # Determine if discover is installed, or use python -m fallback
    if command -v discover &> /dev/null; then
        # Using installed discover CLI
        DISCOVER_CMD="discover scan \"${REPO_DIR}\" -p \"${DEMO_PROJECT}\""
    else
        # Fallback: use Python module invocation (no installation required)
        log "ℹ️  discover CLI not found, using python -m app.cli"
        DISCOVER_CMD="python -m app.cli scan \"${REPO_DIR}\" -p \"${DEMO_PROJECT}\""
    fi

    # Add config
    DISCOVER_CMD="${DISCOVER_CMD} -c \"${OUTPUT_DIR}/discovery.yaml\""

    # Add options
    [ "${RESCAN}" = true ] && DISCOVER_CMD="${DISCOVER_CMD} --rescan"

    log "Command: ${DISCOVER_CMD}"
    log ""

    # Run discovery
    eval "${DISCOVER_CMD}"

    DISCOVERY_EXIT=$?
    if [ ${DISCOVERY_EXIT} -eq 0 ]; then
        log "✅ Discovery completed successfully"
    else
        log "❌ Discovery failed with exit code ${DISCOVERY_EXIT}"
        exit 1
    fi
}

show_results() {
    log_section "Results Summary"

    log "Output directory: ${DOCS_DIR}/"
    log ""

    if [ -d "${DOCS_DIR}" ]; then
        log "📁 Generated documents:"
        echo ""

        # Count documents by type
        for dir in ASIS ASD ASSC SPEC DM IF INT DES ADR PF; do
            if [ -d "${DOCS_DIR}/${dir}" ]; then
                count=$(find "${DOCS_DIR}/${dir}" -name "*.md" | wc -l)
                [ ${count} -gt 0 ] && log "   ${dir}/  — ${count} document(s)"
            fi
        done

        # Mining reports
        if [ -d "${DOCS_DIR}/mining_reports" ]; then
            mining_count=$(find "${DOCS_DIR}/mining_reports" -name "*.md" | wc -l)
            log "   mining_reports/  — ${mining_count} report(s)"
        fi

        log ""
        log "📊 Next steps:"
        log "   1. Review domain docs: ${DOCS_DIR}/ASIS/"
        log "   2. Check process flows: ${DOCS_DIR}/PF/"
        if [ "${ENABLE_MINING}" = true ]; then
            log "   3. Review mining analysis: ${DOCS_DIR}/mining_reports/"
        fi
        log "   4. Check logs: ${LOG_FILE}"
    else
        log "⚠️  Output directory not found at ${DOCS_DIR}"
    fi
}

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

main() {
    # Parse command-line arguments
    while [ $# -gt 0 ]; do
        case "$1" in
            --no-mining)
                ENABLE_MINING=false
                shift
                ;;
            --quick)
                ENABLE_SELF_REVIEW=false
                shift
                ;;
            --rescan)
                RESCAN=true
                shift
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done

    # Create output directory and log file
    mkdir -p "${OUTPUT_DIR}"
    touch "${LOG_FILE}"

    # Print header
    log_section "AI-Discovery Demo: ${DEMO_NAME}"

    log "Repository:     ${REPO_URL}"
    log "Output:         ${DOCS_DIR}/"
    log "Provider:       ${MLC_PROVIDER}"
    log "Mining:         $([ "${ENABLE_MINING}" = true ] && echo 'enabled' || echo 'disabled')"
    log "Self-review:    $([ "${ENABLE_SELF_REVIEW}" = true ] && echo 'enabled' || echo 'disabled')"
    log "Start time:     $(date)"

    # Run pipeline stages
    check_mlc_server
    prepare_repo
    create_config

    log_section "Starting Discovery Pipeline"
    log "This will take 1-3 hours depending on repository size and options."
    log "Log file: ${LOG_FILE}"
    log ""

    START_TIME=$(date +%s)

    run_discovery

    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    MINUTES=$((DURATION / 60))
    SECONDS=$((DURATION % 60))

    # Show results
    show_results

    log_section "Discovery Complete"
    log "Duration: ${MINUTES}m ${SECONDS}s"
    log "End time: $(date)"
    log ""
    log "✅ All stages completed successfully!"
}

# ──────────────────────────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────────────────────────

if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    show_help
    exit 0
fi

main "$@"
