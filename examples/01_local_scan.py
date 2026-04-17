#!/usr/bin/env python3
"""
Example: Scan a local repository.

Usage:
    python examples/01_local_scan.py

This example shows how to scan a local repository using the CLI.
The scan will create a SQLite database and markdown documentation
in the output directory.
"""

from ai_discovery.cli import app

if __name__ == "__main__":
    # Scan a local repository
    # Replace /path/to/repo with an actual path
    # Replace myproject with a project slug

    import sys

    repo_path = "/path/to/repo"  # Change this to your repo path
    project_slug = "myproject"    # Change this to your project slug

    if repo_path == "/path/to/repo":
        print("⚠️  Please update the repo_path in this script first!")
        print(f"Example: python examples/01_local_scan.py /path/to/django")
        sys.exit(1)

    print(f"Scanning: {repo_path}")
    print(f"Project: {project_slug}")
    print()

    # This runs the CLI programmatically
    # Equivalent to: discover scan /path/to/repo -p myproject
    app(["scan", repo_path, "-p", project_slug])
