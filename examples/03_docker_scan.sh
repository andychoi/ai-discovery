#!/bin/bash
set -e

# Example: Scan repository using Docker Compose
#
# Usage:
#   bash examples/03_docker_scan.sh [REPO_URL] [PROJECT_NAME]
#
# Examples:
#   bash examples/03_docker_scan.sh
#   bash examples/03_docker_scan.sh https://github.com/django/django django
#   bash examples/03_docker_scan.sh /path/to/local/repo myproject

# Default values
REPO_URL=${1:-https://github.com/pallets/flask}
PROJECT_NAME=${2:-flask}

echo "=========================================="
echo "AI-Discovery Docker Scan"
echo "=========================================="
echo "Repository: $REPO_URL"
echo "Project:    $PROJECT_NAME"
echo ""

# Check if Docker Compose is available
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ Error: Docker Compose is not installed"
    echo "Install it from: https://docs.docker.com/compose/install/"
    exit 1
fi

# Run the scan
echo "Starting scan..."
echo ""

docker compose run --rm discovery scan "$REPO_URL" -p "$PROJECT_NAME"

echo ""
echo "✅ Scan complete!"
echo ""
echo "Results saved to: ./data/discovery-output/"
echo "Database: ./data/discovery-output/discovery.db"
echo "Docs:     ./data/discovery-output/docs/"
echo ""
echo "Query results: docker compose run --rm discovery query 'SELECT * FROM code_nodes LIMIT 10'"
