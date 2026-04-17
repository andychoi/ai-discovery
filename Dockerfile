FROM python:3.13-slim
WORKDIR /app

# Git for repo cloning
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY . /app
RUN pip install --no-cache-dir -e .

# Output directory for scan results
RUN mkdir -p /app-output
VOLUME /app-output

# Set Python path
ENV PYTHONPATH=/app

# Entry point: discover CLI
ENTRYPOINT ["discover"]
