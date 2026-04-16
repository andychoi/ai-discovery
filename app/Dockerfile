FROM python:3.13-slim
WORKDIR /app

# Git for repo cloning; build-essential + libsqlite3-dev not needed —
# Python 3.13's stdlib sqlite3 supports enable_load_extension in this image.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY discovery/requirements.txt .
# pysqlite3-binary has no 3.13 wheel — skip it; stdlib sqlite3 works fine here
RUN grep -v pysqlite3 requirements.txt > /tmp/reqs.txt \
    && pip install --no-cache-dir -r /tmp/reqs.txt

# Copy discovery package + the shared modules it imports.
# Build context is ./app so we can reach app/shared from here.
# - model_defaults: tier→model id mapping consumed by discovery/config.py
# - llm_invoke: Bedrock/Ollama invocation primitives consumed by ai/llm_client.py
COPY discovery/ /app/app/
COPY shared/model_defaults.py /app/shared/model_defaults.py
COPY shared/llm_invoke.py /app/shared/llm_invoke.py
RUN touch /app/shared/__init__.py

# Output directory for scan results
RUN mkdir -p /app-output
VOLUME /app-output

ENV PYTHONPATH=/app

ENTRYPOINT ["python", "-m", "app"]
