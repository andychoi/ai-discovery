# Installation Guide

## Quick Start

### Option 1: Editable Install (Recommended for Development)

```bash
cd /path/to/ai-discovery
pip install -e .
```

This installs the package in development mode, making the `discover` CLI available globally.

### Option 2: Regular Install

```bash
cd /path/to/ai-discovery
pip install .
```

### Option 3: Install with Development Tools

```bash
pip install -e ".[dev]"
```

Includes pytest and pytest-asyncio for running tests.

---

## Verify Installation

After installation, verify the CLI is available:

```bash
discover --help
```

You should see:

```
Brownfield codebase discovery & doc generation.

Usage: discover [OPTIONS] COMMAND [ARGS]...

Commands:
  ingest  Batch-upsert generated docs to a knowledge base.
  scan    Scan a repository, analyse its codebase...
```

---

## Running the Demo

After installation, run the demo:

```bash
# Terminal 1: Start MLX server
ollama serve mlx-community/Qwen3.5-2B-4bit

# Terminal 2: Run discovery
./demo.sh
```

---

## Project Structure

```
ai-discovery/
├── setup.py                  ← Package configuration
├── pyproject.toml           ← Modern Python package config
├── requirements.txt         ← Root-level requirements reference
├── MANIFEST.in              ← Include data files
├── INSTALL.md               ← This file
│
├── app/                     ← Main package
│   ├── __init__.py
│   ├── __main__.py          ← Entry point for python -m app
│   ├── cli.py               ← CLI interface (discover command)
│   ├── pipeline.py          ← Discovery pipeline
│   ├── config.py            ← Configuration management
│   ├── requirements.txt      ← Package dependencies
│   ├── ai/                  ← AI/ML modules
│   ├── graph/               ← Call graph building
│   ├── parsers/             ← Code parsers
│   ├── output/              ← Document generation
│   └── ...
│
├── docs/                    ← Documentation
├── examples/                ← Working examples
├── demo.sh                  ← Demo script
└── README.md
```

---

## Troubleshooting

### `discover: command not found`

The package is not installed. Run:

```bash
pip install -e .
```

Or use the fallback (works without installation):

```bash
python -m app.cli scan <repo> -p <project>
```

### Import Errors

Make sure you're in the correct directory:

```bash
cd /Users/andymini/ai/ai-discovery
pip install -e .
```

### Module Not Found Errors

If you get "No module named 'app'", ensure:

1. You're running from the project root: `pwd` should show `.../ai-discovery`
2. The package is installed: `pip show ai-discovery`
3. Your Python environment is activated (if using venv)

---

## Development Workflow

### Install for Development

```bash
# Clone the repo
git clone https://github.com/yourusername/ai-discovery
cd ai-discovery

# Create virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate

# Install in editable mode
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest app/tests/
```

### Modify Code

Changes to files in `app/` are immediately available (editable install).

### Create Distribution

```bash
# Build wheel and source distribution
pip install build
python -m build

# Upload to PyPI (if you have credentials)
pip install twine
twine upload dist/*
```

---

## Configuration Files

### `setup.py`

Traditional Python package configuration. Specifies:
- Package name and version
- Dependencies from `app/requirements.txt`
- Console script entry point (`discover` command)
- Package discovery

### `pyproject.toml`

Modern Python package configuration (PEP 518/621). Includes:
- Build system specification
- Project metadata
- Dependency specifications
- Entry points

### `MANIFEST.in`

Controls which non-Python files are included in distributions:
- README and LICENSE
- YAML configs
- Jinja2 templates
- Documentation

---

## Editable Install Explained

`pip install -e .` does:

1. **Builds** package metadata
2. **Creates** link to source directory (doesn't copy files)
3. **Installs** console scripts (`discover` command)
4. **Registers** package in site-packages

Benefits:
- Changes to Python files are immediately available
- No need to reinstall after code changes
- Can develop and test simultaneously

---

## Next Steps

1. **Install**: `pip install -e .`
2. **Verify**: `discover --help`
3. **Run demo**: `./demo.sh`
4. **Check docs**: `cat DEMO_README.md`

---

## Questions?

- Quick start: See `DEMO_QUICKSTART.txt`
- Full guide: See `DEMO_README.md`
- CLI help: `discover --help` or `discover scan --help`
- Integration: See `docs/PM4PY_INTEGRATION.md`
