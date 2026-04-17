#!/usr/bin/env python3
"""
Example: Scan a GitHub repository.

Usage:
    python examples/02_github_scan.py

This example shows how to scan a public GitHub repository.
The CLI will clone it temporarily, analyze it, and generate docs.

You can modify the REPO_URL and PROJECT_NAME below to scan different repos.
"""

from ai_discovery.cli import app

if __name__ == "__main__":
    # Scan a public GitHub repository
    repo_url = "https://github.com/pallets/flask"
    project_slug = "flask"

    print(f"Scanning: {repo_url}")
    print(f"Project: {project_slug}")
    print()

    # This runs the CLI programmatically
    # Equivalent to: discover scan https://github.com/pallets/flask -p flask
    app(["scan", repo_url, "-p", project_slug])
