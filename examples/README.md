# AI-Discovery Usage Examples

Three ways to use ai-discovery:

1. **Docker Compose** (no local setup required)
2. **Local CLI** (Python 3.10+)
3. **Configuration** (custom YAML)

## Example 1: Scan Local Repository

```bash
cd ai-discovery
cp .env.sample .env
# Edit .env with AWS credentials
discover scan ~/my-project -p my-project
```

Output: `./data/discovery-output/`

## Example 2: Scan GitHub Repository

```bash
discover scan https://github.com/django/django -p django
```

## Example 3: Docker Scan

```bash
docker compose run --rm discovery scan https://github.com/pallets/flask -p flask
```

Or run `bash examples/03_docker_scan.sh` for an automated example.

## Example 4: Custom Configuration

```bash
discover scan ~/my-project -p my-project -c examples/config.example.yaml
```

See `config.example.yaml` for all available options.

## Example 5: Query Results

```bash
discover query "SELECT name, node_type, domain FROM code_nodes LIMIT 20"
```

---

## Running Examples Programmatically

See `01_local_scan.py` and `02_github_scan.py` for examples of calling the CLI from Python code.

```bash
python examples/01_local_scan.py
python examples/02_github_scan.py
```

---

For more help: `discover --help`
